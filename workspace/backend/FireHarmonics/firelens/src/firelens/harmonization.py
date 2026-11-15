"""Diagnostics for MODIS / VIIRS harmonisation over their overlap period.

Nothing in this module produces a harmonised product. It quantifies how the
sensors differ so that a harmonisation method can be chosen and validated.

Two complementary views are implemented:

1. **Aggregate (cell-month) comparison** – relationships between monthly
   MODIS and VIIRS S-NPP counts / FRP on the shared grid, restricted to
   months both sensors observed. Uncertainty uses a month-block bootstrap
   because cell-months within a month are not independent.

2. **Near-coincident cluster comparison** – Aqua MODIS and Suomi NPP VIIRS
   both have ~13:30 local equator-crossing times, so many of their
   observations are near-simultaneous. Detections from both sensors that lie
   within overlapping footprints and within ``max_dt_min`` minutes are linked
   into *fire clusters* (connected components). A cluster is a group of
   adjacent hot pixels in one overpass - not a wildfire incident.
   FIRMS publishes detections only (no clear-sky non-detections), so whether
   the *other* sensor observed a location is approximated by requiring it to
   have at least one detection within ``context_km`` and ``max_dt_min``.
   This proxy is documented as a limitation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

EARTH_RADIUS_KM = 6371.0088


# ---------------------------------------------------------------- aggregates
def regional_monthly(grid: pd.DataFrame, coverage: pd.DataFrame) -> pd.DataFrame:
    """Region totals per source and month joined with coverage."""
    tot = grid.groupby(["source", "month"]).agg(
        n_detections=("n_detections", "sum"), n_vegetation=("n_vegetation", "sum"),
        n_conf_nominal_high=("n_conf_nominal_high", "sum"), frp_sum_mw=("frp_sum_mw", "sum"),
        active_cells=("cell_id", "nunique")).reset_index()
    cov = coverage[["source", "month", "coverage_fraction", "n_platforms"]]
    out = cov.merge(tot, on=["source", "month"], how="left")
    observed = out["coverage_fraction"].notna() & (out["coverage_fraction"] > 0)
    for c in ["n_detections", "n_vegetation", "n_conf_nominal_high", "frp_sum_mw", "active_cells"]:
        out[c] = out[c].fillna(0).where(observed)
    return out


def _loglog_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    slope, intercept = np.polyfit(np.log10(x), np.log10(y), 1)
    return float(slope), float(intercept)


def cellmonth_comparison(hf: pd.DataFrame, metric: str = "n_detections", n_boot: int = 1000,
                         seed: int = 0) -> dict:
    """Compare MODIS vs VIIRS at cell-month level over months both observed.

    Returns descriptive statistics only; no correction factor is implied.
    """
    d = hf[hf["both_observed"]].copy()
    m, v = d[f"modis_{metric}"].to_numpy(float), d[f"viirs_{metric}"].to_numpy(float)
    active = (m > 0) | (v > 0)
    d, m, v = d[active], m[active], v[active]
    both = (m > 0) & (v > 0)
    res: dict = {
        "metric": metric,
        "n_cell_months_active": int(len(d)),
        "n_months": int(d["month"].nunique()),
        "share_both_detected": float(both.mean()) if len(d) else np.nan,
        "share_viirs_only": float(((v > 0) & (m == 0)).mean()) if len(d) else np.nan,
        "share_modis_only": float(((m > 0) & (v == 0)).mean()) if len(d) else np.nan,
        "total_modis": float(m.sum()), "total_viirs": float(v.sum()),
        "ratio_of_totals_viirs_to_modis": float(v.sum() / m.sum()) if m.sum() > 0 else np.nan,
    }
    if len(d) >= 3:
        rho, p = stats.spearmanr(m, v)
        res["spearman_rho"], res["spearman_p"] = float(rho), float(p)
    if both.sum() >= 3:
        res["median_cell_ratio_viirs_to_modis"] = float(np.median(v[both] / m[both]))
        res["loglog_slope"], res["loglog_intercept"] = _loglog_fit(m[both], v[both])
        res["n_cell_months_both"] = int(both.sum())

    # Month-block bootstrap for ratio of totals and log-log slope.
    rng = np.random.default_rng(seed)
    months = d["month"].unique()
    if len(months) >= 5 and n_boot > 0:
        by_month = {k: g.index.to_numpy() for k, g in d.groupby("month")}
        pos = pd.Series(np.arange(len(d)), index=d.index)
        ratios, slopes = [], []
        for _ in range(n_boot):
            pick = rng.choice(months, size=len(months), replace=True)
            ix = pos.loc[np.concatenate([by_month[k] for k in pick])].to_numpy()
            mm, vv = m[ix], v[ix]
            if mm.sum() > 0:
                ratios.append(vv.sum() / mm.sum())
            bb = (mm > 0) & (vv > 0)
            if bb.sum() >= 3 and np.ptp(np.log10(mm[bb])) > 0:
                slopes.append(_loglog_fit(mm[bb], vv[bb])[0])
        if ratios:
            res["ratio_of_totals_ci95"] = [float(x) for x in np.percentile(ratios, [2.5, 97.5])]
        if slopes:
            res["loglog_slope_ci95"] = [float(x) for x in np.percentile(slopes, [2.5, 97.5])]
    return res


# ------------------------------------------------------- coincident clusters
def _xyz_km(lat, lon) -> np.ndarray:
    la, lo = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    return EARTH_RADIUS_KM * np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def _time_blocks(t: np.ndarray, gap: float) -> np.ndarray:
    order = np.argsort(t)
    brk = np.r_[False, np.diff(t[order]) > gap]
    blocks = np.empty(len(t), dtype=np.int64)
    blocks[order] = np.cumsum(brk)
    return blocks


def coincident_clusters(modis: pd.DataFrame, viirs: pd.DataFrame, max_dt_min: float = 20.0,
                        tol_km: float = 0.5, context_km: float = 100.0,
                        modis_platform: str = "Aqua", viirs_platform: str = "Suomi NPP") -> pd.DataFrame:
    """Link near-coincident Aqua-MODIS and S-NPP-VIIRS detections into clusters.

    Two detections are linked when their time difference is <= ``max_dt_min``
    and their centre distance is <= half the sum of their pixel diagonals plus
    ``tol_km`` (i.e. footprints overlap within a geolocation tolerance).
    """
    cols = ["latitude", "longitude", "acq_datetime_utc", "scan_km", "track_km", "frp_mw", "daynight",
            "platform", "qc_usable"]
    m = modis.loc[(modis["platform"] == modis_platform) & modis["qc_usable"], cols].assign(sensor="MODIS")
    v = viirs.loc[(viirs["platform"] == viirs_platform) & viirs["qc_usable"], cols].assign(sensor="VIIRS")
    pts = pd.concat([m, v], ignore_index=True)
    pts = pts[pts["acq_datetime_utc"].notna()].reset_index(drop=True)
    if pts.empty:
        return pd.DataFrame()
    t = (pts["acq_datetime_utc"] - pd.Timestamp("2000-01-01", tz="UTC")).dt.total_seconds().to_numpy() / 60
    xyz = _xyz_km(pts["latitude"], pts["longitude"])
    diag = np.hypot(pts["scan_km"].fillna(1.0), pts["track_km"].fillna(1.0)).to_numpy()
    is_m = (pts["sensor"] == "MODIS").to_numpy()
    blocks = _time_blocks(t, max_dt_min)

    labels = np.full(len(pts), -1, dtype=np.int64)
    eligible = np.zeros(len(pts), dtype=bool)
    next_label = 0
    for b in np.unique(blocks):
        ix = np.flatnonzero(blocks == b)
        tree = cKDTree(xyz[ix])
        pairs = tree.query_pairs(r=float(diag[ix].max() + tol_km), output_type="ndarray")
        if len(pairs):
            i, j = ix[pairs[:, 0]], ix[pairs[:, 1]]
            dist = np.linalg.norm(xyz[i] - xyz[j], axis=1)
            keep = (dist <= 0.5 * (diag[i] + diag[j]) + tol_km) & (np.abs(t[i] - t[j]) <= max_dt_min)
            i, j = pairs[keep, 0], pairs[keep, 1]
        else:
            i = j = np.array([], dtype=np.int64)
        graph = coo_matrix((np.ones(len(i)), (i, j)), shape=(len(ix), len(ix)))
        _, lab = connected_components(graph, directed=False)
        labels[ix] = lab + next_label
        next_label += lab.max() + 1

        # Co-observation proxy: the other sensor detected something nearby in time/space.
        for sel_self, sel_other in ((is_m[ix], ~is_m[ix]), (~is_m[ix], is_m[ix])):
            a, o = ix[sel_self], ix[sel_other]
            if len(a) == 0 or len(o) == 0:
                continue
            otree = cKDTree(xyz[o])
            near = otree.query_ball_point(xyz[a], r=context_km)
            eligible[a] = [len(n) > 0 and bool(np.any(np.abs(t[o[n]] - ta) <= max_dt_min))
                           for n, ta in zip(near, t[a])]

    pts["cluster"] = labels
    pts["eligible"] = eligible
    pts["t_min"] = t
    pts["is_modis"] = is_m
    pts["frp_m"] = np.where(is_m, pts["frp_mw"], 0.0)
    pts["frp_v"] = np.where(~is_m, pts["frp_mw"], 0.0)
    g = pts.groupby("cluster")
    cl = pd.DataFrame({
        "date": g["acq_datetime_utc"].min().dt.tz_localize(None).dt.normalize(),
        "daynight": g["daynight"].first(),
        "lat": g["latitude"].mean(), "lon": g["longitude"].mean(),
        "n_modis": g["is_modis"].sum(), "n_viirs": g["is_modis"].size() - g["is_modis"].sum(),
        "frp_modis_mw": g["frp_m"].sum(), "frp_viirs_mw": g["frp_v"].sum(),
        "eligible": g["eligible"].any(),
    })
    tm = pts[is_m].groupby("cluster")["t_min"].mean()
    tv = pts[~is_m].groupby("cluster")["t_min"].mean()
    cl["dt_viirs_minus_modis_min"] = (tv - tm).reindex(cl.index)
    cl["category"] = np.select([(cl.n_modis > 0) & (cl.n_viirs > 0), cl.n_modis > 0],
                               ["both", "modis_only"], "viirs_only")
    return cl.reset_index()


def cluster_summary(cl: pd.DataFrame, frp_bins: tuple[float, ...] = (0, 2, 5, 10, 20, 50, 100, 250, 1e9)) -> dict:
    e = cl[cl["eligible"]]
    cnt = e["category"].value_counts().to_dict()
    both, mo, vo = cnt.get("both", 0), cnt.get("modis_only", 0), cnt.get("viirs_only", 0)
    res = {
        "n_clusters_total": int(len(cl)), "n_clusters_eligible": int(len(e)),
        "both": int(both), "modis_only": int(mo), "viirs_only": int(vo),
        "p_viirs_given_modis": both / (both + mo) if both + mo else np.nan,
        "p_modis_given_viirs": both / (both + vo) if both + vo else np.nan,
    }
    b = e[e["category"] == "both"]
    if len(b) >= 3:
        res["median_pixel_ratio_viirs_to_modis"] = float((b.n_viirs / b.n_modis).median())
        ok = (b.frp_modis_mw > 0) & (b.frp_viirs_mw > 0)
        res["median_frp_ratio_viirs_to_modis"] = float((b.frp_viirs_mw[ok] / b.frp_modis_mw[ok]).median())
        rho, _ = stats.spearmanr(b.frp_modis_mw[ok], b.frp_viirs_mw[ok])
        res["frp_spearman_rho"] = float(rho)
        res["median_abs_dt_min"] = float(b["dt_viirs_minus_modis_min"].abs().median())
    # Probability that MODIS co-detects a VIIRS cluster, by VIIRS cluster FRP.
    vc = e[e["n_viirs"] > 0].copy()
    vc["frp_bin"] = pd.cut(vc["frp_viirs_mw"], list(frp_bins), right=False)
    curve = vc.groupby("frp_bin", observed=False).agg(
        n_viirs_clusters=("category", "size"),
        n_with_modis=("category", lambda s: int((s == "both").sum())))
    curve["p_modis_detects"] = curve["n_with_modis"] / curve["n_viirs_clusters"].where(curve["n_viirs_clusters"] > 0)
    res["modis_detection_curve"] = [
        {"frp_bin": str(k), **{c: (None if pd.isna(x) else float(x)) for c, x in row.items()}}
        for k, row in curve.iterrows()]
    return res
