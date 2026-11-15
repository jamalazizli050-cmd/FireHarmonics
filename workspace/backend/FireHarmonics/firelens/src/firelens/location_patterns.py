"""Historical patterns for a location disc, computed from real detections.

All statistics use the consistent S-NPP VIIRS series inside the disc. Months
that were not observed (coverage < 90%) are excluded, never counted as zero.
Statistics that need complete calendar years (annual totals, seasonal shares,
trends) only use years in which all 12 months were observed. The functions
reuse the regional methods in :mod:`firelens.patterns`: seasonal shares,
Theil-Sen + Mann-Kendall, robust same-month z-scores with a materiality
criterion, Getis-Ord Gi*, and Isolation Forest.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import patterns as P
from .grid import cell_center, cell_index
from .location_features import disc_area_km2, haversine_km

MONTHS = P.MONTHS
FINE_RES_DEG = 0.05
MIN_YEARS_FOR_ANOMALY = 4


def _r(x, nd=4):
    if x is None:
        return None
    x = float(x)
    return None if not np.isfinite(x) else round(x, nd)


def disc_monthly(det: pd.DataFrame, cov: pd.DataFrame, months: pd.PeriodIndex, radius_km: float) -> pd.DataFrame:
    """Monthly counts/FRP inside the disc; NaN where the month is unobserved."""
    d = det[det["dist_km"] <= radius_km] if len(det) else det
    g = d.groupby("month") if len(d) else None
    out = pd.DataFrame(index=months)
    if g is not None:
        out["count"] = g.size().reindex(months, fill_value=0).astype(float)
        out["frp"] = g["frp_mw"].sum().reindex(months, fill_value=0.0).astype(float)
    else:
        out["count"], out["frp"] = 0.0, 0.0
    c = cov.set_index(pd.PeriodIndex(cov["month"], freq="M"))
    out["coverage_fraction"] = c["coverage_fraction"].reindex(months).to_numpy()
    out["observed"] = c["observed"].reindex(months).fillna(False).to_numpy(dtype=bool)
    out["products"] = c["products"].reindex(months).to_numpy()
    out.loc[~out["observed"], ["count", "frp"]] = np.nan
    return out


def complete_years(m: pd.DataFrame) -> list[int]:
    obs = m["observed"].groupby(m.index.year).agg(["sum", "size"])
    return [int(y) for y, r in obs.iterrows() if r["sum"] == 12 and r["size"] == 12]


def historical_activity(m: pd.DataFrame, det: pd.DataFrame, radius_km: float) -> dict:
    obs = m[m["observed"]]
    yrs = complete_years(m)
    full = obs[obs.index.year.isin(yrs)]
    annual = full["count"].groupby(full.index.year).sum()
    d = det[det["dist_km"] <= radius_km] if len(det) else det
    area = disc_area_km2(radius_km)
    top = obs["count"].idxmax() if len(obs) and obs["count"].max() > 0 else None
    return {
        "series": "S-NPP VIIRS 375 m vegetation-fire detections (FIRMS type 0) within the radius",
        "unit": "detections (hotspot pixels), not fires",
        "n_months_observed": int(len(obs)), "n_months_unobserved": int((~m["observed"]).sum()),
        "total_detections_observed_months": int(obs["count"].sum()),
        "months_with_detections": int((obs["count"] > 0).sum()),
        "share_of_observed_months_with_detections": _r((obs["count"] > 0).mean()) if len(obs) else None,
        "complete_years": yrs,
        "annual_totals_complete_years": {int(y): int(v) for y, v in annual.items()},
        "mean_annual_detections": _r(annual.mean(), 2) if len(annual) else None,
        "median_annual_detections": _r(annual.median(), 2) if len(annual) else None,
        "detections_per_1000km2_per_year": _r(annual.mean() / area * 1000, 3) if len(annual) else None,
        "disc_area_km2": round(area, 1),
        "max_month": {"month": str(top), "detections": int(obs.loc[top, "count"])} if top is not None else None,
        "mean_frp_per_detection_mw": _r(obs["frp"].sum() / obs["count"].sum(), 2) if obs["count"].sum() > 0 else None,
        "night_share": _r(d["daynight"].eq("N").mean(), 3) if len(d) else None,
        "monthly": [{"month": str(p), "detections": None if not r["observed"] else int(r["count"]),
                     "frp_sum_mw": None if not r["observed"] else round(float(r["frp"]), 1),
                     "observed": bool(r["observed"]), "coverage_fraction": round(float(r["coverage_fraction"]), 3),
                     "products": r["products"] or None}
                    for p, r in m.iterrows()],
    }


def seasonal_patterns(m: pd.DataFrame) -> dict:
    obs = m[m["observed"]]
    yrs = complete_years(m)
    by_m = []
    for k in range(1, 13):
        v = obs.loc[obs.index.month == k, "count"]
        by_m.append({"month": MONTHS[k - 1], "years_observed": int(len(v)),
                     "years_with_detections": int((v > 0).sum()),
                     "fraction_of_years_with_detections": _r((v > 0).mean()) if len(v) else None,
                     "median_detections": _r(v.median(), 1) if len(v) else None,
                     "mean_detections": _r(v.mean(), 2) if len(v) else None})
    out = {"by_calendar_month": by_m, "complete_years_used": yrs}
    s = obs.loc[obs.index.year.isin(yrs), "count"]
    if len(yrs) < 2 or s.sum() == 0:
        out["status"] = "no_detections" if len(yrs) >= 2 else "insufficient_complete_years"
        return out
    prof = P.seasonal_profile(s)
    share = prof["monthly_share"]
    low = sorted(share, key=share.get)[:3]
    out.update({"status": "ok", "monthly_share_of_annual_detections": share, "peak_month": prof["peak_month"],
                "core_season_months_80pct": prof["core_season_months_80pct"],
                "lowest_activity_months": low,
                "months_below_1pct_share": [k for k, v in share.items() if v < 0.01]})
    return out


def long_term_trends(m: pd.DataFrame) -> dict:
    obs = m[m["observed"]]
    s = obs.loc[obs.index.year.isin(complete_years(m)), "count"]
    t = P.trend(s) if len(s) else {"n_years": 0, "note": "too few complete years"}
    if "theil_sen_pct_per_year" not in t:
        return {"status": "insufficient_complete_years", "n_complete_years": t.get("n_years", 0),
                "note": "a trend needs at least 5 complete observed years"}
    if s.sum() == 0:
        return {"status": "no_detections", "n_complete_years": t["n_years"]}
    return {"status": "ok", "method": "Theil-Sen slope of log10(annual detections + 1); Mann-Kendall test",
            "n_complete_years": t["n_years"], "years": t["years"],
            "theil_sen_pct_per_year": _r(t["theil_sen_pct_per_year"], 2),
            "ci95_pct_per_year": [_r(v, 2) for v in t["ci95_pct_per_year"]],
            "mann_kendall_tau": _r(t["mann_kendall_tau"], 3), "p_value": _r(t["p_value"], 4),
            "significant_at_0.05": t["significant_at_0.05"],
            "interannual_cv": _r(t["interannual_cv"], 3),
            "direction": "increasing" if t["theil_sen_pct_per_year"] > 0 else "decreasing" if t["theil_sen_pct_per_year"] < 0 else "flat"}


def monthly_anomaly_list(m: pd.DataFrame, z_thresh: float = 3.0) -> tuple[list[dict], dict]:
    s = m.loc[m["observed"], "count"]
    if len(s) == 0 or s.sum() == 0:
        return [], {"status": "no_detections"}
    a = P.monthly_anomalies(s, z_thresh=z_thresh)
    n_other = pd.Series([int(((s.index.month == pd.Period(x).month)).sum()) - 1 for x in a["month"]])
    a = a[(n_other >= MIN_YEARS_FOR_ANOMALY).to_numpy()]
    hits = a[a["anomaly"]].sort_values("z", key=np.abs, ascending=False)
    out = [{"month": r.month, "detections": int(r.value), "same_month_median_other_years": _r(r.same_month_median, 1),
            "robust_z": _r(r.z, 2), "direction": "high" if r.z > 0 else "low",
            "method": "robust z of log1p(detections) vs the same calendar month in other observed years; "
                      "|z| >= 3 and departure >= 1% of mean annual detections"} for r in hits.itertuples()]
    return out, {"status": "ok", "n_months_tested": int(len(a)), "z_threshold": z_thresh,
                 "min_other_years": MIN_YEARS_FOR_ANOMALY}


def _fine_cells(det: pd.DataFrame, lat0: float, lon0: float, radius_km: float) -> pd.DataFrame:
    """All 0.05-degree cells whose centre lies inside the disc (zeros included)."""
    dlat = radius_km / 111.2 + FINE_RES_DEG
    dlon = radius_km / (111.32 * max(np.cos(np.radians(min(abs(lat0) + dlat, 89.9))), 1e-6)) + FINE_RES_DEG
    lats = np.arange(np.floor((lat0 - dlat) / FINE_RES_DEG), np.ceil((lat0 + dlat) / FINE_RES_DEG)) * FINE_RES_DEG + FINE_RES_DEG / 2
    lons = np.arange(np.floor((lon0 - dlon) / FINE_RES_DEG), np.ceil((lon0 + dlon) / FINE_RES_DEG)) * FINE_RES_DEG + FINE_RES_DEG / 2
    la, lo = np.meshgrid(lats[(lats > -90) & (lats < 90)], lons, indexing="ij")
    la, lo = la.ravel(), ((lo.ravel() + 180) % 360) - 180
    keep = haversine_km(lat0, lon0, la, lo) <= radius_km
    row, col, cid = cell_index(la[keep], lo[keep], FINE_RES_DEG)
    clat, clon = cell_center(cid, FINE_RES_DEG)
    return pd.DataFrame({"cell_id": cid, "row": row, "col": col, "lat": clat, "lon": clon}).drop_duplicates("cell_id")


def hotspots(det: pd.DataFrame, m: pd.DataFrame, lat0: float, lon0: float, radius_km: float, top_n: int = 10) -> tuple[list[dict], dict]:
    d = det[(det["dist_km"] <= radius_km) & det["month"].isin(m.index[m["observed"]])] if len(det) else det
    if len(d) == 0:
        return [], {"status": "no_detections"}
    grid = _fine_cells(d, lat0, lon0, radius_km)
    _, _, cid = cell_index(d["latitude"], d["longitude"], FINE_RES_DEG)
    d = d.assign(cell_id=cid, year=d["acq_date"].dt.year)
    n_years = max(len(set(m.index[m["observed"]].year)), 1)
    tot = d.groupby("cell_id").size()
    yrs_active = d.groupby("cell_id")["year"].nunique()
    grid = grid.assign(total=tot.reindex(grid["cell_id"]).fillna(0).to_numpy(),
                       years_active=yrs_active.reindex(grid["cell_id"]).fillna(0).to_numpy())
    grid["gi_star_z"] = P.gi_star(grid, grid["total"].to_numpy() / n_years)
    grid["hotspot_99"] = grid["gi_star_z"] > 2.58
    total = grid["total"].sum()
    top = grid[grid["total"] > 0].sort_values("total", ascending=False).head(top_n)
    out = [{"cell_lat": round(float(r.lat), 3), "cell_lon": round(float(r.lon), 3), "cell_size_deg": FINE_RES_DEG,
            "total_detections": int(r.total), "mean_annual_detections": round(float(r.total) / n_years, 2),
            "share_of_disc_detections": round(float(r.total) / total, 4),
            "years_active": int(r.years_active), "years_observed": n_years,
            "recurring": bool(r.years_active >= max(2, n_years / 2)),
            "gi_star_z": round(float(r.gi_star_z), 2), "gi_star_hotspot_99": bool(r.hotspot_99)}
           for r in top.itertuples()]
    hs = grid[grid["hotspot_99"]]
    return out, {"status": "ok", "grid_res_deg": FINE_RES_DEG, "n_cells_in_disc": int(len(grid)),
                 "n_cells_with_detections": int((grid["total"] > 0).sum()),
                 "n_gi_star_hotspot_cells_99": int(len(hs)),
                 "share_of_detections_in_gi_star_hotspots": round(float(hs["total"].sum() / total), 4) if total else None,
                 "n_recurring_cells": int((grid["years_active"] >= max(2, n_years / 2)).sum()),
                 "method": "0.05-degree cells inside the disc; Gi* (3x3 queen+self) on mean annual detections; "
                           "z > 2.58 descriptive, not corrected for multiple testing; recurring = active in "
                           ">= half of observed years"}


def cell_month_outliers(det: pd.DataFrame, m: pd.DataFrame, radius_km: float, top_n: int = 8) -> tuple[list[dict], dict]:
    d = det[(det["dist_km"] <= radius_km) & det["month"].isin(m.index[m["observed"]])] if len(det) else det
    if len(d) == 0:
        return [], {"status": "no_detections"}
    _, _, cid = cell_index(d["latitude"], d["longitude"], FINE_RES_DEG)
    d = d.assign(cell_id=cid, night=d["daynight"].eq("N"))
    g = d.groupby(["cell_id", "month"])
    cm = pd.DataFrame({"count": g.size(), "frp": g["frp_mw"].sum(), "night_share": g["night"].mean(),
                       "active_days": g["acq_date"].nunique(), "lat": g["latitude"].mean(),
                       "lon": g["longitude"].mean()}).reset_index()
    if len(cm) < 100:
        return [], {"status": "too_few_cell_months", "n_cell_months": int(len(cm)), "min_required": 100}
    iso = P.isolation_forest_anomalies(cm)
    f = iso[iso["iso_anomaly"]].sort_values("iso_score", ascending=False).head(top_n)
    out = [{"month": str(r.month), "lat": round(float(r.lat), 3), "lon": round(float(r.lon), 3),
            "detections": int(r.count), "frp_sum_mw": round(float(r.frp), 1),
            "departure_log_vs_same_month": round(float(r.departure), 2), "isolation_score": round(float(r.iso_score), 3)}
           for r in f.itertuples()]
    return out, {"status": "ok", "n_cell_months": int(len(iso)), "n_flagged": int(iso["iso_anomaly"].sum()),
                 "method": "Isolation Forest (contamination 1%) on 0.05-degree cell-months; flags are hypotheses "
                           "for inspection, not confirmed events"}
