"""Pattern discovery on monthly fire activity: seasonality, trends, hotspots,
anomalies and regional contrasts.

Robustness principle: a pattern is reported as robust only if it appears in
independent sensor records (Aqua-MODIS observed and S-NPP VIIRS observed,
plus the MODIS-equivalent series where harmonisation was validated). Detection
counts are never compared across sensors directly.

Methods are deliberately simple: calendar-month climatologies, Theil-Sen slopes
with Mann-Kendall tests on annual totals, robust z-scores against same-month
climatology, Getis-Ord Gi* for spatial hotspots, and Isolation Forest for
unusual cell-months. With ~10 years of data, trends are descriptive and
low-powered.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import IsolationForest

from .catalog import REGIONS
from .cleaning import load_detections
from .config import Settings
from .grid import cell_index

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def regional_monthly(settings: Settings, region: str, source: str, start: date, end: date,
                     platform: str | None = None) -> pd.DataFrame:
    d = load_detections(settings, REGIONS[region], source, start, end,
                        columns=["acq_date", "frp_mw", "fire_type", "platform", "qc_usable"], usable_only=True)
    d = d[d["fire_type"].fillna(-1).eq(0)]
    if platform:
        d = d[d["platform"] == platform]
    g = d.groupby(d["acq_date"].dt.to_period("M"))
    months = pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M")
    return pd.DataFrame({"count": g.size(), "frp": g["frp_mw"].sum()}).reindex(months, fill_value=0)


def seasonal_profile(s: pd.Series) -> dict:
    """Mean share of annual activity per calendar month; peak and core season."""
    df = pd.DataFrame({"v": s.to_numpy(), "y": s.index.year, "m": s.index.month})
    full = df.groupby("y")["m"].nunique() == 12
    df = df[df["y"].isin(full[full].index)]
    tot = df.groupby("y")["v"].transform("sum").replace(0, np.nan)
    share = (df["v"] / tot).groupby(df["m"]).mean().reindex(range(1, 13)).fillna(0)
    order = share.sort_values(ascending=False)
    core = order.index[: int(np.searchsorted(order.cumsum().to_numpy(), 0.8) + 1)]
    return {"monthly_share": {MONTHS[m - 1]: round(float(v), 4) for m, v in share.items()},
            "peak_month": MONTHS[int(order.index[0]) - 1],
            "core_season_months_80pct": [MONTHS[m - 1] for m in sorted(core)],
            "n_core_months": int(len(core)), "n_full_years": int(full.sum())}


def trend(s: pd.Series) -> dict:
    """Theil-Sen slope of log10 annual totals (%/yr) with Mann-Kendall test."""
    ann = s.groupby(s.index.year).sum()
    months = s.groupby(s.index.year).size()
    ann = ann[months == 12]
    if len(ann) < 5:
        return {"n_years": int(len(ann)), "note": "too few complete years"}
    y = np.log10(ann.to_numpy() + 1)
    x = ann.index.to_numpy()
    slope, _, lo, hi = stats.theilslopes(y, x)
    tau, p = stats.kendalltau(x, y)
    pct = lambda b: float((10 ** b - 1) * 100)
    return {"n_years": int(len(ann)), "years": [int(x[0]), int(x[-1])],
            "theil_sen_pct_per_year": pct(slope), "ci95_pct_per_year": [pct(lo), pct(hi)],
            "mann_kendall_tau": float(tau), "p_value": float(p),
            "significant_at_0.05": bool(p < 0.05),
            "annual_totals": {int(k): float(v) for k, v in ann.items()},
            "interannual_cv": float(ann.std() / ann.mean()) if ann.mean() > 0 else None}


def monthly_anomalies(s: pd.Series, z_thresh: float = 3.0, min_abs_share: float = 0.01) -> pd.DataFrame:
    """Robust z-score of log1p activity vs the same calendar month in OTHER years.

    A month is an ``anomaly`` only if |z| >= ``z_thresh`` AND its absolute departure
    from the same-month median is >= ``min_abs_share`` of the series' mean annual
    total. The second condition stops a handful of detections in a month that is
    normally zero (e.g. Siberian winter) from being reported as a regional anomaly.
    """
    s = s.astype(float)
    v = np.log1p(s)
    n_years = max(len(set(s.index.year)), 1)
    min_abs = min_abs_share * s.sum() / n_years
    rows = []
    for per, val in v.items():
        same = v[(v.index.month == per.month) & (v.index.year != per.year)]
        med = same.median()
        mad = 1.4826 * (same - med).abs().median()
        scale = max(mad, 0.25)  # floor: avoids huge z when other years are identical
        rows.append({"month": str(per), "value": float(s[per]), "same_month_median": float(np.expm1(med)),
                     "z": float((val - med) / scale)})
    out = pd.DataFrame(rows)
    out["material"] = (out["value"] - out["same_month_median"]).abs() >= min_abs
    out["anomaly"] = (out["z"].abs() >= z_thresh) & out["material"]
    return out


def cell_month_table(settings: Settings, region: str, source: str, start: date, end: date,
                     res: float = 0.25) -> pd.DataFrame:
    d = load_detections(settings, REGIONS[region], source, start, end,
                        columns=["latitude", "longitude", "acq_date", "frp_mw", "fire_type", "daynight", "qc_usable"],
                        usable_only=True)
    d = d[d["fire_type"].fillna(-1).eq(0)]
    _, _, cid = cell_index(d["latitude"], d["longitude"], res)
    d = d.assign(cell_id=cid, month=d["acq_date"].dt.to_period("M"), night=d["daynight"].eq("N"))
    g = d.groupby(["cell_id", "month"])
    t = pd.DataFrame({"count": g.size(), "frp": g["frp_mw"].sum(), "night_share": g["night"].mean(),
                      "active_days": g["acq_date"].nunique(), "lat": g["latitude"].mean(),
                      "lon": g["longitude"].mean()}).reset_index()
    t["region"] = region
    return t


def gi_star(grid: pd.DataFrame, values: np.ndarray) -> np.ndarray:
    """Getis-Ord Gi* z-scores with a 3x3 (queen + self) neighbourhood.

    ``grid`` holds integer ``row``/``col`` cell indices for every cell of the
    study area (cells without activity included, with value 0).
    """
    x = np.asarray(values, float)
    key = pd.Series(x, index=pd.MultiIndex.from_arrays([grid["row"].to_numpy(), grid["col"].to_numpy()]))
    s = np.zeros(len(grid))
    w = np.zeros(len(grid))
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            nb = key.reindex(pd.MultiIndex.from_arrays([grid["row"].to_numpy() + dr, grid["col"].to_numpy() + dc]))
            s += nb.fillna(0).to_numpy()
            w += nb.notna().to_numpy()
    n, xbar, sd = len(x), x.mean(), x.std(ddof=0)
    num = s - xbar * w
    with np.errstate(invalid="ignore", divide="ignore"):
        den = sd * np.sqrt((n * w - w ** 2) / max(n - 1, 1))
        return np.where(den > 0, num / den, 0.0)


def getis_ord_hotspots(cells: pd.DataFrame, value: str, res: float = 0.25) -> pd.DataFrame:
    """Gi* z-scores with a 3x3 (queen + self) neighbourhood over all cells in the bbox.

    Cells with no activity are included as zeros. Not corrected for multiple
    testing; z > 2.58 is used as a descriptive threshold.
    """
    from .forecast import region_cells
    region = cells["region"].iloc[0]
    grid = region_cells(region, res).set_index("cell_id")
    x = cells.set_index("cell_id")[value].reindex(grid.index).fillna(0)
    out = grid.reset_index()[["cell_id", "lat", "lon"]]
    out[value] = x.to_numpy()
    out["gi_star_z"] = gi_star(grid, x.to_numpy())
    out["hotspot_99"] = out["gi_star_z"] > 2.58
    return out


def isolation_forest_anomalies(cm: pd.DataFrame, contamination: float = 0.01, seed: int = 0) -> pd.DataFrame:
    """Unusual cell-months within a region.

    Features: log activity, log FRP, FRP per detection, night share, active
    days, and departure from the cell's same-calendar-month median in other
    years. Isolation Forest flags unusual combinations; the flags are
    hypotheses for inspection, not confirmed events.
    """
    d = cm.copy()
    d["lc"] = np.log1p(d["count"])
    d["lf"] = np.log1p(d["frp"])
    d["frp_per_det"] = np.log1p(d["frp"] / d["count"])
    d["moy"] = d["month"].dt.month
    d["yr"] = d["month"].dt.year
    # departure from the same cell & calendar month in other years (missing months count as 0)
    piv = d.pivot_table(index=["cell_id", "moy"], columns="yr", values="lc", fill_value=0)
    dep = []
    for (c, mo, yr, lc) in d[["cell_id", "moy", "yr", "lc"]].itertuples(index=False):
        row = piv.loc[(c, mo)]
        others = row.drop(yr, errors="ignore")
        dep.append(lc - (others.median() if len(others) else 0))
    d["departure"] = dep
    feats = ["lc", "lf", "frp_per_det", "night_share", "active_days", "departure"]
    X = d[feats].fillna(0).to_numpy()
    iso = IsolationForest(n_estimators=300, contamination=contamination, random_state=seed).fit(X)
    d["iso_score"] = -iso.score_samples(X)
    d["iso_anomaly"] = iso.predict(X) == -1
    return d
