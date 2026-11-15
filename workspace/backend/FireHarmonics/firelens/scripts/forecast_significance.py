"""Per-year metrics and month-block bootstrap of skill differences (held-out predictions)."""
import json
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score
from firelens.config import load_settings
from firelens.io import read_parquet

S = load_settings(require_key=False)
d = read_parquet(S.reports_dir / "results" / "forecast" / "heldout_predictions.parquet")
out = {}
for split, g in d.groupby("split"):
    g = g.copy()
    g["year"] = g["month"].str[:4].astype(int)
    per_year = {}
    for y, gy in g.groupby("year"):
        per_year[int(y)] = {n: {"roc_auc": roc_auc_score(gy.y, gy[c]), "pr_auc": average_precision_score(gy.y, gy[c]),
                                "brier": float(np.mean((gy[c] - gy.y) ** 2))}
                            for n, c in (("xgboost", "p_xgb"), ("seasonal", "p_seasonal"), ("historical", "p_hist"))}
    months = g["month"].unique()
    idx = {m: np.flatnonzero(g["month"].to_numpy() == m) for m in months}
    rng = np.random.default_rng(1)
    diffs = {"auc_vs_seasonal": [], "auc_vs_hist": [], "brier_vs_seasonal": [], "brier_vs_hist": []}
    y = g.y.to_numpy(); px, ps, ph = g.p_xgb.to_numpy(), g.p_seasonal.to_numpy(), g.p_hist.to_numpy()
    for _ in range(300):
        ii = np.concatenate([idx[m] for m in rng.choice(months, len(months))])
        yy = y[ii]
        ax = roc_auc_score(yy, px[ii])
        diffs["auc_vs_seasonal"].append(ax - roc_auc_score(yy, ps[ii]))
        diffs["auc_vs_hist"].append(ax - roc_auc_score(yy, ph[ii]))
        bx = np.mean((px[ii] - yy) ** 2)
        diffs["brier_vs_seasonal"].append(np.mean((ps[ii] - yy) ** 2) - bx)   # >0: xgb better
        diffs["brier_vs_hist"].append(np.mean((ph[ii] - yy) ** 2) - bx)
    out[split] = {"per_year": per_year,
                  "bootstrap_month_blocks_300": {k: {"mean": float(np.mean(v)), "ci95": [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))],
                                                     "share_xgb_better": float(np.mean(np.array(v) > 0))} for k, v in diffs.items()}}
(S.reports_dir / "results" / "forecast" / "forecast_significance.json").write_text(json.dumps(out, indent=1))
for s, v in out.items():
    print("==", s)
    for yr, m in v["per_year"].items():
        print(yr, {n: {k: round(x, 4) for k, x in mm.items()} for n, mm in m.items()})
    for k, b in v["bootstrap_month_blocks_300"].items():
        print(k, round(b["mean"], 4), [round(x, 4) for x in b["ci95"]], b["share_xgb_better"])

# Figures (read the files written above)
from firelens import forecast_figures
print(forecast_figures.make_all(S))
