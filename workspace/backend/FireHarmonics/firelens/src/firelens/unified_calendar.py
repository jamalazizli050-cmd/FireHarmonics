"""One burning-activity calendar from MODIS and VIIRS (2002-present): the FireHarmonics continuity chain.

Model
-----
1. **Unit: active 1-km cell-overpasses.** Every quality-controlled vegetation-fire detection is snapped to a common
   0.01 deg grid; a grid cell that burns in a given day or night overpass counts once, however many pixels the sensor
   split the fire into. This removes most of the 375 m (VIIRS) vs 1 km (MODIS) pixel fragmentation before any
   calibration. Day and night are kept apart (VIIRS is far more sensitive at night).
2. **Scale: Aqua MODIS.** The reference record is Aqua MODIS (2002-07 onwards, ~13:30/01:30 overpass); Terra is
   excluded so the record does not jump when Aqua starts.
3. **Chain.** Each later sensor is linked to the one before it on the years both flew:
   ``Aqua <- theta1 <- S-NPP VIIRS`` (2012-2021, before Aqua's orbit drift) and
   ``S-NPP <- theta2 <- NOAA-20 VIIRS`` (2019 onwards). A month observed only by NOAA-20 (after S-NPP delivery ended on
   2026-11-02) is ``theta1 * theta2 * NOAA-20``; uncertainty is propagated through both links.
4. **Coefficients with partial pooling.** Per link and day/night stratum, ``ref ~ Poisson(theta * target)`` with a
   Gamma prior centred on the regional ratio (5 deg cell, then 15 deg neighbourhood, then global; from
   ``harmonization_priors.json``). The posterior mean ``theta = (sum ref + kappa*prior) / (sum target + kappa)`` uses
   the local overlap where it has data and falls back smoothly to the region where it does not; nothing is ever
   divided by a zero month.
5. **Uncertainty.** Year-block bootstrap of every link plus negative-binomial counting noise whose dispersion is
   estimated on the overlap months; each calendar month carries a 90 % interval.
6. **Anomalies.** Each month is placed against the same calendar month of a fixed 2003-2021 baseline (percentile,
   median, difference); "high" means above the 90th percentile with the interval clear of the median.
7. **Validation, held out in time.** "MODIS switched off": theta1 fitted on 2012-2016 (priors from those years only)
   predicts Aqua in 2017-2021 from S-NPP alone; the bridge test does the same for NOAA-20 -> S-NPP (fit 2019-2022,
   test 2023-2025). Both are compared with raw VIIRS and with the previous pixel-ratio method.

Hotspot detections are observations, not fires; the calendar measures satellite-observed burning activity on one
consistent scale, not the number of fires or burned area. Cloud-hidden fires are not reconstructed.
"""
from __future__ import annotations

import io
import json
import math
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

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

# ------------------------------------------------------------------ constants
FIRST_YEAR = {"modis": 2002, "viirs-snpp": 2012, "viirs-jpss1": 2018}   # first FIRMS yearly-archive year
MODIS_START = pd.Period("2002-07", "M")         # first full month of Aqua MODIS
VIIRS_START = pd.Period("2012-01", "M")
N20_FIRST_FULL_YEAR = 2019                      # NOAA-20 standard processing starts during 2018
AQUA_FIT_YEARS = list(range(2012, 2022))        # Aqua orbit drift begins in 2022
N20_FIT_FIRST = 2019
SPLITS = {"aqua_snpp": (list(range(2012, 2017)), list(range(2017, 2022))),
          "snpp_n20": (list(range(2019, 2023)), list(range(2023, 2026)))}
GRID_DEG = 0.01
STRATA = ("D", "N")
KAPPA = 40.0                                    # prior strength, in target active units (tuned on the benchmark)
BASELINE_YEARS = (2003, 2021)
BOOTSTRAP_REPS = 400
NOW_DAYS = 14
PRIORS_FILE = Path(__file__).with_name("harmonization_priors.json")
# Used only when harmonization_priors.json is missing or has no data: pooled ratios over the 450 country-year files
# (US, Angola, Zambia, southern/central Europe, UK) cached when the table was first built (2026-11-15).
DEFAULT_PRIOR = {"aqua_snpp": {"D": 0.343, "N": 0.055}, "snpp_n20": {"D": 0.939, "N": 0.899}}
MIN_PRIOR_UNITS = 200

LABEL = "FireHarmonics continuity chain (Aqua MODIS <- S-NPP VIIRS <- NOAA-20 VIIRS), experimental"
UNIT = "active 1-km cell-overpasses on the Aqua-MODIS scale"
UNIT_DEFINITION = ("A 1-km grid cell (0.01 deg) with at least one quality-controlled vegetation-fire detection in a "
                   "day or night overpass counts once, however many pixels the sensor split the fire into.")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

LIMITATIONS = [
    "Detections are satellite observations, not fires; the calendar is satellite-observed burning activity on one "
    "consistent scale, not the number of fires or burned area.",
    "FIRMS hotspot files contain no clear-sky non-fire observations, so the activity is not normalised for clouds or "
    "observation effort; fires hidden by clouds or smoke are not reconstructed.",
    "One coefficient per link and day/night stratum, assumed stable in time; validated forward in time where the "
    "overlap allows (see validation), not beyond it.",
    "Low-confidence detections and non-vegetation (static/industrial) sources are excluded.",
    "Years from the FIRMS yearly archives are treated as fully observed; FIRMS-listed missing days are not removed.",
]


# ------------------------------------------------------------------ data
def disc_bbox(lat: float, lon: float, radius_km: float) -> BBox:
    dlat = radius_km / 111.0 + 0.01
    dlon = radius_km / (111.32 * max(math.cos(math.radians(min(abs(lat) + dlat, 89.0))), 1e-3)) + 0.01
    return BBox(max(lon - dlon, -180), max(lat - dlat, -90), min(lon + dlon, 180), min(lat + dlat, 90))


def _usable(rows: pd.DataFrame, source_id: str) -> pd.DataFrame:
    """FireLens standardization + QC on raw archive rows (strings)."""
    if rows.empty:
        return pd.DataFrame(columns=["latitude", "longitude", "acq_date", "fire_type", "satellite_code", "daynight",
                                     "confidence_class"])
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
        if not allow_download and not all(ca.is_resolved(settings, n, y, product) for n in names):
            continue
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
    det = pd.concat(frames, ignore_index=True) if frames else _usable(pd.DataFrame(), source_id)
    return det, got


def quality(det: pd.DataFrame, aqua_only: bool = False) -> pd.DataFrame:
    """Vegetation fires, nominal/high confidence, known day/night (and Aqua only for MODIS)."""
    if det.empty:
        return det
    keep = det["fire_type"].fillna(0).eq(0) & det["daynight"].isin(STRATA)
    if "confidence_class" in det.columns:
        keep &= det["confidence_class"].astype("string").fillna("").ne("low")
    if aqua_only:
        keep &= det["satellite_code"].isin(["Aqua", "A"])
    return det[keep]


def active_units(det: pd.DataFrame, months: pd.PeriodIndex) -> pd.DataFrame:
    """Active 1-km cell-overpasses per month and day/night stratum (columns D, N)."""
    out = pd.DataFrame(0.0, index=months, columns=list(STRATA))
    if det.empty or not len(months):
        return out
    day = pd.to_datetime(det["acq_date"]).dt.normalize()
    k = pd.DataFrame({"day": day.to_numpy(), "dn": det["daynight"].to_numpy(),
                      "i": np.floor(det["latitude"].to_numpy(dtype=float) / GRID_DEG).astype(np.int64),
                      "j": np.floor(det["longitude"].to_numpy(dtype=float) / GRID_DEG).astype(np.int64)}).drop_duplicates()
    c = k.groupby([pd.to_datetime(k["day"]).dt.to_period("M"), "dn"]).size().unstack(fill_value=0)
    return c.reindex(index=months, columns=list(STRATA), fill_value=0).astype(float)


def unit_totals(det: pd.DataFrame) -> np.ndarray:
    """Active 1-km cell-overpasses (D, N) over a whole window."""
    if det.empty:
        return np.zeros(2)
    k = pd.DataFrame({"day": pd.to_datetime(det["acq_date"]).dt.normalize().to_numpy(), "dn": det["daynight"].to_numpy(),
                      "i": np.floor(det["latitude"].to_numpy(dtype=float) / GRID_DEG).astype(np.int64),
                      "j": np.floor(det["longitude"].to_numpy(dtype=float) / GRID_DEG).astype(np.int64)}).drop_duplicates()
    c = k["dn"].value_counts()
    return np.array([float(c.get(d, 0)) for d in STRATA])


def pixel_counts(det: pd.DataFrame, months: pd.PeriodIndex) -> pd.Series:
    """Monthly detection (pixel) counts, as used by the previous ratio method."""
    if det.empty:
        return pd.Series(0.0, index=months)
    return det.groupby(pd.to_datetime(det["acq_date"]).dt.to_period("M")).size().reindex(months, fill_value=0).astype(float)


# ------------------------------------------------------------------ regional priors
@lru_cache(maxsize=1)
def _prior_table() -> dict | None:
    try:
        return json.loads(priors_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def priors_path() -> Path:
    """A table rebuilt on this server ($FIRELENS_DATA_DIR/cache) wins over the one shipped with the code."""
    import os
    local = Path(os.environ["FIRELENS_DATA_DIR"]) / "cache" / PRIORS_FILE.name if os.getenv("FIRELENS_DATA_DIR") else None
    return local if local is not None and local.exists() else PRIORS_FILE


LINK_PRODUCTS = {"aqua_snpp": ("aqua", "viirs-snpp"), "snpp_n20": ("viirs-snpp", "viirs-jpss1")}


def prior_ratio(lat: float, lon: float, link: str, years: list[int]) -> tuple[dict, dict]:
    """Regional ratio per stratum from the prior table, restricted to ``years`` (no leakage into held-out years)."""
    table = _prior_table()
    if not table:
        return dict(DEFAULT_PRIOR[link]), {d: "built-in default" for d in STRATA}
    ref_p, tgt_p = LINK_PRODUCTS[link]
    g = float(table.get("grid_deg", 5))
    ci, cj = math.floor(lon / g), math.floor(lat / g)
    cells = table["cells"]
    ys = [str(y) for y in years]

    def sums(keys):
        r, t = np.zeros(2), np.zeros(2)
        for key in keys:
            c = cells.get(key)
            if not c:
                continue
            for y in ys:
                r += np.asarray(c.get(ref_p, {}).get(y, [0, 0]), dtype=float)
                t += np.asarray(c.get(tgt_p, {}).get(y, [0, 0]), dtype=float)
        return r, t

    scopes = [(f"{g:g} deg cell", [f"{ci},{cj}"]),
              (f"{3 * g:g} deg neighbourhood", [f"{ci + a},{cj + b}" for a in (-1, 0, 1) for b in (-1, 0, 1)]),
              ("global", list(cells))]
    prior, scope = {}, {}
    computed = [(name, *sums(keys)) for name, keys in scopes]
    for k, d in enumerate(STRATA):
        for name, r, t in computed:
            if t[k] >= MIN_PRIOR_UNITS and r[k] >= MIN_PRIOR_UNITS / 4:
                prior[d], scope[d] = float(r[k] / t[k]), name
                break
        else:
            prior[d], scope[d] = DEFAULT_PRIOR[link][d], "built-in default"
    return prior, scope


# ------------------------------------------------------------------ calibration
def _yearly(u: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    y = u[[p.year in years for p in u.index]]
    return y.groupby(y.index.year).sum().reindex(years, fill_value=0.0)


def _theta(ry: pd.DataFrame, ty: pd.DataFrame, prior: dict, kappa: float) -> np.ndarray:
    return np.array([(ry[d].sum() + kappa * prior[d]) / (ty[d].sum() + kappa) for d in STRATA])


def _dispersion(ref: np.ndarray, mu: np.ndarray) -> float:
    """Negative-binomial size k (method of moments): Var = mu + mu^2 / k. inf = Poisson."""
    den = float(np.sum((ref - mu) ** 2 - mu))
    num = float(np.sum(mu ** 2))
    return math.inf if den <= 0 or num <= 0 else max(num / den, 0.05)


def _nb(rng: np.random.Generator, lam: np.ndarray, k: float) -> np.ndarray:
    lam = np.maximum(lam, 0.0)
    if math.isinf(k):
        return rng.poisson(lam)
    return rng.poisson(rng.gamma(k, lam / k))


def fit_link(ref: pd.DataFrame, tgt: pd.DataFrame, years: list[int], prior: dict, kappa: float = KAPPA,
             rng: np.random.Generator | None = None, reps: int = BOOTSTRAP_REPS) -> dict:
    """Partial-pooling ratio ref/target per stratum on ``years``, its bootstrap draws and the count dispersion."""
    rng = rng or np.random.default_rng(7)
    years = [y for y in years if y in set(ref.index.year) and y in set(tgt.index.year)]
    if not years:
        return {"years": [], "theta": np.array([prior[d] for d in STRATA]),
                "boot": np.tile([prior[d] for d in STRATA], (reps, 1)), "k": math.inf,
                "ref_units": [0.0, 0.0], "target_units": [0.0, 0.0]}
    ry, ty = _yearly(ref, years), _yearly(tgt, years)
    theta = _theta(ry, ty, prior, kappa)
    yrs = np.array(years)
    boot = np.empty((reps, 2))
    for b in range(reps):
        pick = rng.choice(yrs, size=len(yrs), replace=True)
        boot[b] = _theta(ry.loc[pick], ty.loc[pick], prior, kappa)
    sel = [p for p in ref.index if p.year in years and p in tgt.index]
    mu = (tgt.loc[sel] * theta).sum(axis=1).to_numpy()
    obs = ref.loc[sel].sum(axis=1).to_numpy()
    return {"years": years, "theta": theta, "boot": boot, "k": _dispersion(obs, mu),
            "ref_units": [float(ry[d].sum()) for d in STRATA], "target_units": [float(ty[d].sum()) for d in STRATA]}


def link_summary(name: str, ref_name: str, tgt_name: str, fit: dict, prior: dict, scope: dict, kappa: float) -> dict:
    lo, hi = np.percentile(fit["boot"], [5, 95], axis=0)
    return {"link": name, "reference": ref_name, "target": tgt_name, "fit_years": fit["years"],
            "ratio": {d: round(float(fit["theta"][i]), 4) for i, d in enumerate(STRATA)},
            "ratio_ci90": {d: [round(float(lo[i]), 4), round(float(hi[i]), 4)] for i, d in enumerate(STRATA)},
            "prior": {d: round(float(prior[d]), 4) for d in STRATA}, "prior_scope": scope,
            "local_weight": {d: round(fit["target_units"][i] / (fit["target_units"][i] + kappa), 3)
                             for i, d in enumerate(STRATA)},
            "overlap_units": {"reference": dict(zip(STRATA, fit["ref_units"])),
                              "target": dict(zip(STRATA, fit["target_units"]))},
            "dispersion_k": None if math.isinf(fit["k"]) else round(fit["k"], 3)}


# ------------------------------------------------------------------ validation
def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 6 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


def _annual_err(pred: pd.Series, obs: pd.Series) -> tuple[float | None, list]:
    py, oy = pred.groupby(pred.index.year).sum(), obs.groupby(obs.index.year).sum()
    ok = oy[oy >= 5].index
    if not len(ok):
        return None, []
    err = (py[ok] - oy[ok]).abs() / oy[ok]
    return float(err.median()), list(ok)


def forward_test(link: str, ref: pd.DataFrame, tgt: pd.DataFrame, ref_pix: pd.Series, tgt_pix: pd.Series,
                 lat: float, lon: float, kappa: float = KAPPA, seed: int = 11) -> dict:
    """Fit on the early years, predict the reference in the later years from the target sensor alone."""
    train, test = SPLITS[link]
    have = set(ref.index.year) & set(tgt.index.year)
    train, test = [y for y in train if y in have], [y for y in test if y in have]
    if len(train) < 3 or len(test) < 2:
        return {"status": "insufficient_years", "train_years": train, "test_years": test}
    rng = np.random.default_rng(seed)
    prior, _ = prior_ratio(lat, lon, link, train)
    fit = fit_link(ref, tgt, train, prior, kappa, rng)
    months = [p for p in ref.index if p.year in test and p in tgt.index]
    obs = ref.loc[months].sum(axis=1)
    if obs.sum() < 10:
        return {"status": "too_little_activity", "train_years": train, "test_years": test,
                "explanation": "Fewer than 10 reference units in the test years: errors would be meaningless."}
    u = tgt.loc[months].to_numpy()
    new = pd.Series(u @ fit["theta"], index=obs.index)
    lam = fit["boot"] @ u.T                                   # (reps, months)
    draws = _nb(rng, lam, fit["k"])
    lo, hi = np.percentile(draws, [5, 95], axis=0)
    coverage = float(np.mean((obs.to_numpy() >= lo) & (obs.to_numpy() <= hi)))
    # previous method: pixel ratio on the training years
    tr = [p for p in ref_pix.index if p.year in train and p in tgt_pix.index]
    r_pix = float(ref_pix.loc[tr].sum() / tgt_pix.loc[tr].sum()) if tgt_pix.loc[tr].sum() > 0 else None
    obs_pix = ref_pix.loc[months]
    raw_pix = tgt_pix.loc[months]
    old = raw_pix * r_pix if r_pix is not None else None
    # anomalies: departures from the training-years climatology of the reference
    clim_u = ref.loc[[p for p in ref.index if p.year in train]].sum(axis=1)
    clim_u = clim_u.groupby(clim_u.index.month).mean()
    clim_p = ref_pix.loc[tr].groupby(ref_pix.loc[tr].index.month).mean()
    m_idx = np.array([p.month for p in months])

    def anom(series, clim):
        return series.to_numpy() - clim.reindex(m_idx).to_numpy()

    def metrics(pred, ref_obs, clim):
        if pred is None:
            return None
        err, yrs = _annual_err(pred, ref_obs)
        logerr = float(np.median(np.abs(np.log1p(pred.to_numpy()) - np.log1p(ref_obs.to_numpy()))))
        return {"median_abs_pct_error_annual": None if err is None else round(err, 4),
                "median_abs_log_error_monthly": round(logerr, 4),
                "anomaly_rank_correlation": (lambda s: None if s is None else round(s, 3))(
                    _spearman(anom(pred, clim), anom(ref_obs, clim))), "years_scored": yrs}

    by_year = []
    for y in test:
        sel = [p for p in months if p.year == y]
        by_year.append({"year": y, "reference": float(obs.loc[sel].sum()), "continuity_chain": round(float(new.loc[sel].sum()), 1),
                        "reference_pixels": float(obs_pix.loc[sel].sum()),
                        "pixel_ratio": None if old is None else round(float(old.loc[sel].sum()), 1),
                        "raw_target_pixels": float(raw_pix.loc[sel].sum())})
    res = {"status": "ok", "train_years": train, "test_years": test, "months_tested": len(months),
           "methods": {"continuity_chain": metrics(new, obs, clim_u),
                       "pixel_ratio": metrics(old, obs_pix, clim_p),
                       "raw_target": metrics(raw_pix, obs_pix, clim_p)},
           "interval_90_coverage": round(coverage, 3), "by_year": by_year}
    a = (res["methods"]["continuity_chain"] or {}).get("median_abs_pct_error_annual")
    b = (res["methods"]["pixel_ratio"] or {}).get("median_abs_pct_error_annual")
    res["beats_pixel_ratio"] = None if a is None or b is None else bool(a <= b)
    return res


# ------------------------------------------------------------------ season timing
def season_timing(values: pd.Series, today: date) -> dict:
    """Typical onset / peak / end months (10 % / max / 90 % of a fire-year's activity) and this year's status."""
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
        return {"status": "insufficient_data", "explanation": "Fewer than three complete fire-years with activity."}
    med = {k: int(round(float(np.median([y[k] for y in years])))) for k in ("onset", "peak", "end", "length_months")}
    name = lambda i: MONTHS[order[i] - 1]
    out = {"status": "ok", "fire_year_starts": MONTHS[start - 1],
           "definition": "Onset = month when 10 % of a fire-year's activity is reached, end = 90 %, peak = busiest "
                         "month; the fire-year starts at the calendar month with the least activity.",
           "typical": {"onset": name(med["onset"]), "peak": name(med["peak"]), "end": name(med["end"]),
                       "length_months": med["length_months"]},
           "years": [{**y, "onset": name(y["onset"]), "peak": name(y["peak"]), "end": name(y["end"])} for y in years]}
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
                          "detections_so_far": round(float(so_far.sum()), 1),
                          "typical_detections_by_now": round(typical_by_now, 2),
                          "onset": name(onset_now) if onset_now is not None else None,
                          "onset_status": status}
    return out


# ------------------------------------------------------------------ anomalies
def add_anomalies(rows: list[dict]) -> None:
    """Percentile vs the same calendar month in the fixed baseline; 'high' when clearly above the 90th percentile."""
    base: dict[int, list[float]] = {}
    for r in rows:
        p = pd.Period(r["month"], "M")
        if BASELINE_YEARS[0] <= p.year <= BASELINE_YEARS[1] and r["value"] is not None:
            base.setdefault(p.month, []).append(r["value"])
    for r in rows:
        v = r["value"]
        b = np.asarray(base.get(pd.Period(r["month"], "M").month, []), dtype=float)
        r.update(percentile=None, baseline_median=None, diff_from_median=None, anomaly=None)
        if v is None or len(b) < 5:
            continue
        med, p90 = float(np.median(b)), float(np.percentile(b, 90))
        r["percentile"] = round(float((b < v).mean() * 100 + (b == v).mean() * 50), 1)
        r["baseline_median"] = round(med, 2)
        r["diff_from_median"] = round(v - med, 2)
        lo = r["interval_90"][0] if r.get("interval_90") else v
        if v > p90 and lo > med and v - med >= 2:
            r["anomaly"] = "high"


# ------------------------------------------------------------------ current conditions
def current_conditions(settings: Settings, client, lat: float, lon: float, radius_km: float, today: date,
                       snpp_history: pd.DataFrame, static_cells: set, history_years: list[int],
                       theta_n20: np.ndarray | None) -> dict:
    """Last NOW_DAYS days of NOAA-20 NRT, on the S-NPP scale (theta2), vs the same window in past S-NPP years."""
    start = today - timedelta(days=NOW_DAYS - 1)
    base = {"window": {"start": str(start), "end": str(today), "days": NOW_DAYS},
            "sensor": "VIIRS NOAA-20 near real-time", "reference": "S-NPP VIIRS, same calendar window, 2012 onwards",
            "unit": "active 1-km cell-overpasses",
            "limitations": ["NOAA-20 is put on the S-NPP scale with the bridge coefficient of the continuity chain.",
                            "Near-real-time data may still change; the most recent day can be incomplete.",
                            "A value above the past range is unusual for this place, not a forecast of danger."]}
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
    det = quality(det)
    pixels = int(len(det))
    u = unit_totals(det)
    theta = theta_n20 if theta_n20 is not None else np.ones(2)
    now = float(u @ theta)
    hist = []
    h = quality(snpp_history) if len(snpp_history) else snpp_history
    for y in sorted(history_years):
        try:
            a, b = start.replace(year=y), today.replace(year=y)
        except ValueError:   # 29 February
            a, b = start.replace(year=y, day=28), today.replace(year=y, day=28)
        if len(h):
            d = pd.to_datetime(h["acq_date"]).dt.date
            n = float(unit_totals(h[(d >= a) & (d <= b)]).sum())
        else:
            n = 0.0
        hist.append({"year": y, "detections": n})
    counts = np.array([x["detections"] for x in hist], dtype=float)
    out = {**base, "detections": pixels, "active_cells": float(u.sum()), "snpp_equivalent": round(now, 2),
           "bridge_ratio": {d: round(float(theta[i]), 4) for i, d in enumerate(STRATA)}}
    if len(counts) < 5:
        return {**out, "status": "insufficient_history", "history": hist,
                "explanation": "Fewer than five past years for the same window."}
    pct = float((counts < now).mean() * 100 + (counts == now).mean() * 50)
    q25, q50, q75, q90 = np.percentile(counts, [25, 50, 75, 90])
    if now == 0 and q90 == 0:
        cat = "quiet_as_usual"
    elif now <= 2 and q90 <= 1:
        cat = "isolated_detections"
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
    return {**out, "status": "ok", "category": cat, "percentile": round(pct, 1),
            "history_median": float(q50), "history_p90": float(q90), "history_max": float(counts.max()),
            "history": hist,
            "explanation": f"{now:.1f} S-NPP-equivalent active cells ({pixels} NOAA-20 detection"
                           f"{'' if pixels == 1 else 's'}) in the last {NOW_DAYS} days vs a median of {q50:g} in the "
                           f"same window of {len(counts)} past years."}


# ------------------------------------------------------------------ background prefetch
_PREFETCHING: set[tuple[str, str, int]] = set()
_PREFETCH_GUARD = __import__("threading").Lock()


def prefetch_in_background(settings: Settings, jobs: list[tuple[str, str, int]]) -> None:
    """Download missing country-year files without blocking the analysis (daemon thread, de-duplicated)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    with _PREFETCH_GUARD:
        todo = [j for j in jobs if j not in _PREFETCHING]
        _PREFETCHING.update(todo)
    if not todo:
        return

    def one(job):
        product, name, year = job
        try:
            ca.ensure_country_year(settings, name, year, product=product)
        except Exception as e:  # noqa: BLE001 - background best effort
            log.info("Background archive %s %s %s: %s", product, name, year, e)
        finally:
            with _PREFETCH_GUARD:
                _PREFETCHING.discard(job)

    def run():
        with ThreadPoolExecutor(max_workers=max(1, min(4, int(getattr(settings, "download_workers", 4))))) as pool:
            list(pool.map(one, todo))
    threading.Thread(target=run, name="firms-archive-prefetch", daemon=True).start()


# ------------------------------------------------------------------ entry point
def _months_for(years: list[int], first: pd.Period | None = None) -> pd.PeriodIndex:
    if not years:
        return pd.PeriodIndex([], freq="M")
    idx = pd.period_range(pd.Period(f"{min(years)}-01", "M"), pd.Period(f"{max(years)}-12", "M"), freq="M")
    idx = idx[[p.year in years for p in idx]]
    return idx[idx >= first] if first is not None else idx


def build(settings: Settings, lat: float, lon: float, radius_km: float, today: date, ml_monthly: pd.DataFrame | None,
          allow_download: bool = True, client=None, session=None, tracker=None, recent: pd.DataFrame | None = None,
          kappa: float = KAPPA) -> tuple[dict, dict]:
    """Return (unified_calendar, current_conditions). Never raises for data problems; reports a status instead.

    ``ml_monthly`` (FireLens S-NPP monthly series with an ``observed`` flag) and ``recent`` (its detections) extend
    the S-NPP record past the last archive year."""
    try:
        names = ca.countries_for_bbox(disc_bbox(lat, lon, radius_km))
    except ca.ArchiveUnavailable as e:
        msg = {"status": "unavailable", "explanation": f"FIRMS country archives cannot cover this area: {e}"}
        return {**msg, "label": LABEL, "limitations": LIMITATIONS}, {**msg, "window": None}
    last_year = today.year - 1
    span = {p: list(range(FIRST_YEAR[p], last_year + 1)) for p in FIRST_YEAR}
    needed = [(p, n, y) for p in ("modis", "viirs-snpp") for n in names for y in span[p]]
    optional = [("viirs-jpss1", n, y) for n in names for y in span["viirs-jpss1"]]
    missing = [j for j in needed if not ca.is_resolved(settings, j[1], j[2], j[0])]
    missing_opt = [j for j in optional if not ca.is_resolved(settings, j[1], j[2], j[0])]
    if allow_download and session is None and (missing or missing_opt):
        # Never make the analysis wait for NASA downloads: fetch in the background, use what is cached now.
        prefetch_in_background(settings, missing + missing_opt)
    dl = allow_download and session is not None          # synchronous downloads only with an explicit session
    if missing and allow_download and session is None:
        countries = sorted({j[1].replace("_", " ") for j in missing})
        now = {"status": "unavailable", "window": None,
               "explanation": "The S-NPP history for this area is still being downloaded."}
        return {"status": "preparing", "label": LABEL, "limitations": LIMITATIONS, "countries": names,
                "missing_files": len(missing),
                "explanation": f"NASA yearly archives for {', '.join(countries)} are being downloaded in the "
                               f"background ({len(missing)} files). The MODIS+VIIRS calendar will be available "
                               "when you analyse this area again in a few minutes."}, now

    rng = np.random.default_rng(7)
    modis_raw, m_years = disc_detections(settings, names, "modis", span["modis"], lat, lon, radius_km, dl, session, tracker)
    snpp_raw, s_years = disc_detections(settings, names, "viirs-snpp", span["viirs-snpp"], lat, lon, radius_km, dl,
                                        session, tracker)
    n20_raw, n_years = disc_detections(settings, names, "viirs-jpss1", span["viirs-jpss1"], lat, lon, radius_km, dl,
                                       session, tracker)
    static_cells = set()
    if len(snpp_raw):
        nv = snpp_raw[snpp_raw["fire_type"].fillna(0) != 0]
        static_cells = set(zip(np.floor(nv["latitude"] * 100).astype(int), np.floor(nv["longitude"] * 100).astype(int)))
    aqua, snpp, n20 = quality(modis_raw, aqua_only=True), quality(snpp_raw), quality(n20_raw)
    if not m_years or not s_years:
        return {"status": "unavailable", "label": LABEL, "limitations": LIMITATIONS, "countries": names,
                "explanation": "MODIS or VIIRS yearly archives are not available for this area"
                               + ("" if allow_download else " in the local cache (downloads disabled)") + "."}, \
            current_conditions(settings, client, lat, lon, radius_km, today, snpp_raw, static_cells, [], None)

    a_m, s_m, n_m = _months_for(m_years, MODIS_START), _months_for(s_years), _months_for(n_years)
    A, S, N = active_units(aqua, a_m), active_units(snpp, s_m), active_units(n20, n_m)
    # extend S-NPP past the archive with the FireLens record (observed months only)
    if ml_monthly is not None and len(ml_monthly) and recent is not None:
        last_arch = s_m.max() if len(s_m) else VIIRS_START - 1
        obs_months = [p for p in ml_monthly.index[ml_monthly["observed"].astype(bool)] if p > last_arch]
        if obs_months:
            r = recent
            if "dist_km" in r.columns:
                r = r[r["dist_km"] <= radius_km]
            r = quality(r)
            extra = active_units(r, pd.PeriodIndex(obs_months, freq="M"))
            S = pd.concat([S, extra])

    # ---- links
    p1, sc1 = prior_ratio(lat, lon, "aqua_snpp", AQUA_FIT_YEARS)
    f1 = fit_link(A, S, AQUA_FIT_YEARS, p1, kappa, rng)
    n20_fit_years = [y for y in range(N20_FIT_FIRST, last_year + 1)]
    p2, sc2 = prior_ratio(lat, lon, "snpp_n20", n20_fit_years)
    f2 = fit_link(S, N, n20_fit_years, p2, kappa, rng) if len(N) else None
    chain = [link_summary("aqua_snpp", "Aqua MODIS", "S-NPP VIIRS", f1, p1, sc1, kappa)]
    chain.append(link_summary("snpp_n20", "S-NPP VIIRS", "NOAA-20 VIIRS", f2, p2, sc2, kappa) if f2 else
                 {"link": "snpp_n20", "reference": "S-NPP VIIRS", "target": "NOAA-20 VIIRS", "status": "unavailable",
                  "prior": {d: round(p2[d], 4) for d in STRATA}, "prior_scope": sc2,
                  "explanation": "NOAA-20 yearly archives are not cached for this area yet; the regional ratio is used."})
    theta2 = f2["theta"] if f2 else np.array([p2[d] for d in STRATA])
    boot2 = f2["boot"] if f2 else np.tile(theta2, (BOOTSTRAP_REPS, 1))

    # ---- calendar
    last = max([m.max() for m in (S.index, N.index) if len(m)])
    rows = []
    for p in pd.period_range(MODIS_START, last, freq="M"):
        a = float(A.loc[p].sum()) if p in A.index else None
        s = S.loc[p].to_numpy() if p in S.index else None
        n = N.loc[p].to_numpy() if p in N.index else None
        val, src, iv = None, None, None
        if p < VIIRS_START:
            val, src = a, ("modis_aqua" if a is not None else None)
        elif s is not None:
            val, src = float(s @ f1["theta"]), "viirs_snpp_scaled"
            d = _nb(rng, f1["boot"] @ s, f1["k"])
            iv = [float(x) for x in np.percentile(d, [5, 95])]
        elif n is not None:
            val, src = float(n @ (f1["theta"] * theta2)), "viirs_noaa20_scaled"
            d = _nb(rng, (f1["boot"] * boot2) @ n, f1["k"])
            iv = [float(x) for x in np.percentile(d, [5, 95])]
        rows.append({"month": str(p), "value": None if val is None else round(val, 2), "source": src,
                     "interval_90": None if iv is None else [round(iv[0], 1), round(iv[1], 1)],
                     "modis_aqua": a, "viirs_snpp": None if s is None else float(s.sum()),
                     "viirs_noaa20": None if n is None else float(n.sum())})
    add_anomalies(rows)

    # ---- validation
    pix = lambda det, idx: pixel_counts(det, idx)
    a_pix = pix(modis_raw[modis_raw["satellite_code"].isin(["Aqua", "A"]) & modis_raw["fire_type"].fillna(0).eq(0)]
                if len(modis_raw) else modis_raw, a_m)
    s_pix = pix(snpp_raw[snpp_raw["fire_type"].fillna(0).eq(0)] if len(snpp_raw) else snpp_raw, s_m)
    n_pix = pix(n20_raw[n20_raw["fire_type"].fillna(0).eq(0)] if len(n20_raw) else n20_raw, n_m)
    validation = {
        "method": "Held out in time: coefficients fitted on early overlap years (regional priors from the same years "
                  "only) predict the reference sensor in later years from the newer sensor alone",
        "aqua_snpp": forward_test("aqua_snpp", A, S.loc[[p for p in S.index if p in s_m]], a_pix, s_pix, lat, lon, kappa),
        "snpp_n20": (forward_test("snpp_n20", S.loc[[p for p in S.index if p in s_m]], N, s_pix, n_pix, lat, lon, kappa)
                     if len(N) else {"status": "unavailable", "explanation": "NOAA-20 archives not cached."}),
    }
    seam = [r for r in rows if r["month"][:4] in ("2012", "2013")]
    a_seam = sum(r["modis_aqua"] or 0 for r in seam)
    validation["seam_2012"] = {
        "harmonized_over_aqua_2012_2013": round(sum(r["value"] or 0 for r in seam) / a_seam, 3) if a_seam >= 10 else None,
        "explanation": "1.0 means no artificial step where the calendar switches from Aqua to S-NPP."}

    cal = {"status": "ok", "label": LABEL, "method": "continuity_chain", "unit": UNIT, "unit_definition": UNIT_DEFINITION,
           "countries": names, "reference_sensor": "Aqua MODIS (MODIS_SP, 1 km)",
           "calibrated_sensor": "S-NPP VIIRS (375 m), then NOAA-20 VIIRS (375 m)", "kappa": kappa,
           "chain": chain, "validation": validation, "baseline_years": list(BASELINE_YEARS),
           "monthly": rows, "limitations": LIMITATIONS,
           "archives": {"modis_years": m_years, "viirs_snpp_years": s_years, "viirs_noaa20_years": n_years,
                        "noaa20_missing_files": len(missing_opt)}}
    series = pd.Series({pd.Period(x["month"], "M"): x["value"] for x in rows}, dtype=float)
    cal["season"] = season_timing(series, today)
    now = current_conditions(settings, client, lat, lon, radius_km, today, snpp_raw, static_cells,
                             [y for y in s_years if y < today.year], theta2)
    return cal, now
