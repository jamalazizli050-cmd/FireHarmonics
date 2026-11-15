"""Background job manager for location analyses (importable; no HTTP server).

* ``start_analysis(...)`` validates the request synchronously (invalid input
  raises :class:`~firelens.inference.InvalidRequestError`) and returns a new
  job ID immediately; the analysis runs on a bounded thread pool.
* ``get_job_status`` / ``get_events`` support polling; ``subscribe`` registers
  a callback (for SSE/WebSocket bridges); ``stream_events`` is an async
  iterator for asyncio back-ends.
* Every job runs the same :func:`firelens.inference.run_analysis` as the
  synchronous ``analyze_location``.
* NASA downloads are serialised by one process-wide lock and one shared,
  rate-limited FIRMS client (:mod:`firelens.location_data`); after acquiring
  the lock a job re-plans against the cache, so concurrent jobs never fetch
  the same chunk twice. CPU stages of different jobs run in parallel up to
  ``max_workers``.

Persistence (implemented and tested): job records are written as JSON under
``data/jobs/`` on creation, on every stage change and warning, and at
completion/failure. Results of completed jobs are stored alongside, so
status and results survive a process restart. Jobs that were queued or
running when the process stopped are marked ``failed`` with error type
``interrupted`` (recoverable) when a new manager loads the directory; call
``retry_job`` to resubmit them (already-downloaded chunks are reused from
the cache). Live callbacks and async streams do not survive a restart, and
there is no cross-process download lock: two *processes* analysing the same
new tile at the same time may both download it.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings, load_settings
from .logging_utils import get_logger
from .progress import ProgressTracker

log = get_logger(__name__)

TERMINAL = {"completed", "failed"}
PERSIST_EVENT_TYPES = {"status", "stage", "stage_done", "warning", "completed", "failed"}


class JobNotFound(KeyError):
    pass


class JobNotFinished(RuntimeError):
    pass


class JobFailed(RuntimeError):
    def __init__(self, error: dict):
        super().__init__(error.get("message", "job failed"))
        self.error = error


def classify_error(e: Exception) -> tuple[str, bool]:
    """(error_type, recoverable) for a failure inside an analysis."""
    from .firms_client import FirmsAuthError, FirmsRateLimitError, FirmsRequestError, FirmsTransientError
    from .location_data import DataUnavailableError
    import requests
    if isinstance(e, FirmsAuthError):
        return "firms_auth", True
    if isinstance(e, FirmsRateLimitError):
        return "firms_rate_limited", True
    if isinstance(e, (FirmsTransientError, requests.RequestException)):
        return "network", True
    if isinstance(e, FirmsRequestError):
        return "firms_request_rejected", False
    if isinstance(e, DataUnavailableError):
        return "data_unavailable", True
    if isinstance(e, FileNotFoundError):
        return "missing_file", True
    if isinstance(e, ValueError):
        return "invalid_request", False
    return "internal_error", False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    job_id: str
    request: dict
    allow_download: bool
    created_at: str
    tracker: ProgressTracker | None = None
    retry_of: str | None = None
    result: dict | None = None
    stored: dict | None = None            # snapshot loaded from disk (restored jobs)
    done: threading.Event = field(default_factory=threading.Event)


class JobManager:
    def __init__(self, settings: Settings | None = None, max_workers: int = 2, store_dir: str | Path | None = None,
                 persist: bool = True):
        self.settings = settings or load_settings(require_key=False)
        self.persist = persist
        self.store = Path(store_dir) if store_dir else self.settings.data_dir / "jobs"
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="firelens-job")
        if persist:
            self.store.mkdir(parents=True, exist_ok=True)
            self._restore()

    # ------------------------------------------------------------ persistence
    def _path(self, job_id: str, suffix: str = ".json") -> Path:
        return self.store / f"{job_id}{suffix}"

    def _write(self, path: Path, obj: dict) -> None:
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def _save(self, job: Job) -> None:
        if not self.persist:
            return
        rec = {"job_id": job.job_id, "request": job.request, "allow_download": job.allow_download,
               "created_at": job.created_at, "retry_of": job.retry_of,
               "snapshot": job.tracker.snapshot() if job.tracker else (job.stored or {}).get("snapshot"),
               "events_tail": (job.tracker.events[-30:] if job.tracker else (job.stored or {}).get("events_tail", [])),
               "saved_at": _now()}
        try:
            self._write(self._path(job.job_id), rec)
        except OSError as e:  # persistence is best-effort; the in-memory state stays authoritative
            log.warning("could not persist job %s: %s", job.job_id, e)

    def _restore(self) -> None:
        for p in sorted(self.store.glob("*.json")):
            if p.name.endswith(".result.json"):
                continue
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                log.warning("skipping unreadable job record %s: %s", p, e)
                continue
            snap = rec.get("snapshot") or {}
            if snap.get("status") not in TERMINAL:
                snap.update({"status": "failed", "error": {
                    "type": "interrupted", "recoverable": True, "failed_stage": snap.get("stage"),
                    "message": "the process stopped before this job finished; call retry_job() to resubmit"},
                    "finished_at": _now(), "message": "Failed: interrupted by process restart"})
                rec["snapshot"] = snap
            job = Job(rec["job_id"], rec["request"], rec.get("allow_download", True), rec["created_at"],
                      retry_of=rec.get("retry_of"), stored=rec)
            job.done.set()
            self._jobs[job.job_id] = job
            self._write(p, rec)

    # ------------------------------------------------------------ submission
    def start_analysis(self, latitude, longitude, radius_km=25.0, target_month=None, *, history_years=None,
                       allow_download: bool = True, callback=None, _retry_of: str | None = None) -> str:
        from .inference import DEFAULT_HISTORY_YEARS, validate_request
        req = validate_request(latitude, longitude, radius_km, target_month,
                               DEFAULT_HISTORY_YEARS if history_years is None else history_years)
        return self._submit(req, allow_download, callback, _retry_of)

    def _submit(self, req: dict, allow_download: bool, callback, retry_of: str | None) -> str:
        job_id = uuid.uuid4().hex
        job = Job(job_id, req, allow_download, _now(), retry_of=retry_of)

        def on_event(ev, _job=job):
            if ev["type"] in PERSIST_EVENT_TYPES:
                self._save(_job)

        job.tracker = ProgressTracker(job_id, callbacks=[on_event] + ([callback] if callback else []))
        with self._lock:
            self._jobs[job_id] = job
        job.tracker._emit("status")          # 'queued' event
        self._pool.submit(self._run, job)
        return job_id

    def _run(self, job: Job) -> None:
        from .inference import run_analysis
        try:
            job.result = run_analysis(job.request, job.tracker, self.settings, allow_download=job.allow_download)
            if self.persist:
                self._write(self._path(job.job_id, ".result.json"), job.result)
            self._save(job)
        except Exception as e:  # explicit failure, never a silent partial result
            et, rec = classify_error(e)
            log.exception("job %s failed (%s)", job.job_id, et)
            job.tracker.fail(et, str(e), rec)
        finally:
            job.done.set()

    def retry_job(self, job_id: str, allow_download: bool | None = None) -> str:
        """Resubmit a failed job with the same parameters; returns a new job ID."""
        job = self._get(job_id)
        st = self.get_job_status(job_id)
        if st["status"] != "failed":
            raise ValueError(f"job {job_id} is {st['status']}; only failed jobs can be retried")
        from .inference import validate_request
        r = job.request
        req = validate_request(r["latitude"], r["longitude"], r["radius_km"], r["target_month"], r["history_years"])
        return self._submit(req, job.allow_download if allow_download is None else allow_download, None, job_id)

    # ------------------------------------------------------------ queries
    def _get(self, job_id: str) -> Job:
        with self._lock:
            if job_id not in self._jobs:
                raise JobNotFound(job_id)
            return self._jobs[job_id]

    def get_job_status(self, job_id: str) -> dict:
        job = self._get(job_id)
        snap = job.tracker.snapshot() if job.tracker else dict((job.stored or {}).get("snapshot") or {})
        snap.update({"job_id": job_id, "request": job.request, "created_at": job.created_at,
                     "retry_of": job.retry_of, "restored_from_disk": job.tracker is None,
                     "result_available": snap.get("status") == "completed"})
        return snap

    def get_job_result(self, job_id: str, timeout: float | None = None) -> dict:
        """The completed analysis. Waits up to ``timeout`` seconds if given.

        Raises JobNotFinished (still queued/running) or JobFailed (with ``.error``).
        """
        job = self._get(job_id)
        if timeout is not None:
            job.done.wait(timeout)
        st = self.get_job_status(job_id)
        if st["status"] == "completed":
            if job.result is None and self.persist:
                job.result = json.loads(self._path(job_id, ".result.json").read_text(encoding="utf-8"))
            return job.result
        if st["status"] == "failed":
            raise JobFailed(st.get("error") or {})
        raise JobNotFinished(f"job {job_id} is {st['status']} ({st.get('progress_percent')}%)")

    def get_events(self, job_id: str, after_seq: int = 0) -> list[dict]:
        job = self._get(job_id)
        evs = job.tracker.events if job.tracker else (job.stored or {}).get("events_tail", [])
        return [e for e in evs if e["seq"] > after_seq]

    def list_jobs(self) -> list[dict]:
        with self._lock:
            ids = list(self._jobs)
        return [{k: v for k, v in self.get_job_status(i).items() if k != "warnings"} for i in ids]

    # ------------------------------------------------------------ push interfaces
    def subscribe(self, job_id: str, callback, replay: bool = True):
        """Call ``callback(event)`` for every future event (and past ones if ``replay``).

        Returns an ``unsubscribe()`` function. Callbacks run on the worker thread.
        """
        job = self._get(job_id)
        if job.tracker is None:  # restored job: replay what was stored
            for e in self.get_events(job_id):
                callback(e)
            return lambda: None
        with job.tracker._lock:  # replay and registration are atomic: no event is lost or duplicated
            if replay:
                for e in list(job.tracker.events):
                    callback(e)
            job.tracker.add_callback(callback)
        return lambda: job.tracker.remove_callback(callback)

    async def stream_events(self, job_id: str, after_seq: int = 0):
        """Async iterator of events (for SSE/WebSocket handlers); ends after completed/failed."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        unsub = self.subscribe(job_id, lambda e: loop.call_soon_threadsafe(q.put_nowait, e), replay=True)
        try:
            if self.get_job_status(job_id).get("restored_from_disk"):
                while not q.empty():
                    e = q.get_nowait()
                    if e["seq"] > after_seq:
                        yield e
                return
            while True:
                e = await q.get()
                if e["seq"] <= after_seq:
                    continue
                yield e
                if e["type"] in ("completed", "failed"):
                    return
        finally:
            unsub()

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


_DEFAULT: JobManager | None = None
_DEFAULT_LOCK = threading.Lock()


def default_manager(**kwargs) -> JobManager:
    """Process-wide manager used by ``firelens.inference.start_analysis``."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = JobManager(**kwargs)
        return _DEFAULT
