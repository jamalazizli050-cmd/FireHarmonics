"""Location-level (disc) monthly series and leakage-free forecast features.

One code path serves both the pooled-model training set (thousands of discs
sampled inside the five downloaded regions) and inference for a single
arbitrary location, so features are computed identically in both cases.

Unit of analysis
----------------
A *window* is a disc of ``radius_km`` around (lat, lon). For every month we
count usable S-NPP VIIRS vegetation-fire detections (FIRMS ``type`` 0) whose
coordinates fall inside the disc, plus the same count in a fixed 100 km
*context* disc (regional activity around the location).

Months that the satellite record does not cover (coverage below the
threshold, before the S-NPP record, after the product's last available day,
or FIRMS-listed missing days) are NaN, never 0.

Forecast features for target month ``t`` at lead ``L`` use months ``<= t-L``
only, except same-calendar-month climatology which uses months ``t-12,
t-24, ...`` (all earlier than ``t-L`` because ``L <= 11``).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0088
CONTEXT_KM = 100.0
MAX_LEAD = 12

STATIC = ["lat", "abs_lat", "lon", "log_area_km2"]
FEATURES_ALL = STATIC + [
    "lead", "month_sin", "month_cos", "local_season_sin", "local_season_cos",
    "lag_1", "lag_2", "lag_3", "lag_12", "any_last",
    "roll3", "roll6", "roll12", "frp_roll12",
    "hist_freq", "n_hist_months", "clim_same_month", "clim_years", "clim_mean_log",
    "months_since_fire", "p_seasonal_raw",
    "ctx_lag_1", "ctx_roll12", "ctx_hist_freq", "ctx_clim_same_month"]


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2 +
         np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def disc_area_km2(radius_km: float) -> float:
    """Area of a spherical cap of geodesic radius ``radius_km``."""
    return float(2 * np.pi * EARTH_RADIUS_KM ** 2 * (1 - np.cos(radius_km / EARTH_RADIUS_KM)))


def disc_series(det_lat, det_lon, det_month_idx, det_frp, lat0: float, lon0: float,
                radii_km, n_months: int, context_km: float = CONTEXT_KM) -> dict:
    """Monthly detection counts and FRP sums inside discs around one centre.

    Returns ``{"count": (n_radii, n_months), "frp": (...), "ctx": (n_months,)}``.
    Unobserved months are *not* masked here (see :func:`mask_unobserved`).
    """
    radii = np.atleast_1d(np.asarray(radii_km, float))
    rmax = max(float(radii.max()), context_km)
    det_lat = np.asarray(det_lat, float)
    # cheap latitude pre-filter before the exact great-circle distance
    band = np.abs(det_lat - lat0) <= rmax / 111.0 + 0.05
    idx = np.flatnonzero(band)
    d = haversine_km(lat0, lon0, det_lat[idx], np.asarray(det_lon, float)[idx])
    keep = d <= rmax
    idx, d = idx[keep], d[keep]
    m = np.asarray(det_month_idx)[idx]
    f = np.nan_to_num(np.asarray(det_frp, float)[idx])
    count = np.zeros((len(radii), n_months))
    frp = np.zeros((len(radii), n_months))
    for i, r in enumerate(radii):
        sel = d <= r
        count[i] = np.bincount(m[sel], minlength=n_months)[:n_months]
        frp[i] = np.bincount(m[sel], weights=f[sel], minlength=n_months)[:n_months]
    ctx = np.bincount(m[d <= context_km], minlength=n_months)[:n_months].astype(float)
    return {"count": count, "frp": frp, "ctx": ctx}


def mask_unobserved(a: np.ndarray, observed: np.ndarray) -> np.ndarray:
    out = np.asarray(a, float).copy()
    out[..., ~np.asarray(observed, bool)] = np.nan
    return out


# --------------------------------------------------------------- features
def _shift(a: np.ndarray, k: int) -> np.ndarray:
    """a[:, t-k] at position t (NaN where t-k < 0)."""
    out = np.full_like(a, np.nan)
    if k < a.shape[1]:
        out[:, k:] = a[:, :a.shape[1] - k]
    return out


def _rolling_nanmean(a: np.ndarray, w: int, min_obs: int) -> np.ndarray:
    """Mean of a[:, t-w+1..t] over observed (non-NaN) months."""
    v = np.nan_to_num(a)
    o = (~np.isnan(a)).astype(float)
    cv = np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(v, axis=1)], axis=1)
    co = np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(o, axis=1)], axis=1)
    t = np.arange(a.shape[1])
    lo = np.maximum(t - w + 1, 0)
    s = cv[:, t + 1] - cv[:, lo]
    n = co[:, t + 1] - co[:, lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = s / n
    out[n < min_obs] = np.nan
    return out


def _expanding(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean and count of observed values over a[:, 0..t]."""
    v = np.cumsum(np.nan_to_num(a), axis=1)
    n = np.cumsum(~np.isnan(a), axis=1).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, v / n, np.nan), n


def _same_month_previous_years(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean/count of observed a[:, t-12], a[:, t-24], ... at position t."""
    mean = np.full_like(a, np.nan)
    cnt = np.zeros_like(a)
    for o in range(12):
        cols = np.arange(o, a.shape[1], 12)
        sub = a[:, cols]
        v = np.cumsum(np.nan_to_num(sub), axis=1)
        n = np.cumsum(~np.isnan(sub), axis=1).astype(float)
        # previous years only: shift by one position along the year axis
        v = np.concatenate([np.zeros((a.shape[0], 1)), v[:, :-1]], axis=1)
        n = np.concatenate([np.zeros((a.shape[0], 1)), n[:, :-1]], axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean[:, cols] = np.where(n > 0, v / n, np.nan)
        cnt[:, cols] = n
    return mean, cnt


def seasonal_baseline(clim_same_month, clim_years, hist_freq) -> np.ndarray:
    """Share of past years with fire in the same calendar month, shrunk towards
    the window's overall monthly fire frequency with 2 pseudo-years (the
    baseline used in the California forecast, applied to discs)."""
    n = np.nan_to_num(np.asarray(clim_years, float))
    k = np.nan_to_num(np.asarray(clim_same_month, float)) * n
    prior = np.nan_to_num(np.asarray(hist_freq, float))
    return (k + 2 * prior) / (n + 2)


def build_features(count: np.ndarray, frp: np.ndarray, ctx: np.ndarray, months: pd.PeriodIndex,
                   lat: np.ndarray, lon: np.ndarray, radius_km: np.ndarray, lead: int) -> dict:
    """Feature matrices (n_windows, n_months) for target months at ``lead``.

    ``count``/``frp``/``ctx`` are (n_windows, n_months) with NaN for unobserved
    months. To forecast beyond the data, pass arrays extended with NaN months.
    """
    if not 1 <= lead <= MAX_LEAD - 1:
        raise ValueError(f"lead must be 1..{MAX_LEAD - 1}")
    count = np.atleast_2d(np.asarray(count, float))
    frp = np.atleast_2d(np.asarray(frp, float))
    ctx = np.atleast_2d(np.asarray(ctx, float))
    nw, nm = count.shape
    col = lambda v: np.broadcast_to(np.asarray(v, float).reshape(-1, 1), (nw, nm))
    lc, lf, lx = np.log1p(count), np.log1p(frp), np.log1p(ctx)
    y = np.where(np.isnan(count), np.nan, (count > 0).astype(float))
    yx = np.where(np.isnan(ctx), np.nan, (ctx > 0).astype(float))
    L = lead
    feats: dict[str, np.ndarray] = {}
    feats["lat"], feats["lon"] = col(lat), col(lon)
    feats["abs_lat"] = np.abs(feats["lat"])
    feats["log_area_km2"] = col([np.log(disc_area_km2(r)) for r in np.atleast_1d(radius_km)])
    feats["lead"] = np.full((nw, nm), float(L))
    moy = np.asarray(months.month)
    ang = 2 * np.pi * (moy - 1) / 12
    feats["month_sin"], feats["month_cos"] = col(np.ones(nw)) * np.sin(ang), col(np.ones(nw)) * np.cos(ang)
    # hemisphere-adjusted season: southern-hemisphere months shifted by six
    south = col(np.asarray(lat) < 0)
    ang_l = ang[None, :] + np.where(south > 0, np.pi, 0.0)
    feats["local_season_sin"], feats["local_season_cos"] = np.sin(ang_l), np.cos(ang_l)
    feats["lag_1"], feats["lag_2"], feats["lag_3"] = _shift(lc, L), _shift(lc, L + 1), _shift(lc, L + 2)
    feats["lag_12"] = _shift(lc, 12)
    feats["any_last"] = _shift(y, L)
    for w in (3, 6, 12):
        feats[f"roll{w}"] = _shift(_rolling_nanmean(lc, w, (w + 1) // 2), L)
    feats["frp_roll12"] = _shift(_rolling_nanmean(lf, 12, 6), L)
    hf, hn = _expanding(y)
    feats["hist_freq"], feats["n_hist_months"] = _shift(hf, L), np.nan_to_num(_shift(hn, L))
    cm, cn = _same_month_previous_years(y)
    feats["clim_same_month"], feats["clim_years"] = cm, cn
    feats["clim_mean_log"] = _same_month_previous_years(lc)[0]
    # months since the last observed fire, counted to the target month
    t = np.arange(nm)
    last = np.where(y > 0, t[None, :], -1)
    last = np.maximum.accumulate(last, axis=1)
    last_s = _shift(last.astype(float), L)
    feats["months_since_fire"] = np.where(np.nan_to_num(last_s, nan=-1) >= 0, np.minimum(t[None, :] - last_s, 120), 121.0)
    feats["p_seasonal_raw"] = seasonal_baseline(cm, cn, feats["hist_freq"])
    feats["ctx_lag_1"] = _shift(lx, L)
    feats["ctx_roll12"] = _shift(_rolling_nanmean(lx, 12, 6), L)
    feats["ctx_hist_freq"] = _shift(_expanding(yx)[0], L)
    feats["ctx_clim_same_month"] = _same_month_previous_years(lx)[0]
    feats["y"] = y
    return feats


def to_frame(feats: dict, months: pd.PeriodIndex, window_ids=None, min_hist_months: int = 12,
             require_target: bool = True) -> pd.DataFrame:
    """Flatten feature matrices to rows (window, month).

    Keeps rows with at least ``min_hist_months`` observed months before the
    source month and (if ``require_target``) an observed target.
    """
    nw, nm = feats["y"].shape
    wid = np.repeat(np.arange(nw) if window_ids is None else np.asarray(window_ids), nm)
    df = pd.DataFrame({k: v.reshape(-1) for k, v in feats.items()})
    df["window"] = wid
    df["month"] = np.tile(np.asarray(months.astype(str)), nw)
    df["year"] = np.tile(np.asarray(months.year), nw)
    keep = df["n_hist_months"] >= min_hist_months
    if require_target:
        keep &= df["y"].notna()
    return df[keep].reset_index(drop=True)
