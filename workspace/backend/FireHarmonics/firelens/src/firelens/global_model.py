"""Pooled ("global") next-month fire-occurrence model for arbitrary discs.

Training data
-------------
Discs of 10, 25, 50 and 100 km radius centred on a ~0.5-degree lattice inside
the five downloaded regions (California, Zambia/Angola, Rondonia, Yakutia,
Punjab), restricted to centres whose 100 km context disc lies inside the
region box. Series: usable S-NPP VIIRS SP vegetation detections, 2012-2022.
Each region gets equal total sample weight (California would otherwise
dominate).

Target: ``y = 1`` if at least one detection falls in the disc in the target
month. Rows are stacked for leads 1, 2 and 3 months (``lead`` is a feature).

Validation (all metrics on held-out data)
-----------------------------------------
* temporal: train target years <= 2018, isotonic calibration on 2019, test 2020-2022;
* leave-one-region-out (LORO): same years, and the test region is also
  excluded from training and calibration.
Baselines: the disc's same-calendar-month climatology (``p_seasonal_raw``),
raw and isotonic-calibrated.

Method-selection rule (fixed in code before the validation results were read)
-----------------------------------------------------------------------------
For lead L:
* location inside a training region R: XGBoost if its temporal-test Brier
  skill score vs the best baseline in R is > 0 and the month-block bootstrap
  95% lower bound is > 0;
* any other location: XGBoost only if the LORO Brier skill score is > 0 in
  at least 4 of the 5 held-out regions and the pooled LORO bootstrap 95%
  lower bound is > 0;
* otherwise the seasonal baseline (whichever variant had the lower Brier in
  the same held-out setting). Leads > 3 always use the seasonal baseline.
Model design (with/without longitude; plain or boosted from the baseline
logit) is chosen on an inner split that never touches the test years: train
target years <= 2016, calibrate on 2017, select on 2018-2019 by the mean of the
temporal and LORO Brier scores. The chosen design is then evaluated once on
2020-2022 with the split above.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from . import location_data as LD
from . import location_features as LF
from .catalog import HARMONIZATION_REGIONS, REGIONS
from .cleaning import load_detections
from .config import Settings
from .coverage import fetch_missing_dates
from .logging_utils import get_logger

log = get_logger(__name__)

RADII_KM = (10.0, 25.0, 50.0, 100.0)
LEADS = (1, 2, 3)
TRAIN_START, TRAIN_END = date(2012, 1, 1), date(2022, 12, 31)
SPACING_DEG = 0.5
MODEL_NAME = "global_disc_forecaster"
REL_EDGES = [0, .02, .05, .1, .2, .3, .5, .7, .9, 1.0]
XGB_PARAMS = dict(n_estimators=400, max_depth=5, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                  min_child_weight=20, reg_lambda=1.0, tree_method="hist", eval_metric="logloss",
                  random_state=0, n_jobs=4)


# ------------------------------------------------------------------ training data
def region_centres(region: str, spacing_deg: float = SPACING_DEG, context_km: float = LF.CONTEXT_KM):
    b = REGIONS[region].bbox
    m_lat = (context_km + 5) / 111.2
    out = []
    lat = b.south + m_lat
    while lat <= b.north - m_lat + 1e-9:
        c = np.cos(np.radians(lat))
        m_lon = (context_km + 5) / (111.32 * c)
        step = spacing_deg / c
        lon = b.west + m_lon
        while lon <= b.east - m_lon + 1e-9:
            out.append((round(lat, 4), round(lon, 4)))
            lon += step
        lat += spacing_deg
    return out


def region_arrays(settings: Settings, region: str, start: date = TRAIN_START, end: date = TRAIN_END,
                  radii=RADII_KM) -> dict:
    """Monthly disc series for all training centres of one region (SP only)."""
    reg = REGIONS[region]
    months = pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M")
    plan = LD.make_plan(settings, [reg.bbox], start, end, [(LD.SP, max(start, LD.SERIES_START), end)])
    cov = LD.month_coverage(plan, fetch_missing_dates(settings), months)
    observed = cov["observed"].to_numpy()
    det = load_detections(settings, reg, LD.SP, start, end, columns=LD.DET_COLUMNS, usable_only=True)
    det = det[det["fire_type"].fillna(-1).eq(0)]
    midx = (det["acq_date"].dt.to_period("M") - months[0]).map(lambda x: x.n).to_numpy()
    centres = region_centres(region)
    C, F, X, lat, lon, rad, cid = [], [], [], [], [], [], []
    for i, (la, lo) in enumerate(centres):
        ds = LF.disc_series(det["latitude"].to_numpy(), det["longitude"].to_numpy(), midx,
                            det["frp_mw"].to_numpy(), la, lo, radii, len(months))
        for j, r in enumerate(radii):
            C.append(ds["count"][j]); F.append(ds["frp"][j]); X.append(ds["ctx"])
            lat.append(la); lon.append(lo); rad.append(r); cid.append(i)
    m = LF.mask_unobserved
    return {"region": region, "months": months, "count": m(np.array(C), observed), "frp": m(np.array(F), observed),
            "ctx": m(np.array(X), observed), "lat": np.array(lat), "lon": np.array(lon), "radius": np.array(rad),
            "centre": np.array(cid), "observed": observed, "n_centres": len(centres)}


def training_table(arrs: list[dict], leads=LEADS) -> pd.DataFrame:
    frames = []
    for a in arrs:
        for L in leads:
            f = LF.build_features(a["count"], a["frp"], a["ctx"], a["months"], a["lat"], a["lon"], a["radius"], L)
            df = LF.to_frame(f, a["months"])
            df["region"] = a["region"]
            df["radius_km"] = a["radius"][df["window"].to_numpy()]
            df["centre"] = a["centre"][df["window"].to_numpy()]
            frames.append(df)
    t = pd.concat(frames, ignore_index=True)
    t["weight"] = 1.0 / t.groupby("region")["y"].transform("size")
    t["weight"] *= len(t) / t["weight"].sum()
    return t


# ------------------------------------------------------------------ model
def baseline_margin(d: pd.DataFrame) -> np.ndarray:
    p = np.clip(d["p_seasonal_raw"].to_numpy(float), 0.005, 0.995)
    return np.log(p / (1 - p))


@dataclass
class GlobalForecaster:
    booster: object
    calibrator: IsotonicRegression
    baseline_calibrator: IsotonicRegression
    features: list
    margin: bool = False   # boost from the logit of the seasonal baseline

    def raw(self, d: pd.DataFrame) -> np.ndarray:
        bm = baseline_margin(d) if self.margin else None
        return self.booster.predict_proba(d[self.features], base_margin=bm)[:, 1]

    def predict(self, d: pd.DataFrame) -> np.ndarray:
        return self.calibrator.predict(self.raw(d))

    def baseline(self, d: pd.DataFrame, calibrated: bool) -> np.ndarray:
        raw = np.clip(d["p_seasonal_raw"].to_numpy(float), 0, 1)
        return self.baseline_calibrator.predict(raw) if calibrated else np.clip(raw, 1e-3, 1 - 1e-3)


def fit(train: pd.DataFrame, calib: pd.DataFrame, features: list[str], margin: bool = False) -> GlobalForecaster:
    from xgboost import XGBClassifier
    clf = XGBClassifier(**XGB_PARAMS)
    clf.fit(train[features], train["y"], sample_weight=train["weight"],
            base_margin=baseline_margin(train) if margin else None)
    m = GlobalForecaster(clf, None, None, list(features), margin)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-3, y_max=1 - 1e-3)
    iso.fit(m.raw(calib), calib["y"], sample_weight=calib["weight"])
    biso = IsotonicRegression(out_of_bounds="clip", y_min=1e-3, y_max=1 - 1e-3)
    biso.fit(np.clip(calib["p_seasonal_raw"], 0, 1), calib["y"], sample_weight=calib["weight"])
    m.calibrator, m.baseline_calibrator = iso, biso
    return m


def _metrics(y, p) -> dict:
    y, p = np.asarray(y, int), np.asarray(p, float)
    out = {"n": int(len(y)), "base_rate": float(y.mean()), "brier": float(brier_score_loss(y, p))}
    if 0 < y.sum() < len(y):
        out["roc_auc"] = float(roc_auc_score(y, p))
        out["pr_auc"] = float(average_precision_score(y, p))
    return out


def _bootstrap_bss(df: pd.DataFrame, a: str, b: str, n_boot: int = 500, seed: int = 0) -> dict:
    """Brier skill of column ``a`` vs ``b``; 95% CI from resampling whole months."""
    e = df.assign(_ea=(df[a] - df["y"]) ** 2, _eb=(df[b] - df["y"]) ** 2).groupby("month")[["_ea", "_eb"]].sum()
    ea, eb = e["_ea"].to_numpy(), e["_eb"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(ea), size=(n_boot, len(ea)))
    bss = 1 - ea[idx].sum(1) / eb[idx].sum(1)
    return {"bss": float(1 - ea.sum() / eb.sum()), "ci95": [float(np.quantile(bss, .025)), float(np.quantile(bss, .975))],
            "n_months": int(len(ea))}


def _reliability(y, p) -> list[dict]:
    y, p = np.asarray(y, int), np.asarray(p, float)
    b = np.clip(np.digitize(p, REL_EDGES[1:-1]), 0, len(REL_EDGES) - 2)
    return [{"bin": [REL_EDGES[i], REL_EDGES[i + 1]], "n": int((b == i).sum()),
             "mean_forecast": float(p[b == i].mean()), "observed_frequency": float(y[b == i].mean())}
            for i in range(len(REL_EDGES) - 1) if (b == i).sum() > 0]


def _score(model: GlobalForecaster, te: pd.DataFrame) -> pd.DataFrame:
    return te[["region", "month", "year", "lead", "radius_km", "y"]].assign(
        p_xgb=model.predict(te), p_base_raw=model.baseline(te, False), p_base_cal=model.baseline(te, True))


def _summaries(pred: pd.DataFrame, by_region: bool) -> dict:
    out = {}
    groups = [("all", pred)] + (list(pred.groupby("region")) if by_region else [])
    for name, g in groups:
        out[name] = {}
        for L, gl in g.groupby("lead"):
            best = "p_base_raw" if brier_score_loss(gl["y"], gl["p_base_raw"]) <= brier_score_loss(gl["y"], gl["p_base_cal"]) else "p_base_cal"
            out[name][int(L)] = {"xgboost": _metrics(gl["y"], gl["p_xgb"]),
                                 "seasonal_raw": _metrics(gl["y"], gl["p_base_raw"]),
                                 "seasonal_calibrated": _metrics(gl["y"], gl["p_base_cal"]),
                                 "best_baseline": "seasonal_raw" if best == "p_base_raw" else "seasonal_calibrated",
                                 "xgb_vs_best_baseline": _bootstrap_bss(gl, "p_xgb", best)}
    return out


def validate(table: pd.DataFrame, features: list[str], train_end: int = 2018, calib_year: int = 2019,
             test_years=(2020, 2021, 2022), margin: bool = False) -> dict:
    tr = table[table.year <= train_end]
    ca = table[table.year == calib_year]
    te = table[table.year.isin(test_years)]
    m = fit(tr, ca, features, margin)
    temporal = _score(m, te)
    loro = []
    for r in sorted(table.region.unique()):
        mr = fit(tr[tr.region != r], ca[ca.region != r], features, margin)
        loro.append(_score(mr, te[te.region == r]))
        log.info("LORO %s done", r)
    loro = pd.concat(loro, ignore_index=True)
    imp = m.booster.get_booster().get_score(importance_type="gain")
    return {"temporal": _summaries(temporal, True), "loro": _summaries(loro, True),
            "feature_importance_gain": dict(sorted(imp.items(), key=lambda kv: -kv[1])),
            "_pred_temporal": temporal, "_pred_loro": loro}


def decide(val: dict, regions=HARMONIZATION_REGIONS) -> dict:
    """Apply the pre-registered method-selection rule (see module docstring)."""
    gate = {"in_domain": {}, "out_of_domain": {}}
    for L in LEADS:
        for r in regions:
            s = val["temporal"].get(r, {}).get(L)
            ok = bool(s and s["xgb_vs_best_baseline"]["bss"] > 0 and s["xgb_vs_best_baseline"]["ci95"][0] > 0)
            gate["in_domain"].setdefault(r, {})[L] = {
                "method": "xgboost_pooled" if ok else "seasonal_baseline",
                "baseline_variant": s["best_baseline"] if s else "seasonal_raw",
                "bss_vs_best_baseline": s["xgb_vs_best_baseline"] if s else None}
        wins = [r for r in regions if val["loro"].get(r, {}).get(L) and val["loro"][r][L]["xgb_vs_best_baseline"]["bss"] > 0]
        pooled = val["loro"]["all"][L]
        ok = len(wins) >= 4 and pooled["xgb_vs_best_baseline"]["ci95"][0] > 0
        gate["out_of_domain"][L] = {"method": "xgboost_pooled" if ok else "seasonal_baseline",
                                    "baseline_variant": pooled["best_baseline"],
                                    "heldout_regions_where_xgb_beats_baseline": wins,
                                    "pooled_loro_bss": pooled["xgb_vs_best_baseline"]}
    return gate


def reliability_tables(val: dict) -> dict:
    """Held-out reliability per (setting, method, lead) for empirical uncertainty."""
    out = {}
    for setting, key in (("in_domain", "_pred_temporal"), ("out_of_domain", "_pred_loro")):
        p = val[key]
        out[setting] = {}
        for L, g in p.groupby("lead"):
            out[setting][int(L)] = {"xgboost_pooled": _reliability(g["y"], g["p_xgb"]),
                                    "seasonal_raw": _reliability(g["y"], g["p_base_raw"]),
                                    "seasonal_calibrated": _reliability(g["y"], g["p_base_cal"])}
    return out


# ------------------------------------------------------------------ persistence
def model_dir(settings: Settings) -> Path:
    return settings.data_dir / "models" / "global"


def save(settings: Settings, model: GlobalForecaster, meta: dict) -> dict:
    import joblib
    d = model_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    model.booster.get_booster().save_model(str(d / f"{MODEL_NAME}.ubj"))
    joblib.dump(model, d / f"{MODEL_NAME}.joblib")
    (d / f"{MODEL_NAME}.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    return {"pipeline": str(d / f"{MODEL_NAME}.joblib"), "meta": str(d / f"{MODEL_NAME}.json")}


_CACHE: dict = {}


def load(settings: Settings) -> tuple[GlobalForecaster, dict]:
    import joblib
    d = model_dir(settings)
    key = str(d)
    p, mp = d / f"{MODEL_NAME}.joblib", d / f"{MODEL_NAME}.json"
    if not p.exists() or not mp.exists():
        raise FileNotFoundError(f"Global model not found in {d}; run scripts/train_global_model.py")
    stamp = (p.stat().st_mtime, mp.stat().st_mtime)
    if key not in _CACHE or _CACHE[key][0] != stamp:
        _CACHE[key] = (stamp, joblib.load(p), json.loads(mp.read_text(encoding="utf-8")))
    return _CACHE[key][1], _CACHE[key][2]


def training_region_of(lat: float, lon: float) -> str | None:
    for r in HARMONIZATION_REGIONS:
        if bool(REGIONS[r].bbox.contains(lat, lon)):
            return r
    return None
