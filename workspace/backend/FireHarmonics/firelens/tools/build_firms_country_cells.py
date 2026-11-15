"""Build ``src/firelens/firms_country_cells.json``: which FIRMS yearly country archive files cover each 1-degree cell.

Inputs
------
* Natural Earth 1:50m admin-0 countries (public domain) as TopoJSON, e.g. ``world-atlas@2.0.2/countries-50m.json``.
* The list of FIRMS country file names (``viirs-snpp_<YEAR>_<Name>.csv``), e.g. read from the central directory of
  ``https://firms.modaps.eosdis.nasa.gov/data/country/zips/viirs-snpp_2023_all_countries.zip``.

Method
------
Every polygon part of every country is rasterised onto the 1-degree grid used by FireLens cache tiles. A cell is
assigned to a country when the polygon intersects the cell **expanded by MARGIN_DEG** on every side, so small
differences between Natural Earth borders and FIRMS's own country boundaries do not drop a neighbouring country.
Polygon parts that cannot be matched to a FIRMS file (micro-states, disputed or overseas territories without their
own file, e.g. Andorra, W. Sahara, Somaliland) mark their cells as *unresolved*; FireLens then never uses the
archive for a tile that touches such a cell and falls back to the Area API.

Usage: python tools/build_firms_country_cells.py <countries-50m.json> <firms-names.txt> [out.json]
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

MARGIN_DEG = 0.25

# Natural Earth name -> FIRMS file name, where simple "spaces to underscores" does not work.
NE_TO_FIRMS = {
    "Antigua and Barb.": "Antigua_and_Barbuda", "Bosnia and Herz.": "Bosnia_and_Herzegovina",
    "British Virgin Is.": "British_Virgin_Islands", "Brunei": "Brunei_Darussalam", "Cabo Verde": "Cape_Verde",
    "Cayman Is.": "Cayman_Islands", "Central African Rep.": "Central_African_Republic", "Congo": "Republic_of_Congo",
    "Côte d'Ivoire": "Cote_d_Ivoire", "Curaçao": "Curacao", "Czechia": "Czech_Republic",
    "Dem. Rep. Congo": "Democratic_Republic_of_the_Congo", "Dominican Rep.": "Dominican_Republic",
    "Eq. Guinea": "Equatorial_Guinea", "Faeroe Is.": "Faeroe_Islands", "Falkland Is.": "Falkland_Islands",
    "Fr. Polynesia": "French_Polynesia", "Fr. S. Antarctic Lands": "French_Southern_and_Antarctic_Lands",
    "Gambia": "The_Gambia", "Heard I. and McDonald Is.": "Heard_I_and_McDonald_Islands", "Laos": "Lao_PDR",
    "Macedonia": "Macedonia_Former_Yugoslav_Republic_of", "N. Mariana Is.": "Northern_Mariana_Islands",
    "North Korea": "Dem_Rep_Korea", "South Korea": "Republic_of_Korea", "Russia": "Russian_Federation",
    "S. Sudan": "South_Sudan", "Solomon Is.": "Solomon_Islands", "St-Martin": "Saint-Martin",
    "St. Kitts and Nevis": "Saint_Kitts_and_Nevis", "St. Vin. and Gren.": "Saint_Vincent_and_the_Grenadines",
    "São Tomé and Principe": "Sao_Tome_and_Principe", "Turks and Caicos Is.": "Turks_and_Caicos_Islands",
    "U.S. Virgin Is.": "United_States_Virgin_Islands", "United States of America": "United_States",
    "eSwatini": "Swaziland",
}

# Overseas parts that FIRMS publishes under their own file although Natural Earth includes them in the parent.
# (parent NE name, lon range, lat range, FIRMS file)
OVERSEAS = [
    ("France", (-55.0, -51.0), (1.5, 6.5), "French_Guiana"),
    ("France", (-62.0, -60.8), (15.7, 16.7), "Guadeloupe"),
    ("France", (-61.4, -60.6), (14.2, 15.0), "Martinique"),
    ("France", (54.9, 56.0), (-21.6, -20.7), "Reunion"),
    ("France", (44.8, 45.5), (-13.2, -12.4), "Mayotte"),
]
# Parent countries whose remote parts must match an OVERSEAS rule or stay unresolved (FIRMS may file them apart).
STRICT_PARENT_MAIN = {"France": ((-6.0, 10.0), (41.0, 52.0))}


def decode_arcs(topo):
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        out.append(pts)
    return out


def ring_coords(arcs, idxs):
    pts = []
    for i in idxs:
        seg = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
        pts.extend(seg if not pts else seg[1:])
    return pts


def polygons(geom, arcs):
    if geom["type"] == "Polygon":
        return [[ring_coords(arcs, r) for r in geom["arcs"]]]
    if geom["type"] == "MultiPolygon":
        return [[ring_coords(arcs, r) for r in poly] for poly in geom["arcs"]]
    return []


def cells_for_polygon(rings, margin=MARGIN_DEG):
    """1-degree cells (lon_floor, lat_floor) intersecting the polygon expanded by ``margin``."""
    cells = set()
    # 1) boundary: sample every edge densely; mark cells within `margin` of the sample point
    for ring in rings:
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
            n = max(1, int(math.hypot(x1 - x0, y1 - y0) / 0.05))
            for k in range(n + 1):
                x = x0 + (x1 - x0) * k / n
                y = y0 + (y1 - y0) * k / n
                for cx in {math.floor(x - margin), math.floor(x), math.floor(x + margin)}:
                    for cy in {math.floor(y - margin), math.floor(y), math.floor(y + margin)}:
                        cells.add((cx, cy))
    # 2) interior: scanline fill at the centre of every 1-degree row (even-odd over all rings)
    outer = rings[0]
    ys = [p[1] for p in outer]
    for row in range(math.floor(min(ys)), math.ceil(max(ys))):
        yc = row + 0.5
        xs = []
        for ring in rings:
            for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
                if (y0 <= yc < y1) or (y1 <= yc < y0):
                    xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
        xs.sort()
        for a, b in zip(xs[0::2], xs[1::2]):
            for cx in range(math.floor(a), math.floor(b) + 1):
                cells.add((cx, row))
    return {(((cx + 180) % 360) - 180, cy) for cx, cy in cells if -90 <= cy < 90}


def centroid(rings):
    xs = [p[0] for p in rings[0]]
    ys = [p[1] for p in rings[0]]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def main(topo_path, names_path, out_path):
    topo = json.loads(Path(topo_path).read_text(encoding="utf-8"))
    firms = {n.strip() for n in Path(names_path).read_text(encoding="utf-8").splitlines() if n.strip()}
    arcs = decode_arcs(topo)
    cell_map: dict[tuple[int, int], set[str]] = {}
    unresolved: set[tuple[int, int]] = set()
    unmatched: dict[str, int] = {}
    for g in topo["objects"]["countries"]["geometries"]:
        ne = g["properties"]["name"]
        base = NE_TO_FIRMS.get(ne, ne.replace(" ", "_").replace(".", ""))
        for rings in polygons(g, arcs):
            cx, cy = centroid(rings)
            target = base if base in firms else None
            for parent, (x0, x1), (y0, y1), name in OVERSEAS:
                if ne == parent and x0 <= cx <= x1 and y0 <= cy <= y1:
                    target = name
            if ne in STRICT_PARENT_MAIN and target == base:
                (x0, x1), (y0, y1) = STRICT_PARENT_MAIN[ne]
                if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                    target = None  # remote part without a known FIRMS file
            cells = cells_for_polygon(rings)
            if target is None:
                unresolved |= cells
                unmatched[ne] = unmatched.get(ne, 0) + 1
            else:
                for c in cells:
                    cell_map.setdefault(c, set()).add(target)
    out = {
        "generated": date.today().isoformat(),
        "method": f"Natural Earth 1:50m admin-0 polygons rasterised to 1-degree cells with a {MARGIN_DEG}-degree margin",
        "sources": {"boundaries": "Natural Earth 1:50m admin-0 (public domain) via world-atlas@2.0.2",
                    "firms_file_names": "central directory of viirs-snpp_2023_all_countries.zip"},
        "cell_key": "floor(lon),floor(lat) of the cell's south-west corner",
        "cells": {f"{c[0]},{c[1]}": sorted(v) for c, v in sorted(cell_map.items())},
        "unresolved_cells": sorted(f"{c[0]},{c[1]}" for c in unresolved),
        "unmatched_natural_earth_parts": dict(sorted(unmatched.items())),
    }
    Path(out_path).write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"{len(cell_map)} cells, {len(unresolved)} unresolved cells, unmatched: {sorted(unmatched)}")


if __name__ == "__main__":
    here = Path(__file__).resolve().parents[1]
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else here / "src" / "firelens" / "firms_country_cells.json")
