"""MODIS-equivalent VIIRS activity: models, predictions, uncertainty, evaluation.

Target
------
What Aqua-MODIS would have recorded, given S-NPP VIIRS observations of the same
place at nearly the same time. VIIRS is *not* treated as ground truth: MODIS
pixels with no VIIRS counterpart ("MODIS-only") are part of the target and are
modelled explicitly.

Model (fitted on comparable clusters only, i.e. with established Aqua coverage)
----------------------------------------------------------------------------
For each VIIRS cluster *i* in the jointly observed domain:

* ``p_i = P(Aqua-MODIS detects cluster i | x_i)``: logistic regression on
  VIIRS cluster FRP (spline), max pixel FRP, pixel count, day/night, MODIS
  view position (|cross-track|, spline), VIIRS view position, overpass time
  offset and I4 saturation;
* ``mu_i = E[MODIS pixels assigned | detected, x_i]`` and
  ``phi_i = E[MODIS FRP assigned | detected, x_i]``: Poisson/quasi-Poisson
  GLMs (log link) on the detected clusters;
* MODIS-only activity: multiplicative factors ``k_n`` and ``k_f`` (all
  comparable MODIS pixels or FRP / those assigned to VIIRS clusters), estimated
  on the training data.

MODIS-equivalent pixel count = ``k_n * sum_i p_i mu_i``; FRP = ``k_f * sum_i p_i phi_i``.

Baselines the model must beat on held-out data
----------------------------------------------
* ``raw``: VIIRS pixel count / FRP unchanged (no harmonisation);
* ``constant``: VIIRS totals x training ratio (MODIS total / VIIRS total), i.e.
  the "single correction factor" approach;
* ``frp_only``: the same two-part structure using VIIRS cluster FRP as the only
  predictor.

FRP *calibration* (per co-detected cluster, ``log10 FRP_MODIS ~ log10 FRP_VIIRS + ...``)
is evaluated separately from count harmonisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LinearRegression, LogisticRegression, PoissonRegressor
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import SplineTransformer, StandardScaler

FULL_LINEAR = ["lfrp_max", "lnpix", "night", "viirs_ax", "abs_dt", "saturated", "night_x_lfrp", "ax_x_lfrp"]
GLM_FEATURES_FULL = ["lfrp", "lfrp_max", "lnpix", "night", "aqua_ax", "viirs_ax"]
GLM_FEATURES_FRP = ["lfrp"]


def add_features(cl: pd.DataFrame) -> pd.DataFrame:
    d = cl.copy()
    d["lfrp"] = np.log10(d["frp_sum"].clip(lower=0) + 0.1)
    d["lfrp_max"] = np.log10(d["frp_max"].clip(lower=0) + 0.1)
    d["lnpix"] = np.log10(d["n_pix"])
    d["night"] = d["night"].astype(float)
    d["saturated"] = d["saturated"].astype(float)
    d["abs_dt"] = d["aqua_dt_min"].abs()
    d["aqua_ax"] = d["aqua_ax"].fillna(0.5).clip(0, 1.1)
    d["viirs_ax"] = d["viirs_ax"].fillna(0.5).clip(0, 1.1)
    d["night_x_lfrp"] = d["night"] * d["lfrp"]
    d["ax_x_lfrp"] = d["aqua_ax"] * d["lfrp"]
    return d


def _detector(kind: str):
    if kind == "frp_only":
        ct = ColumnTransformer([("frp", SplineTransformer(n_knots=6, degree=3, extrapolation="linear"), ["lfrp"])])
    else:
        ct = ColumnTransformer([
            ("frp", SplineTransformer(n_knots=6, degree=3, extrapolation="linear"), ["lfrp"]),
            ("ax", SplineTransformer(n_knots=4, degree=2, extrapolation="linear"), ["aqua_ax"]),
            ("lin", StandardScaler(), FULL_LINEAR)])
    return make_pipeline(ct, LogisticRegression(C=1.0, max_iter=3000))


def _glm(features):
    return make_pipeline(ColumnTransformer([("x", StandardScaler(), features)]),
                         PoissonRegressor(alpha=1e-4, max_iter=3000))


@dataclass
class FittedModel:
    kind: str
    detector: object
    count_glm: object
    frp_glm: object
    k_n: float
    k_f: float
    frp_log_sigma: float
    glm_features: list = field(default_factory=list)

    def predict_parts(self, d: pd.DataFrame) -> pd.DataFrame:
        p = self.detector.predict_proba(d)[:, 1]
        mu = self.count_glm.predict(d[self.glm_features])
        phi = self.frp_glm.predict(d[self.glm_features])
        return pd.DataFrame({"p": p, "mu": mu, "phi": phi}, index=d.index)


def fit_model(train_cl: pd.DataFrame, train_modis: pd.DataFrame, kind: str = "full") -> FittedModel:
    """``train_cl``: comparable clusters with features; ``train_modis``: comparable MODIS pixels."""
    d = train_cl
    y = d["modis_detected"].astype(int).to_numpy()
    det = _detector(kind).fit(d, y)
    feats = GLM_FEATURES_FULL if kind == "full" else GLM_FEATURES_FRP
    dd = d[d["modis_detected"]]
    cnt = _glm(feats).fit(dd[feats], dd["n_modis"].to_numpy(float))
    frp = _glm(feats).fit(dd[feats], dd["frp_modis"].to_numpy(float))
    k_n = len(train_modis) / max(dd["n_modis"].sum(), 1)
    k_f = train_modis["frp_mw"].sum() / max(dd["frp_modis"].sum(), 1e-9)
    pos = dd[dd["frp_modis"] > 0]
    resid = np.log(pos["frp_modis"].to_numpy()) - np.log(frp.predict(pos[feats]))
    sigma = float(np.std(resid)) if len(resid) > 2 else 1.0
    return FittedModel(kind, det, cnt, frp, float(k_n), float(k_f), sigma, feats)


@dataclass
class ConstantModel:
    r_n: float
    r_f: float
    p_rate: float


def fit_constant(train_cl: pd.DataFrame, train_modis: pd.DataFrame) -> ConstantModel:
    return ConstantModel(len(train_modis) / max(train_cl["n_pix"].sum(), 1),
                         train_modis["frp_mw"].sum() / max(train_cl["frp_sum"].sum(), 1e-9),
                         float(train_cl["modis_detected"].mean()))


# ------------------------------------------------------------ FRP calibration
@dataclass
class FrpCalibration:
    model: object
    features: list


CAL_FEATURES = ["lfrp", "aqua_ax", "night", "viirs_ax", "saturated", "lnpix"]


def fit_frp_calibration(train_cl: pd.DataFrame) -> FrpCalibration:
    d = train_cl[(train_cl["n_modis"] > 0) & (train_cl["frp_modis"] > 0) & (train_cl["frp_sum"] > 0)]
    m = make_pipeline(StandardScaler(), LinearRegression()).fit(d[CAL_FEATURES], np.log10(d["frp_modis"]))
    return FrpCalibration(m, CAL_FEATURES)


def frp_calibration_metrics(test_cl: pd.DataFrame, cal: FrpCalibration) -> dict:
    d = test_cl[(test_cl["n_modis"] > 0) & (test_cl["frp_modis"] > 0) & (test_cl["frp_sum"] > 0)]
    if len(d) < 10:
        return {"n": int(len(d))}
    obs = np.log10(d["frp_modis"].to_numpy())
    before = np.log10(d["frp_sum"].to_numpy())
    after = cal.model.predict(d[cal.features])

    def m(pred):
        e = pred - obs
        return {"bias_median_log10": float(np.median(e)), "mae_log10": float(np.mean(np.abs(e))),
                "rmse_log10": float(np.sqrt(np.mean(e ** 2))), "pearson_r": float(np.corrcoef(pred, obs)[0, 1])}
    return {"n": int(len(d)), "before_identity": m(before), "after_calibration": m(after)}


# ------------------------------------------------------------------ metrics
def detection_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    y = np.asarray(y, int)
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    out = {"n": int(len(y)), "observed_rate": float(y.mean()), "predicted_rate": float(p.mean()),
           "brier": float(brier_score_loss(y, p)), "log_loss": float(log_loss(y, p, labels=[0, 1]))}
    out["auc"] = float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None
    bins = np.clip((p * 10).astype(int), 0, 9)
    ece = sum(abs(y[bins == b].mean() - p[bins == b].mean()) * (bins == b).sum() for b in range(10) if (bins == b).any()) / len(y)
    out["ece"] = float(ece)
    out["reliability"] = [{"bin": int(b), "n": int((bins == b).sum()), "p_mean": float(p[bins == b].mean()),
                           "observed": float(y[bins == b].mean())} for b in range(10) if (bins == b).any()]
    return out


def monthly_metrics(obs: pd.Series, pred: pd.Series, min_obs: int = 10, lo=None, hi=None) -> dict:
    """Agreement of monthly totals. Months with < ``min_obs`` observed MODIS
    pixels are excluded from log metrics (ratios of tiny numbers are noise)."""
    df = pd.DataFrame({"obs": obs, "pred": pred}).dropna()
    use = df[df["obs"] >= min_obs]
    res = {"n_months": int(len(df)), "n_months_scored": int(len(use)),
           "total_ratio_pred_to_obs": float(df["pred"].sum() / df["obs"].sum()) if df["obs"].sum() > 0 else None}
    if len(use) >= 3:
        lr = np.log10(use["pred"].clip(lower=1e-9) / use["obs"])
        res.update({"median_abs_log10_ratio": float(lr.abs().median()), "mean_log10_ratio": float(lr.mean()),
                    "share_within_factor_1_5": float((lr.abs() <= np.log10(1.5)).mean()),
                    "pearson_r_log": float(np.corrcoef(np.log10(use["pred"] + 1), np.log10(use["obs"] + 1))[0, 1])})
        if lo is not None and hi is not None:
            l, h = pd.Series(lo).reindex(use.index), pd.Series(hi).reindex(use.index)
            res["pi90_coverage"] = float(((use["obs"] >= l) & (use["obs"] <= h)).mean())
    return res


# ------------------------------------------------------- uncertainty (bootstrap)
def _block_resample(cl: pd.DataFrame, md: pd.DataFrame, rng) -> tuple[pd.DataFrame, pd.DataFrame]:
    days = cl["acq_date"].unique()
    pick = rng.choice(days, size=len(days), replace=True)
    counts = pd.Series(pick).value_counts()
    rep_c = cl["acq_date"].map(counts).fillna(0).astype(int).to_numpy()
    rep_m = md["acq_date"].map(counts).fillna(0).astype(int).to_numpy()
    return cl.loc[cl.index.repeat(rep_c)], md.loc[md.index.repeat(rep_m)]


def bootstrap_monthly(train_cl, train_md, test_cl, n_boot: int = 50, seed: int = 0) -> pd.DataFrame:
    """Monthly 90% prediction intervals for MODIS-equivalent counts and FRP.

    Combines parameter uncertainty (day-block bootstrap refits) with sampling
    noise (Bernoulli detection, Poisson pixel counts, log-normal FRP). Clusters
    are simulated as independent, so intervals can be too narrow when whole
    overpasses are affected together (e.g. cloud); empirical coverage is
    reported to check this.
    """
    rng = np.random.default_rng(seed)
    months = test_cl["month"].to_numpy()
    um, inv = np.unique(months, return_inverse=True)
    sims_n, sims_f = [], []
    for _ in range(n_boot):
        cl_b, md_b = _block_resample(train_cl, train_md, rng)
        if cl_b["modis_detected"].nunique() < 2:
            continue
        fm = fit_model(cl_b, md_b, "full")
        parts = fm.predict_parts(test_cl)
        dmask = rng.random(len(parts)) < parts["p"].to_numpy()
        n = rng.poisson(parts["mu"].to_numpy()) * dmask
        f = parts["phi"].to_numpy() * np.exp(fm.frp_log_sigma * rng.standard_normal(len(parts)) - fm.frp_log_sigma ** 2 / 2) * dmask
        sims_n.append(np.bincount(inv, weights=n * fm.k_n, minlength=len(um)))
        sims_f.append(np.bincount(inv, weights=f * fm.k_f, minlength=len(um)))
    sn, sf = np.array(sims_n), np.array(sims_f)
    return pd.DataFrame({"month": um,
                         "count_lo": np.percentile(sn, 5, axis=0), "count_hi": np.percentile(sn, 95, axis=0),
                         "frp_lo": np.percentile(sf, 5, axis=0), "frp_hi": np.percentile(sf, 95, axis=0),
                         "n_boot": len(sn)}).set_index("month")


# --------------------------------------------------------------- evaluation
def predict_monthly(test_cl, test_md, models: dict, const: ConstantModel) -> pd.DataFrame:
    """Monthly observed vs predicted totals in the jointly observed domain."""
    g = test_cl.groupby("month")
    out = pd.DataFrame({"viirs_raw_count": g["n_pix"].sum(), "viirs_raw_frp": g["frp_sum"].sum(),
                        "viirs_clusters": g.size()})
    mo = test_md.groupby("month")
    out["modis_obs_count"] = mo.size()
    out["modis_obs_frp"] = mo["frp_mw"].sum()
    out = out.fillna(0)
    out["const_count"] = out["viirs_raw_count"] * const.r_n
    out["const_frp"] = out["viirs_raw_frp"] * const.r_f
    for name, fm in models.items():
        parts = fm.predict_parts(test_cl)
        tmp = pd.DataFrame({"month": test_cl["month"], "c": parts["p"] * parts["mu"] * fm.k_n,
                            "f": parts["p"] * parts["phi"] * fm.k_f, "p": parts["p"]})
        gg = tmp.groupby("month")
        out[f"{name}_count"] = gg["c"].sum()
        out[f"{name}_frp"] = gg["f"].sum()
        out[f"{name}_clusters_detected"] = gg["p"].sum()
    out["modis_obs_clusters_detected"] = test_cl.groupby("month")["modis_detected"].sum()
    return out.fillna(0)


def evaluate_fold(train_cl, train_md, test_cl, test_md, n_boot: int = 50, seed: int = 0,
                  conformal_group: str | None = None) -> dict:
    """Fit on train, evaluate on test. Inputs restricted to the comparable domain."""
    models = {"full": fit_model(train_cl, train_md, "full"), "frp_only": fit_model(train_cl, train_md, "frp_only")}
    const = fit_constant(train_cl, train_md)
    y = test_cl["modis_detected"].astype(int).to_numpy()
    res = {"n_train_clusters": int(len(train_cl)), "n_test_clusters": int(len(test_cl)),
           "n_test_modis_pixels": int(len(test_md)),
           "detection": {"constant_rate": detection_metrics(y, np.full(len(y), const.p_rate)),
                         "frp_only": detection_metrics(y, models["frp_only"].detector.predict_proba(test_cl)[:, 1]),
                         "full": detection_metrics(y, models["full"].detector.predict_proba(test_cl)[:, 1])},
           "k_factors": {"k_n": models["full"].k_n, "k_f": models["full"].k_f},
           "constant_ratios": {"r_n": const.r_n, "r_f": const.r_f}}
    mon = predict_monthly(test_cl, test_md, models, const)
    pi = bootstrap_monthly(train_cl, train_md, test_cl, n_boot=n_boot, seed=seed) if n_boot else None
    if pi is not None:
        mon = mon.join(pi, how="left")
    conf = conformal_residuals(train_cl, train_md, conformal_group) if conformal_group else {"count": [], "frp": []}
    for target in ("count", "frp"):
        lo, hi = conformal_interval(mon[f"full_{target}"], conf[target])
        mon[f"{target}_conf_lo"], mon[f"{target}_conf_hi"] = (lo, hi) if lo is not None else (np.nan, np.nan)
    res["conformal_n_residuals"] = {k: len(v) for k, v in conf.items()}
    for target, obs_col in (("count", "modis_obs_count"), ("frp", "modis_obs_frp")):
        min_obs = 10 if target == "count" else 100.0
        res[f"monthly_{target}"] = {
            "raw": monthly_metrics(mon[obs_col], mon[f"viirs_raw_{target}"], min_obs),
            "constant": monthly_metrics(mon[obs_col], mon[f"const_{target}"], min_obs),
            "frp_only": monthly_metrics(mon[obs_col], mon[f"frp_only_{target}"], min_obs),
            "full": monthly_metrics(mon[obs_col], mon[f"full_{target}"], min_obs,
                                    mon.get(f"{target}_lo"), mon.get(f"{target}_hi")),
        }
        conf_cov = monthly_metrics(mon[obs_col], mon[f"full_{target}"], min_obs,
                                   mon[f"{target}_conf_lo"], mon[f"{target}_conf_hi"]).get("pi90_coverage")
        res[f"monthly_{target}"]["full"]["pi90_coverage_bootstrap"] = res[f"monthly_{target}"]["full"].pop("pi90_coverage", None)
        res[f"monthly_{target}"]["full"]["pi90_coverage_conformal"] = conf_cov if mon[f"{target}_conf_lo"].notna().any() else None
    res["frp_calibration"] = frp_calibration_metrics(test_cl, fit_frp_calibration(train_cl))
    return {"metrics": res, "monthly": mon}


def conformal_residuals(train_cl: pd.DataFrame, train_md: pd.DataFrame, group_col: str,
                        min_obs_count: int = 10, min_obs_frp: float = 100.0) -> dict:
    """Out-of-sample monthly log10(obs/pred) residuals from inner cross-validation
    over ``group_col`` (years or regions) inside the training set. Their
    5th/95th percentiles give split-conformal 90% prediction intervals, which,
    unlike the bootstrap, include month-level correlated noise (cloud, smoke,
    fire behaviour between overpasses)."""
    groups = train_cl[group_col].unique()
    res = {"count": [], "frp": []}
    if len(groups) < 2:
        return res
    for g in groups:
        tr_c, te_c = train_cl[train_cl[group_col] != g], train_cl[train_cl[group_col] == g]
        tr_m, te_m = train_md[train_md[group_col] != g], train_md[train_md[group_col] == g]
        if tr_c["modis_detected"].nunique() < 2 or te_c.empty:
            continue
        mon = predict_monthly(te_c, te_m, {"full": fit_model(tr_c, tr_m, "full")}, fit_constant(tr_c, tr_m))
        for target, obs, mn in (("count", "modis_obs_count", min_obs_count), ("frp", "modis_obs_frp", min_obs_frp)):
            u = mon[(mon[obs] >= mn) & (mon[f"full_{target}"] > 0)]
            res[target].extend(np.log10(u[obs] / u[f"full_{target}"]).tolist())
    return res


def conformal_interval(pred: pd.Series, residuals: list, alpha: float = 0.10):
    if len(residuals) < 8:
        return None, None
    lo_q, hi_q = np.quantile(residuals, [alpha / 2, 1 - alpha / 2])
    return pred * 10 ** lo_q, pred * 10 ** hi_q


def verdict(metrics: dict, min_rel_improvement: float = 0.10) -> dict:
    """Pre-registered success rule for one held-out fold and target.

    'improved' requires ALL of:
      * the full model's median |log10(pred/obs)| over scored months is at least
        ``min_rel_improvement`` (10%) lower than the better of the raw-VIIRS
        and constant-factor baselines;
      * the full model's total over the held-out set is within a factor 1.25 of
        the observed MODIS total.
    """
    out = {}
    for target in ("count", "frp"):
        mm = metrics[f"monthly_{target}"]
        f, r, c = (mm[k].get("median_abs_log10_ratio") for k in ("full", "raw", "constant"))
        tr = mm["full"].get("total_ratio_pred_to_obs")
        if f is None or r is None or c is None or tr is None:
            out[target] = "insufficient data"
            continue
        best = min(r, c)
        unbiased = 0.8 <= tr <= 1.25
        if f <= (1 - min_rel_improvement) * best and unbiased:
            out[target] = "improved"
        elif f <= (1 - min_rel_improvement) * best:
            out[target] = "lower error but total biased (ratio outside 0.8-1.25)"
        elif f < best:
            out[target] = "marginal (<10% better than best baseline)"
        else:
            out[target] = "not better than best baseline (" + ("raw" if r <= c else "constant") + ")"
    return out
