"""Shared matplotlib styling for FireLens figures.

Colours follow a fixed categorical order so a sensor keeps its colour in every
chart: MODIS = slot 1 (blue), VIIRS = slot 2 (orange), a third series (e.g.
Aqua when Terra/Aqua are split) = slot 3 (aqua). Magnitudes use single-hue
sequential ramps.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
SENSOR_COLORS = {"MODIS": SERIES[0], "VIIRS": SERIES[1]}
PLATFORM_COLORS = {"Terra": SERIES[0], "Aqua": SERIES[2], "Suomi NPP": SERIES[1]}

SEQ_BLUE = LinearSegmentedColormap.from_list("seq_blue", ["#dbe9f9", "#2a78d6", "#0b2e5c"])
SEQ_ORANGE = LinearSegmentedColormap.from_list("seq_orange", ["#fde3d6", "#eb6834", "#6b2408"])
SENSOR_CMAPS = {"MODIS": SEQ_BLUE, "VIIRS": SEQ_ORANGE}


def apply_style() -> None:
    mpl.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "figure.dpi": 110, "savefig.dpi": 150, "savefig.bbox": "tight",
        "figure.constrained_layout.use": True,
        "font.size": 10, "axes.titlesize": 11.5, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelcolor": TEXT_2, "axes.edgecolor": GRID, "text.color": TEXT,
        "xtick.color": TEXT_2, "ytick.color": TEXT_2,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "lines.linewidth": 2.0, "legend.frameon": False,
        "axes.prop_cycle": mpl.cycler(color=SERIES),
    })


def save(fig, figures_dir: str | Path, name: str) -> Path:
    path = Path(figures_dir) / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    return path


def lonlat_aspect(ax, mean_lat: float) -> None:
    """Approximate equal-area appearance for lon/lat plots at a given latitude."""
    import numpy as np
    ax.set_aspect(1 / np.cos(np.radians(mean_lat)))
