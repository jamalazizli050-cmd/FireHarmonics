"""Coverage-aware matching of Aqua-MODIS and Suomi-NPP VIIRS detections.

Builds, for one region and period, the table the harmonisation model is
trained and evaluated on.

Units
-----
* **VIIRS fire cluster**: contiguous S-NPP VIIRS fire pixels from one overpass
  (pixels linked when footprints touch, within 8 minutes). This is the unit of
  VIIRS-observed fire activity. It is *not* a wildfire incident.
* **MODIS pixel**: an Aqua MODIS fire detection, FIRMS's native unit.

Coverage (no detection is treated as a non-detection without it)
-----------------------------------------------------------------
* For every VIIRS cluster, the Aqua MYD03 granules are searched for
  footprints containing the cluster centroid. The Aqua observation nearest in
  time gives ``aqua_dt_min`` (Aqua time minus VIIRS time) and ``aqua_xtrack``
  (MODIS view position). The cluster is **comparable** only if Aqua observed
  it within ``max_dt_min``.
* Symmetrically, every Aqua pixel is checked against S-NPP VNP03IMG granules.
  It is comparable if VIIRS observed it within ``max_dt_min``.
* Geometric coverage does not mean clear-sky coverage. Clouds and smoke that
  appear between the two overpasses are part of the difference being modelled.

Matching
--------
A MODIS pixel *detects* a VIIRS cluster when it was acquired within
``same_pass_min`` of the Aqua observation time at that cluster and the
footprints overlap: centre distance <= half the sum of the pixel diagonals plus
``match_tol_km``. Each MODIS pixel is *assigned* to its nearest overlapping
cluster, so pixel counts and FRP are never double counted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .catalog import Region
from .cleaning import load_detections
from .config import Settings
from .io import read_parquet, write_parquet
from .logging_utils import get_logger
from .swath import AQUA_MYD03, SNPP_VNP03IMG, GranuleGeometry, granules_for, to_minutes, unit_vectors

log = get_logger(__name__)
EARTH_R = 6371.0088
VIIRS_I4_SATURATION_K = 367.0


@dataclass(frozen=True)
class MatchConfig:
    max_dt_min: float = 20.0        # comparable-overpass window (Aqua vs S-NPP)
    search_dt_min: float = 100.0    # window used to investigate coverage
    same_pass_min: float = 3.0      # MODIS pixel must belong to the Aqua pass at the cluster
    cluster_tol_km: float = 0.1     # VIIRS pixel adjacency tolerance
    match_tol_km: float = 0.5       # geolocation tolerance for MODIS/VIIRS footprint overlap
    vegetation_only: bool = True    # FIRMS type 0 only (excludes static/industrial, volcano, offshore)


def _xyz(lat, lon) -> np.ndarray:
    return EARTH_R * unit_vectors(lat, lon)


def _diag(df: pd.DataFrame) -> np.ndarray:
    return np.hypot(df["scan_km"].to_numpy(float), df["track_km"].to_numpy(float))


def _locate_own(geo: GranuleGeometry, df: pd.DataFrame) -> pd.DataFrame:
    """Refine acquisition time / view position from the detection's own granule."""
    t = to_minutes(df["acq_datetime_utc"])
    loc = geo.locate(df["latitude"], df["longitude"], t, window_min=2.0)
    out = df.copy()
    out["t_obs"] = t + 0.5  # FIRMS acq_time is truncated to the minute
    out["xtrack"] = np.nan
    out["located"] = False
    if len(loc):
        loc = loc.astype({"point": int, "granule": int, "xtrack": float, "t_obs": float})
        loc["err"] = (loc["t_obs"] - t[loc["point"].to_numpy()]).abs()
        best = loc.sort_values("err").drop_duplicates("point").set_index("point")
        ix = best.index.to_numpy()
        out.loc[out.index[ix], "xtrack"] = best["xtrack"].to_numpy()
        # Keep the minute-resolution FIRMS time but use the interpolated sub-minute time when consistent.
        good = best["err"].to_numpy() <= 1.5
        out.loc[out.index[ix[good]], "t_obs"] = best["t_obs"].to_numpy()[good]
        out.loc[out.index[ix], "located"] = True
    return out


def duplicate_stats(df: pd.DataFrame, sensor: str) -> dict:
    """Near-duplicate detections: same platform, same minute, centres closer
    than half the smaller along-track pixel size (e.g. MODIS bow-tie overlap at
    swath edges). Reported, not removed: FIRMS counts include them."""
    if df.empty:
        return {"sensor": sensor, "n": 0}
    xyz = _xyz(df["latitude"], df["longitude"])
    t = df["t_obs"].to_numpy()
    track = df["track_km"].to_numpy(float)
    tree = cKDTree(xyz)
    pairs = tree.query_pairs(r=float(np.nanmax(track)) * 0.5, output_type="ndarray")
    if len(pairs):
        i, j = pairs[:, 0], pairs[:, 1]
        d = np.linalg.norm(xyz[i] - xyz[j], axis=1)
        keep = (d < 0.5 * np.minimum(track[i], track[j])) & (np.abs(t[i] - t[j]) <= 1.5)
        i, j = i[keep], j[keep]
    else:
        i = j = np.array([], dtype=int)
    involved = np.unique(np.concatenate([i, j]))
    xt = np.abs(df["xtrack"].to_numpy(float))
    return {"sensor": sensor, "n": int(len(df)), "near_duplicate_pairs": int(len(i)),
            "pixels_involved": int(len(involved)),
            "share_pixels_involved": float(len(involved) / len(df)),
            "share_involved_with_abs_xtrack_gt_0_6": float(np.mean(xt[involved] > 0.6)) if len(involved) else None}


def cluster_viirs(v: pd.DataFrame, tol_km: float) -> np.ndarray:
    """Connected components of touching VIIRS pixels within an overpass."""
    xyz = _xyz(v["latitude"], v["longitude"])
    diag = _diag(v)
    t = v["t_obs"].to_numpy()
    order = np.argsort(t)
    blocks = np.empty(len(v), dtype=np.int64)
    blocks[order] = np.cumsum(np.r_[False, np.diff(t[order]) > 10.0])
    labels = np.empty(len(v), dtype=np.int64)
    nxt = 0
    for b in np.unique(blocks):
        ix = np.flatnonzero(blocks == b)
        tree = cKDTree(xyz[ix])
        pairs = tree.query_pairs(r=float(diag[ix].max() + tol_km), output_type="ndarray")
        if len(pairs):
            i, j = ix[pairs[:, 0]], ix[pairs[:, 1]]
            ok = (np.linalg.norm(xyz[i] - xyz[j], axis=1) <= 0.5 * (diag[i] + diag[j]) + tol_km) & \
                 (np.abs(t[i] - t[j]) <= 8.0)
            a, c = pairs[ok, 0], pairs[ok, 1]
        else:
            a = c = np.array([], dtype=int)
        g = coo_matrix((np.ones(len(a)), (a, c)), shape=(len(ix), len(ix)))
        _, lab = connected_components(g, directed=False)
        labels[ix] = lab + nxt
        nxt += lab.max() + 1
    return labels


def _nearest_observation(geo: GranuleGeometry, lat, lon, t, window: float) -> pd.DataFrame:
    """Per point: the other sensor's observation nearest in time (or NaN)."""
    n = len(t)
    out = pd.DataFrame({"dt_min": np.full(n, np.nan), "xtrack": np.full(n, np.nan),
                        "n_obs_window": np.zeros(n, dtype=int), "granule_day_night": [""] * n})
    loc = geo.locate(lat, lon, t, window_min=window)
    if len(loc) == 0:
        return out
    loc = loc.astype({"point": int, "granule": int, "xtrack": float, "t_obs": float})
    # Several granules of one pass can contain a point (overlap at granule edges);
    # count distinct passes as observations > 10 min apart.
    loc["dt"] = loc["t_obs"] - np.asarray(t)[loc["point"].to_numpy()]
    loc["adt"] = loc["dt"].abs()
    best = loc.sort_values("adt").drop_duplicates("point").set_index("point")
    out.loc[best.index, "dt_min"] = best["dt"].to_numpy()
    out.loc[best.index, "xtrack"] = best["xtrack"].to_numpy()
    out.loc[best.index, "granule_day_night"] = geo.g["day_night"].to_numpy()[best["granule"].to_numpy()]
    loc["pass"] = (loc["t_obs"] // 10).astype(int)
    out.loc[:, "n_obs_window"] = loc.groupby("point")["pass"].nunique().reindex(range(n), fill_value=0).to_numpy()
    return out


def build_region(settings: Settings, region: Region, start: date, end: date,
                 cfg: MatchConfig = MatchConfig(), use_cache: bool = True) -> dict[str, pd.DataFrame | dict]:
    out_dir = settings.processed_dir / "harmonization" / region.name
    tag = f"{start}_{end}_dt{cfg.max_dt_min:g}"
    paths = {k: out_dir / f"{k}_{tag}.parquet" for k in ("clusters", "modis", "viirs")}
    if use_cache and all(p.exists() for p in paths.values()):
        import json
        res = {k: read_parquet(p) for k, p in paths.items()}
        res["diagnostics"] = json.loads((out_dir / f"diagnostics_{tag}.json").read_text())
        return res

    cols = ["latitude", "longitude", "acq_date", "acq_datetime_utc", "scan_km", "track_km", "frp_mw",
            "daynight", "platform", "confidence_class", "brightness_k", "fire_type", "qc_usable"]
    m = load_detections(settings, region, "MODIS_SP", start, end, columns=cols, usable_only=True)
    v = load_detections(settings, region, "VIIRS_SNPP_SP", start, end, columns=cols, usable_only=True)
    m = m[m["platform"] == "Aqua"]
    if cfg.vegetation_only:
        m = m[m["fire_type"].fillna(-1).eq(0)]
        v = v[v["fire_type"].fillna(-1).eq(0)]
    m, v = m.reset_index(drop=True), v.reset_index(drop=True)
    log.info("%s: %d Aqua-MODIS and %d S-NPP VIIRS usable vegetation detections", region.name, len(m), len(v))

    geo_a = GranuleGeometry(granules_for(settings, AQUA_MYD03, region, start, end))
    geo_v = GranuleGeometry(granules_for(settings, SNPP_VNP03IMG, region, start, end))
    diag: dict = {"region": region.name, "start": str(start), "end": str(end), "config": asdict(cfg),
                  "aqua_granules": len(geo_a.g), "viirs_granules": len(geo_v.g),
                  "aqua_orientation": pd.Series(geo_a.orient_method).value_counts().to_dict(),
                  "viirs_orientation": pd.Series(geo_v.orient_method).value_counts().to_dict(),
                  "aqua_direction_rule_agreement": geo_a.direction_rule_agreement,
                  "viirs_direction_rule_agreement": geo_v.direction_rule_agreement}

    m = _locate_own(geo_a, m)
    v = _locate_own(geo_v, v)
    diag["modis_located_share"] = float(m["located"].mean()) if len(m) else None
    diag["viirs_located_share"] = float(v["located"].mean()) if len(v) else None
    diag["duplicates"] = [duplicate_stats(m, "MODIS"), duplicate_stats(v, "VIIRS")]

    # --- VIIRS clusters
    v["cluster"] = cluster_viirs(v, cfg.cluster_tol_km)
    v["diag_km"] = _diag(v)
    v["sat"] = v["brightness_k"] >= VIIRS_I4_SATURATION_K - 0.01
    v["high"] = v["confidence_class"].astype(str).eq("high")
    v["night"] = v["daynight"].eq("N")
    g = v.groupby("cluster")
    cl = pd.DataFrame({
        "lat": g["latitude"].mean(), "lon": g["longitude"].mean(), "t_viirs": g["t_obs"].mean(),
        "n_pix": g.size(), "frp_sum": g["frp_mw"].sum(), "frp_max": g["frp_mw"].max(),
        "scan_mean": g["scan_km"].mean(), "viirs_ax": g["xtrack"].apply(lambda s: np.nanmean(np.abs(s))),
        "night": g["night"].mean() > 0.5, "share_high_conf": g["high"].mean(), "saturated": g["sat"].any(),
        "acq_date": g["acq_date"].first(),
    }).reset_index()
    cl["month"] = cl["acq_date"].dt.to_period("M").astype(str)
    cl["year"] = cl["acq_date"].dt.year
    cl["region"] = region.name

    # --- Aqua coverage of VIIRS clusters
    cov = _nearest_observation(geo_a, cl["lat"], cl["lon"], cl["t_viirs"], cfg.search_dt_min)
    cl["aqua_dt_min"], cl["aqua_xtrack"] = cov["dt_min"].to_numpy(), cov["xtrack"].to_numpy()
    cl["aqua_n_obs_window"] = cov["n_obs_window"].to_numpy()
    cl["aqua_ax"] = np.abs(cl["aqua_xtrack"])
    cl["t_aqua"] = cl["t_viirs"] + cl["aqua_dt_min"]
    cl["aqua_covered"] = cl["aqua_dt_min"].notna()
    cl["comparable"] = cl["aqua_dt_min"].abs() <= cfg.max_dt_min

    # --- VIIRS coverage of Aqua pixels
    covm = _nearest_observation(geo_v, m["latitude"], m["longitude"], m["t_obs"], cfg.search_dt_min)
    m["viirs_dt_min"] = covm["dt_min"].to_numpy()
    m["viirs_covered"] = m["viirs_dt_min"].notna()
    m["comparable"] = m["viirs_dt_min"].abs() <= cfg.max_dt_min
    m["diag_km"] = _diag(m)

    # --- Match MODIS pixels to VIIRS clusters observed by Aqua (any dt within the search window)
    cov_cl = cl.loc[cl["aqua_covered"], ["cluster", "t_aqua"]].set_index("cluster")["t_aqua"]
    vv = v[v["cluster"].isin(cov_cl.index)].reset_index(drop=True)
    vv_t_aqua = cov_cl.reindex(vv["cluster"]).to_numpy()
    m["cluster"] = -1
    detected = np.zeros(int(cl["cluster"].max()) + 1 if len(cl) else 0, dtype=bool)
    if len(vv) and len(m):
        vxyz = _xyz(vv["latitude"], vv["longitude"])
        tree = cKDTree(vxyz)
        mxyz = _xyz(m["latitude"], m["longitude"])
        r = 0.5 * (m["diag_km"].to_numpy() + vv["diag_km"].max()) + cfg.match_tol_km
        cand = tree.query_ball_point(mxyz, r=r)
        mt, md = m["t_obs"].to_numpy(), m["diag_km"].to_numpy()
        vd, vcl = vv["diag_km"].to_numpy(), vv["cluster"].to_numpy()
        assign = np.full(len(m), -1)
        for k, c in enumerate(cand):
            if not c:
                continue
            c = np.asarray(c)
            ok = np.abs(vv_t_aqua[c] - mt[k]) <= cfg.same_pass_min
            if not ok.any():
                continue
            c = c[ok]
            dist = np.linalg.norm(vxyz[c] - mxyz[k], axis=1)
            ok = dist <= 0.5 * (md[k] + vd[c]) + cfg.match_tol_km
            if not ok.any():
                continue
            c, dist = c[ok], dist[ok]
            detected[np.unique(vcl[c])] = True
            assign[k] = vcl[c[np.argmin(dist)]]
        m["cluster"] = assign
    m["matched"] = m["cluster"] >= 0
    agg = m[m["matched"]].groupby("cluster").agg(n_modis=("frp_mw", "size"), frp_modis=("frp_mw", "sum"))
    cl["modis_detected"] = detected[cl["cluster"].to_numpy()] if len(cl) else False
    cl = cl.join(agg, on="cluster")
    cl["n_modis"] = cl["n_modis"].fillna(0).astype(int)
    cl["frp_modis"] = cl["frp_modis"].fillna(0.0)
    m["month"] = m["acq_date"].dt.to_period("M").astype(str)
    m["year"] = m["acq_date"].dt.year
    m["region"] = region.name

    # --- coverage diagnostics
    adt = cl["aqua_dt_min"].abs()
    diag["clusters"] = {
        "n": int(len(cl)),
        "share_aqua_observed_within_search": float(cl["aqua_covered"].mean()),
        "share_comparable": float(cl["comparable"].mean()),
        "comparable_n": int(cl["comparable"].sum()),
        "abs_dt_quantiles_covered": adt.dropna().quantile([.1, .25, .5, .75, .9]).round(1).to_dict(),
    }
    diag["modis_pixels"] = {
        "n": int(len(m)), "share_viirs_observed_within_search": float(m["viirs_covered"].mean()),
        "share_comparable": float(m["comparable"].mean()),
        "share_comparable_matched": float(m.loc[m["comparable"], "matched"].mean()) if m["comparable"].any() else None,
    }
    bins = [0, 5, 10, 15, 20, 30, 45, 60, 100]
    dc = cl[cl["aqua_covered"]].assign(bin=pd.cut(adt[cl["aqua_covered"]], bins, include_lowest=True))
    diag["detection_rate_by_abs_dt"] = [
        {"abs_dt_bin": str(k), "n_clusters": int(len(s)), "p_detected": float(s["modis_detected"].mean()),
         "p_detected_frp_ge_20MW": float(s.loc[s.frp_sum >= 20, "modis_detected"].mean()) if (s.frp_sum >= 20).any() else None}
        for k, s in dc.groupby("bin", observed=True)]

    out_dir.mkdir(parents=True, exist_ok=True)
    keep_v = ["latitude", "longitude", "t_obs", "xtrack", "frp_mw", "scan_km", "track_km", "cluster", "daynight", "acq_date"]
    write_parquet(cl, paths["clusters"])
    write_parquet(m.drop(columns=["qc_usable"]), paths["modis"])
    write_parquet(v[keep_v], paths["viirs"])
    import json
    (out_dir / f"diagnostics_{tag}.json").write_text(json.dumps(diag, indent=2, default=str))
    log.info("%s: %d clusters (%d comparable), %d Aqua pixels (%d comparable)", region.name, len(cl),
             int(cl["comparable"].sum()), len(m), int(m["comparable"].sum()))
    return {"clusters": cl, "modis": m, "viirs": v[keep_v], "diagnostics": diag}
