"""Figures for pattern discovery (all values read from reports/results/patterns)."""
from __future__ import annotations

import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm

from . import plots
from .config import Settings
from .io import read_parquet

SERIES_STYLE = {"modis_aqua": ("Aqua-MODIS", plots.SERIES[0], "-"),
                "viirs_snpp": ("S-NPP VIIRS", plots.SERIES[1], "-"),
                "modis_equivalent": ("MODIS-equivalent (full domain, not validated)", plots.SERIES[2], "--")}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def make_all(settings: Settings) -> list[str]:
    plots.apply_style()
    rdir = settings.reports_dir / "results" / "patterns"
    fdir = settings.figures_dir / "patterns"
    pj = json.loads((rdir / "patterns.json").read_text())
    ser = pd.read_csv(rdir / "regional_monthly_series.csv")
    regions = list(pj)
    out = []

    # p01 seasonal profile (share of annual detections per calendar month)
    fig, axes = plt.subplots(1, len(regions), figsize=(3.1 * len(regions), 3.4), sharey=True)
    for ax, r in zip(axes, regions):
        for s in ("modis_aqua", "viirs_snpp"):
            sh = pj[r][s]["seasonality"]["monthly_share"]
            lab, col, ls = SERIES_STYLE[s]
            ax.plot(range(12), [sh[m] for m in MONTHS], color=col, ls=ls, marker="o", ms=3, label=lab)
        ax.set_xticks(range(12), [m[0] for m in MONTHS])
        ax.set_title(r.replace("_", "/"))
    axes[0].set_ylabel("Share of annual detections")
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="outside lower center", ncols=2, fontsize=9)
    fig.suptitle("Fire season by region, 2012–2021 (vegetation fires)", fontweight="bold", x=0.01, ha="left")
    out.append(str(plots.save(fig, fdir, "p01_seasonal_profiles")))
    plt.close(fig)

    # p02 annual totals indexed to each series' own 2012-2021 mean (removes the sensor-level offset)
    fig, axes = plt.subplots(1, len(regions), figsize=(3.1 * len(regions), 3.6), sharey=True)
    for ax, r in zip(axes, regions):
        txt = []
        for s in ("modis_aqua", "viirs_snpp", "modis_equivalent"):
            if s not in pj[r]:
                continue
            tr = pj[r][s]["trend_count"]
            ann = pd.Series(tr["annual_totals"]).astype(float)
            lab, col, ls = SERIES_STYLE[s]
            ax.plot(ann.index.astype(int), ann / ann.mean(), color=col, ls=ls, marker="o", ms=3, label=lab)
            if s != "modis_equivalent":
                txt.append(f"{lab.split()[0]}: {tr['theil_sen_pct_per_year']:+.0f}%/yr, p={tr['p_value']:.2f}")
        ax.axhline(1, color=plots.TEXT_2, lw=0.8, ls=":")
        ax.set_yscale("log")
        ax.set_title(r.replace("_", "/"))
        ax.text(0.02, 0.98, "\n".join(txt), transform=ax.transAxes, fontsize=7.5, color=plots.TEXT_2, va="top")
        ax.set_xticks([2012, 2015, 2018, 2021])
    axes[0].set_ylabel("Annual detections / 2012–2021 mean")
    axes[0].set_ylim(0.02, 20)
    h, l = [], []
    for a in axes:
        for hh, ll in zip(*a.get_legend_handles_labels()):
            if ll not in l:
                h.append(hh)
                l.append(ll)
    fig.legend(h, l, loc="outside lower center", ncols=3, fontsize=9)
    fig.suptitle("Interannual variability and trend (Theil–Sen slope, Mann–Kendall p)", fontweight="bold",
                 x=0.01, ha="left")
    out.append(str(plots.save(fig, fdir, "p02_annual_trends")))
    plt.close(fig)

    # p03 hotspot maps (VIIRS mean annual detections per 0.25 deg cell, Gi* 99% hotspots outlined)
    hs = read_parquet(rdir / "hotspots_gi_star.parquet")
    fig, axes = plt.subplots(1, len(regions), figsize=(3.4 * len(regions), 4.0))
    res = 0.25
    for ax, r in zip(axes, regions):
        g = hs[hs.region == r]
        lat_e = np.arange(np.floor(g.lat.min() / res) * res, np.ceil(g.lat.max() / res) * res + res / 2, res)
        lon_e = np.arange(np.floor(g.lon.min() / res) * res, np.ceil(g.lon.max() / res) * res + res / 2, res)
        val = np.full((len(lat_e) - 1, len(lon_e) - 1), np.nan)
        hot = np.zeros_like(val)
        ii = np.clip(((g.lat - lat_e[0]) // res).astype(int), 0, val.shape[0] - 1)
        jj = np.clip(((g.lon - lon_e[0]) // res).astype(int), 0, val.shape[1] - 1)
        val[ii, jj] = np.where(g.mean_annual_detections > 0, g.mean_annual_detections, np.nan)
        hot[ii, jj] = g.hotspot_99.astype(float)
        pos = g.mean_annual_detections[g.mean_annual_detections > 0]
        m = ax.pcolormesh(lon_e, lat_e, val, cmap=plots.SEQ_ORANGE,
                          norm=LogNorm(vmin=max(pos.min(), 0.1), vmax=pos.max()))
        lc, la = (lon_e[:-1] + lon_e[1:]) / 2, (lat_e[:-1] + lat_e[1:]) / 2
        ax.contour(lc, la, hot, levels=[0.5], colors=plots.TEXT, linewidths=1.0)
        plots.lonlat_aspect(ax, g.lat.mean())
        ax.set_title(f"{r.replace('_', '/')}: {int(g.hotspot_99.sum())} hotspot cells", fontsize=10.5)
        ax.grid(False)
        ax.xaxis.set_major_locator(plt.MaxNLocator(4))
        fig.colorbar(m, ax=ax, shrink=0.6, orientation="horizontal", label="VIIRS detections / yr")
    fig.suptitle("Persistent fire hotspots, S-NPP VIIRS 2012–2021 (0.25° cells; black outline = Gi* hotspot, 99%)",
                 fontweight="bold", x=0.01, ha="left")
    out.append(str(plots.save(fig, fdir, "p03_hotspots")))
    plt.close(fig)
    return out
