"""Automatic, cache-aware FIRMS retrieval for an arbitrary location.

How a location is served
------------------------
1. The analysis needs every detection within ``max(radius, 100 km)`` of the
   point. That disc's bounding box is snapped outward to a 1-degree grid
   (``tile``); snapping makes nearby requests share one cache entry. Near the
   antimeridian the box is split in two.
2. The record is a single consistent sensor: **S-NPP VIIRS**.
   ``VIIRS_SNPP_SP`` (standard processing) is used up to its last available
   day; ``VIIRS_SNPP_NRT`` covers the period after that. MODIS and VIIRS
   counts are never combined.
3. For every 5-day request chunk (epoch-aligned, as in
   :mod:`firelens.acquisition`) the planner looks for an already-downloaded
   region whose box contains the tile and whose manifest holds that chunk.
   This reuses the five project regions (2012-2022) and earlier location
   tiles. Only chunks found nowhere are downloaded, into the tile's own cache.
4. Downloads run under one process-wide lock and one shared, rate-limited
   FIRMS client, so concurrent jobs never request the same chunk twice
   (after taking the lock, the plan is recomputed against the cache).
5. Coverage is tracked per day: a day counts as observed only if it was
   retrieved, lies inside the S-NPP record and the product's availability
   window, and is not on FIRMS's missing-data list. Months below 90%
   coverage are *unobserved* (NaN), never zero.

The NRT product has no ``type`` column, so static (non-vegetation) sources
cannot be removed from NRT rows directly. They are approximated by excluding
NRT detections in 0.01-degree cells where the standard product recorded a
non-vegetation ``type`` (volcano, static land source, offshore) during the
analysis window.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import acquisition, cleaning, country_archive, coverage
from .catalog import REGIONS, SNPP, WORLD, BBox, Region
from .config import Settings
from .firms_client import FirmsAuthError, FirmsClient
from .location_features import CONTEXT_KM, haversine_km
from .logging_utils import get_logger

log = get_logger(__name__)

SP, NRT = "VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"
PLATFORM = SNPP.name
SERIES_START = SNPP.start             # 2012-01-20
MIN_MONTH_COVERAGE = 0.9
TILE_DEG = 1.0
AVAILABILITY_MAX_AGE_H = 24
DET_COLUMNS = ["latitude", "longitude", "acq_date", "frp_mw", "fire_type", "daynight", "qc_usable"]

DOWNLOAD_LOCK = threading.Lock()        # availability-table refresh (short, global)
_TILE_LOCKS: dict[str, threading.Lock] = {}
_TILE_LOCKS_GUARD = threading.Lock()


def tiles_lock(tiles: list[BBox]) -> threading.Lock:
    """One lock per cache-tile set: different areas download concurrently (the shared, thread-safe FIRMS client
    still spaces requests and enforces the transaction budget); jobs for the same area never fetch a chunk twice."""
    key = "|".join(sorted(tile_region(t).name for t in tiles))
    with _TILE_LOCKS_GUARD:
        return _TILE_LOCKS.setdefault(key, threading.Lock())
PROCESS_LOCK = threading.RLock()        # processed-file writes vs reads across jobs
_CLIENT_LOCK = threading.Lock()
_CLIENT: FirmsClient | None = None
_LOCAL = threading.local()               # current job's progress tracker (for rate-limit events)


class DataUnavailableError(RuntimeError):
    """Required data is neither cached nor retrievable (e.g. no MAP_KEY)."""


# ---------------------------------------------------------------- geometry
def analysis_tiles(lat: float, lon: float, radius_km: float, tile_deg: float = TILE_DEG) -> list[BBox]:
    """Snapped bounding box(es) of the disc of ``max(radius, context)`` km."""
    ext = max(radius_km, CONTEXT_KM) + 5.0
    dlat = ext / 111.2
    s, n = lat - dlat, lat + dlat
    coslat = np.cos(np.radians(min(abs(lat) + dlat, 89.9)))
    dlon = ext / (111.32 * max(coslat, 1e-6))
    snap_lo = lambda v: float(np.floor(v / tile_deg) * tile_deg)
    snap_hi = lambda v: float(np.ceil(v / tile_deg) * tile_deg)
    s, n = max(snap_lo(s), -90.0), min(snap_hi(n), 90.0)
    if s <= -90 + 1e-9 or n >= 90 - 1e-9 or dlon >= 180:
        return [BBox(-180.0, s, 180.0, n)]
    w, e = snap_lo(lon - dlon), snap_hi(lon + dlon)
    if w < -180:
        return [BBox(w + 360, s, 180.0, n), BBox(-180.0, s, e, n)]
    if e > 180:
        return [BBox(w, s, 180.0, n), BBox(-180.0, s, e - 360, n)]
    return [BBox(w, s, e, n)]


def tile_region(b: BBox) -> Region:
    return Region("tile_" + "_".join(f"{v:g}" for v in (b.west, b.south, b.east, b.north)), b,
                  "Location-analysis cache tile")


def _contains(outer: BBox, inner: BBox) -> bool:
    tol = 1e-9
    return (outer.west <= inner.west + tol and outer.east >= inner.east - tol and
            outer.south <= inner.south + tol and outer.north >= inner.north - tol)


def _area(b: BBox) -> float:
    return (b.east - b.west) * (np.sin(np.radians(b.north)) - np.sin(np.radians(b.south)))


# ---------------------------------------------------------------- cache index
def known_regions(settings: Settings, source: str) -> list[Region]:
    """Every region with a download manifest for ``source`` (named or tile)."""
    out = []
    base = settings.raw_dir / source
    if not base.exists():
        return out
    for mf in sorted(base.glob("*/manifest.jsonl")):
        name = mf.parent.name
        if name in REGIONS:
            out.append(REGIONS[name])
            continue
        try:
            with mf.open(encoding="utf-8") as f:
                first = json.loads(f.readline())
            bbox = WORLD if first["bbox"] == "world" else BBox.parse(first["bbox"])
            out.append(Region(name, bbox, "cached"))
        except Exception as e:  # unreadable manifest: ignore as a cache source
            log.warning("Skipping manifest %s: %s", mf, e)
    return out


def covered_days(settings: Settings, source: str, region: Region, avail_end: date | None) -> set[int]:
    """Ordinal days held by successful manifest entries of ``region``."""
    days: set[int] = set()
    for ch, e in acquisition.cached_chunks(settings, source, region):
        if e.path is None or not (settings.raw_dir / e.path).exists():
            continue
        last = ch.end if e.complete or avail_end is None else min(ch.end, avail_end)
        days.update(range(ch.start.toordinal(), last.toordinal() + 1))
    return days


# ---------------------------------------------------------------- availability
def availability(settings: Settings, client_factory=None, allow_refresh: bool = True) -> tuple[pd.DataFrame, str]:
    """FIRMS data_availability table (cached up to 24 h). Returns (table, note)."""
    from .pipeline import availability_table
    cache = settings.cache_dir / "data_availability.csv"
    fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < AVAILABILITY_MAX_AGE_H * 3600
    if fresh or not allow_refresh:
        if not cache.exists():
            raise DataUnavailableError("No cached FIRMS data_availability table and refresh not allowed")
        return availability_table(settings), "cached" if fresh else "cached (stale; refresh disabled)"
    try:
        with DOWNLOAD_LOCK:
            client = (client_factory or shared_client)(settings)
            return availability_table(settings, client, refresh=True), "refreshed"
    except (FirmsAuthError, DataUnavailableError) as e:
        if cache.exists():
            return availability_table(settings), f"cached (refresh failed: {e})"
        raise DataUnavailableError(f"Cannot obtain FIRMS data availability: {e}") from e


def product_windows(avail: pd.DataFrame, start: date, end: date) -> list[tuple[str, date, date]]:
    """[(source, first_day, last_day)] of the S-NPP series inside start..end."""
    from .pipeline import availability_for
    out = []
    sp = availability_for(avail, SP)
    nrt = availability_for(avail, NRT)
    sp_end = None
    if sp:
        s, e = max(start, sp[0], SERIES_START), min(end, sp[1])
        sp_end = sp[1]
        if s <= e:
            out.append((SP, s, e))
    if nrt:
        s = max(start, nrt[0], SERIES_START, (sp_end + timedelta(days=1)) if sp_end else nrt[0])
        e = min(end, nrt[1])
        if s <= e:
            out.append((NRT, s, e))
    return out


# ---------------------------------------------------------------- plan
@dataclass
class Plan:
    tiles: list[BBox]
    start: date
    end: date
    products: list[tuple[str, date, date]]
    # (source, region name) -> set of ordinal days served from that cached region
    assigned: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    # (source, tile region name) -> list of (chunk start, chunk end) to download
    missing: dict[tuple[str, str], list[tuple[date, date]]] = field(default_factory=dict)
    regions: dict[str, Region] = field(default_factory=dict)

    @property
    def n_requests(self) -> int:
        return sum(len(v) for v in self.missing.values())


def make_plan(settings: Settings, tiles: list[BBox], start: date, end: date,
              products: list[tuple[str, date, date]]) -> Plan:
    plan = Plan(tiles, start, end, products)
    for source, s, e in products:
        regions = known_regions(settings, source)
        for tile in tiles:
            treg = tile_region(tile)
            plan.regions[treg.name] = treg
            cands = sorted([r for r in regions if _contains(r.bbox, tile)], key=lambda r: _area(r.bbox))
            cover = {r.name: covered_days(settings, source, r, e) for r in cands}
            for r in cands:
                plan.regions[r.name] = r
            for ch in acquisition.iter_chunks(s, e):
                lo, hi = max(ch.start, s), min(ch.end, e)
                need = set(range(lo.toordinal(), hi.toordinal() + 1))
                for r in cands:
                    if need <= cover[r.name]:
                        plan.assigned.setdefault((source, r.name), set()).update(need)
                        break
                else:
                    plan.missing.setdefault((source, treg.name), []).append((ch.start, ch.end))
    return plan


# ---------------------------------------------------------------- download
def shared_client(settings: Settings) -> FirmsClient:
    """Process-wide FIRMS client: one throttle and one transaction budget for all jobs."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT.s.map_key != settings.map_key:
            if not settings.map_key:
                raise FirmsAuthError("NASA_FIRMS_MAP_KEY is not configured; downloads are impossible")
            _CLIENT = FirmsClient(settings, sleep=_reporting_sleep)
        return _CLIENT


def _reporting_sleep(seconds: float) -> None:
    tr = getattr(_LOCAL, "tracker", None)
    if tr is None or seconds < 30:
        time.sleep(seconds)
        return
    tr.begin_wait(seconds, f"Waiting {seconds:.0f}s for the FIRMS transaction budget / retry back-off")
    try:
        time.sleep(seconds)
    finally:
        tr.end_wait()


def _runs(chunks: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Merge consecutive chunks into contiguous runs."""
    out = []
    for s, e in sorted(chunks):
        if out and s <= out[-1][1] + timedelta(days=1):
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def ensure_data(settings: Settings, tiles: list[BBox], start: date, end: date, tracker,
                allow_download: bool = True, client_factory=None) -> tuple[Plan, dict]:
    """Plan, download (if needed and allowed) and process. Returns (plan, info)."""
    from .pipeline import availability_for
    tracker.start_stage("plan", message="Checking FIRMS availability and the local cache")
    avail, avail_note = availability(settings, client_factory, allow_refresh=allow_download)
    products = product_windows(avail, start, end)
    plan = make_plan(settings, tiles, start, end, products)
    info = {"availability": avail_note, "chunks_downloaded": 0, "chunks_failed": 0, "rows_downloaded": 0,
            "chunks_reused_from_concurrent_job": 0, "download_skipped": False,
            "chunks_from_country_archive": 0, "country_archive": [],
            "product_availability": {sid: [str(d) for d in (availability_for(avail, sid) or ())] for sid in (SP, NRT)}}
    tracker.set_download_plan(plan.n_requests)
    tracker.end_stage()
    if plan.n_requests == 0:
        return plan, info
    if not allow_download:
        # Cache-only: still serve completed SP years from country archives already on local disk (no network).
        if getattr(settings, "use_country_archive", False):
            arch_log = []
            for (source, rname), chunks in list(plan.missing.items()):
                if source != SP:
                    continue
                try:
                    res = country_archive.fill_from_archive(settings, plan.regions[rname], chunks,
                                                            availability_for(avail, SP), client=None,
                                                            tracker=tracker, allow_download=False)
                except Exception as e:
                    if getattr(e, "abort_job", False):
                        raise
                    res = country_archive.ArchiveResult(reason=f"archive error: {type(e).__name__}")
                arch_log.append({"tile": rname, **res.as_dict()})
                if res.used:
                    info["chunks_from_country_archive"] += res.chunks_written
                    for y in res.years:
                        with PROCESS_LOCK:
                            cleaning.process(settings, SP, plan.regions[rname], date(y, 1, 1), date(y, 12, 31))
            info["country_archive"] = arch_log
            plan = make_plan(settings, tiles, start, end, products)
        if plan.n_requests:
            info["download_skipped"] = True
            tracker.warn(f"{plan.n_requests} request chunks are not cached and downloads are disabled; "
                         "those days are reported as unobserved", kind="partial_data")
        return plan, info

    tracker.start_stage("wait_download_slot", message="Waiting for another analysis of the same area")
    with tiles_lock(tiles):
        tracker.end_stage()
        before = plan.n_requests
        plan = make_plan(settings, tiles, start, end, products)  # another job may have fetched chunks
        info["chunks_reused_from_concurrent_job"] = before - plan.n_requests
        tracker.start_stage("download", total=plan.n_requests,
                            message=f"Downloading {plan.n_requests} FIRMS request chunks (5 days each)")
        client = (client_factory or shared_client)(settings)
        _LOCAL.tracker = tracker
        try:
            touched: dict[tuple[str, str], list[tuple[date, date]]] = {}
            if getattr(settings, "use_country_archive", False):
                # Completed SP years first from FIRMS yearly country archives (no Area API transactions);
                # whatever they cannot serve safely is downloaded from the Area API below.
                archive_log = []
                for (source, rname), chunks in list(plan.missing.items()):
                    if source != SP:
                        continue
                    tracker.advance(0, message="Checking FIRMS yearly country archives")
                    try:
                        res = country_archive.fill_from_archive(settings, plan.regions[rname], chunks,
                                                                availability_for(avail, SP), client=client,
                                                                tracker=tracker)
                    except FirmsAuthError:
                        raise
                    except Exception as e:  # never let the bulk path break an analysis
                        if getattr(e, "abort_job", False):
                            raise
                        log.warning("Country archive failed for %s: %s", rname, e)
                        res = country_archive.ArchiveResult(reason=f"archive error: {type(e).__name__}")
                    archive_log.append({"tile": rname, **res.as_dict()})
                    if res.used:
                        info["chunks_from_country_archive"] += res.chunks_written
                        touched.setdefault((SP, rname), []).extend(
                            (date(y, 1, 1), date(y, 12, 31)) for y in res.years)
                info["country_archive"] = archive_log
                plan = make_plan(settings, tiles, start, end, products)
            for (source, rname), chunks in plan.missing.items():
                region = plan.regions[rname]
                window = availability_for(avail, source)
                for rs, re_ in _runs(chunks):
                    def on_chunk(status, n_rows, _src=source):
                        tracker.advance(1, message=f"{_src}: {status} ({n_rows} rows)")
                    summ = acquisition.download(client, source, region, rs, re_, availability=window,
                                                on_chunk=on_chunk)
                    info["chunks_downloaded"] += summ.chunks_downloaded
                    info["chunks_failed"] += summ.chunks_failed
                    info["rows_downloaded"] += summ.rows_downloaded
                    touched.setdefault((source, rname), []).append((rs, re_))
        finally:
            _LOCAL.tracker = None
        tracker.end_stage()
        if info["chunks_failed"]:
            tracker.warn(f"{info['chunks_failed']} request chunks failed after retries; their days are "
                         "unobserved (coverage gaps are reported per month)", kind="partial_data")
        years = sorted({(src, rn, y) for (src, rn), runs in touched.items() for s, e in runs
                        for y in range(s.year, e.year + 1)})
        tracker.start_stage("process", total=len(years), message="Validating and storing downloaded records")
        for src, rn, y in years:
            with PROCESS_LOCK:
                cleaning.process(settings, src, plan.regions[rn], date(y, 1, 1), date(y, 12, 31))
            tracker.advance(1, message=f"{src} {rn} {y} processed")
        tracker.end_stage()
        plan = make_plan(settings, tiles, start, end, products)
    return plan, info


# ---------------------------------------------------------------- observations
@dataclass
class Observations:
    lat: float
    lon: float
    months: pd.PeriodIndex
    detections: pd.DataFrame          # usable vegetation detections within max(radius, context)
    coverage: pd.DataFrame            # per month: coverage_fraction, observed, product
    provenance: dict


def month_coverage(plan: Plan, missing_dates: pd.DataFrame | None, months: pd.PeriodIndex) -> pd.DataFrame:
    """Per-month observed fraction of days for the S-NPP series."""
    retrieved: dict[int, str] = {}
    for (source, _), days in plan.assigned.items():
        for d in days:
            retrieved.setdefault(d, source)
    missing = set()
    if missing_dates is not None and len(missing_dates):
        md = missing_dates[missing_dates["platform"] == PLATFORM]
        missing = {pd.Timestamp(d).date().toordinal() for d in md["date"]}
    rows = []
    for m in months:
        d0, d1 = m.start_time.date().toordinal(), m.end_time.date().toordinal()
        days = range(d0, d1 + 1)
        obs = [d for d in days if d in retrieved and d not in missing]
        prods = sorted({retrieved[d] for d in obs})
        rows.append({"month": str(m), "days_in_month": len(days), "days_observed": len(obs),
                     "days_firms_missing": sum(d in missing for d in days),
                     "coverage_fraction": len(obs) / len(days), "products": "+".join(prods)})
    out = pd.DataFrame(rows)
    out["observed"] = out["coverage_fraction"] >= MIN_MONTH_COVERAGE
    return out


def load_observations(settings: Settings, plan: Plan, lat: float, lon: float, radius_km: float,
                      months: pd.PeriodIndex) -> Observations:
    """Usable vegetation detections within ``max(radius, context)`` km of the point."""
    rmax = max(radius_km, CONTEXT_KM)
    frames, non_veg_sp, sources_used = [], [], []
    for (source, rname), days in sorted(plan.assigned.items()):
        if not days:
            continue
        region = plan.regions[rname]
        d0, d1 = date.fromordinal(min(days)), date.fromordinal(max(days))
        det = cleaning.load_detections(settings, region, source, d0, d1, columns=DET_COLUMNS, usable_only=True)
        if det.empty:
            sources_used.append({"product": source, "cache_region": rname, "first_day": str(d0),
                                 "last_day": str(d1), "n_days": len(days), "usable_rows_in_disc": 0})
            continue
        dist = haversine_km(lat, lon, det["latitude"], det["longitude"])
        det = det[dist <= rmax].assign(dist_km=dist[dist <= rmax], product=source)
        ords = det["acq_date"].to_numpy().astype("datetime64[D]").astype(np.int64) + 719163  # date.toordinal()
        det = det[np.isin(ords, np.fromiter(days, np.int64))]
        if source == SP:
            non_veg_sp.append(det[det["fire_type"].fillna(0) != 0])
        frames.append(det)
        sources_used.append({"product": source, "cache_region": rname, "first_day": str(d0), "last_day": str(d1),
                             "n_days": len(days), "usable_rows_in_disc": int(len(det))})
    det = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=DET_COLUMNS + ["dist_km", "product"])
    n_static_nrt = 0
    if len(det):
        is_nrt = det["product"].eq(NRT)
        static_cells = set()
        if non_veg_sp:
            nv = pd.concat(non_veg_sp)
            static_cells = set(zip(np.floor(nv["latitude"] * 100).astype(int), np.floor(nv["longitude"] * 100).astype(int)))
        cell = list(zip(np.floor(det["latitude"] * 100).astype(int), np.floor(det["longitude"] * 100).astype(int)))
        in_static = np.array([c in static_cells for c in cell], dtype=bool)
        veg_sp = ~is_nrt & det["fire_type"].fillna(-1).eq(0)
        veg_nrt = is_nrt & ~in_static
        n_static_nrt = int((is_nrt & in_static).sum())
        n_nonveg_sp = int((~is_nrt & ~det["fire_type"].fillna(-1).eq(0)).sum())
        det = det[veg_sp | veg_nrt].reset_index(drop=True)
    else:
        n_nonveg_sp = 0
    det["month"] = det["acq_date"].dt.to_period("M") if len(det) else pd.Series(dtype="period[M]")
    missing_dates = coverage.fetch_missing_dates(settings)
    cov = month_coverage(plan, missing_dates, months)
    prov = {"cache_sources": sources_used,
            "excluded_non_vegetation_sp_detections": n_nonveg_sp,
            "excluded_nrt_detections_at_known_static_sources": n_static_nrt,
            "firms_missing_dates_table": "used" if missing_dates is not None else "unavailable"}
    return Observations(lat, lon, months, det, cov, prov)
