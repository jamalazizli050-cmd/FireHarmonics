"""Historical data acquisition with local caching and incremental downloads.

Design
------
* A request window (start..end) is split into chunks of ``chunk_days`` (<= 5,
  the Area API maximum). Chunk boundaries are aligned to a fixed epoch so that
  overlapping or extended date ranges reuse previously downloaded chunks.
* Each chunk's raw CSV response is stored verbatim (immutable record of what
  NASA returned) under ``data/raw/<source>/<region>/<year>/``.
* A JSON-lines manifest per source/region records every chunk: dates, row
  count, checksum, retrieval time, and status. Completed Standard Processing
  chunks are never re-downloaded. NRT chunks are re-fetched while they may
  still change (until replaced by SP data).
* Failed chunks are recorded with status ``error`` so coverage gaps are
  explicit rather than silent.
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .catalog import Region, get_source
from .config import Settings
from .firms_client import MAX_DAY_RANGE, FirmsAPIError, FirmsAuthError, FirmsClient
from .logging_utils import get_logger

log = get_logger(__name__)

CHUNK_EPOCH = date(2000, 1, 1)
NRT_SETTLE_DAYS = 3


@dataclass(frozen=True)
class Chunk:
    start: date
    days: int

    @property
    def end(self) -> date:  # inclusive
        return self.start + timedelta(days=self.days - 1)

    @property
    def id(self) -> str:
        return f"{self.start.isoformat()}_{self.days}d"


def iter_chunks(start: date, end: date, chunk_days: int = MAX_DAY_RANGE) -> Iterator[Chunk]:
    """Epoch-aligned chunks covering ``start..end`` (inclusive)."""
    if not 1 <= chunk_days <= MAX_DAY_RANGE:
        raise ValueError(f"chunk_days must be 1..{MAX_DAY_RANGE}")
    if end < start:
        raise ValueError("end date precedes start date")
    offset = (start - CHUNK_EPOCH).days // chunk_days * chunk_days
    cur = CHUNK_EPOCH + timedelta(days=offset)
    while cur <= end:
        yield Chunk(cur, chunk_days)
        cur += timedelta(days=chunk_days)


@dataclass
class ManifestEntry:
    source: str
    region: str
    bbox: str
    chunk_start: str
    chunk_days: int
    status: str            # "ok" | "error"
    n_rows: int
    path: str | None       # relative to raw_dir
    sha256: str | None
    retrieved_at: str
    message: str = ""
    # False when the chunk extends past the product's availability window at
    # retrieval time (data for the later days may still appear).
    complete: bool = True


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, ManifestEntry] = {}
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        e = ManifestEntry(**json.loads(line))
                        self.entries[f"{e.chunk_start}_{e.chunk_days}d"] = e  # last write wins

    def get(self, chunk: Chunk) -> ManifestEntry | None:
        return self.entries.get(chunk.id)

    def append(self, entry: ManifestEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")
        self.entries[f"{entry.chunk_start}_{entry.chunk_days}d"] = entry


def region_dir(settings: Settings, source_id: str, region: Region) -> Path:
    return settings.raw_dir / source_id / region.name


def manifest_for(settings: Settings, source_id: str, region: Region) -> Manifest:
    return Manifest(region_dir(settings, source_id, region) / "manifest.jsonl")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def _needs_download(entry: ManifestEntry | None, chunk: Chunk, standard: bool, raw_dir: Path,
                    today: date) -> bool:
    if entry is None or entry.status != "ok" or not entry.complete:
        return True
    if entry.path is None or not (raw_dir / entry.path).exists():
        return True
    if standard:
        return False
    # NRT: refresh until the chunk is old enough to have settled.
    retrieved = datetime.fromisoformat(entry.retrieved_at).date()
    return retrieved <= chunk.end + timedelta(days=NRT_SETTLE_DAYS) and today > retrieved


@dataclass
class DownloadSummary:
    source: str
    region: str
    chunks_total: int = 0
    chunks_downloaded: int = 0
    chunks_cached: int = 0
    chunks_failed: int = 0
    rows_downloaded: int = 0
    skipped_unavailable: int = 0


def download(client: FirmsClient, source_id: str, region: Region, start: date, end: date,
             chunk_days: int = MAX_DAY_RANGE, availability: tuple[date, date] | None = None,
             force: bool = False, on_chunk=None) -> DownloadSummary:
    """Download (incrementally) all chunks for ``source_id`` over ``region``.

    ``on_chunk(status, n_rows)`` is called after every chunk that needed a
    request (status ``"ok"`` or ``"error"``); cached chunks are not reported.
    """
    src = get_source(source_id)
    settings = client.s
    manifest = manifest_for(settings, source_id, region)
    out = DownloadSummary(source_id, region.name)
    today = datetime.now(timezone.utc).date()

    todo: list[Chunk] = []
    for chunk in iter_chunks(start, end, chunk_days):
        out.chunks_total += 1
        if availability and (chunk.end < availability[0] or chunk.start > availability[1]):
            out.skipped_unavailable += 1
            continue
        entry = manifest.get(chunk)
        if not force and not _needs_download(entry, chunk, src.is_standard, settings.raw_dir, today):
            out.chunks_cached += 1
            continue
        todo.append(chunk)

    def store(chunk: Chunk, text: str | None, err: Exception | None) -> None:
        """Write one chunk's outcome (always on the calling thread: files, manifest, callbacks)."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if err is not None:
            log.error("Chunk %s %s %s failed: %s", source_id, region.name, chunk.id, err)
            manifest.append(ManifestEntry(source_id, region.name, region.bbox.to_api(),
                                          chunk.start.isoformat(), chunk.days, "error", 0, None,
                                          None, now, str(err)[:300]))
            out.chunks_failed += 1
            if on_chunk:
                on_chunk("error", 0)
            return
        rel = Path(source_id) / region.name / str(chunk.start.year) / f"{source_id}_{region.name}_{chunk.id}.csv"
        _atomic_write(settings.raw_dir / rel, text)
        n_rows = max(text.rstrip("\n").count("\n"), 0)
        complete = availability is None or chunk.end <= availability[1]
        manifest.append(ManifestEntry(source_id, region.name, region.bbox.to_api(),
                                      chunk.start.isoformat(), chunk.days, "ok", n_rows,
                                      rel.as_posix(), hashlib.sha256(text.encode()).hexdigest(), now,
                                      "" if complete else f"partial: availability ends {availability[1]}",
                                      complete))
        out.chunks_downloaded += 1
        out.rows_downloaded += n_rows
        log.info("%s %s %s: %d rows", source_id, region.name, chunk.id, n_rows)
        if on_chunk:
            on_chunk("ok", n_rows)

    def fetch(chunk: Chunk) -> str:
        return client.area_csv(source_id, region.bbox, chunk.start, chunk.days)

    workers = max(1, min(int(getattr(settings, "download_workers", 1) or 1), len(todo) or 1))
    if workers == 1:
        for chunk in todo:
            try:
                text = fetch(chunk)
            except FirmsAuthError:
                raise
            except FirmsAPIError as e:
                store(chunk, None, e)
                continue
            store(chunk, text, None)
    else:
        # Only the HTTP requests run in parallel; results are stored in chunk order as they complete.
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="firms-dl")
        try:
            pending = {pool.submit(fetch, chunk): chunk for chunk in todo}
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for fut in sorted(done, key=lambda f: pending[f].start):
                    chunk = pending.pop(fut)
                    try:
                        text = fut.result()
                    except FirmsAuthError:
                        for other in pending:
                            other.cancel()
                        raise
                    except FirmsAPIError as e:
                        store(chunk, None, e)
                        continue
                    store(chunk, text, None)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    log.info("Download summary: %s", out)
    return out


def cached_chunks(settings: Settings, source_id: str, region: Region, start: date | None = None,
                  end: date | None = None) -> list[tuple[Chunk, ManifestEntry]]:
    """Successful manifest entries overlapping ``start..end``."""
    manifest = manifest_for(settings, source_id, region)
    result = []
    for e in manifest.entries.values():
        if e.status != "ok":
            continue
        ch = Chunk(date.fromisoformat(e.chunk_start), e.chunk_days)
        if start and ch.end < start:
            continue
        if end and ch.start > end:
            continue
        result.append((ch, e))
    return sorted(result, key=lambda t: t[0].start)
