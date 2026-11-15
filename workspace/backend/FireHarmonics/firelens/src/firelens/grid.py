"""Shared global grid and monthly aggregation.

The grid is a regular latitude/longitude grid anchored at (-90, -180), so cell
IDs are globally unique and identical for every sensor and region. The
default 0.25 degree resolution is coarser than both sensors' pixels (MODIS
~1 km, VIIRS 375 m at nadir), which limits sensitivity to geolocation
differences between sensors.

Output of this module is a *preliminary aggregation*: counts are per sensor
and are NOT harmonised. MODIS and VIIRS counts must not be added or compared
1:1 (see ``harmonization.py``).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_RES_DEG = 0.25
HARMONIZATION_STATUS = "preliminary_aggregation_unharmonized"


def grid_shape(res: float) -> tuple[int, int]:
    n_rows, n_cols = 180 / res, 360 / res
    if not (float(n_rows).is_integer() and float(n_cols).is_integer()):
        raise ValueError("Grid resolution must divide 180 and 360 exactly")
    return int(n_rows), int(n_cols)


def cell_index(lat, lon, res: float = DEFAULT_RES_DEG) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (row, col, cell_id) for coordinates. Edges (lat=90, lon=180) go to the last cell."""
    n_rows, n_cols = grid_shape(res)
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    row = np.clip(np.floor((lat + 90.0) / res), 0, n_rows - 1).astype(np.int64)
    col = np.clip(np.floor((lon + 180.0) / res), 0, n_cols - 1).astype(np.int64)
    return row, col, row * n_cols + col


def cell_center(cell_id, res: float = DEFAULT_RES_DEG) -> tuple[np.ndarray, np.ndarray]:
    _, n_cols = grid_shape(res)
    cell_id = np.asarray(cell_id, dtype=np.int64)
    row, col = cell_id // n_cols, cell_id % n_cols
    return -90.0 + (row + 0.5) * res, -180.0 + (col + 0.5) * res


def add_grid(df: pd.DataFrame, res: float = DEFAULT_RES_DEG) -> pd.DataFrame:
    df = df.copy()
    _, _, cid = cell_index(df["latitude"], df["longitude"], res)
    df["cell_id"] = cid
    return df


def monthly_grid(df: pd.DataFrame, res: float = DEFAULT_RES_DEG) -> pd.DataFrame:
    """Aggregate usable detections to (source, platform, cell, month).

    Platform is kept as a key because MODIS_SP mixes Terra (~10:30/22:30 local)
    and Aqua (~13:30/01:30), which differ in sampling from S-NPP VIIRS (~13:30/01:30).

    Expects processed detections; rows with ``qc_usable == False`` are excluded
    (their numbers are reported in the QC summaries).
    """
    if "qc_usable" in df:
        df = df[df["qc_usable"]]
    if df.empty:
        return pd.DataFrame()
    df = add_grid(df, res)
    df["month"] = df["acq_date"].dt.to_period("M")
    df["is_veg"] = df["fire_type"].fillna(-1).eq(0)
    df["is_high"] = df["confidence_class"].astype(str).eq("high")
    df["is_nom_high"] = df["confidence_class"].astype(str).isin(["nominal", "high"])
    df["is_day"] = df["daynight"].eq("D")
    df["overpass"] = df["platform"].astype(str) + "|" + df["acq_datetime_utc"].astype(str)
    g = df.groupby(["source", "sensor", "platform", "cell_id", "month"], observed=True)
    out = g.agg(
        n_detections=("latitude", "size"),
        n_vegetation=("is_veg", "sum"),
        n_conf_high=("is_high", "sum"),
        n_conf_nominal_high=("is_nom_high", "sum"),
        n_day=("is_day", "sum"),
        frp_sum_mw=("frp_mw", "sum"),
        frp_median_mw=("frp_mw", "median"),
        n_active_days=("acq_date", "nunique"),
        n_overpass_granules=("overpass", "nunique"),
    ).reset_index()
    out["n_night"] = out["n_detections"] - out["n_day"]
    out["cell_lat"], out["cell_lon"] = cell_center(out["cell_id"], res)
    out["grid_res_deg"] = res
    out["harmonization_status"] = HARMONIZATION_STATUS
    return out


def harmonization_frame(grid: pd.DataFrame, coverage: pd.DataFrame, modis_source: str = "MODIS_SP",
                        viirs_source: str = "VIIRS_SNPP_SP", min_coverage: float = 0.9,
                        res: float = DEFAULT_RES_DEG,
                        modis_platforms: tuple[str, ...] | None = None) -> pd.DataFrame:
    """Wide cell-month table pairing MODIS and VIIRS with coverage indicators.

    Rows: every cell that had at least one detection from either sensor, for
    every month in the coverage table. Counts are 0 where the sensor observed
    the month and detected nothing, and NaN where the sensor did not observe
    (coverage below ``min_coverage`` or outside its record). This keeps
    "no fire detected" distinct from "no data".

    ``modis_platforms=("Aqua",)`` restricts MODIS to Aqua, whose overpass time
    matches S-NPP; ``coverage`` must then hold Aqua-only coverage for MODIS.
    """
    if modis_platforms:
        grid = grid[(grid["source"] != modis_source) | grid["platform"].isin(modis_platforms)]
    grid = grid[grid["source"].isin([modis_source, viirs_source])]
    cells = np.sort(grid["cell_id"].unique())
    months = pd.period_range(coverage["month"].min(), coverage["month"].max(), freq="M")
    idx = pd.MultiIndex.from_product([cells, months], names=["cell_id", "month"])
    out = pd.DataFrame(index=idx).reset_index()
    metrics = ["n_detections", "n_vegetation", "n_conf_nominal_high", "frp_sum_mw"]
    for prefix, sid in (("modis", modis_source), ("viirs", viirs_source)):
        g = grid[grid["source"] == sid].groupby(["cell_id", "month"])[metrics].sum()
        g.columns = [f"{prefix}_{c}" for c in metrics]
        out = out.join(g, on=["cell_id", "month"])
        cov = coverage[coverage["source"] == sid].set_index("month")["coverage_fraction"]
        out[f"{prefix}_coverage"] = out["month"].map(cov)
        observed = out[f"{prefix}_coverage"].fillna(0) >= min_coverage
        out[f"{prefix}_observed"] = observed
        for c in g.columns:
            out[c] = out[c].fillna(0).where(observed)
    out["both_observed"] = out["modis_observed"] & out["viirs_observed"]
    out["modis_platforms"] = "+".join(modis_platforms) if modis_platforms else "all"
    out["cell_lat"], out["cell_lon"] = cell_center(out["cell_id"], res)
    out["grid_res_deg"] = res
    out["harmonization_status"] = HARMONIZATION_STATUS
    return out
