"""Bulk cache source: FIRMS yearly per-country VIIRS S-NPP archives.

NASA FIRMS publishes, for every completed year, the standard-processing (SP) VIIRS S-NPP detections of each country
as one CSV (``/data/country/viirs-snpp/<YEAR>/viirs-snpp_<YEAR>_<Country>.csv``). Downloading those files costs no
Area API transactions, so a new location's history no longer needs ~73 Area API requests per year.

Equivalence: for a tile fully inside the United Kingdom, the country files reproduce the Area API SP responses
exactly (2,108 detections over 2019, 2023 and 2025, every column; the archive only zero-pads ``acq_time``, which is
normalised here). Chunks are therefore written into the tile's normal raw cache with the same CSV columns and a
manifest entry, and the rest of the pipeline (cleaning, coverage, patterns, forecast) is unchanged.

Safety rules (the archive is used only when it can be complete for the tile):
* Only ``VIIRS_SNPP_SP`` chunks that lie entirely inside completed years with archive files and inside the SP
  availability window.
* Every FIRMS country whose territory (Natural Earth 1:50m, +0.25 deg margin) touches the tile must have a file for
  the year; if a tile touches a territory without a FIRMS file, the archive is not used for that tile.
* A spot check compares a few chunks against the Area API before anything is written; on any mismatch the archive is
  rejected for that tile and the normal API download runs.
Country-year files are kept locally as Parquet (all columns as strings) so later tiles in the same country are served
without network access.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import requests

from .acquisition import Chunk, ManifestEntry, _atomic_write, iter_chunks, manifest_for
from .catalog import BBox, Region
from .config import Settings
from .firms_client import FirmsAPIError, FirmsAuthError
from .logging_utils import get_logger

log = get_logger(__name__)

SOURCE = "VIIRS_SNPP_SP"
PRODUCT_DIR = "viirs-snpp"
COLUMNS = ["latitude", "longitude", "bright_ti4", "scan", "track", "acq_date", "acq_time", "satellite", "instrument",
           "confidence", "version", "bright_ti5", "frp", "daynight", "type"]
MODIS_COLUMNS = ["latitude", "longitude", "brightness", "scan", "track", "acq_date", "acq_time", "satellite",
                 "instrument", "confidence", "version", "bright_t31", "frp", "daynight", "type"]
# FIRMS directory name -> (CSV columns, FireLens standard-processing source id)
PRODUCTS = {"viirs-snpp": (COLUMNS, "VIIRS_SNPP_SP"), "viirs-jpss1": (COLUMNS, "VIIRS_NOAA20_SP"),
            "modis": (MODIS_COLUMNS, "MODIS_SP")}
COMPARE = ["latitude", "longitude", "acq_date", "acq_time", "bright_ti4", "bright_ti5", "frp", "scan", "track",
           "satellite", "confidence", "version", "daynight", "type"]
FIRST_ARCHIVE_YEAR = 2012
_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


class ArchiveUnavailable(RuntimeError):
    """The archive cannot serve this tile/year (missing file, unresolved territory, download failure)."""


# ------------------------------------------------------------------ country table
@lru_cache(maxsize=1)
def _table() -> tuple[dict[tuple[int, int], tuple[str, ...]], frozenset[tuple[int, int]]]:
    raw = json.loads((Path(__file__).with_name("firms_country_cells.json")).read_text(encoding="utf-8"))
    key = lambda s: tuple(int(v) for v in s.split(","))
    cells = {key(k): tuple(v) for k, v in raw["cells"].items()}
    return cells, frozenset(key(k) for k in raw["unresolved_cells"])


def countries_for_bbox(b: BBox) -> list[str]:
    """FIRMS country files needed to cover ``b`` completely. Raises ArchiveUnavailable if any part is unresolved."""
    import math
    cells, unresolved = _table()
    names: set[str] = set()
    for lon in range(math.floor(b.west), math.ceil(b.east)):
        for lat in range(math.floor(b.south), math.ceil(b.north)):
            c = (((lon + 180) % 360) - 180, lat)
            if c in unresolved:
                raise ArchiveUnavailable(f"tile touches a territory without a FIRMS country file (cell {c})")
            names.update(cells.get(c, ()))
    if not names:
        raise ArchiveUnavailable("tile is not covered by any FIRMS country file (open ocean)")
    return sorted(names)


# ------------------------------------------------------------------ files
def archive_url(settings: Settings, name: str, year: int, product: str = PRODUCT_DIR) -> str:
    return f"{settings.base_url.rstrip('/')}/data/country/{product}/{year}/{product}_{year}_{name}.csv"


def archive_path(settings: Settings, name: str, year: int, product: str = PRODUCT_DIR) -> Path:
    return settings.cache_dir / "firms_country" / product / str(year) / f"{name}.parquet"


def is_resolved(settings: Settings, name: str, year: int, product: str = PRODUCT_DIR) -> bool:
    """True when the file is cached, or NASA has confirmed (404) that it does not exist."""
    p = archive_path(settings, name, year, product)
    return p.exists() or p.with_suffix(".absent").exists()


def _lock_for(key: str) -> threading.Lock:
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(key, threading.Lock())


def ensure_country_year(settings: Settings, name: str, year: int, session: requests.Session | None = None,
                        on_progress=None, product: str = PRODUCT_DIR, allow_download: bool = True) -> Path:
    """Download + convert one country-year file once (thread-safe). Raises ArchiveUnavailable if NASA has none."""
    columns = PRODUCTS[product][0]
    out = archive_path(settings, name, year, product)
    with _lock_for(str(out)):
        if out.exists():
            return out
        if out.with_suffix(".absent").exists():
            raise ArchiveUnavailable(f"no FIRMS archive file for {name} {year}")
        if not allow_download:
            raise ArchiveUnavailable(f"{product} {name} {year} is not cached and downloads are disabled")
        url = archive_url(settings, name, year, product)
        sess = session or requests.Session()
        try:
            resp = sess.get(url, stream=True, timeout=settings.request_timeout_s)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise ArchiveUnavailable(f"download of {name} {year} failed: {type(e).__name__}") from e
        if resp.status_code == 404:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.with_suffix(".absent").write_text("404 from FIRMS", encoding="utf-8")
            raise ArchiveUnavailable(f"no FIRMS archive file for {name} {year}")
        if resp.status_code != 200:
            raise ArchiveUnavailable(f"FIRMS archive {name} {year} returned HTTP {resp.status_code}")
        tmp = out.with_suffix(".csv.part")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        try:
            with tmp.open("wb") as f:
                for block in resp.iter_content(1 << 20):
                    f.write(block)
                    got += len(block)
                    if on_progress and (got // (8 << 20)) != ((got - len(block)) // (8 << 20)):
                        on_progress(name, year, got, total)
            if total and got != total:
                raise ArchiveUnavailable(f"FIRMS archive {name} {year} truncated ({got}/{total} bytes)")
            with tmp.open("rb") as f:
                head = f.read(200).decode("utf-8", "replace")
            if not head.startswith("latitude,longitude,"):
                raise ArchiveUnavailable(f"FIRMS archive {name} {year} has an unexpected format")
            opts = pacsv.ConvertOptions(column_types={c: pa.string() for c in columns}, strings_can_be_null=False)
            tbl = pacsv.read_csv(tmp, convert_options=opts)
            missing = [c for c in columns if c not in tbl.column_names]
            if missing:
                raise ArchiveUnavailable(f"FIRMS archive {name} {year} lacks columns {missing}")
            tbl = tbl.select(columns)
            # The archive zero-pads acq_time ("0259"); the Area API does not ("259").
            acq = pc.cast(pc.cast(tbl["acq_time"], pa.int32()), pa.string())
            tbl = tbl.set_column(columns.index("acq_time"), "acq_time", acq)
            tbl = tbl.append_column("_lat", pc.cast(tbl["latitude"], pa.float64()))
            tbl = tbl.append_column("_lon", pc.cast(tbl["longitude"], pa.float64()))
            tbl = tbl.sort_by([("_lat", "ascending"), ("_lon", "ascending")])
            part = out.with_suffix(".parquet.part")
            pq.write_table(tbl, part, compression="zstd", row_group_size=50_000)
            part.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
        log.info("FIRMS country archive %s %s cached (%d bytes)", name, year, got)
        return out


def tile_rows(settings: Settings, names: list[str], year: int, bbox: BBox, session=None, on_progress=None,
              product: str = PRODUCT_DIR, allow_download: bool = True) -> pd.DataFrame:
    """All archive rows of ``year`` inside ``bbox`` (inclusive, like the Area API box) from the given countries."""
    columns = PRODUCTS[product][0]
    frames = []
    for name in names:
        path = ensure_country_year(settings, name, year, session=session, on_progress=on_progress, product=product,
                                   allow_download=allow_download)
        flt = [("_lat", ">=", bbox.south), ("_lat", "<=", bbox.north), ("_lon", ">=", bbox.west), ("_lon", "<=", bbox.east)]
        frames.append(pq.read_table(path, filters=flt).to_pandas())
    if not frames:
        return pd.DataFrame(columns=columns)
    df = pd.concat(frames, ignore_index=True)
    # A detection appears once per country; de-duplicate defensively (identical rows in two files).
    return df.drop_duplicates(subset=columns)


def _csv_text(rows: pd.DataFrame) -> str:
    buf = io.StringIO()
    rows.sort_values(["acq_date", "acq_time", "latitude", "longitude"], key=None)[COLUMNS].to_csv(
        buf, index=False, lineterminator="\n")
    return buf.getvalue()


def _row_set(df: pd.DataFrame) -> set[tuple]:
    if df.empty:
        return set()
    d = df.copy()
    d["acq_time"] = pd.to_numeric(d["acq_time"], errors="coerce").astype("Int64").astype(str)
    return set(map(tuple, d[COMPARE].astype(str).itertuples(index=False, name=None)))


# ------------------------------------------------------------------ filling a tile
@dataclass
class ArchiveResult:
    used: bool = False
    countries: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    chunks_written: int = 0
    spot_check: str = "not_run"
    reason: str = ""

    def as_dict(self) -> dict:
        return {"used": self.used, "countries": self.countries, "years": self.years,
                "chunks_from_archive": self.chunks_written, "spot_check": self.spot_check, "reason": self.reason}


def fill_from_archive(settings: Settings, region: Region, chunks: list[tuple[date, date]],
                      window: tuple[date, date] | None, client=None, session=None, tracker=None,
                      today: date | None = None, allow_download: bool = True) -> ArchiveResult:
    """Serve missing SP chunks of ``region`` (a FireLens tile) from the yearly country archives.

    ``client`` (a FirmsClient) enables the Area API spot check. Without it the archive is still used, and the result
    records ``spot_check: "not_run"``.
    """
    res = ArchiveResult()
    today = today or datetime.now(timezone.utc).date()
    try:
        names = countries_for_bbox(region.bbox)
    except ArchiveUnavailable as e:
        res.reason = str(e)
        return res
    res.countries = names
    last_complete_year = today.year - 1
    eligible: list[Chunk] = []
    for s, e in chunks:
        for ch in iter_chunks(s, e):
            if ch.start.year < FIRST_ARCHIVE_YEAR or ch.end.year > last_complete_year:
                continue
            if window and (ch.start < window[0] or ch.end > window[1]):
                continue
            eligible.append(ch)
    if not eligible:
        res.reason = "no missing chunk lies inside completed archive years"
        return res

    def progress(name, year, got, total):
        if tracker is not None and total:
            tracker.advance(0, message=f"FIRMS yearly archive {name.replace('_', ' ')} {year}: "
                                       f"{got / 1e6:.0f} / {total / 1e6:.0f} MB")

    years = sorted({y for ch in eligible for y in range(ch.start.year, ch.end.year + 1)})
    rows_by_year: dict[int, pd.DataFrame] = {}
    for y in years:
        try:
            rows_by_year[y] = tile_rows(settings, names, y, region.bbox, session=session, on_progress=progress,
                                        allow_download=allow_download)
        except ArchiveUnavailable as e:
            log.info("Country archive not usable for %s %s: %s", region.name, y, e)
    usable = [ch for ch in eligible if all(y in rows_by_year for y in range(ch.start.year, ch.end.year + 1))]
    if not usable:
        res.reason = "no archive files for the needed years"
        return res
    rows = pd.concat([rows_by_year[y] for y in sorted(rows_by_year)], ignore_index=True)
    rows = rows[rows["acq_date"].str.len() == 10]

    def chunk_rows(ch: Chunk) -> pd.DataFrame:
        lo, hi = ch.start.isoformat(), ch.end.isoformat()
        return rows[(rows["acq_date"] >= lo) & (rows["acq_date"] <= hi)]

    # Spot check against the Area API on the busiest chunks (most informative about missing territory).
    if client is not None and settings.archive_spot_check_chunks > 0:
        counts = sorted(((len(chunk_rows(ch)), ch.start, ch) for ch in usable), reverse=True)
        sample = [ch for _, _, ch in counts[: settings.archive_spot_check_chunks]]
        try:
            for ch in sample:
                api = pd.read_csv(io.StringIO(client.area_csv(SOURCE, region.bbox, ch.start, ch.days)),
                                  dtype=str, keep_default_na=False)
                if _row_set(api) != _row_set(chunk_rows(ch)):
                    res.spot_check = f"failed ({ch.id}: API {len(api)} rows, archive {len(chunk_rows(ch))} rows)"
                    res.reason = "archive differs from the Area API for this tile; using the API instead"
                    log.warning("Country archive rejected for %s: %s", region.name, res.spot_check)
                    return res
        except FirmsAuthError:
            raise
        except FirmsAPIError as e:
            res.spot_check = f"not_completed ({e})"
        else:
            res.spot_check = f"passed ({len(sample)} chunks identical to the Area API)"

    manifest = manifest_for(settings, SOURCE, region)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    msg = f"FIRMS yearly country archive ({', '.join(names)})"
    for ch in usable:
        text = _csv_text(chunk_rows(ch))
        rel = Path(SOURCE) / region.name / str(ch.start.year) / f"{SOURCE}_{region.name}_{ch.id}.csv"
        _atomic_write(settings.raw_dir / rel, text)
        manifest.append(ManifestEntry(SOURCE, region.name, region.bbox.to_api(), ch.start.isoformat(), ch.days, "ok",
                                      max(text.rstrip("\n").count("\n"), 0), rel.as_posix(),
                                      hashlib.sha256(text.encode()).hexdigest(), now, msg, True))
        if tracker is not None:
            tracker.advance(1, message=f"{SOURCE}: {ch.id} from yearly country archive")
    res.used = True
    res.years = sorted({ch.start.year for ch in usable})
    res.chunks_written = len(usable)
    return res

