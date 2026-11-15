"""FireLens universal location analysis: one call for any point on Earth.

Synchronous use::

    from firelens.inference import analyze_location
    result = analyze_location(37.7, -119.5, radius_km=25)            # JSON-serialisable dict

Background use (job manager, progress polling / callbacks / async stream)::

    from firelens.inference import start_analysis, get_job_status, get_job_result
    job_id = start_analysis(-13.0, 24.0, radius_km=50)
    get_job_status(job_id)       # status, stage, progress_percent, eta_seconds, warnings
    get_job_result(job_id)       # the same dict analyze_location returns

See docs/GLOBAL_INFERENCE_API.md for the output schema and limitations.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from . import global_model as G
from . import location_data as LD
from . import location_features as LF
from . import location_patterns as LP
from . import unified_calendar as UC
from .config import Settings, load_settings
from .logging_utils import get_logger
from .progress import ProgressTracker

log = get_logger(__name__)

SCHEMA_VERSION = "1.0"
ENGINE_VERSION = "0.1.0"
DEFAULT_RADIUS_KM = 25.0
MIN_RADIUS_KM, MAX_RADIUS_KM = 1.0, 200.0
DEFAULT_HISTORY_YEARS = 10
MIN_HISTORY_YEARS, MAX_HISTORY_YEARS = 3, 14
FORECAST_HORIZON_MONTHS = 12      # target month at most 12 months after the last observed month
XGB_MAX_LEAD = max(G.LEADS)
MIN_OBSERVED_MONTHS = 36
MIN_TARGET_MONTH_YEARS = 3
ATTRIBUTION = ("NASA FIRMS (Fire Information for Resource Management System), VIIRS 375 m active fire product "
               "from Suomi NPP: VIIRS_SNPP_SP (standard processing) and VIIRS_SNPP_NRT (near real-time). "
               "https://firms.modaps.eosdis.nasa.gov")


class InvalidRequestError(ValueError):
    """The request parameters are invalid (raised before any work starts)."""


# ------------------------------------------------------------------ validation
def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def validate_request(latitude, longitude, radius_km=DEFAULT_RADIUS_KM, target_month=None,
                     history_years=DEFAULT_HISTORY_YEARS, today: date | None = None) -> dict:
    today = today or _utc_today()
    try:
        lat, lon, rad = float(latitude), float(longitude), float(radius_km)
    except (TypeError, ValueError):
        raise InvalidRequestError("latitude, longitude and radius_km must be numbers")
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        raise InvalidRequestError("latitude/longitude must be numbers, not booleans")
    if not all(math.isfinite(v) for v in (lat, lon, rad)):
        raise InvalidRequestError("latitude, longitude and radius_km must be finite")
    if not -90 <= lat <= 90:
        raise InvalidRequestError(f"latitude {lat} outside [-90, 90]")
    if not -180 <= lon <= 180:
        raise InvalidRequestError(f"longitude {lon} outside [-180, 180]")
    if not MIN_RADIUS_KM <= rad <= MAX_RADIUS_KM:
        raise InvalidRequestError(f"radius_km {rad} outside [{MIN_RADIUS_KM:g}, {MAX_RADIUS_KM:g}]")
    if isinstance(history_years, bool) or not isinstance(history_years, (int, np.integer)) \
            or not MIN_HISTORY_YEARS <= int(history_years) <= MAX_HISTORY_YEARS:
        raise InvalidRequestError(f"history_years must be an integer in [{MIN_HISTORY_YEARS}, {MAX_HISTORY_YEARS}]")
    current = pd.Period(today, "M")
    if target_month is None:
        target = current + 1
    else:
        if not isinstance(target_month, str) or not re.fullmatch(r"\d{4}-\d{2}", target_month):
            raise InvalidRequestError("target_month must be a 'YYYY-MM' string")
        try:
            target = pd.Period(target_month, "M")
        except ValueError:
            raise InvalidRequestError(f"target_month {target_month!r} is not a valid month")
        if not 1 <= int(target_month[5:]) <= 12:
            raise InvalidRequestError(f"target_month {target_month!r} is not a valid month")
    if target < current:
        raise InvalidRequestError(f"target_month {target} is in the past; historical months are in historical_activity")
    if target > current + FORECAST_HORIZON_MONTHS:
        raise InvalidRequestError(f"target_month {target} is beyond the {FORECAST_HORIZON_MONTHS}-month forecast horizon")
    return {"latitude": lat, "longitude": lon, "radius_km": rad, "target_month": str(target),
            "history_years": int(history_years), "request_date_utc": str(today)}


# ------------------------------------------------------------------ helpers
def engine_settings(settings: Settings | None = None) -> Settings:
    s = settings or load_settings(require_key=False)
    # interactive engine: bounded waits per request (the pipeline default is 300 s x 6 attempts)
    return s.with_overrides(request_timeout_s=min(s.request_timeout_s, 60.0), max_retries=min(s.max_retries, 4))


def _clean(o):
    """Recursively convert to JSON-safe builtins (NaN/inf -> None)."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return float(o) if math.isfinite(float(o)) else None
    if isinstance(o, (date, datetime, pd.Timestamp, pd.Period)):
        return str(o)
    if o is None or isinstance(o, str):
        return o
    return str(o)


def _harmonization_block(lat: float, lon: float, settings: Settings) -> dict:
    region = G.training_region_of(lat, lon)
    status = None
    p = settings.reports_dir / "results" / "harmonization" / "validation_status.json"
    if region and p.exists():
        st = json.loads(p.read_text(encoding="utf-8")).get(region, {})
        status = {"overall": st.get("status"), "count": st.get("count", {}).get("status"),
                  "frp": st.get("frp", {}).get("status")}
    return {
        "series_used": "consistent_sensor: S-NPP VIIRS only (no MODIS-VIIRS mixing)",
        "modis_viirs_mixed": False,
        "project_region": region,
        "regional_harmonization_status": status,
        "note": ("MODIS-equivalent harmonisation was validated only for detection counts in California and "
                 "Zambia/Angola, at regional scale and over the jointly observed domain; it is not validated at "
                 "location (disc) scale, so location statistics use the consistent S-NPP VIIRS record instead."),
    }


def _reliability_for(rel: dict, setting: str, lead: int, method_key: str, p: float) -> dict | None:
    tab = rel.get(setting, {}).get(str(min(lead, XGB_MAX_LEAD)), {}).get(method_key)
    if not tab:
        return None
    for b in tab:
        lo, hi = b["bin"]
        if lo <= p < hi or (hi >= 1 and p <= hi):
            return {"forecast_bin": [lo, hi], "n_heldout_forecasts": b["n"],
                    "observed_frequency_heldout": round(b["observed_frequency"], 4),
                    "mean_forecast_heldout": round(b["mean_forecast"], 4)}
    return None


def _category(ratio: float | None, p: float, p_norm: float) -> str | None:
    if ratio is None:
        return None
    if p < 0.02 and p_norm < 0.02:
        return "near_normal"
    return "below_normal" if ratio < 0.67 else "above_normal" if ratio > 1.5 else "near_normal"


# ------------------------------------------------------------------ prediction
def _predict(req: dict, m: pd.DataFrame, obs: LD.Observations, settings: Settings, tracker) -> tuple[dict, list[str]]:
    lim: list[str] = []
    target = pd.Period(req["target_month"], "M")
    base = {"target_month": str(target), "status": "insufficient_data", "probability": None, "method": None,
            "target_definition": ("Probability of at least one usable S-NPP VIIRS vegetation-fire detection within "
                                  f"{req['radius_km']:g} km of the point during {target} (satellite detection, "
                                  "not an individual wildfire ignition)."),
            "relative_activity": None}
    observed = m.index[m["observed"]]
    if len(observed) == 0:
        base["reason"] = "no_observed_months"
        return base, lim
    last = observed.max()
    lead = (target - last).n
    base.update({"last_observed_month": str(last), "lead_months": int(lead)})
    if lead < 1 or lead > FORECAST_HORIZON_MONTHS:
        base["reason"] = f"target is {lead} months after the last observed month; horizon is 1..{FORECAST_HORIZON_MONTHS}"
        return base, lim
    n_obs = int(m["observed"].sum())
    same = m[m["observed"] & (m.index.month == target.month)]
    if n_obs < MIN_OBSERVED_MONTHS or len(same) < MIN_TARGET_MONTH_YEARS:
        base["reason"] = (f"insufficient observed history: {n_obs} observed months (need {MIN_OBSERVED_MONTHS}) and "
                          f"{len(same)} observed {target.strftime('%B')}s (need {MIN_TARGET_MONTH_YEARS})")
        return base, lim
    ctx_total = int((obs.detections["month"].isin(observed)).sum()) if len(obs.detections) else 0
    if ctx_total == 0:
        n = len(same)
        base["reason"] = "no_fire_detections_within_100km"
        base["explanation"] = (f"No usable vegetation-fire detection within {LF.CONTEXT_KM:g} km in {n_obs} observed "
                               "months. The satellite record cannot distinguish a non-flammable or unmonitored surface "
                               "(open water, ice, bare rock, dense urban area) from a fire regime too rare to observe, "
                               "so no probability is issued.")
        base["observed_rate_upper_bound_95"] = {
            "value": round(1 - 0.05 ** (1 / n), 4),
            "definition": f"one-sided 95% upper bound on the per-month rate for {target.strftime('%B')} from 0 events "
                          f"in {n} observed years (exact binomial)"}
        return base, lim

    model, meta = G.load(settings)
    months = m.index
    ext = pd.period_range(months[0], max(target, months[-1]), freq="M")
    pad = len(ext) - len(months)
    d = obs.detections
    ctx = np.array(d.groupby("month").size().reindex(months, fill_value=0), dtype=float) if len(d) else np.zeros(len(months))
    ctx[~m["observed"].to_numpy()] = np.nan
    arr = lambda v: np.concatenate([v, np.full(pad, np.nan)])[None, :]
    eff_lead = min(lead, LF.MAX_LEAD - 1)
    feats = LF.build_features(arr(m["count"].to_numpy()), arr(m["frp"].to_numpy()), arr(ctx), ext,
                              [req["latitude"]], [req["longitude"]], [req["radius_km"]], eff_lead)
    row = LF.to_frame(feats, ext, min_hist_months=0, require_target=False)
    row = row[row["month"] == str(target)].iloc[[0]]
    row["lead"] = float(min(lead, XGB_MAX_LEAD))
    region = G.training_region_of(req["latitude"], req["longitude"])
    gate = meta["method_gate"]
    gl = str(min(lead, XGB_MAX_LEAD))
    g = gate["in_domain"][region][gl] if region else gate["out_of_domain"][gl]
    setting = "in_domain" if region else "out_of_domain"
    variant = g["baseline_variant"]
    p_base = float(model.baseline(row, calibrated=(variant == "seasonal_calibrated"))[0])
    xgb_ok_inputs = lead <= XGB_MAX_LEAD and float(row["n_hist_months"].iloc[0]) >= 12
    p_xgb = float(model.predict(row)[0]) if xgb_ok_inputs else None
    use_xgb = g["method"] == "xgboost_pooled" and xgb_ok_inputs
    if use_xgb:
        p, method, mkey = p_xgb, "xgboost_pooled", "xgboost_pooled"
        reason = (f"pooled XGBoost passed the held-out validation rule for "
                  f"{'region ' + region if region else 'locations outside the training regions'} at lead {gl}")
    else:
        p, method, mkey = p_base, "seasonal_baseline", variant
        if lead > XGB_MAX_LEAD:
            reason = f"lead {lead} months exceeds the XGBoost horizon ({XGB_MAX_LEAD}); seasonal baseline used"
        else:
            where = f"inside training region {region}" if region else "outside the five training regions"
            reason = (f"pooled XGBoost did not pass the held-out validation rule for locations {where} at lead "
                      f"{gl} (Brier skill vs seasonal baseline not significantly > 0); seasonal baseline used")
    k = int((same["count"] > 0).sum())
    n = int(len(same))
    p_norm = k / n
    ratio = p / p_norm if p_norm > 0 else None
    recent = m[m["observed"]].tail(3)
    rec_rows = []
    for per, r in recent.iterrows():
        others = m[m["observed"] & (m.index.month == per.month) & (m.index != per)]["count"]
        rec_rows.append({"month": str(per), "detections": int(r["count"]),
                         "same_month_median_other_years": float(others.median()) if len(others) else None})
    ev_key = region if region else "all"
    ev_src = meta["validation"]["temporal" if region else "loro"].get(ev_key, {}).get(gl, {})
    m_key = {"xgboost_pooled": "xgboost", "seasonal_raw": "seasonal_raw", "seasonal_calibrated": "seasonal_calibrated"}[mkey]
    base.update({
        "status": "ok", "probability": round(p, 4), "method": method, "method_reason": reason,
        "baseline_variant": None if use_xgb else variant,
        "seasonal_norm": {"calendar_month": target.strftime("%B"), "years_observed": n,
                          "years_with_detections": k, "frequency": round(p_norm, 4),
                          "median_detections": float(same["count"].median()),
                          "mean_detections": round(float(same["count"].mean()), 2)},
        "relative_activity": {
            "ratio_to_seasonal_norm": round(ratio, 3) if ratio is not None else None,
            "category": (_category(ratio, p, p_norm) if ratio is not None
                         else "near_normal" if p < 0.02 else "no_past_activity_this_month"),
            "definition": ("forecast probability / observed share of past years with >= 1 detection in the same "
                           "calendar month; below_normal < 0.67, above_normal > 1.5 (both < 0.02: near_normal). "
                           "no_past_activity_this_month: no past year had a detection in this calendar month, so the "
                           "ratio is undefined; the probability then comes only from the disc's overall fire frequency "
                           "(shrinkage prior)."),
            "recent_observed_months": rec_rows},
        "validation_evidence": {
            "setting": f"{'temporal hold-out (2020-2022) in ' + region if region else 'leave-one-region-out (2020-2022), pooled over 5 held-out regions'}",
            "lead_evaluated": int(gl), "method_metrics_heldout": ev_src.get(m_key),
            "xgboost_vs_best_baseline_brier_skill": ev_src.get("xgb_vs_best_baseline")},
        "uncertainty": {
            "empirical_reliability": _reliability_for(meta["reliability"], setting, lead, mkey, p),
            "note": ("Held-out frequency of detection among past forecasts in the same probability bin, from the "
                     "validation setting above. No interval is given for this location: it is not supported "
                     "empirically.")},
        "model_comparison": {"xgboost_pooled": {
            "probability": round(p_xgb, 4) if p_xgb is not None else None, "used": use_xgb,
            "validated_for_this_location_and_lead": g["method"] == "xgboost_pooled",
            "note": "reported for transparency; only the 'probability' field above is the engine's forecast"},
            "seasonal_baseline": {"probability": round(p_base, 4), "variant": variant, "used": not use_xgb}},
        "model": {"name": meta["model"], "design": meta.get("design_variant"),
                  "trained_regions": meta["training_regions"], "training_period": meta["training_period"]},
    })
    if region is None:
        lim.append("Location is outside the five training regions; the forecast is the location's own seasonal "
                   "climatology, whose held-out skill was measured only in those regions.")
    if lead > 1:
        lim.append(f"Lead time is {lead} months: the last fully observed month is {last}, "
                   f"because the current month is incomplete and near-real-time data lag.")
    return base, lim


# ------------------------------------------------------------------ core
def _unified_calendar(settings, lat, lon, rad, today, m, allow_download, tracker):
    """MODIS+VIIRS calendar (local calibration), season timing and current conditions; never fails the analysis."""
    if not getattr(settings, "use_country_archive", False):
        off = {"status": "disabled", "explanation": "FIRMS yearly archives are disabled (FIRELENS_COUNTRY_ARCHIVE=0)."}
        return dict(off), dict(off)
    client = None
    if settings.map_key and allow_download:
        try:
            client = LD.shared_client(settings)
        except Exception as e:  # no key / bad key: the calendar still works from the archives
            log.warning("No FIRMS client for current conditions: %s", e)
    try:
        cal, now = UC.build(settings, lat, lon, rad, today, m, allow_download=allow_download, client=client,
                            tracker=tracker)
        if not allow_download and now.get("status") == "unavailable":
            now["explanation"] = "Cache-only analysis: near-real-time data were not requested."
        return cal, now
    except Exception as e:
        if getattr(e, "abort_job", False):
            raise
        log.exception("Unified calendar failed")
        fail = {"status": "failed", "explanation": f"The MODIS+VIIRS calendar could not be built ({type(e).__name__})."}
        return dict(fail), dict(fail)


def run_analysis(req: dict, tracker: ProgressTracker, settings: Settings | None = None,
                 allow_download: bool = True) -> dict:
    """Run one analysis for a validated request (used by analyze_location and the job manager)."""
    settings = engine_settings(settings)
    tracker.start_stage("validate", message="Request validated")
    tracker.end_stage()
    lat, lon, rad = req["latitude"], req["longitude"], req["radius_km"]
    today = date.fromisoformat(req["request_date_utc"])
    # Archive-only mode: the history ends with the last completed year of the FIRMS yearly archives, so no Area API
    # downloads are needed (the current year is not in the archives).
    archive_only = bool(getattr(settings, "archive_only_history", False))
    end_day = today
    skip = None
    if archive_only:
        # Completed years from the yearly archives + only the last ~3 months from the Area API (enough for the
        # forecast's 1-3 month lead); earlier months of the current year are not retrieved and stay unobserved.
        recent_start = (pd.Period(today, "M") - 3).start_time.date()
        if recent_start > date(today.year, 1, 1):
            skip = (date(today.year, 1, 1), recent_start - pd.Timedelta(days=1).to_pytimedelta())
    cur = pd.Period(end_day, "M")
    start_m = cur - 12 * req["history_years"]
    start = max(start_m.start_time.date(), LD.SERIES_START)
    months = pd.period_range(pd.Period(start, "M"), cur, freq="M")
    tiles = LD.analysis_tiles(lat, lon, rad)
    plan, info = LD.ensure_data(settings, tiles, start, end_day, tracker, allow_download=allow_download, skip=skip)

    tracker.start_stage("load", message="Loading cached observations")
    with LD.PROCESS_LOCK:
        obs = LD.load_observations(settings, plan, lat, lon, rad, months)
    tracker.end_stage()

    tracker.start_stage("patterns", message="Computing historical statistics and patterns")
    m = LP.disc_monthly(obs.detections, obs.coverage, months, rad)
    hist = LP.historical_activity(m, obs.detections, rad)
    seas = LP.seasonal_patterns(m)
    trend = LP.long_term_trends(m)
    anomalies, anom_meta = LP.monthly_anomaly_list(m)
    cell_out, cell_meta = LP.cell_month_outliers(obs.detections, m, rad)
    hs, hs_meta = LP.hotspots(obs.detections, m, lat, lon, rad)
    uc_cal, uc_now = _unified_calendar(settings, lat, lon, rad, today, m, allow_download, tracker)
    tracker.end_stage()

    tracker.start_stage("predict", message="Forecasting the target month")
    pred, pred_lim = _predict(req, m, obs, settings, tracker)
    tracker.end_stage()

    tracker.start_stage("finalize")
    unobs = m[~m["observed"]]
    complete = info["chunks_failed"] == 0 and not info["download_skipped"]
    cov = {
        "sensor_series": "S-NPP VIIRS 375 m (VIIRS_SNPP_SP, then VIIRS_SNPP_NRT after the SP record ends)",
        "window": {"start": str(start), "end": str(end_day), "months": len(months)},
        "archive_only_history": archive_only,
        "not_retrieved": {"start": str(skip[0]), "end": str(skip[1])} if skip else None,
        "products": [{"product": s, "first_day": str(a), "last_day": str(b)} for s, a, b in plan.products],
        "product_availability": info["product_availability"],
        "min_month_coverage_for_observed": LD.MIN_MONTH_COVERAGE,
        "months_observed": int(m["observed"].sum()), "months_unobserved": int(len(unobs)),
        "unobserved_months": [{"month": str(p), "coverage_fraction": round(float(r.coverage_fraction), 3)}
                              for p, r in unobs.iterrows()],
        "last_observed_month": str(m.index[m["observed"]].max()) if m["observed"].any() else None,
        "zero_vs_missing": "months with coverage >= 0.9 and no detections are observed zeros; months below 0.9 "
                           "are unobserved and reported as null",
        "retrieval": {"availability_table": info["availability"],
                      "chunks_downloaded_this_call": info["chunks_downloaded"],
                      "chunks_failed_this_call": info["chunks_failed"],
                      "rows_downloaded_this_call": info["rows_downloaded"],
                      "chunks_reused_from_concurrent_job": info["chunks_reused_from_concurrent_job"],
                      "chunks_from_country_archive": info.get("chunks_from_country_archive", 0),
                      "country_archive": info.get("country_archive", []),
                      "downloads_skipped": info["download_skipped"],
                      "chunks_still_missing": plan.n_requests},
        "cache_sources": obs.provenance["cache_sources"],
        "exclusions": {k: v for k, v in obs.provenance.items() if k.startswith("excluded")},
        "firms_missing_dates_table": obs.provenance["firms_missing_dates_table"],
        "complete": bool(complete and plan.n_requests == 0),
        "attribution": ATTRIBUTION,
    }
    limitations = [
        "Counts are satellite hotspot detections (375 m pixels at ~01:30 and ~13:30 local overpass), not fires, "
        "burned area or ignitions; one fire produces many detections and short fires between overpasses are missed.",
        "Clouds, smoke and thick canopy hide fires; detections are not corrected for cloud cover.",
        "Near-real-time (NRT) detections lack the FIRMS 'type' flag; static industrial/volcanic sources are removed "
        "only where the standard product flagged them in the analysis window.",
        "No weather, fuel or land-cover data are used.",
        "The pooled XGBoost model is validated only in five regions (California, Zambia/Angola, Rondonia, Yakutia, "
        "Punjab); it is not claimed to be globally validated.",
    ] + pred_lim
    if skip:
        limitations.append(f"To keep analyses fast, {skip[0]:%Y-%m-%d} to {skip[1]:%Y-%m-%d} is not retrieved (completed "
                            "years come from the FIRMS yearly archives, the last ~3 months from the Area API); those "
                            "months are unobserved, not zero.")
    if not cov["complete"]:
        limitations.append("Some requested days could not be retrieved; those months are unobserved (see data_coverage).")
    if trend.get("status") == "ok" and not trend.get("significant_at_0.05"):
        limitations.append("The long-term trend is not statistically significant at 0.05.")
    status = "insufficient_data" if pred["status"] != "ok" else ("ok" if cov["complete"] else "partial")
    result = {
        "schema_version": SCHEMA_VERSION, "engine_version": ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "location": {"latitude": lat, "longitude": lon, "radius_km": rad,
                     "cache_tiles": [t.to_api() for t in tiles]},
        "request": req,
        "historical_activity": hist,
        "seasonal_patterns": seas,
        "long_term_trends": trend,
        "anomalies": anomalies,
        "anomaly_detection": {**anom_meta, "cell_month_outliers": cell_out, "cell_month_outliers_method": cell_meta},
        "hotspots": hs,
        "hotspot_analysis": hs_meta,
        "prediction": pred,
        "data_coverage": cov,
        "harmonization": _harmonization_block(lat, lon, settings),
        "unified_calendar": uc_cal,
        "current_conditions": uc_now,
        "warnings": [w["message"] for w in tracker.warnings],
        "limitations": limitations,
    }
    result = _clean(result)
    json.dumps(result, allow_nan=False)  # guarantee strict JSON
    tracker.end_stage()
    tracker.complete()
    return result


def analyze_location(latitude: float, longitude: float, radius_km: float = DEFAULT_RADIUS_KM,
                     target_month: str | None = None, *, history_years: int = DEFAULT_HISTORY_YEARS,
                     allow_download: bool = True, progress_callback=None,
                     settings: Settings | None = None) -> dict:
    """Analyse any location synchronously and return a JSON-serialisable dict.

    ``progress_callback(event: dict)`` (optional) receives progress events as
    the analysis runs; see :mod:`firelens.progress`.
    Raises :class:`InvalidRequestError` for invalid input. Data/API failures
    raise the underlying FireLens/FIRMS exception after emitting a 'failed' event.
    """
    req = validate_request(latitude, longitude, radius_km, target_month, history_years)
    tracker = ProgressTracker(job_id=None, callbacks=[progress_callback] if progress_callback else None)
    try:
        return run_analysis(req, tracker, settings, allow_download=allow_download)
    except Exception as e:
        from .jobs import classify_error
        et, recoverable = classify_error(e)
        tracker.fail(et, str(e), recoverable)
        raise


# ------------------------------------------------------------------ background jobs (default manager)
def get_job_manager(**kwargs):
    from .jobs import default_manager
    return default_manager(**kwargs)


def start_analysis(latitude: float, longitude: float, radius_km: float = DEFAULT_RADIUS_KM,
                   target_month: str | None = None, **kwargs) -> str:
    """Queue an analysis on the default job manager and return its job ID immediately."""
    return get_job_manager().start_analysis(latitude, longitude, radius_km, target_month, **kwargs)


def get_job_status(job_id: str) -> dict:
    return get_job_manager().get_job_status(job_id)


def get_job_result(job_id: str, timeout: float | None = None) -> dict:
    return get_job_manager().get_job_result(job_id, timeout=timeout)
