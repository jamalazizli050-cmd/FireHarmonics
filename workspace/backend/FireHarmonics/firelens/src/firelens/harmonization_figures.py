"""Figures for the harmonisation prototype (before/after, validation, coverage)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from . import harmonize as H
from . import plots
from .plots import SERIES, SURFACE, TEXT_2

# Fixed categorical order for regions (slots 1-5 of the reference palette).
REGION_COLORS = {"california": "#2a78d6", "zambia_angola": "#eb6834", "rondonia": "#1baf7a",
                 "yakutia": "#eda100", "punjab": "#e87ba4"}
OBS, RAW, EQUIV, CONST = SERIES[0], SERIES[1], SERIES[2], "#8a8984"


def _region_color(r):
    return REGION_COLORS.get(r, TEXT_2)


def coverage_figure(data: dict, fig_dir):
    regions = list(data)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.2))
    bins = np.arange(0, 101, 5)
    for r in regions:
        cl = data[r]["clusters_all"]
        adt = cl["aqua_dt_min"].abs().dropna()
        h, _ = np.histogram(adt, bins=bins)
        share = h / max(len(cl), 1)
        axes[0].step(bins[:-1], share, where="post", color=_region_color(r), lw=2,
                     label=f"{r} ({cl['comparable'].mean():.0%} comparable)")
    axes[0].axvspan(0, 20, color=GRID_SHADE, zorder=0)
    axes[0].set_xlabel("|Aqua time - VIIRS time| at the cluster (min)")
    axes[0].set_ylabel("share of VIIRS clusters")
    axes[0].set_title("Aqua coverage of VIIRS fire clusters (shaded = comparable, <= 20 min)")
    axes[0].legend(fontsize=8)
    for r in regions:
        rows = pd.DataFrame(data[r]["diagnostics"]["detection_rate_by_abs_dt"])
        if rows.empty:
            continue
        x = np.arange(len(rows))
        axes[1].plot(x, rows["p_detected_frp_ge_20MW"], marker="o", ms=5, color=_region_color(r), label=r)
        axes[1].set_xticks(x, rows["abs_dt_bin"].str.replace("(-0.001", "[0", regex=False), rotation=30, fontsize=8)
    axes[1].set_ylim(0, 1)
    axes[1].set_xlabel("|Aqua - VIIRS| time offset (min)")
    axes[1].set_ylabel("P(Aqua detects | VIIRS cluster >= 20 MW)")
    axes[1].set_title("Agreement vs overpass time offset")
    axes[1].legend(fontsize=8)
    plots.save(fig, fig_dir, "h01_coverage_and_time_offset")
    plt.close(fig)


GRID_SHADE = "#eef3fb"


def detection_curves_figure(data: dict, fig_dir):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.3), sharey=True)
    frp_bins = [0, 2, 5, 10, 20, 50, 100, 250, 1e9]
    labels = ["<2", "2-5", "5-10", "10-20", "20-50", "50-100", "100-250", ">=250"]
    for r in data:
        cl = data[r]["cl"]
        g = cl.groupby(pd.cut(cl["frp_sum"], frp_bins, right=False), observed=False)["modis_detected"]
        rate, n = g.mean(), g.size()
        rate[n < 20] = np.nan
        axes[0].plot(range(len(labels)), rate.to_numpy(), marker="o", ms=5, color=_region_color(r), label=r)
        ax_bins = [0, .2, .4, .6, .8, 1.2]
        big = cl[cl["frp_sum"].between(10, 50)]
        g2 = big.groupby(pd.cut(big["aqua_ax"], ax_bins), observed=False)["modis_detected"]
        rate2, n2 = g2.mean(), g2.size()
        rate2[n2 < 20] = np.nan
        axes[1].plot(range(len(ax_bins) - 1), rate2.to_numpy(), marker="o", ms=5, color=_region_color(r), label=r)
        for k, (night, mk) in enumerate([(0.0, "o"), (1.0, "s")]):
            sub = cl[cl["night"] == night]
            g3 = sub.groupby(pd.cut(sub["frp_sum"], frp_bins, right=False), observed=False)["modis_detected"]
            rate3, n3 = g3.mean(), g3.size()
            rate3[n3 < 20] = np.nan
            axes[2].plot(range(len(labels)), rate3.to_numpy(), marker=mk, ms=5, ls="-" if night == 0 else "--",
                         color=_region_color(r), label=f"{r} {'night' if night else 'day'}" if r == "california" else None)
    axes[0].set_xticks(range(len(labels)), labels, rotation=30)
    axes[0].set_xlabel("VIIRS cluster summed FRP (MW)")
    axes[0].set_ylabel("observed P(Aqua-MODIS detects)")
    axes[0].set_title("By fire size (comparable clusters)")
    axes[0].legend(fontsize=8)
    axes[1].set_xticks(range(5), ["0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8+"])
    axes[1].set_xlabel("MODIS |cross-track| position (0 = nadir, 1 = swath edge)")
    axes[1].set_title("By MODIS view position (clusters 10-50 MW)")
    axes[2].set_xticks(range(len(labels)), labels, rotation=30)
    axes[2].set_xlabel("VIIRS cluster summed FRP (MW)")
    axes[2].set_title("Day (solid, circles) vs night (dashed, squares)")
    from matplotlib.lines import Line2D
    axes[2].legend(handles=[Line2D([], [], color=TEXT_2, marker="o", ls="-", label="day"),
                            Line2D([], [], color=TEXT_2, marker="s", ls="--", label="night")], fontsize=8)
    axes[0].set_ylim(0, 1.02)
    plots.save(fig, fig_dir, "h02_detection_probability_curves")
    plt.close(fig)


def reliability_figure(folds: list, fig_dir):
    kinds = [k for k in ("loyo", "transfer_ca", "loro") if any(f["experiment"] == k for f in folds)]
    fig, axes = plt.subplots(1, len(kinds), figsize=(4.6 * len(kinds), 4.3), squeeze=False)
    for ax, kind in zip(axes[0], kinds):
        for f in folds:
            if f["experiment"] != kind:
                continue
            rel = pd.DataFrame(f["metrics"]["detection"]["full"]["reliability"])
            rel = rel[rel["n"] >= 30]
            ax.plot(rel["p_mean"], rel["observed"], marker="o", ms=4, lw=1.4, alpha=0.85,
                    color=_region_color(f["test_region"]), label=f["fold"].split(":", 1)[1])
        ax.plot([0, 1], [0, 1], ls="--", color=TEXT_2, lw=1)
        ax.set_xlabel("predicted P(MODIS detects)")
        ax.set_ylabel("observed frequency")
        ax.set_title({"loyo": "Held-out years", "transfer_ca": "Trained on California",
                      "loro": "Held-out regions"}[kind])
        ax.legend(fontsize=7)
    plots.save(fig, fig_dir, "h03_reliability")
    plt.close(fig)


def before_after_figure(monthly: pd.DataFrame, experiment: str, fig_dir, name: str, target: str = "count"):
    m = monthly[monthly["experiment"] == experiment].copy()
    if m.empty:
        return
    regions = list(dict.fromkeys(m["region"]))
    fig, axes = plt.subplots(len(regions), 1, figsize=(12, 2.9 * len(regions)), squeeze=False)
    unit = "MODIS pixels" if target == "count" else "FRP (MW)"
    for ax, r in zip(axes[:, 0], regions):
        d = m[m["region"] == r].copy()
        d["t"] = pd.PeriodIndex(d["month"], freq="M").to_timestamp()
        d = d.sort_values("t")
        if f"{target}_conf_lo" in d and d[f"{target}_conf_lo"].notna().any():
            ax.fill_between(d["t"], d[f"{target}_conf_lo"].clip(lower=0.5), d[f"{target}_conf_hi"], color=EQUIV,
                            alpha=0.18, lw=0, label="90% conformal interval")
        ax.plot(d["t"], d[f"viirs_raw_{target}"], color=RAW, lw=1.6, label="VIIRS raw (before)")
        ax.plot(d["t"], d[f"const_{target}"], color=CONST, lw=1.2, ls="--", label="constant-factor baseline")
        ax.plot(d["t"], d[f"full_{target}"], color=EQUIV, lw=2, label="MODIS-equivalent (after)")
        ax.plot(d["t"], d[f"modis_obs_{target}"], color=OBS, lw=0, marker="o", ms=4, label="Aqua-MODIS observed")
        ax.set_yscale("symlog", linthresh=10 if target == "count" else 100)
        ax.set_ylabel(unit)
        ax.set_title(f"{r}: monthly {('pixel counts' if target == 'count' else 'FRP')} in the jointly observed domain "
                     f"({'held-out year' if experiment == 'loyo' else 'held-out region' if experiment == 'loro' else 'trained on California'})")
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axes[0, 0].legend(fontsize=8, ncol=5, loc="upper left")
    plots.save(fig, fig_dir, name)
    plt.close(fig)


def error_summary_figure(summary: pd.DataFrame, fig_dir):
    fig, axes = plt.subplots(1, 2, figsize=(14, max(4, 0.32 * summary["fold"].nunique() + 1.5)), sharey=True)
    folds = list(dict.fromkeys(summary["fold"]))
    y = np.arange(len(folds))
    methods = [("raw", RAW, "VIIRS raw"), ("constant", CONST, "constant factor"),
               ("frp_only", "#6250d6", "FRP-only model"), ("full", EQUIV, "full model")]
    for ax, target in zip(axes, ("count", "frp")):
        s = summary[summary["target"] == target].set_index("fold").reindex(folds)
        for k, (m, c, lab) in enumerate(methods):
            ax.scatter(10 ** s[f"medAbsLog10_{m}"], y + (k - 1.5) * 0.15, color=c, s=28, label=lab, zorder=3)
        ax.set_xscale("log")
        ax.axvline(1.5, color=TEXT_2, lw=0.8, ls=":")
        ax.set_xlabel("median monthly error factor  10^|log10(pred/obs)|  (1 = perfect)")
        ax.set_title(f"Held-out monthly {'pixel-count' if target == 'count' else 'FRP'} error")
    axes[0].set_yticks(y, folds, fontsize=8)
    axes[0].invert_yaxis()
    axes[0].legend(fontsize=8, loc="lower left", bbox_to_anchor=(0, 1.02), ncol=4, frameon=False)
    plots.save(fig, fig_dir, "h06_error_summary")
    plt.close(fig)


def scatter_figure(monthly: pd.DataFrame, fig_dir, target="count"):
    kinds = [k for k in ("loyo", "transfer_ca", "loro") if (monthly["experiment"] == k).any()]
    fig, axes = plt.subplots(1, len(kinds), figsize=(4.8 * len(kinds), 4.6), squeeze=False)
    for ax, kind in zip(axes[0], kinds):
        m = monthly[(monthly["experiment"] == kind) & (monthly[f"modis_obs_{target}"] > 0)]
        ax.scatter(m[f"modis_obs_{target}"], m[f"viirs_raw_{target}"], s=10, color=RAW, alpha=0.5, label="VIIRS raw")
        ax.scatter(m[f"modis_obs_{target}"], m[f"full_{target}"], s=12, color=EQUIV, alpha=0.8, label="MODIS-equivalent")
        lim = [max(1, m[f"modis_obs_{target}"].min() / 2), m[[f"modis_obs_{target}", f"viirs_raw_{target}"]].max().max() * 2]
        ax.plot(lim, lim, ls="--", lw=1, color=TEXT_2)
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlabel(f"Aqua-MODIS observed monthly {target}")
        ax.set_ylabel("estimate")
        ax.set_title({"loyo": "Held-out years", "transfer_ca": "Trained on California", "loro": "Held-out regions"}[kind])
        ax.legend(fontsize=8)
    plots.save(fig, fig_dir, f"h07_monthly_scatter_{target}")
    plt.close(fig)


def frp_calibration_figure(data: dict, fig_dir):
    """FRP calibration per co-detected cluster: leave-one-region-out predictions."""
    regions = list(data)
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), sharex=True, sharey=True)
    for r in regions:
        tr = pd.concat([data[o]["cl"] for o in regions if o != r] or [data[r]["cl"]], ignore_index=True)
        cal = H.fit_frp_calibration(tr)
        d = data[r]["cl"]
        d = d[(d["n_modis"] > 0) & (d["frp_modis"] > 0) & (d["frp_sum"] > 0)]
        if len(d) > 4000:
            d = d.sample(4000, random_state=0)
        obs = np.log10(d["frp_modis"])
        axes[0].scatter(np.log10(d["frp_sum"]), obs, s=3, alpha=0.35, color=_region_color(r), label=r, rasterized=True)
        axes[1].scatter(cal.model.predict(d[cal.features]), obs, s=3, alpha=0.35, color=_region_color(r), rasterized=True)
    for ax, t in zip(axes, ["Before: VIIRS cluster FRP", "After: calibrated (region held out)"]):
        ax.plot([-1, 5], [-1, 5], ls="--", lw=1, color=TEXT_2)
        ax.set_xlabel(f"log10 {t.split(': ')[1]} (MW)")
        ax.set_title(t)
    axes[0].set_ylabel("log10 Aqua-MODIS FRP assigned to cluster (MW)")
    axes[0].legend(fontsize=8, markerscale=4)
    plots.save(fig, fig_dir, "h08_frp_calibration")
    plt.close(fig)


def make_all(settings, data, folds, monthly, summary):
    plots.apply_style()
    fig_dir = settings.figures_dir / "harmonization"
    coverage_figure(data, fig_dir)
    detection_curves_figure(data, fig_dir)
    reliability_figure(folds, fig_dir)
    before_after_figure(monthly, "loyo", fig_dir, "h04_before_after_heldout_years_count", "count")
    before_after_figure(monthly, "loro", fig_dir, "h05_before_after_heldout_regions_count", "count")
    before_after_figure(monthly, "loro", fig_dir, "h05b_before_after_heldout_regions_frp", "frp")
    before_after_figure(monthly, "transfer_ca", fig_dir, "h05c_before_after_trained_on_california_count", "count")
    error_summary_figure(summary, fig_dir)
    scatter_figure(monthly, fig_dir, "count")
    scatter_figure(monthly, fig_dir, "frp")
    frp_calibration_figure(data, fig_dir)
