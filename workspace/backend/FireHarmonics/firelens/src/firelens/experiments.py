"""Held-out validation experiments for the MODIS-equivalent VIIRS prototype.

Experiments (all evaluated only in the jointly observed domain):

* ``loyo:<region>``  - leave one year out within a region (local calibration);
* ``transfer_ca:<region>`` - train on California (all years), test on another region;
* ``loro:<region>`` - leave one region out (train on all other regions, all years).

Outputs: ``reports/results/harmonization/`` (JSON metrics, monthly CSV tables,
unreliable-case table) and ``reports/figures/harmonization/``.
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from . import harmonize as H
from .catalog import REGIONS
from .config import Settings
from .logging_utils import get_logger
from .matching import MatchConfig, build_region

log = get_logger(__name__)


def load_datasets(settings: Settings, regions: list[str], start: date, end: date,
                  cfg: MatchConfig = MatchConfig()) -> dict:
    data = {}
    for r in regions:
        try:
            res = build_region(settings, REGIONS[r], start, end, cfg)
        except Exception as e:  # missing data for a region should not stop the others
            log.error("Could not build %s: %s", r, e)
            continue
        cl = H.add_features(res["clusters"])
        data[r] = {"clusters_all": cl, "modis_all": res["modis"], "diagnostics": res["diagnostics"],
                   "cl": cl[cl["comparable"]].copy(), "md": res["modis"][res["modis"]["comparable"]].copy()}
    return data


def standardize_geometry(cl_all: pd.DataFrame, fill_ax: float = 0.5, std_dt: float = 10.0) -> pd.DataFrame:
    """Features for ALL VIIRS clusters (full domain), not only comparable ones.

    MODIS view position: the nearest Aqua overpass's cross-track position when
    Aqua observed the location within the search window, otherwise ``fill_ax``.
    Overpass offset: fixed at ``std_dt`` minutes (inside the training range).
    """
    d = cl_all.copy()
    d["aqua_ax"] = d["aqua_ax"].fillna(fill_ax).clip(0, 1.1)
    d["ax_x_lfrp"] = d["aqua_ax"] * d["lfrp"]
    d["abs_dt"] = std_dt
    return d


def full_domain_monthly(train_cl, train_md, test_cl_all, test_md_all) -> pd.DataFrame:
    """Monthly MODIS-equivalent totals over ALL VIIRS clusters vs ALL Aqua pixels.

    This tests the product a user would actually receive. It is expected to
    differ from Aqua where the two sensors sample differently (VIIRS's wider
    swath gives more overpasses per day, especially at high latitude).
    """
    fm = H.fit_model(train_cl, train_md, "full")
    const = H.fit_constant(train_cl, train_md)
    d = standardize_geometry(test_cl_all, fill_ax=float(train_cl["aqua_ax"].median()))
    parts = fm.predict_parts(d)
    g = pd.DataFrame({"month": d["month"], "c": parts["p"] * parts["mu"] * fm.k_n,
                      "f": parts["p"] * parts["phi"] * fm.k_f, "n_pix": d["n_pix"],
                      "frp": d["frp_sum"]}).groupby("month")
    out = pd.DataFrame({"full_count": g["c"].sum(), "full_frp": g["f"].sum(),
                        "viirs_raw_count": g["n_pix"].sum(), "viirs_raw_frp": g["frp"].sum()})
    mo = test_md_all.groupby("month")
    out["modis_obs_count"] = mo.size()
    out["modis_obs_frp"] = mo["frp_mw"].sum()
    out = out.fillna(0)
    out["const_count"] = out["viirs_raw_count"] * const.r_n
    out["const_frp"] = out["viirs_raw_frp"] * const.r_f
    return out


def _full_domain_metrics(fd: pd.DataFrame) -> dict:
    res = {}
    for target, mn in (("count", 10), ("frp", 100.0)):
        res[target] = {k: H.monthly_metrics(fd[f"modis_obs_{target}"], fd[col], mn)
                       for k, col in (("raw", f"viirs_raw_{target}"), ("constant", f"const_{target}"),
                                      ("full", f"full_{target}"))}
    return res


def _fold(name, kind, test_region, train_cl, train_md, test_cl, test_md, n_boot, conformal_group, seed,
          test_cl_all=None, test_md_all=None):
    if train_cl["modis_detected"].nunique() < 2 or test_cl.empty:
        return None
    r = H.evaluate_fold(train_cl, train_md, test_cl, test_md, n_boot=n_boot, seed=seed,
                        conformal_group=conformal_group)
    if test_cl_all is not None:
        fd = full_domain_monthly(train_cl, train_md, test_cl_all, test_md_all)
        r["metrics"]["full_domain"] = _full_domain_metrics(fd)
        r["monthly"] = r["monthly"].join(fd.add_prefix("fd_"), how="outer")
    mon = r["monthly"].reset_index().rename(columns={"index": "month"})
    mon.insert(0, "fold", name)
    mon.insert(1, "experiment", kind)
    mon.insert(2, "region", test_region)
    return {"fold": name, "experiment": kind, "test_region": test_region, "metrics": r["metrics"],
            "verdict": H.verdict(r["metrics"])}, mon


def run_experiments(data: dict, n_boot: int = 30, seed: int = 0) -> tuple[list, pd.DataFrame]:
    folds, monthly = [], []
    regions = list(data)

    def add(out):
        if out:
            folds.append(out[0])
            monthly.append(out[1])
            log.info("%s: %s", out[0]["fold"], out[0]["verdict"])

    # 1) Leave-one-year-out within each region
    for r in regions:
        cl, md = data[r]["cl"], data[r]["md"]
        for y in sorted(cl["year"].unique()):
            ca_, ma_ = data[r]["clusters_all"], data[r]["modis_all"]
            add(_fold(f"loyo:{r}:{y}", "loyo", r, cl[cl.year != y], md[md.year != y],
                      cl[cl.year == y], md[md.year == y], n_boot, "year", seed,
                      ca_[ca_.year == y], ma_[ma_.year == y]))
    # 2) Train on California, test elsewhere
    if "california" in data:
        ca = data["california"]
        for r in regions:
            if r != "california":
                add(_fold(f"transfer_ca:{r}", "transfer_ca", r, ca["cl"], ca["md"], data[r]["cl"], data[r]["md"],
                          n_boot, "year", seed, data[r]["clusters_all"], data[r]["modis_all"]))
    # 3) Leave one region out
    if len(regions) >= 3:
        for r in regions:
            tr_c = pd.concat([data[o]["cl"] for o in regions if o != r], ignore_index=True)
            tr_m = pd.concat([data[o]["md"] for o in regions if o != r], ignore_index=True)
            add(_fold(f"loro:{r}", "loro", r, tr_c, tr_m, data[r]["cl"], data[r]["md"], n_boot, "region", seed,
                      data[r]["clusters_all"], data[r]["modis_all"]))
    return folds, pd.concat(monthly, ignore_index=True) if monthly else pd.DataFrame()


def unreliable_cases(monthly: pd.DataFrame, min_obs: int = 10) -> pd.DataFrame:
    """Months where the MODIS-equivalent estimate should not be trusted."""
    m = monthly.copy()
    m["log10_ratio_count"] = np.log10(m["full_count"].clip(lower=1e-9) / m["modis_obs_count"].replace(0, np.nan))
    m["log10_ratio_frp"] = np.log10(m["full_frp"].clip(lower=1e-9) / m["modis_obs_frp"].replace(0, np.nan))
    reasons = []
    for _, row in m.iterrows():
        rs = []
        if row["modis_obs_count"] < min_obs:
            rs.append(f"few observations (<{min_obs} MODIS pixels)")
        else:
            if abs(row["log10_ratio_count"]) > np.log10(2):
                rs.append("count off by >2x")
            if pd.notna(row.get("count_conf_lo")) and not (row["count_conf_lo"] <= row["modis_obs_count"] <= row["count_conf_hi"]):
                rs.append("count outside 90% conformal PI")
        if row["modis_obs_frp"] >= 100 and pd.notna(row["log10_ratio_frp"]) and abs(row["log10_ratio_frp"]) > np.log10(2):
            rs.append("FRP off by >2x")
        reasons.append("; ".join(rs))
    m["unreliable_reason"] = reasons
    return m[m["unreliable_reason"] != ""]


def summarize(folds: list) -> pd.DataFrame:
    rows = []
    for f in folds:
        mt = f["metrics"]
        for target in ("count", "frp"):
            mm = mt[f"monthly_{target}"]
            row = {"fold": f["fold"], "experiment": f["experiment"], "test_region": f["test_region"], "target": target,
                   "n_months_scored": mm["full"].get("n_months_scored"), "verdict": f["verdict"][target]}
            for k in ("raw", "constant", "frp_only", "full"):
                row[f"medAbsLog10_{k}"] = mm[k].get("median_abs_log10_ratio")
                row[f"totalRatio_{k}"] = mm[k].get("total_ratio_pred_to_obs")
            row["pi90_cov_conformal"] = mm["full"].get("pi90_coverage_conformal")
            row["pi90_cov_bootstrap"] = mm["full"].get("pi90_coverage_bootstrap")
            if "full_domain" in mt:
                for k in ("raw", "constant", "full"):
                    row[f"fd_medAbsLog10_{k}"] = mt["full_domain"][target][k].get("median_abs_log10_ratio")
                    row[f"fd_totalRatio_{k}"] = mt["full_domain"][target][k].get("total_ratio_pred_to_obs")
            row["auc_full"] = mt["detection"]["full"]["auc"]
            row["auc_frp_only"] = mt["detection"]["frp_only"]["auc"]
            row["logloss_full"] = mt["detection"]["full"]["log_loss"]
            row["logloss_const"] = mt["detection"]["constant_rate"]["log_loss"]
            rows.append(row)
    return pd.DataFrame(rows)


def save(settings: Settings, data: dict, folds: list, monthly: pd.DataFrame) -> dict:
    out = settings.reports_dir / "results" / "harmonization"
    out.mkdir(parents=True, exist_ok=True)

    def conv(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        return str(o)
    (out / "folds.json").write_text(json.dumps(folds, indent=1, default=conv))
    monthly.to_csv(out / "monthly_predictions.csv", index=False)
    summ = summarize(folds)
    summ.to_csv(out / "summary.csv", index=False)
    unrel = unreliable_cases(monthly)
    unrel.to_csv(out / "unreliable_months.csv", index=False)
    diag = {r: d["diagnostics"] for r, d in data.items()}
    (out / "matching_diagnostics.json").write_text(json.dumps(diag, indent=1, default=conv))
    return {"summary": summ, "unreliable": unrel}


def save_final_models(settings: Settings, data: dict, cfg: MatchConfig, start: date, end: date,
                      summary: pd.DataFrame) -> dict:
    """Fit and save final models: one local model per region (all years) and
    one pooled model (all regions), with the configuration needed to reproduce
    them. Validation status per region is copied from the held-out folds."""
    import platform as _pf

    import joblib
    import sklearn
    out = settings.data_dir / "models" / "harmonization"
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"created": pd.Timestamp.now(tz="UTC").isoformat(), "period": [str(start), str(end)],
                "match_config": cfg.__dict__,
                "detector_features": ["lfrp (spline)", "aqua_ax (spline)"] + H.FULL_LINEAR,
                "glm_features": H.GLM_FEATURES_FULL, "frp_calibration_features": H.CAL_FEATURES,
                "versions": {"python": _pf.python_version(), "sklearn": sklearn.__version__,
                             "pandas": pd.__version__, "numpy": np.__version__}, "models": {}}
    sets = {r: (d["cl"], d["md"]) for r, d in data.items()}
    if len(data) > 1:
        sets["pooled"] = (pd.concat([d["cl"] for d in data.values()], ignore_index=True),
                          pd.concat([d["md"] for d in data.values()], ignore_index=True))
    for name, (cl, md) in sets.items():
        fm = H.fit_model(cl, md, "full")
        path = out / f"{name}.joblib"
        joblib.dump({"model": fm, "frp_calibration": H.fit_frp_calibration(cl),
                     "constant": H.fit_constant(cl, md)}, path)
        loyo = summary[(summary["experiment"] == "loyo") & (summary["test_region"] == name)]
        loro = summary[(summary["experiment"] == "loro") & (summary["test_region"] == name)]
        manifest["models"][name] = {
            "file": path.name, "n_train_clusters": int(len(cl)), "n_train_modis_pixels": int(len(md)),
            "k_n": fm.k_n, "k_f": fm.k_f,
            "heldout_year_verdicts": loyo.groupby("target")["verdict"].apply(list).to_dict(),
            "heldout_region_verdicts": loro.groupby("target")["verdict"].apply(list).to_dict()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    return manifest
