"""Download the FIRMS yearly country archives (MODIS, S-NPP VIIRS, NOAA-20 VIIRS) needed by demo locations, ahead of time.

No MAP_KEY is needed. Afterwards, analyses of these places (and of any other place in the same countries) take their
2002-2025 history from the local cache; only the current year still comes from the Area API.

Usage (from firelens/):
    python tools/prewarm_archives.py --all                 # every FIRMS country (~8,000 files, several GB)
    python tools/prewarm_archives.py                       # default demo points below
    python tools/prewarm_archives.py 37.7,-119.5 -13,24    # lat,lon pairs
    python tools/prewarm_archives.py --all --products=viirs-jpss1   # only NOAA-20 (added for the continuity chain)

Products: modis (Aqua+Terra, 2002+), viirs-snpp (2012+), viirs-jpss1 (NOAA-20, 2018+). Default: all three.

The cache goes to $FIRELENS_DATA_DIR/cache/firms_country (default firelens/data). Big fire countries need
0.5-2 GB each; keep FIRELENS_DATA_DIR outside cloud-synced folders.
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firelens import country_archive as ca  # noqa: E402
from firelens.config import load_settings  # noqa: E402
from firelens.unified_calendar import FIRST_YEAR, disc_bbox  # noqa: E402

DEMO_POINTS = {
    "Yosemite, California": (37.7, -119.5),
    "Western Zambia / Angola border": (-13.0, 24.0),
    "Rondonia, Brazil": (-10.5, -63.0),
    "Punjab, India": (30.7, 75.5),
}


def main(argv: list[str]) -> int:
    settings = load_settings(require_key=False)
    last = date.today().year - 1
    products = list(FIRST_YEAR)
    for a in [a for a in argv if a.startswith("--products=")]:
        products = [p for p in a.split("=", 1)[1].split(",") if p]
        unknown = set(products) - set(FIRST_YEAR)
        if unknown:
            print(f"unknown products: {sorted(unknown)}; choose from {sorted(FIRST_YEAR)}")
            return 2
    argv = [a for a in argv if not a.startswith("--products=")]
    jobs = set()

    def add(n):
        for p in products:
            jobs.update((p, n, y) for y in range(FIRST_YEAR[p], last + 1))
    if argv == ["--all"]:
        import json
        table = json.loads((Path(ca.__file__).with_name("firms_country_cells.json")).read_text(encoding="utf-8"))
        names = sorted({n for v in table["cells"].values() for n in v})
        print(f"all {len(names)} FIRMS countries")
        for n in names:
            add(n)
        argv, points = [], {}
    else:
        points = DEMO_POINTS if not argv else {a: tuple(float(x) for x in a.split(",")) for a in argv}
    for label, (lat, lon) in points.items():
        try:
            names = ca.countries_for_bbox(disc_bbox(lat, lon, 100.0))
        except ca.ArchiveUnavailable as e:
            print(f"skip {label}: {e}")
            continue
        print(f"{label}: {', '.join(names)}")
        for n in names:
            add(n)
    print(f"products: {', '.join(products)}")
    t0 = time.time()
    from concurrent.futures import ThreadPoolExecutor

    def one(job):
        product, name, year = job
        try:
            ca.ensure_country_year(settings, name, year, product=product)
            return 1
        except ca.ArchiveUnavailable as e:
            print(f"  {product} {name} {year}: {e}", flush=True)
            return 0
    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, ok in enumerate(pool.map(one, sorted(jobs)), 1):
            done += ok
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)} files ({time.time() - t0:.0f}s)", flush=True)
    print(f"{done}/{len(jobs)} country-year files cached in {time.time() - t0:.0f}s under "
          f"{settings.cache_dir / 'firms_country'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
