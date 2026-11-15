"""Orchestration for pattern discovery and next-month forecasting."""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from . import forecast as F
from . import harmonize as H
from . import patterns as P
from .catalog import REGIONS
from .config import Settings
from .io import write_parquet
from .logging_utils import get_logger

log = get_logger(__name__)


def _conv(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


def harmonized_regional_series(settings: Settings, region: str, start: date, end: date) -> pd.DataFrame | None:
    """Full-domain MODIS-equivalent monthly series from VIIRS using the saved
    local harmonisation model for ``region`` (None if no model exists)."""
    import joblib
    from .experiments import standardize_geometry
    from .matching import build_region
    path = settings.data_dir / "models" / "harmonization" / f"{region}.joblib"
    if not path.exists():
        return None
    bundle = joblib.load(path)
    fm = bundle["model"]
    res = build_region(settings, REGIONS[region], start, end)
    cl = H.add_features(res["clusters"])
    d = standardize_geometry(cl, fill_ax=0.5)
    parts = fm.predict_parts(d)
    g = pd.DataFrame({"month": d["month"], "c": parts["p"] * parts["mu"] * fm.k_n,
                      "f": parts["p"] * parts["phi"] * fm.k_f}).groupby("month").sum()
    g.index = pd.PeriodIndex(g.index, freq="M")
    months = pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M")
    return g.rename(columns={"c": "count", "f": "frp"}).reindex(months, fill_value=0)


def run_patterns(settings: Settings, regions: list[str], start: date, end: date,
                 harmonized_regions: list[str]) -> dict:
    out_dir = settings.reports_dir / "results" / "patterns"
    out_dir.mkdir(parents=True, exist_ok=True)
    results, anomalies_all, hotspots_all, series_all = {}, [], [], []
    for r in regions:
        series = {"viirs_snpp": P.regional_monthly(settings, r, "VIIRS_SNPP_SP", start, end),
                  "modis_aqua": P.regional_monthly(settings, r, "MODIS_SP", start, end, platform="Aqua")}
        if r in harmonized_regions:
            hs = harmonized_regional_series(settings, r, start, end)
            if hs is not None:
                series["modis_equivalent"] = hs
        # Data for both sensors were acquired for the whole window, so months with
        # zero detections are real zeros (no trimming). S-NPP VIIRS SP starts
        # 2012-01-20, so January 2012 is partial for VIIRS (low-activity month).
        res_r = {"series_available": {}, "window": [str(pd.Period(start, "M")), str(pd.Period(end, "M"))],
                 "notes": ["VIIRS S-NPP record starts 2012-01-20; January 2012 is partial for VIIRS."]
                 if start < date(2012, 1, 20) else []}
        for name, s in series.items():
            nonzero = s.index[s["count"] > 0]
            res_r["series_available"][name] = [str(nonzero.min()), str(nonzero.max())] if len(nonzero) else None
            ss = s
            res_r[name] = {"seasonality": P.seasonal_profile(ss["count"]),
                           "seasonality_frp": P.seasonal_profile(ss["frp"]),
                           "trend_count": P.trend(ss["count"]), "trend_frp": P.trend(ss["frp"])}
            an = P.monthly_anomalies(ss["count"])
            an["series"], an["region"] = name, r
            anomalies_all.append(an)
            series_all.append(ss.reset_index().rename(columns={"index": "month"}).assign(series=name, region=r))
        # robustness: anomalies flagged in >= 2 independent series
        an_r = pd.concat([a for a in anomalies_all if a["region"].iloc[0] == r])
        flagged = an_r[an_r["anomaly"]].groupby("month")["series"].apply(sorted)
        res_r["regional_anomalies"] = [{"month": m, "series": s, "robust": len(s) >= 2,
                                        "z": {row.series: round(row.z, 2) for row in an_r[(an_r.month == m)].itertuples()}}
                                       for m, s in flagged.items()]
        # trend agreement across sensors
        tr = {k: res_r[k]["trend_count"] for k in series if "theil_sen_pct_per_year" in res_r[k]["trend_count"]}
        signs = {k: np.sign(v["theil_sen_pct_per_year"]) for k, v in tr.items()}
        res_r["trend_agreement"] = {"signs": {k: int(v) for k, v in signs.items()},
                                    "same_sign": len(set(signs.values())) == 1,
                                    "any_significant": any(v["significant_at_0.05"] for v in tr.values())}
        # cell level (VIIRS: highest resolution, consistent record)
        cm = P.cell_month_table(settings, r, "VIIRS_SNPP_SP", start, end)
        yrs = cm["month"].dt.year.nunique()
        cells = cm.groupby("cell_id").agg(total=("count", "sum"), months_active=("month", "nunique"),
                                          lat=("lat", "mean"), lon=("lon", "mean")).reset_index()
        cells["mean_annual_detections"] = cells["total"] / max(yrs, 1)
        cells["region"] = r
        hs = P.getis_ord_hotspots(cells, "mean_annual_detections")
        hs["region"] = r
        hotspots_all.append(hs)
        res_r["hotspots"] = {"n_cells": int(len(hs)), "n_hotspot_cells_gi99": int(hs["hotspot_99"].sum()),
                             "share_activity_in_hotspots": float(hs.loc[hs.hotspot_99, "mean_annual_detections"].sum()
                                                                 / max(hs["mean_annual_detections"].sum(), 1e-9)),
                             "top_cells": hs.sort_values("mean_annual_detections", ascending=False).head(5)[
                                 ["cell_id", "lat", "lon", "mean_annual_detections", "gi_star_z"]].round(3).to_dict("records")}
        iso = P.isolation_forest_anomalies(cm)
        iso_flag = iso[iso["iso_anomaly"]]
        reg_anom_months = set(an_r.loc[an_r["anomaly"], "month"])
        res_r["isolation_forest"] = {
            "n_cell_months": int(len(iso)), "n_flagged": int(len(iso_flag)),
            "flagged_by_year": iso_flag["month"].dt.year.value_counts().sort_index().to_dict(),
            "share_flagged_in_regional_anomaly_months": float(iso_flag["month"].astype(str).isin(reg_anom_months).mean())
            if len(iso_flag) else None,
            "top": iso_flag.sort_values("iso_score", ascending=False).head(8)[
                ["cell_id", "month", "lat", "lon", "count", "frp", "departure", "iso_score"]].assign(
                month=lambda x: x["month"].astype(str)).round(3).to_dict("records")}
        write_parquet(iso.assign(month=iso["month"].astype(str)), out_dir / f"isolation_forest_{r}.parquet")
        area_km2 = _bbox_area_km2(REGIONS[r].bbox)
        v = series["viirs_snpp"]
        v = v[v["count"] > 0]
        res_r["region_summary"] = {
            "bbox_area_km2": round(area_km2), "viirs_detections_per_1000km2_per_year":
                float(v["count"].sum() / (len(v.index.year.unique())) / area_km2 * 1000) if len(v) else None,
            "viirs_mean_frp_per_detection_MW": float(series["viirs_snpp"]["frp"].sum() / max(series["viirs_snpp"]["count"].sum(), 1)),
            "night_share_viirs": float((cm["count"] * cm["night_share"]).sum() / max(cm["count"].sum(), 1))}
        results[r] = res_r
        log.info("patterns %s done", r)
    pd.concat(anomalies_all).to_csv(out_dir / "regional_anomalies.csv", index=False)
    write_parquet(pd.concat(hotspots_all), out_dir / "hotspots_gi_star.parquet")
    sa = pd.concat(series_all)
    sa["month"] = sa["month"].astype(str)
    sa.to_csv(out_dir / "regional_monthly_series.csv", index=False)
    (out_dir / "patterns.json").write_text(json.dumps(results, indent=1, default=_conv))
    return results


def _bbox_area_km2(b) -> float:
    R = 6371.0088
    return (np.radians(b.east - b.west) * R ** 2 *
            abs(np.sin(np.radians(b.north)) - np.sin(np.radians(b.south))))


def run_forecast(settings: Settings, regions: list[str], start: date, end: date, train_end: int,
                 calib_year: int, test_years: list[int], cfg: F.ForecastConfig = F.ForecastConfig(),
                 diagnostic_regions: list[str] | None = None) -> dict:
    out_dir = settings.reports_dir / "results" / "forecast"
    out_dir.mkdir(parents=True, exist_ok=True)
    panel = F.add_features(F.build_panel(settings, regions, start, end, cfg))
    res = F.run_validation(panel, cfg, train_end, calib_year, test_years, regions)
    preds = res.pop("predictions")
    preds["month"] = preds["month"].astype(str)
    write_parquet(preds, out_dir / "heldout_predictions.parquet")
    tmodel = res.pop("temporal_model")
    # Final model for deployment: trained on all years up to the last test year - 1, calibrated on the last.
    d = F.usable_rows(panel, cfg.min_history_months)
    last = max(test_years)
    final = F.fit_forecaster(d[d.year < last], d[d.year == last], cfg)
    files = F.save_model(settings, final, {"trained_years": [int(d.year.min()), last - 1], "calibration_year": last,
                                           "regions": regions, "target": "P(>=1 VIIRS S-NPP vegetation-fire detection "
                                           "in 0.25 deg cell in next month)", "validation": "see forecast_metrics.json"})
    F.save_model(settings, tmodel, {"trained_years": res["temporal"]["train_years"], "calibration_year": calib_year,
                                    "regions": regions, "note": "model evaluated in temporal hold-out"},
                 name="xgb_next_month_validation")
    # Next-month forecast (first month after the data)
    nxt = pd.Period(end, "M") + 1
    ext = panel[["cell_id", "region", "lat", "lon", "row", "col"]].drop_duplicates("cell_id")
    future = ext.assign(month=nxt, count=0, frp=0.0, y=0)
    full = F.add_features(pd.concat([panel[future.columns.intersection(panel.columns).tolist()], future],
                                    ignore_index=True))
    fut = full[full["month"] == nxt]
    fut = fut.assign(p_fire=final.predict(fut), p_seasonal=F.baseline_probs(fut, final.persistence_rates)["seasonal_climatology"])
    fut = fut[["cell_id", "region", "lat", "lon", "p_fire", "p_seasonal"]].assign(month=str(nxt))
    write_parquet(fut, out_dir / f"forecast_{nxt}.parquet")
    res["final_model_files"] = files
    if diagnostic_regions:
        po = F.add_features(F.build_panel(settings, diagnostic_regions, start, end, cfg))
        res["transfer_diagnostic_not_deployed"] = F.transfer_diagnostic(tmodel, po, cfg, test_years)
    res["next_month_forecast"] = {"month": str(nxt), "n_cells": int(len(fut)),
                                  "mean_p": float(fut["p_fire"].mean())}
    (out_dir / "forecast_metrics.json").write_text(json.dumps(res, indent=1, default=_conv))
    return res
