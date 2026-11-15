"""Next-month fire-activity forecasting (XGBoost) on the shared 0.25 deg grid.

Target
------
``y(c, t) = 1`` if at least one usable S-NPP VIIRS vegetation-fire detection
(FIRMS type 0) falls in grid cell ``c`` during month ``t``. A single sensor is
used for the target so that the label means the same thing in every year (no
mixing of MODIS and VIIRS sensitivity).

Leakage control
---------------
* Every feature for month ``t`` is computed from months ``<= t-1`` only
  (lags, rolling windows, expanding climatologies are all shifted).
* The calibration set is a separate year that is not used for fitting.
* Temporal validation: train on early years, calibrate on the next year, test
  on later years. Spatial validation: the test region AND the test years are
  both excluded from training and calibration.
* Weather, vegetation and land-cover features are NOT used: those datasets
  have not been acquired for this project.

Cells that never burned in the training period (ocean, desert, cities) are
easy negatives that inflate scores, so metrics are also reported on
"ever-burned" cells only.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from .catalog import REGIONS
from .cleaning import load_detections
from .config import Settings
from .grid import cell_center, cell_index, grid_shape
from .logging_utils import get_logger

log = get_logger(__name__)

FEATURES = ["lat", "lon", "month_sin", "month_cos",
            "lag1", "lag2", "lag3", "lag12", "any_lag1",
            "roll3", "roll6", "roll12", "frp_roll12",
            "hist_freq", "clim_same_month", "clim_years", "months_since_fire",
            "nbr_lag1", "nbr_roll12", "region_lag1", "region_clim_same_month"]


@dataclass
class ForecastConfig:
    res_deg: float = 0.25
    source: str = "VIIRS_SNPP_SP"
    vegetation_only: bool = True
    min_history_months: int = 12
    xgb_params: dict = field(default_factory=lambda: dict(
        n_estimators=400, max_depth=5, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
        min_child_weight=5, reg_lambda=1.0, tree_method="hist", eval_metric="logloss", random_state=0, n_jobs=4))


# ------------------------------------------------------------------ panel
def region_cells(region_name: str, res: float) -> pd.DataFrame:
    b = REGIONS[region_name].bbox
    n_rows, n_cols = grid_shape(res)
    r0, c0, _ = cell_index([b.south], [b.west], res)
    r1, c1, _ = cell_index([b.north - 1e-9], [b.east - 1e-9], res)
    rows, cols = np.meshgrid(np.arange(r0[0], r1[0] + 1), np.arange(c0[0], c1[0] + 1), indexing="ij")
    cid = (rows * n_cols + cols).ravel()
    lat, lon = cell_center(cid, res)
    return pd.DataFrame({"cell_id": cid, "row": rows.ravel(), "col": cols.ravel(), "lat": lat, "lon": lon,
                         "region": region_name})


def build_panel(settings: Settings, regions: list[str], start: date, end: date,
                cfg: ForecastConfig = ForecastConfig()) -> pd.DataFrame:
    """Complete cell x month panel (zeros where nothing was detected)."""
    frames = []
    months = pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M")
    for r in regions:
        cells = region_cells(r, cfg.res_deg)
        d = load_detections(settings, REGIONS[r], cfg.source, start, end,
                            columns=["latitude", "longitude", "acq_date", "frp_mw", "fire_type", "qc_usable"],
                            usable_only=True)
        if cfg.vegetation_only:
            d = d[d["fire_type"].fillna(-1).eq(0)]
        _, _, d_cid = cell_index(d["latitude"], d["longitude"], cfg.res_deg)
        d = d.assign(cell_id=d_cid, month=d["acq_date"].dt.to_period("M"))
        agg = d.groupby(["cell_id", "month"]).agg(count=("frp_mw", "size"), frp=("frp_mw", "sum"))
        idx = pd.MultiIndex.from_product([cells["cell_id"], months], names=["cell_id", "month"])
        p = agg.reindex(idx, fill_value=0).reset_index()
        p = p.merge(cells, on="cell_id", how="left")
        frames.append(p)
    panel = pd.concat(frames, ignore_index=True)
    panel["y"] = (panel["count"] > 0).astype(int)
    return panel


def add_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Features at month t use only months <= t-1."""
    p = panel.sort_values(["cell_id", "month"]).reset_index(drop=True)
    g = p.groupby("cell_id", sort=False)
    lc = np.log1p(p["count"])
    lf = np.log1p(p["frp"])
    p["_lc"], p["_lf"] = lc, lf
    for k in (1, 2, 3, 12):
        p[f"lag{k}"] = g["_lc"].shift(k)
    p["any_lag1"] = (g["count"].shift(1) > 0).astype(float).where(g["count"].shift(1).notna())
    shifted = g["_lc"].shift(1)
    for w in (3, 6, 12):
        p[f"roll{w}"] = shifted.groupby(p["cell_id"]).transform(lambda s: s.rolling(w, min_periods=w).mean())
    p["frp_roll12"] = g["_lf"].shift(1).groupby(p["cell_id"]).transform(lambda s: s.rolling(12, min_periods=12).mean())
    ys = g["y"].shift(1)
    p["hist_freq"] = ys.groupby(p["cell_id"]).transform(lambda s: s.expanding().mean())
    p["_n_hist"] = ys.groupby(p["cell_id"]).transform(lambda s: s.expanding().count())
    # same calendar month in previous years
    p["_moy"] = p["month"].dt.month
    gm = p.groupby(["cell_id", "_moy"], sort=False)["y"]
    p["clim_same_month"] = gm.transform(lambda s: s.shift(1).expanding().mean())
    p["clim_years"] = gm.transform(lambda s: s.shift(1).expanding().count())
    # months since last fire (before t), capped
    def since(s):
        out, last = np.empty(len(s)), None
        for i, v in enumerate(s.to_numpy()):
            out[i] = np.nan if last is None else min(i - last, 60)
            if v > 0:
                last = i
        return pd.Series(out, index=s.index)
    p["months_since_fire"] = g["y"].transform(since).fillna(61)
    # neighbours (8-connected) at t-1 and their 12-month mean
    key = p.set_index(["row", "col", "month"])["_lc"]
    nb = np.zeros(len(p))
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == dc == 0:
                continue
            idx = pd.MultiIndex.from_arrays([p["row"] + dr, p["col"] + dc, p["month"] - 1])
            nb += key.reindex(idx).fillna(0).to_numpy()
    p["nbr_lag1"] = nb
    p["nbr_roll12"] = p.groupby("cell_id")["nbr_lag1"].transform(lambda s: s.rolling(12, min_periods=12).mean())
    # regional activity (whole bbox) at t-1 and same-month climatology
    reg = p.groupby(["region", "month"])["count"].sum().rename("rc").reset_index()
    reg["_lrc"] = np.log1p(reg["rc"])
    reg = reg.sort_values(["region", "month"])
    reg["region_lag1"] = reg.groupby("region")["_lrc"].shift(1)
    reg["_moy"] = reg["month"].dt.month
    reg["region_clim_same_month"] = reg.groupby(["region", "_moy"])["_lrc"].transform(lambda s: s.shift(1).expanding().mean())
    p = p.merge(reg[["region", "month", "region_lag1", "region_clim_same_month"]], on=["region", "month"], how="left")
    ang = 2 * np.pi * (p["month"].dt.month - 1) / 12
    p["month_sin"], p["month_cos"] = np.sin(ang), np.cos(ang)
    p["year"] = p["month"].dt.year
    return p.drop(columns=["_lc", "_lf", "_moy"])


def usable_rows(p: pd.DataFrame, min_hist: int) -> pd.DataFrame:
    return p[(p["_n_hist"] >= min_hist) & p["lag12"].notna() & p["roll12"].notna()]


# --------------------------------------------------------------- baselines
def baseline_probs(d: pd.DataFrame, persistence_rates: tuple[float, float]) -> dict[str, np.ndarray]:
    """Baseline probabilities from information available before the month.

    * historical_frequency: share of past months with fire in the cell;
    * seasonal_climatology: share of past years with fire in the same calendar
      month, shrunk towards the cell's overall frequency (2 pseudo-years);
    * persistence: P(fire | fire last month) or P(fire | none last month),
      rates estimated on the training set.
    """
    prior = d["hist_freq"].fillna(0).to_numpy()
    n = d["clim_years"].fillna(0).to_numpy()
    k = d["clim_same_month"].fillna(0).to_numpy() * n
    seasonal = (k + 2 * prior) / (n + 2)
    p1, p0 = persistence_rates
    persist = np.where(d["any_lag1"].fillna(0).to_numpy() > 0, p1, p0)
    clip = lambda a: np.clip(a, 1e-4, 1 - 1e-4)
    return {"historical_frequency": clip(prior), "seasonal_climatology": clip(seasonal), "persistence": clip(persist)}


# ------------------------------------------------------------------ metrics
def prob_metrics(y, p) -> dict:
    y = np.asarray(y, int)
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    out = {"n": int(len(y)), "positives": int(y.sum()), "base_rate": float(y.mean()),
           "brier": float(brier_score_loss(y, p)), "log_loss": float(log_loss(y, p, labels=[0, 1]))}
    if 0 < y.sum() < len(y):
        out["roc_auc"] = float(roc_auc_score(y, p))
        out["pr_auc"] = float(average_precision_score(y, p))
    edges = np.array([0, .02, .05, .1, .2, .3, .5, .7, .9, 1.0001])
    b = np.digitize(p, edges) - 1
    rel = [{"bin": f"{edges[i]:.2f}-{min(edges[i + 1], 1):.2f}", "n": int((b == i).sum()),
            "p_mean": float(p[b == i].mean()), "observed": float(y[b == i].mean())}
           for i in range(len(edges) - 1) if (b == i).sum() > 0]
    out["reliability"] = rel
    out["ece"] = float(sum(r["n"] * abs(r["p_mean"] - r["observed"]) for r in rel) / len(y))
    return out


# --------------------------------------------------------------------- model
@dataclass
class ForecastModel:
    booster: object
    calibrator: IsotonicRegression
    features: list
    config: dict
    persistence_rates: tuple

    def predict(self, d: pd.DataFrame, calibrated: bool = True) -> np.ndarray:
        raw = self.booster.predict_proba(d[self.features])[:, 1]
        return self.calibrator.predict(raw) if calibrated else raw


def fit_forecaster(train: pd.DataFrame, calib: pd.DataFrame, cfg: ForecastConfig,
                   features: list[str] = FEATURES) -> ForecastModel:
    from xgboost import XGBClassifier
    clf = XGBClassifier(**cfg.xgb_params)
    clf.fit(train[features], train["y"])
    iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-4, y_max=1 - 1e-4)
    iso.fit(clf.predict_proba(calib[features])[:, 1], calib["y"])
    p1 = float(train.loc[train["any_lag1"] > 0, "y"].mean())
    p0 = float(train.loc[train["any_lag1"] == 0, "y"].mean())
    return ForecastModel(clf, iso, list(features), asdict(cfg), (p1, p0))


def evaluate(model: ForecastModel, test: pd.DataFrame, ever_burned_cells: set) -> dict:
    probs = {"xgboost_calibrated": model.predict(test, True), "xgboost_raw": model.predict(test, False)}
    probs.update(baseline_probs(test, model.persistence_rates))
    out = {}
    for subset, mask in (("all_cells", np.ones(len(test), bool)),
                         ("ever_burned_cells", test["cell_id"].isin(ever_burned_cells).to_numpy())):
        out[subset] = {k: prob_metrics(test["y"].to_numpy()[mask], v[mask]) for k, v in probs.items()}
    out["by_year"] = {int(y): {k: {m: prob_metrics(test["y"].to_numpy()[test["year"].to_numpy() == y],
                                                    v[test["year"].to_numpy() == y]).get(m)
                                  for m in ("roc_auc", "pr_auc", "brier")}
                               for k, v in probs.items()}
                      for y in sorted(test["year"].unique())}
    out["by_region"] = {r: {k: {m: prob_metrics(test["y"].to_numpy()[test["region"].to_numpy() == r],
                                                 v[test["region"].to_numpy() == r]).get(m)
                               for m in ("roc_auc", "pr_auc", "brier", "base_rate")}
                            for k, v in probs.items()}
                        for r in sorted(test["region"].unique())}
    return out, probs


def run_validation(p: pd.DataFrame, cfg: ForecastConfig, train_end: int, calib_year: int,
                   test_years: list[int], regions: list[str], block_deg: float = 2.0,
                   n_block_folds: int = 4) -> dict:
    """Temporal hold-out + leave-one-region-out (region and years both held out)."""
    d = usable_rows(p, cfg.min_history_months)
    results, preds = {}, []

    def split(mask_regions):
        tr = d[(d.year <= train_end) & d.region.isin(mask_regions)]
        ca = d[(d.year == calib_year) & d.region.isin(mask_regions)]
        return tr, ca

    tr, ca = split(regions)
    te = d[d.year.isin(test_years) & d.region.isin(regions)]
    m = fit_forecaster(tr, ca, cfg)
    ever = set(tr.loc[tr["y"] > 0, "cell_id"])
    res, probs = evaluate(m, te, ever)
    results["temporal"] = {"train_years": [int(tr.year.min()), train_end], "calibration_year": calib_year,
                           "test_years": test_years, "regions": regions, "n_train": int(len(tr)),
                           "n_calib": int(len(ca)), "n_test": int(len(te)), "metrics": res,
                           "feature_importance_gain": {k: float(v) for k, v in sorted(
                               m.booster.get_booster().get_score(importance_type="gain").items(),
                               key=lambda kv: -kv[1])}}
    preds.append(te[["cell_id", "region", "month", "lat", "lon", "y"]].assign(
        split="temporal", p_xgb=probs["xgboost_calibrated"], p_seasonal=probs["seasonal_climatology"],
        p_hist=probs["historical_frequency"]))
    results["temporal_model"] = m
    # Spatial hold-out inside the validated domain: 2-degree blocks held out
    # (and test years held out) - neighbouring blocks are never in the same fold.
    blocks = (np.floor(d["lat"] / block_deg).astype(int).astype(str) + "_" +
              np.floor(d["lon"] / block_deg).astype(int).astype(str))
    ub = np.array(sorted(blocks.unique()))
    rng = np.random.default_rng(0)
    fold_of = dict(zip(ub, rng.permutation(len(ub)) % n_block_folds))
    fold = blocks.map(fold_of).to_numpy()
    block_preds, block_meta = [], []
    for k in range(n_block_folds):
        tr = d[(d.year <= train_end) & (fold != k) & d.region.isin(regions)]
        ca = d[(d.year == calib_year) & (fold != k) & d.region.isin(regions)]
        te = d[d.year.isin(test_years) & (fold == k) & d.region.isin(regions)]
        if te.empty or tr["y"].nunique() < 2:
            continue
        mk = fit_forecaster(tr, ca, cfg)
        pr = {"xgboost_calibrated": mk.predict(te, True)}
        pr.update(baseline_probs(te, mk.persistence_rates))
        block_preds.append(te[["cell_id", "region", "month", "lat", "lon", "y", "year"]].assign(block_fold=k, **{f"p_{n}": v for n, v in pr.items()}))
        block_meta.append({"fold": k, "n_test": int(len(te)), "test_blocks": int(sum(v == k for v in fold_of.values()))})
    if block_preds:
        bp = pd.concat(block_preds, ignore_index=True)
        ever = set(d.loc[(d.year <= train_end) & (d.y > 0), "cell_id"])
        m_ever = bp["cell_id"].isin(ever).to_numpy()
        names = [c[2:] for c in bp.columns if c.startswith("p_")]
        results["spatial_blocks"] = {
            "block_deg": block_deg, "n_folds": n_block_folds, "folds": block_meta,
            "metrics": {"all_cells": {n: prob_metrics(bp["y"], bp[f"p_{n}"]) for n in names},
                        "ever_burned_cells": {n: prob_metrics(bp["y"][m_ever], bp[f"p_{n}"][m_ever]) for n in names}}}
        preds.append(bp[["cell_id", "region", "month", "lat", "lon", "y"]].assign(
            split="spatial_blocks", p_xgb=bp["p_xgboost_calibrated"], p_seasonal=bp["p_seasonal_climatology"],
            p_hist=bp["p_historical_frequency"]))
    if len(regions) >= 2:
        results["loro"] = {}
        for r in regions:
            others = [o for o in regions if o != r]
            tr, ca = split(others)
            te = d[d.year.isin(test_years) & (d.region == r)]
            if tr.empty or te.empty:
                continue
            mr = fit_forecaster(tr, ca, cfg)
            ever_r = set(d.loc[(d.year <= train_end) & (d.region == r) & (d.y > 0), "cell_id"])
            res, probs = evaluate(mr, te, ever_r)
            results["loro"][r] = {"train_regions": others, "n_train": int(len(tr)), "n_test": int(len(te)),
                                  "metrics": res}
            preds.append(te[["cell_id", "region", "month", "lat", "lon", "y"]].assign(
                split=f"loro:{r}", p_xgb=probs["xgboost_calibrated"], p_seasonal=probs["seasonal_climatology"],
                p_hist=probs["historical_frequency"]))
    results["predictions"] = pd.concat(preds, ignore_index=True)
    return results


def save_model(settings: Settings, model: ForecastModel, meta: dict, name: str = "xgb_next_month") -> dict:
    import joblib
    out = settings.data_dir / "models" / "forecast"
    out.mkdir(parents=True, exist_ok=True)
    model.booster.get_booster().save_model(str(out / f"{name}.ubj"))
    joblib.dump(model, out / f"{name}.joblib")
    (out / f"{name}.json").write_text(json.dumps({**meta, "features": model.features, "config": model.config,
                                                  "persistence_rates": model.persistence_rates}, indent=1, default=str))
    return {"booster": str(out / f"{name}.ubj"), "pipeline": str(out / f"{name}.joblib")}


def transfer_diagnostic(model: ForecastModel, panel_other: pd.DataFrame, cfg: ForecastConfig,
                        test_years: list[int]) -> dict:
    """Research diagnostic only: apply a model trained in validated regions to
    other regions. Results are reported as metrics; no predictions are exported."""
    d = usable_rows(panel_other, cfg.min_history_months)
    d = d[d.year.isin(test_years)]
    out = {}
    for r, g in d.groupby("region"):
        probs = {"xgboost_calibrated": model.predict(g, True)}
        probs.update(baseline_probs(g, model.persistence_rates))
        out[r] = {k: {m: prob_metrics(g["y"], v).get(m) for m in ("roc_auc", "pr_auc", "brier", "ece", "base_rate")}
                  for k, v in probs.items()}
    return out
