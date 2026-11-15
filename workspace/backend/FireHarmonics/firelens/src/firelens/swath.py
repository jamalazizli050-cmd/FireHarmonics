"""Satellite swath coverage from granule footprints (NASA CMR).

FIRMS publishes detections only, so the absence of a MODIS detection does not
show that MODIS looked at a location. To establish coverage we use the
footprints of the Level-1 geolocation granules that the fire products are
derived from:

* Aqua MODIS: ``MYD03`` collection 6.1 (5-minute granules), CMR concept
  ``C1379841358-LAADS``
* Suomi NPP VIIRS: ``VNP03IMG`` collection 2 (6-minute granules, 375 m), CMR
  concept ``C2105092163-LAADS``

Granule metadata (start/end time, day/night flag, 4-corner footprint polygon)
comes from the public CMR search API
(https://cmr.earthdata.nasa.gov/search/granules.json), which needs no login.

Geometry
--------
Each footprint is projected with a gnomonic projection centred on the granule,
in which great circles are straight lines, so the polygon edges are well
approximated. In CMR corner order, edges (p0,p1) and (p2,p3) are scan lines
(cross-track) and edges (p1,p2) and (p3,p0) are the swath sides. This was
verified on real granules: consecutive granules share a scan edge. Each point
inside a granule gets:

* ``xtrack`` in [-1, 1]: position across the swath (0 = nadir, +/-1 = edge),
* ``along`` in [0, 1]: fractional position along the granule in time order,
* ``t``: estimated acquisition time = start + along * duration.

The swath sides of a 4-corner polygon are straight-line approximations, so
``xtrack`` near +/-1 is approximate. The geometry is valid anywhere on the
globe, including across the antimeridian.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import requests
from matplotlib.path import Path as MplPath

from .catalog import Region
from .config import Settings
from .io import read_parquet, write_parquet
from .logging_utils import get_logger

log = get_logger(__name__)

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"


@dataclass(frozen=True)
class GranuleCollection:
    key: str
    concept_id: str
    platform: str
    sensor: str


AQUA_MYD03 = GranuleCollection("MYD03_061", "C1379841358-LAADS", "Aqua", "MODIS")
SNPP_VNP03IMG = GranuleCollection("VNP03IMG_002", "C2105092163-LAADS", "Suomi NPP", "VIIRS")


# ---------------------------------------------------------------- CMR access
def _cmr_pages(params: dict, session: requests.Session, max_retries: int = 5):
    headers = {}
    while True:
        for attempt in range(max_retries):
            try:
                r = session.get(CMR_URL, params=params, headers=headers, timeout=120)
                if r.status_code >= 500 or r.status_code == 429:
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                break
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                log.warning("CMR request failed (%s); retrying in %ds", e, wait)
                time.sleep(wait)
        entries = r.json()["feed"]["entry"]
        yield entries
        nxt = r.headers.get("CMR-Search-After")
        if not entries or not nxt or len(entries) < params["page_size"]:
            return
        headers = {"CMR-Search-After": nxt}


def fetch_granules(settings: Settings, coll: GranuleCollection, region: Region, year: int,
                   refresh: bool = False, session: requests.Session | None = None) -> pd.DataFrame:
    """Granules of ``coll`` intersecting ``region`` during ``year`` (cached)."""
    cache = settings.cache_dir / "granules" / coll.key / region.name / f"{year}.parquet"
    if cache.exists() and not refresh:
        return read_parquet(cache)
    b = region.bbox
    params = {"collection_concept_id": coll.concept_id, "page_size": 2000,
              "temporal": f"{year}-01-01T00:00:00Z,{year}-12-31T23:59:59Z",
              "bounding_box": f"{b.west},{b.south},{b.east},{b.north}", "sort_key": "start_date"}
    rows, skipped = [], 0
    for page in _cmr_pages(params, session or requests.Session()):
        for e in page:
            polys = e.get("polygons")
            if not polys:
                skipped += 1
                continue
            rows.append({"granule_id": e["producer_granule_id"],
                         "time_start": e["time_start"], "time_end": e["time_end"],
                         "day_night": e.get("day_night_flag", ""), "polygon": polys[0][0],
                         "n_rings": len(polys[0])})
    df = pd.DataFrame(rows)
    if df.empty:
        log.warning("No %s granules for %s %d", coll.key, region.name, year)
        return df
    df["time_start"] = pd.to_datetime(df["time_start"], utc=True)
    df["time_end"] = pd.to_datetime(df["time_end"], utc=True)
    df = df.drop_duplicates("granule_id").sort_values("time_start").reset_index(drop=True)
    df["platform"] = coll.platform
    write_parquet(df, cache)
    log.info("%s %s %d: %d granules (%d without polygon skipped)", coll.key, region.name, year, len(df), skipped)
    return df


# ------------------------------------------------------------------ geometry
def to_minutes(ts) -> np.ndarray:
    """Minutes since 1970-01-01 UTC, independent of the datetime unit (ns/us)."""
    ts = pd.to_datetime(pd.Series(ts), utc=True)
    return ((ts - pd.Timestamp(0, tz="UTC")).dt.total_seconds() / 60.0).to_numpy()


def unit_vectors(lat, lon) -> np.ndarray:
    la, lo = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def parse_polygon(s: str) -> tuple[np.ndarray, np.ndarray]:
    v = np.array(s.split(), dtype=float)
    lat, lon = v[0::2], v[1::2]
    if len(lat) > 1 and lat[0] == lat[-1] and lon[0] == lon[-1]:
        lat, lon = lat[:-1], lon[:-1]
    return lat, lon


EDGES = [(0, 1), (1, 2), (2, 3), (3, 0)]


class GranuleGeometry:
    """Pre-computed gnomonic geometry for a table of granules."""

    def __init__(self, granules: pd.DataFrame):
        self.g = granules.reset_index(drop=True)
        n = len(self.g)
        self.center = np.zeros((n, 3))
        self.e1 = np.zeros((n, 3))
        self.e2 = np.zeros((n, 3))
        self.paths: list[MplPath | None] = []
        self.quad = np.zeros(n, dtype=bool)
        self.corners = np.full((n, 4, 2), np.nan)
        self.corners3d: list[np.ndarray] = []
        for i, s in enumerate(self.g["polygon"]):
            lat, lon = parse_polygon(s)
            v = unit_vectors(lat, lon)
            self.corners3d.append(v)
            c = v.mean(axis=0)
            c /= np.linalg.norm(c)
            east = np.cross([0, 0, 1.0], c)
            if np.linalg.norm(east) < 1e-9:
                east = np.array([1.0, 0, 0])
            east /= np.linalg.norm(east)
            north = np.cross(c, east)
            self.center[i], self.e1[i], self.e2[i] = c, east, north
            xy = self._project(v, i)
            ok = bool(np.isfinite(xy).all())
            self.paths.append(MplPath(xy) if ok else None)
            if len(lat) == 4 and ok:
                self.quad[i] = True
                self.corners[i] = xy
        self.t0 = to_minutes(self.g["time_start"])
        self.t1 = to_minutes(self.g["time_end"])
        self._orient()

    def _project(self, v: np.ndarray, i: int) -> np.ndarray:
        c = self.center[i]
        d = v @ c
        d = np.where(d > 0.05, d, np.nan)  # points > ~87 deg away are not projectable
        return np.stack([(v @ self.e1[i]) / d, (v @ self.e2[i]) / d], axis=-1)

    def _shared_edge(self, i: int, j: int | None) -> int | None:
        if j is None or not self.quad[j]:
            return None
        dist = np.degrees(np.arccos(np.clip(self.corners3d[i] @ self.corners3d[j].T, -1, 1)))
        for k, (a, b) in enumerate(EDGES):
            if dist[a].min() < 0.5 and dist[b].min() < 0.5:
                return k
        return None

    def _orient(self) -> None:
        """Identify scan edges and their time order for every 4-corner granule.

        The granule that follows in the same orbit (start == this end) shares
        this granule's *later* scan edge; the preceding one shares the
        *earlier* scan edge. Granules with neither neighbour fall back to "the
        longer pair of opposite edges are scan edges" (both swaths are wider
        than a granule is long); their time order is then unknown and
        ``along`` is NaN.
        """
        n = len(self.g)
        self.later_mid = np.full((n, 2), np.nan)
        self.earlier_mid = np.full((n, 2), np.nan)
        self.side_a = np.full((n, 2), np.nan)
        self.side_b = np.full((n, 2), np.nan)
        self.orient_method = np.array(["none"] * n, dtype=object)
        by_start = {int(round(t)): i for i, t in enumerate(self.t0)}
        by_end = {int(round(t)): i for i, t in enumerate(self.t1)}
        agree, phys_agree = [], []
        for i in range(n):
            if not self.quad[i]:
                continue
            q = self.corners[i]
            mids = [(q[a] + q[b]) / 2 for a, b in EDGES]
            lengths = [np.linalg.norm(q[a] - q[b]) for a, b in EDGES]
            longer = 0 if lengths[0] + lengths[2] >= lengths[1] + lengths[3] else 1
            k_next = self._shared_edge(i, by_start.get(int(round(self.t1[i]))))
            k_prev = self._shared_edge(i, by_end.get(int(round(self.t0[i]))))
            # Physics rule: Aqua and S-NPP are sun-synchronous with a 13:30
            # ascending node, so they move north in daytime and south at night
            # (valid away from the polar turning points).
            v = self.corners3d[i]
            z = [(v[a, 2] + v[b, 2]) / 2 for a, b in EDGES]  # sin(lat) of edge midpoints
            dn = str(self.g.at[i, "day_night"]).upper()
            north_edge = longer if z[longer] > z[longer + 2] else longer + 2
            phys = north_edge if dn == "DAY" else (north_edge + 2) % 4 if dn == "NIGHT" else None
            if k_next is not None:
                later, method = k_next, "successor"
            elif k_prev is not None:
                later, method = (k_prev + 2) % 4, "predecessor"
            elif phys is not None and max(abs(np.degrees(np.arcsin(z[0]))), abs(np.degrees(np.arcsin(z[2])))) < 75:
                later, method = phys, "day_night_direction"
            else:
                later, method = longer, "longest_edges"
            if method in ("successor", "predecessor"):
                agree.append(later % 2 == longer % 2)
                if phys is not None:
                    phys_agree.append(phys == later)
            self.later_mid[i], self.earlier_mid[i] = mids[later], mids[(later + 2) % 4]
            self.side_a[i], self.side_b[i] = mids[(later + 1) % 4], mids[(later + 3) % 4]
            self.orient_method[i] = method
        self.edge_rule_agreement = (int(np.sum(agree)), len(agree))
        self.direction_rule_agreement = (int(np.sum(phys_agree)), len(phys_agree))

    def locate(self, lat, lon, t_minutes, window_min: float) -> pd.DataFrame:
        """All (point, granule) pairs where the point is inside the footprint and
        the granule observes it within ``window_min`` of ``t_minutes``.

        Returns columns: point, granule, xtrack, along, t_obs (minutes since epoch).
        """
        lat, lon, t = np.asarray(lat, float), np.asarray(lon, float), np.asarray(t_minutes, float)
        v = unit_vectors(lat, lon)
        order = np.argsort(t)
        ts = t[order]
        out = []
        for i in range(len(self.g)):
            path = self.paths[i]
            if path is None:
                continue
            lo = np.searchsorted(ts, self.t0[i] - window_min, "left")
            hi = np.searchsorted(ts, self.t1[i] + window_min, "right")
            if hi <= lo:
                continue
            cand = order[lo:hi]
            xy = self._project(v[cand], i)
            ok = np.isfinite(xy).all(axis=1)
            inside = np.zeros(len(cand), dtype=bool)
            inside[ok] = path.contains_points(xy[ok])
            if not inside.any():
                continue
            idx, pxy = cand[inside], xy[inside]
            if self.quad[i]:
                xt, al = self._quad_coords(i, pxy)
            else:
                xt = al = np.full(len(idx), np.nan)
            t_obs = self.t0[i] + np.where(np.isnan(al), 0.5, al) * (self.t1[i] - self.t0[i])
            keep = np.abs(t_obs - t[idx]) <= window_min
            if keep.any():
                out.append(pd.DataFrame({"point": idx[keep], "granule": i, "xtrack": xt[keep],
                                         "along": al[keep], "t_obs": t_obs[keep]}))
        if not out:
            return pd.DataFrame(columns=["point", "granule", "xtrack", "along", "t_obs"])
        return pd.concat(out, ignore_index=True)

    def _quad_coords(self, i: int, pxy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        later, earlier = self.later_mid[i], self.earlier_mid[i]
        a = later - earlier
        along = np.clip(((pxy - earlier) @ a) / (a @ a), 0, 1)
        if self.orient_method[i] == "longest_edges":
            along = np.full(len(pxy), np.nan)
        c = (self.side_a[i] + self.side_b[i]) / 2
        x = self.side_a[i] - c
        xtrack = ((pxy - c) @ x) / (x @ x)
        return np.clip(xtrack, -1.2, 1.2), along


def granules_for(settings: Settings, coll: GranuleCollection, region: Region, start: date, end: date) -> pd.DataFrame:
    frames = [fetch_granules(settings, coll, region, y) for y in range(start.year, end.year + 1)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    g = pd.concat(frames, ignore_index=True)
    lo, hi = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    return g[(g.time_end >= lo) & (g.time_start < hi)].reset_index(drop=True)
