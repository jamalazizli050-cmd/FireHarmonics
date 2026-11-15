"""Build ``harmonization_priors.json``: regional sensor ratios for the continuity chain's partial pooling.

For every cached FIRMS yearly country archive (MODIS, S-NPP VIIRS, NOAA-20 VIIRS) it counts active 1-km
cell-overpasses (the calendar's unit: a 0.01 deg cell burning in a day or night overpass counts once) of
quality-controlled vegetation fires, and sums them per 5 deg cell, year and day/night. The calendar divides these sums
over any set of years, so held-out validation can use priors from the training years only.

Usage (from firelens/, after tools/prewarm_archives.py):
    python tools/build_harmonization_priors.py              # writes $FIRELENS_DATA_DIR/cache/harmonization_priors.json
    python tools/build_harmonization_priors.py --bundled    # writes src/firelens/harmonization_priors.json (the copy
                                                            # shipped with the code)
    python tools/build_harmonization_priors.py out.json

The analysis reads the copy in $FIRELENS_DATA_DIR/cache first, then the shipped one, then built-in defaults.

Run it again after downloading more archives and restart the backend (the table is read once per process).
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firelens.config import load_settings  # noqa: E402
from firelens.unified_calendar import GRID_DEG, PRIORS_FILE, STRATA  # noqa: E402

PRIOR_GRID = 5.0
# cached directory -> key in the table (MODIS is restricted to Aqua, the calendar's reference)
PRODUCTS = {"modis": "aqua", "viirs-snpp": "viirs-snpp", "viirs-jpss1": "viirs-jpss1"}
COLS = ["acq_date", "satellite", "confidence", "daynight", "type", "_lat", "_lon"]


def _keys(t: pd.DataFrame, product: str) -> pd.DataFrame:
    """Compact unique (day, night flag, cell) keys of the quality-controlled vegetation fires in one row group."""
    keep = t["type"].str.strip().eq("0") & t["daynight"].isin(STRATA)
    if product == "modis":
        conf = pd.to_numeric(t["confidence"], errors="coerce")
        keep &= t["satellite"].isin(["Aqua", "A"]) & conf.ge(30)        # MODIS low confidence is 0-29
    else:
        keep &= t["confidence"].str.strip().str.lower().isin(["n", "h", "nominal", "high"])
    t = t[keep]
    day = pd.to_datetime(t["acq_date"], format="%Y-%m-%d", errors="coerce")
    ok = day.notna().to_numpy()
    return pd.DataFrame({"day": (day[ok].to_numpy().astype("datetime64[D]").astype(np.int32)),
                         "night": t["daynight"].to_numpy()[ok] == "N",
                         "i": np.floor(t["_lat"].to_numpy()[ok] / GRID_DEG).astype(np.int32),
                         "j": np.floor(t["_lon"].to_numpy()[ok] / GRID_DEG).astype(np.int32)}).drop_duplicates()


def units_by_cell(path: Path, product: str) -> dict[str, list[float]]:
    # Row group by row group keeps memory bounded on small servers (big countries have millions of rows a year).
    f = pq.ParquetFile(path)
    parts = [_keys(f.read_row_group(g, columns=COLS).to_pandas(), product) for g in range(f.num_row_groups)]
    k = pd.concat(parts, ignore_index=True).drop_duplicates() if parts else pd.DataFrame()
    if k.empty:
        return {}
    k["cell"] = (np.floor(k["j"] * GRID_DEG / PRIOR_GRID).astype(int).astype(str) + ","
                 + np.floor(k["i"] * GRID_DEG / PRIOR_GRID).astype(int).astype(str))
    k["dn"] = np.where(k["night"], "N", "D")
    c = k.groupby(["cell", "dn"]).size().unstack(fill_value=0).reindex(columns=list(STRATA), fill_value=0)
    return {cell: [float(row["D"]), float(row["N"])] for cell, row in c.iterrows()}


def one(job):
    product, year, path = job
    try:
        return product, year, units_by_cell(Path(path), product)
    except Exception as e:  # noqa: BLE001 - a broken file must not stop the table
        print(f"  skip {path}: {type(e).__name__}: {e}", flush=True)
        return product, year, {}


def main(argv: list[str]) -> int:
    settings = load_settings(require_key=False)
    root = settings.cache_dir / "firms_country"
    if argv == ["--bundled"]:
        out_path = PRIORS_FILE
    elif argv:
        out_path = Path(argv[0])
    else:
        out_path = settings.cache_dir / PRIORS_FILE.name
        out_path.parent.mkdir(parents=True, exist_ok=True)
    jobs = [(prod, int(ydir.name), str(f)) for prod in PRODUCTS if (root / prod).is_dir()
            for ydir in sorted((root / prod).iterdir()) if ydir.name.isdigit() for f in sorted(ydir.glob("*.parquet"))]
    print(f"{len(jobs)} country-year files under {root}", flush=True)
    cells: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(dict))
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=2) as pool:   # memory-bounded: fine on a 2-vCPU, 8 GB VM
        for i, (prod, year, res) in enumerate(pool.map(one, jobs, chunksize=4), 1):
            key = PRODUCTS[prod]
            for cell, v in res.items():
                prev = cells[cell][key].get(str(year), [0.0, 0.0])
                cells[cell][key][str(year)] = [prev[0] + v[0], prev[1] + v[1]]
            if i % 200 == 0:
                print(f"  {i}/{len(jobs)} files ({time.time() - t0:.0f}s)", flush=True)
    table = {"generated": str(date.today()), "grid_deg": PRIOR_GRID, "unit_grid_deg": GRID_DEG,
             "unit": "active 1-km cell-overpasses (vegetation fires, nominal/high confidence), [day, night]",
             "products": {"aqua": "Aqua MODIS (MODIS_SP)", "viirs-snpp": "S-NPP VIIRS (VIIRS_SNPP_SP)",
                          "viirs-jpss1": "NOAA-20 VIIRS (VIIRS_NOAA20_SP)"},
             "files": len(jobs), "cells": {c: dict(v) for c, v in sorted(cells.items())}}
    out_path.write_text(json.dumps(table, separators=(",", ":")), encoding="utf-8")
    print(f"{len(table['cells'])} cells written to {out_path} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
