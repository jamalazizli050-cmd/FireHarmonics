"""Figures for the next-month fire-occurrence forecast (all values read from disk)."""
from __future__ import annotations

import json

import matplotlib.pyplot as plt
import numpy as np

from . import plots
from .config import Settings
from .io import read_parquet

NAMES = {"xgboost_calibrated": "XGBoost (calibrated)", "seasonal_climatology": "Seasonal climatology",
         "historical_frequency": "Historical frequency"}
COLORS = {"xgboost_calibrated": plots.SERIES[1], "seasonal_climatology": plots.SERIES[0],
          "historical_frequency": plots.SERIES[2]}


def make_all(settings: Settings) -> list[str]:
    plots.apply_style()
    rdir = settings.reports_dir / "results" / "forecast"
    fdir = settings.figures_dir / "forecast"
    m = json.loads((rdir / "forecast_metrics.json").read_text())
    sig = json.loads((rdir / "forecast_significance.json").read_text())
    out = []

    # f01 reliability, temporal hold-out
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), sharey=True)
    for ax, grp, title in zip(axes, ("all_cells", "ever_burned_cells"), ("All cells", "Cells with any fire before 2019")):
        ax.plot([0, 1], [0, 1], color=plots.TEXT_2, lw=1, ls="--", label="Perfect calibration")
        for k in NAMES:
            rel = m["temporal"]["metrics"][grp][k]["reliability"]
            ax.plot([r["p_mean"] for r in rel], [r["observed"] for r in rel], marker="o", ms=4,
                    color=COLORS[k], label=f"{NAMES[k]} (ECE {m['temporal']['metrics'][grp][k]['ece']:.3f})")
        ax.set_title(title)
        ax.set_xlabel("Predicted probability")
        ax.legend(fontsize=8, loc="upper left")
    axes[0].set_ylabel("Observed frequency")
    fig.suptitle("California forecast calibration, held-out years 2020–2022", fontweight="bold", x=0.01, ha="left")
    out.append(str(plots.save(fig, fdir, "f01_reliability")))
    plt.close(fig)

    # f02 per-year skill
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    py = sig["temporal"]["per_year"]
    years = sorted(py)
    x = np.arange(len(years))
    for ax, metric, lab in ((axes[0], "roc_auc", "ROC-AUC (higher is better)"), (axes[1], "brier", "Brier score (lower is better)")):
        for i, (k, kk) in enumerate((("xgboost_calibrated", "xgboost"), ("seasonal_climatology", "seasonal"),
                                     ("historical_frequency", "historical"))):
            ax.bar(x + (i - 1) * 0.26, [py[y][kk][metric] for y in years], 0.24, color=COLORS[k], label=NAMES[k])
        ax.set_xticks(x, years)
        ax.set_title(lab)
        vals = [py[y][kk][metric] for y in years for kk in ("xgboost", "seasonal", "historical")]
        lo, hi = min(vals), max(vals)
        ax.set_ylim(lo - (hi - lo) * 1.5, hi + (hi - lo) * 0.6)
        ax.grid(axis="x", visible=False)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, fontsize=8.5, loc="outside lower center", ncols=3)
    fig.suptitle("Held-out skill by year, California (temporal split)", fontweight="bold", x=0.01, ha="left")
    out.append(str(plots.save(fig, fdir, "f02_skill_by_year")))
    plt.close(fig)

    # f03 transfer diagnostic (not deployed)
    td = m.get("transfer_diagnostic_not_deployed", {})
    if td:
        regs = ["california"] + sorted(td)
        vals = {"california": {k: m["temporal"]["metrics"]["all_cells"][k] for k in NAMES}, **td}
        fig, ax = plt.subplots(figsize=(9, 4))
        x = np.arange(len(regs))
        for i, k in enumerate(NAMES):
            ax.bar(x + (i - 1) * 0.26, [vals[r][k]["roc_auc"] for r in regs], 0.24, color=COLORS[k], label=NAMES[k])
        ax.set_xticks(x, [r.replace("_", "/") + ("\n(trained here)" if r == "california" else "\n(transfer)") for r in regs])
        ax.set_ylim(0.5, 1.0)
        ax.set_ylabel("ROC-AUC, 2020–2022")
        ax.grid(axis="x", visible=False)
        ax.legend(fontsize=8, loc="lower left", ncols=3)
        ax.set_title("California-trained model applied elsewhere: diagnostic only, not deployed")
        out.append(str(plots.save(fig, fdir, "f03_transfer_diagnostic")))
        plt.close(fig)

    # f04 forecast map
    for f in sorted(rdir.glob("forecast_*.parquet")):
        fc = read_parquet(f)
        fig, axes = plt.subplots(1, 2, figsize=(10, 5.2), sharey=True)
        for ax, col, title in ((axes[0], "p_fire", "XGBoost (calibrated)"), (axes[1], "p_seasonal", "Seasonal climatology baseline")):
            sc = ax.scatter(fc.lon, fc.lat, c=fc[col], s=9, marker="s", cmap=plots.SEQ_ORANGE, vmin=0,
                            vmax=max(fc.p_fire.max(), fc.p_seasonal.max()))
            ax.set_title(title)
            plots.lonlat_aspect(ax, fc.lat.mean())
            ax.grid(False)
        fig.colorbar(sc, ax=axes, shrink=0.8, label="P(≥1 VIIRS detection in cell)")
        fig.suptitle(f"California next-month forecast, {fc['month'].iloc[0]} (0.25° cells)", fontweight="bold", x=0.01, ha="left")
        out.append(str(plots.save(fig, fdir, f"f04_forecast_map_{fc['month'].iloc[0]}")))
        plt.close(fig)
    return out
