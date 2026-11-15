"""One burning-activity calendar from MODIS and VIIRS (2002-present), its validation, season timing and "now".

Harmonization by local overlap calibration
------------------------------------------
* Reference record: **Aqua MODIS** (1 km, ~13:30 local overpass) from its first full month (2002-07). Terra is
  excluded so the record does not jump when Aqua starts.
* Continuation: **S-NPP VIIRS** (375 m). S-NPP and Aqua cross the equator at the same local time, which is why the
  pair is used (as in the FireLens regional harmonization).
* Both come from the FIRMS yearly per-country archives (standard processing) and pass the same FireLens quality
  control; only vegetation fires (``type`` 0) within the disc are counted.
* On the overlap 2012-2021 (before Aqua's orbit drift), the local ratio ``r = sum(MODIS) / sum(VIIRS)`` converts VIIRS
  counts to the Aqua-MODIS scale. A year-block bootstrap gives a 95 % interval for ``r``.
* The calendar uses Aqua MODIS before 2012 and ``r x VIIRS`` from 2012; every month records which sensor it came from.
* Validation: leave-one-year-out over the overlap (``r`` refitted without year y predicts year y's MODIS total),
  compared with using raw VIIRS counts unchanged.

This is a location-level calibration, not the FireLens regional harmonization model, and it is labelled so.
"""
from __future__ import annotations

import io
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import cleaning
from . import country_archive as ca
from .catalog import BBox, get_source
from .config import Settings
from .firms_client import FirmsAPIError
from .location_features import haversine_km
from .logging_utils import get_logger

log = get_logger(__name__)

MODIS_START = pd.Period("2002-07", "M")         # first full month of Aqua MODIS
VIIRS_START = pd.Period("2012-01", "M")
OVERLAP = (pd.Period("2012-01", "M"), pd.Period("2021-12", "M"))
MIN_OVERLAP_DETECTIONS = 30
BOOTSTRAP_REPS = 1000
NOW_DAYS = 14
LABEL = "Harmonized by local MODIS–VIIRS calibration (experimental, not the FireLens regional model)"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

LIMITATIONS = [
    "Location-level calibration: one MODIS/VIIRS ratio per disc, estimated on 2012-2021. It assumes the ratio is "
    "stable over time; it is not validated outside the overlap years.",
    "Counts are satellite hotspot detections (pixels), not fires. MODIS (1 km) and VIIRS (375 m) see different fire "
    "sizes, so the ratio is an average conversion, not a per-fire match.",
    "Years from the FIRMS yearly archives are treated as fully observed; FIRMS-listed missing days are not removed.",
    "Only Aqua MODIS and S-NPP VIIRS are used (same afternoon overpass). S-NPP delivery ended on 2026-11-02.",
]


# ------------------------------------------------------------------ data
def disc_bbox(lat: float, lon: float, radius_km: float) -> BBox:
    dlat = radius_km / 111.0 + 0.01
    dlon = radius_km / (111.32 * max(math.cos(math.radians(min(abs(lat) + dlat, 89.0))), 1e-3)) + 0.01
    return BBox(max(lon - dlon, -180), max(lat - dlat, -90), min(lon + dlon, 180), min(lat + dlat, 90))


def _usable(rows: pd.DataFrame, source_id: str) -> pd.DataFrame:
    """FireLens standardization + QC on raw archive rows (strings)."""
    if rows.empty:
        return pd.DataFrame(columns=["latitude", "longitude", "acq_date", "fire_type", "satellite_code"])
    src = get_source(source_id)
    std = cleaning.validate(cleaning.standardize(rows.reset_index(drop=True), src))
    return std[std["qc_usable"]]


def disc_detections(settings: Settings, names: list[str], product: str, years: list[int], lat: float, lon: float,
                    radius_km: float, allow_download: bool, session=None, tracker=None) -> tuple[pd.DataFrame, list[int]]:
    """Usable detections of ``product`` within the disc for the given years. Returns (frame, years available)."""
    source_id = ca.PRODUCTS[product][1]
    bbox = disc_bbox(lat, lon, radius_km)
    frames, got = [], []
    if allow_download:
        # Download/convert the country-year files in parallel first (they are independent); reading is fast.
        from concurrent.futures import ThreadPoolExecutor

        def fetch(job):
            try:
                ca.ensure_country_year(settings, job[0], job[1], session=session, product=product)
            except ca.ArchiveUnavailable:
                pass
        jobs = [(n, y) for y in years for n in names if not ca.archive_path(settings, n, y, product).exists()]
        if jobs:
            if tracker is not None:
                tracker.advance(0, message=f"Harmonized calendar: downloading {len(jobs)} {product.upper()} yearly files")
            with ThreadPoolExecutor(max_workers=max(1, min(4, int(getattr(settings, "download_workers", 4))))) as pool:
                list(pool.map(fetch, jobs))
    for y in years:
        if tracker is not None:
            tracker.advance(0, message=f"Harmonized calendar: {product.upper()} {y}")
        try:
            raw = ca.tile_rows(settings, names, y, bbox, session=session, product=product, allow_download=allow_download)
        except ca.ArchiveUnavailable as e:
            log.info("Calendar: %s %s unavailable: %s", product, y, e)
            continue
        got.append(y)
        det = _usable(raw, source_id)
        if len(det):
            dist = haversine_km(lat, lon, det["latitude"], det["longitude"])
            frames.append(det[dist <= radius_km])
    det = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["acq_date", "fire_type"])
    return det, got


def _monthly(det: pd.DataFrame, months: pd.PeriodIndex) -> pd.Series:
    if det.empty:
        return pd.Series(0, index=months, dtype=float)
    return det.groupby(det["acq_date"].dt.to_period("M")).size().reindex(months, fill_value=0).astype(float)


# ------------------------------------------------------------------ calibration + validation
def calibrate(modis: pd.Series, viirs: pd.Series, seed: int = 7) -> dict:
    """Ratio on the overlap months, year-block bootstrap CI and leave-one-year-out validation."""
    ov = modis.index[(modis.index >= OVERLAP[0]) & (modis.index <= OVERLAP[1])]
    ov = ov[ov.isin(viirs.index)]
    m, v = modis.reindex(ov), viirs.reindex(ov)
    years = sorted({p.year for p in ov})
    out = {"overlap": {"start": str(OVERLAP[0]), "end": str(OVERLAP[1]), "months": int(len(ov)), "years": years,
                       "modis_aqua_detections": int(m.sum()), "viirs_snpp_detections": int(v.sum())}}
    if m.sum() < MIN_OVERLAP_DETECTIONS or v.sum() < MIN_OVERLAP_DETECTIONS or len(years) < 3:
        out.update(status="insufficient_overlap",
                   explanation=f"Fewer than {MIN_OVERLAP_DETECTIONS} detections of each sensor in 2012-2021 "
                               "(or fewer than 3 overlap years): no reliable local ratio.")
        return out
    by_year_m = m.groupby(m.index.year).sum()
    by_year_v = v.groupby(v.index.year).sum()
    r = float(m.sum() / v.sum())
    rng = np.random.default_rng(seed)
    yrs = np.array(years)
    boot = []
    for _ in range(BOOTSTRAP_REPS):
        pick = rng.choice(yrs, size=len(yrs), replace=True)
        sv = by_year_v.loc[pick].sum()
        if sv > 0:
            boot.append(by_year_m.loc[pick].sum() / sv)
    lo, hi = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))) if boot else (None, None)
    # leave-one-year-out
    rows = []
    for y in years:
        rest_v = by_year_v.drop(y).sum()
        if rest_v <= 0:
            continue
        r_y = by_year_m.drop(y).sum() / rest_v
        obs, raw = float(by_year_m[y]), float(by_year_v[y])
        rows.append({"year": int(y), "modis_observed": obs, "viirs_raw": raw, "predicted": round(r_y * raw, 2),
                     "ratio_without_year": round(float(r_y), 4)})
    val = pd.DataFrame(rows, columns=["year", "modis_observed", "viirs_raw", "predicted", "ratio_without_year"])
    pos = val[val["modis_observed"] >= 5]

    def mape(pred):
        return float(np.median(np.abs(pred - pos["modis_observed"]) / pos["modis_observed"])) if len(pos) else None

    corr = float(np.corrcoef(m.to_numpy(), v.to_numpy())[0, 1]) if m.std() > 0 and v.std() > 0 else None
    mape_cal, mape_raw = mape(pos["predicted"]), mape(pos["viirs_raw"])
    out.update(status="ok", ratio=round(r, 4), ratio_ci95=[round(lo, 4), round(hi, 4)] if lo is not None else None,
               validation={
                   "method": "leave-one-year-out over 2012-2021 (ratio refitted without the tested year)",
                   "years_tested": int(len(pos)),
                   "median_abs_pct_error_annual": round(mape_cal, 4) if mape_cal is not None else None,
                   "median_abs_pct_error_annual_raw_viirs": round(mape_raw, 4) if mape_raw is not None else None,
                   "monthly_correlation_modis_vs_viirs": round(corr, 4) if corr is not None else None,
                   "improves_on_raw_viirs": (mape_cal < mape_raw) if (mape_cal is not None and mape_raw is not None) else None,
                   "by_year": rows,
               })
    return out


# ------------------------------------------------------------------ season timing
def season_timing(values: pd.Series, today: date) -> dict:
    """Typical onset / peak / end months (10 % / max / 90 % of a fire-year's detections) and this year's status."""
    v = values.dropna()
    clim = v.groupby(v.index.month).mean().reindex(range(1, 13), fill_value=0.0)
    if clim.sum() <= 0:
        return {"status": "no_activity", "explanation": "No detections in the record."}
    start = int(clim.idxmin())                       # fire-year starts at the quietest calendar month
    order = [((start - 1 + i) % 12) + 1 for i in range(12)]
    years, blocks = [], []
    first = pd.Period(year=v.index.min().year, month=start, freq="M")
    while first + 11 <= v.index.max():
        block = v.reindex(pd.period_range(first, first + 11, freq="M"))
        if block.notna().all() and block.sum() > 0:
            blocks.append(block.to_numpy())
            cum = block.cumsum() / block.sum()
            onset = int(np.argmax(cum.to_numpy() >= 0.1))
            end = int(np.argmax(cum.to_numpy() >= 0.9))
            years.append({"fire_year": f"{first}..{first + 11}", "onset": onset, "peak": int(np.argmax(block.to_numpy())),
                          "end": end, "length_months": end - onset + 1, "detections": float(block.sum())})
        first += 12
    if len(years) < 3:
        return {"status": "insufficient_data", "explanation": "Fewer than three complete fire-years with detections."}
    med = {k: int(round(float(np.median([y[k] for y in years])))) for k in ("onset", "peak", "end", "length_months")}
    name = lambda i: MONTHS[order[i] - 1]
    out = {"status": "ok", "fire_year_starts": MONTHS[start - 1],
           "definition": "Onset = month when 10 % of a fire-year's detections are reached, end = 90 %, peak = busiest "
                         "month; the fire-year starts at the calendar month with the least activity.",
           "typical": {"onset": name(med["onset"]), "peak": name(med["peak"]), "end": name(med["end"]),
                       "length_months": med["length_months"]},
           "years": [{**y, "onset": name(y["onset"]), "peak": name(y["peak"]), "end": name(y["end"])} for y in years]}
    # current, incomplete fire-year
    cur = pd.Period(today, "M")
    fy0 = pd.Period(year=cur.year if cur.month >= start else cur.year - 1, month=start, freq="M")
    so_far = v.reindex(pd.period_range(fy0, cur - 1, freq="M")).dropna() if cur > fy0 else pd.Series(dtype=float)
    typical_total = float(np.median([y["detections"] for y in years]))
    if len(so_far) and typical_total > 0:
        cum = so_far.cumsum() / typical_total
        reached = np.nonzero(cum.to_numpy() >= 0.1)[0]
        onset_now = int(reached[0]) if len(reached) else None
        typical_by_now = float(np.median([b[: len(so_far)].sum() for b in blocks]))
        status = None
        if onset_now is not None:
            diff = onset_now - med["onset"]
            status = "earlier_than_usual" if diff <= -1 else "later_than_usual" if diff >= 1 else "as_usual"
        elif len(so_far) > med["onset"]:
            status = "not_started_later_than_usual"
        out["current"] = {"fire_year_start": str(fy0), "months_elapsed": int(len(so_far)),
                          "detections_so_far": float(so_far.sum()),
                          "typical_detections_by_now": round(typical_by_now, 2),
                          "onset": name(onset_now) if onset_now is not None else None,
                          "onset_status": status}
    return out


# ------------------------------------------------------------------ current conditions
def current_conditions(settings: Settings, client, lat: float, lon: float, radius_km: float, today: date,
                       viirs_history: pd.DataFrame, static_cells: set, history_years: list[int] | None = None) -> dict:
    """Last NOW_DAYS days of NOAA-20 VIIRS NRT vs the same calendar window in past S-NPP years."""
    start = today - timedelta(days=NOW_DAYS - 1)
    base = {"window": {"start": str(start), "end": str(today), "days": NOW_DAYS},
            "sensor": "VIIRS NOAA-20 near real-time", "reference": "S-NPP VIIRS, same calendar window, 2012 onwards",
            "limitations": ["Different satellites carrying the same VIIRS instrument: NOAA-20 and S-NPP counts are "
                            "comparable but not identical.",
                            "Near-real-time data may still change; the most recent day can be incomplete.",
                            "A count above the past range is unusual for this place, not a forecast of danger."]}
    if client is None:
        return {**base, "status": "unavailable", "explanation": "No FIRMS MAP_KEY: near-real-time data cannot be read."}
    bbox = disc_bbox(lat, lon, radius_km)
    frames, cur = [], start
    try:
        while cur <= today:
            days = min(5, (today - cur).days + 1)
            text = client.area_csv("VIIRS_NOAA20_NRT", bbox, cur, days)
            frames.append(pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False))
            cur += timedelta(days=days)
    except FirmsAPIError as e:
        return {**base, "status": "unavailable", "explanation": f"FIRMS near-real-time request failed: {e}"}
    raw = pd.concat(frames, ignore_index=True)
    det = _usable(raw, "VIIRS_NOAA20_NRT")
    if len(det):
        det = det[haversine_km(lat, lon, det["latitude"], det["longitude"]) <= radius_km]
        cells = list(zip(np.floor(det["latitude"] * 100).astype(int), np.floor(det["longitude"] * 100).astype(int)))
        det = det[[c not in static_cells for c in cells]]
    now = int(len(det))
    hist = []
    years = history_years if history_years is not None else (
        list(range(2012, today.year)) if len(viirs_history) else [])
    if years:
        h = viirs_history if len(viirs_history) else pd.DataFrame({"acq_date": pd.Series(dtype="datetime64[ns]")})
        for y in sorted(years):
            try:
                a, b = start.replace(year=y), today.replace(year=y)
            except ValueError:   # 29 February
                a, b = start.replace(year=y, day=28), today.replace(year=y, day=28)
            d = h["acq_date"].dt.date
            hist.append({"year": y, "detections": int(((d >= a) & (d <= b)).sum())})
    counts = np.array([x["detections"] for x in hist], dtype=float)
    if len(counts) < 5:
        return {**base, "status": "insufficient_history", "detections": now, "history": hist,
                "explanation": "Fewer than five past years for the same window."}
    pct = float((counts < now).mean() * 100 + (counts == now).mean() * 50)
    q25, q50, q75, q90 = np.percentile(counts, [25, 50, 75, 90])
    if now == 0 and q90 == 0:
        cat = "quiet_as_usual"
    elif now <= 2 and q90 <= 1:
        cat = "isolated_detections"      # a couple of pixels where past years were (almost) quiet: not alarming
    elif now > counts.max():
        cat = "highest_on_record"
    elif now > q90:
        cat = "well_above_normal"
    elif now > q75:
        cat = "above_normal"
    elif now < q25:
        cat = "below_normal"
    else:
        cat = "near_normal"
    return {**base, "status": "ok", "detections": now, "category": cat, "percentile": round(pct, 1),
            "history_median": float(q50), "history_p90": float(q90), "history_max": float(counts.max()),
            "history": hist,
            "explanation": f"{now} detection{'' if now == 1 else 's'} in the last {NOW_DAYS} days vs a median of "
                           f"{q50:g} in the same window of {len(counts)} past years."}


# ------------------------------------------------------------------ entry point
def build(settings: Settings, lat: float, lon: float, radius_km: float, today: date, ml_monthly: pd.DataFrame | None,
          allow_download: bool = True, client=None, session=None, tracker=None) -> tuple[dict, dict]:
    """Return (unified_calendar, current_conditions). Never raises for data problems; reports a status instead."""
    try:
        names = ca.countries_for_bbox(disc_bbox(lat, lon, radius_km))
    except ca.ArchiveUnavailable as e:
        msg = {"status": "unavailable", "explanation": f"FIRMS country archives cannot cover this area: {e}"}
        return {**msg, "label": LABEL, "limitations": LIMITATIONS}, {**msg, "window": None}
    last_year = today.year - 1
    modis, m_years = disc_detections(settings, names, "modis", list(range(MODIS_START.year, last_year + 1)),
                                     lat, lon, radius_km, allow_download, session, tracker)
    viirs, v_years = disc_detections(settings, names, "viirs-snpp", list(range(VIIRS_START.year, last_year + 1)),
                                     lat, lon, radius_km, allow_download, session, tracker)
    modis = modis[modis["satellite_code"].isin(["Aqua", "A"]) & modis["fire_type"].eq(0)] if len(modis) else modis
    static_cells = set()
    if len(viirs):
        nv = viirs[viirs["fire_type"].fillna(0) != 0]
        static_cells = set(zip(np.floor(nv["latitude"] * 100).astype(int), np.floor(nv["longitude"] * 100).astype(int)))
        viirs = viirs[viirs["fire_type"].eq(0)]
    now = current_conditions(settings, client, lat, lon, radius_km, today, viirs, static_cells,
                             history_years=[y for y in v_years if y < today.year])
    if not m_years or not v_years:
        return {"status": "unavailable", "label": LABEL, "limitations": LIMITATIONS, "countries": names,
                "explanation": "MODIS or VIIRS yearly archives are not available for this area"
                               + ("" if allow_download else " in the local cache (downloads disabled)") + "."}, now

    m_months = pd.period_range(MODIS_START, pd.Period(f"{max(m_years)}-12", "M"), freq="M")
    m_months = m_months[[p.year in m_years for p in m_months]]
    v_months = pd.period_range(VIIRS_START, pd.Period(f"{max(v_years)}-12", "M"), freq="M")
    v_months = v_months[[p.year in v_years for p in v_months]]
    m_series, v_series = _monthly(modis, m_months), _monthly(viirs, v_months)
    # continue VIIRS with the FireLens S-NPP record (SP then NRT) after the last archive year
    if ml_monthly is not None and len(ml_monthly):
        extra = ml_monthly[ml_monthly.index > v_months.max()] if len(v_months) else ml_monthly
        extra = extra[extra["observed"]]["count"].astype(float)
        v_series = pd.concat([v_series, extra])
    cal = calibrate(m_series, v_series)
    cal.update(label=LABEL, method="local_overlap_calibration", countries=names,
               reference_sensor="Aqua MODIS (MODIS_SP, 1 km)", calibrated_sensor="S-NPP VIIRS (VIIRS_SNPP_SP/NRT, 375 m)",
               limitations=LIMITATIONS)
    if cal["status"] != "ok":
        return cal, now
    r, ci = cal["ratio"], cal["ratio_ci95"] or [None, None]
    rows = []
    for p in pd.period_range(MODIS_START, v_series.index.max(), freq="M"):
        mv = float(m_series[p]) if p in m_series.index else None
        vv = float(v_series[p]) if p in v_series.index else None
        if p < VIIRS_START:
            val, src, iv = mv, "modis_aqua" if mv is not None else None, None
        else:
            val = round(r * vv, 2) if vv is not None else None
            src = "viirs_snpp_scaled" if vv is not None else None
            iv = [round(ci[0] * vv, 2), round(ci[1] * vv, 2)] if vv is not None and ci[0] is not None else None
        rows.append({"month": str(p), "value": val, "source": src, "interval_95": iv,
                     "modis_aqua": mv, "viirs_snpp": vv})
    cal["monthly"] = rows
    cal["unit"] = "detections on the Aqua-MODIS scale"
    series = pd.Series({pd.Period(x["month"], "M"): x["value"] for x in rows}, dtype=float)
    cal["season"] = season_timing(series, today)
    return cal, now
