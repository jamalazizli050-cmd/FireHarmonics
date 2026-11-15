"""Progress tracking for location analyses (optional; no web server needed).

Progress is derived from completed work, never from a timer:

* Each stage has a *work weight* in nominal seconds. Fixed analysis stages use
  small constants (``STAGE_WEIGHTS``). The download stage's weight is
  ``n_requests x SECONDS_PER_REQUEST`` and the processing stage's is
  ``n_years x SECONDS_PER_PROCESS_UNIT``, both known once the retrieval plan
  is made. The weights only set how much of the bar a stage occupies.
* Within a determinate stage (download, process), the fraction is
  ``completed units / total units`` from real events (one unit per FIRMS
  request chunk actually requested, one per processed year).
* Indeterminate stages (waiting for the download slot, loading, pattern
  discovery, prediction) report ``indeterminate: true``. Their share of the bar
  is added only when they finish.
* ``progress_percent`` is monotonic, is capped at 99.0 while work remains,
  and becomes 100.0 only together with ``status == "completed"``.
* ``eta_seconds`` is given only in the download stage, after at least 3
  requests: measured seconds per request x remaining requests, plus the
  nominal weights of the stages after download. Otherwise it is ``None``.
  Time spent in a rate-limit wait is excluded from the measured rate.
* During a known wait (FIRMS transaction-budget pause or retry back-off of
  >= 30 s) events carry ``waiting = {reason, seconds, resume_at}`` and
  ``eta_seconds`` is ``None``; the wait ends with a ``progress`` event.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable

from .logging_utils import get_logger

log = get_logger(__name__)

STAGES = {
    "validate": "Validating request",
    "plan": "Planning data retrieval (availability + cache)",
    "wait_download_slot": "Waiting for the shared FIRMS download slot",
    "download": "Downloading NASA FIRMS data",
    "process": "Validating and storing downloaded records",
    "load": "Loading observations for the location",
    "patterns": "Historical pattern discovery",
    "predict": "Forecasting the target month",
    "finalize": "Assembling the result",
}
STAGE_ORDER = list(STAGES)
STAGE_WEIGHTS = {"validate": 0.1, "plan": 1.0, "wait_download_slot": 0.0, "download": 0.0, "process": 0.0,
                 "load": 2.0, "patterns": 3.0, "predict": 1.5, "finalize": 0.3}
SECONDS_PER_REQUEST = 2.5       # nominal; replaced by the measured rate for ETA
SECONDS_PER_PROCESS_UNIT = 2.0
MIN_UNITS_FOR_ETA = 3

Callback = Callable[[dict], None]


class ProgressTracker:
    """Collects progress events for one analysis and forwards them to callbacks."""

    def __init__(self, job_id: str | None = None, callbacks: list[Callback] | None = None,
                 clock: Callable[[], float] = time.monotonic, keep_events: int = 2000):
        self.job_id = job_id
        self._callbacks = list(callbacks or [])
        self._clock = clock
        self._lock = threading.RLock()
        self.weights = dict(STAGE_WEIGHTS)
        self.status = "queued"
        self.stage: str | None = None
        self.stage_total: int | None = None
        self.stage_done = 0
        self._stage_t0: float | None = None
        self._stage_wait = 0.0                # seconds of rate-limit waiting inside the current stage
        self._wait: dict | None = None        # current known wait, if any
        self._wait_t0: float | None = None
        self._done_stages: set[str] = set()
        self._percent = 0.0
        self.message = ""
        self.warnings: list[dict] = []
        self.error: dict | None = None
        self.events: list[dict] = []
        self._keep = keep_events
        self._seq = 0
        self.started_at: str | None = None
        self.finished_at: str | None = None

    # ------------------------------------------------------------ callbacks
    def add_callback(self, cb: Callback) -> None:
        with self._lock:
            self._callbacks.append(cb)

    def remove_callback(self, cb: Callback) -> None:
        with self._lock:
            if cb in self._callbacks:
                self._callbacks.remove(cb)

    # ------------------------------------------------------------ internals
    def _fraction_done(self) -> float:
        total = sum(self.weights.values())
        if total <= 0:
            return 0.0
        done = sum(self.weights[s] for s in self._done_stages)
        if self.stage and self.stage not in self._done_stages and self.stage_total:
            done += self.weights.get(self.stage, 0.0) * min(self.stage_done / self.stage_total, 1.0)
        return done / total

    def _eta(self) -> float | None:
        if (self._wait is not None or self.stage != "download" or not self.stage_total
                or self.stage_done < MIN_UNITS_FOR_ETA):
            return None
        rate = max(self._clock() - self._stage_t0 - self._stage_wait, 0.0) / self.stage_done
        later = sum(self.weights[s] for s in STAGE_ORDER[STAGE_ORDER.index("download") + 1:]
                    if s not in self._done_stages)
        return round(rate * (self.stage_total - self.stage_done) + later, 1)

    def _emit(self, event_type: str, **extra) -> dict:
        with self._lock:
            if self.status != "completed":
                self._percent = min(max(self._percent, 100.0 * self._fraction_done()), 99.0)
            self._seq += 1
            determinate = bool(self.stage_total) and self.stage not in self._done_stages
            ev = {
                "job_id": self.job_id, "seq": self._seq, "type": event_type,
                "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "status": self.status, "stage": self.stage,
                "stage_label": STAGES.get(self.stage) if self.stage else None,
                "stage_index": STAGE_ORDER.index(self.stage) + 1 if self.stage in STAGES else None,
                "n_stages": len(STAGE_ORDER),
                "progress_percent": round(self._percent, 1),
                "stage_progress": {"completed": self.stage_done, "total": self.stage_total} if determinate else None,
                "indeterminate": not determinate and self.status == "running",
                "eta_seconds": self._eta() if self.status == "running" else None,
                "waiting": dict(self._wait) if self._wait else None,
                "message": self.message, **extra}
            self.events.append(ev)
            if len(self.events) > self._keep:
                self.events = self.events[-self._keep:]
            cbs = list(self._callbacks)
        for cb in cbs:
            try:
                cb(ev)
            except Exception as e:  # a faulty subscriber must not break the analysis ...
                if getattr(e, "abort_job", False):   # ... unless it deliberately cancels the job
                    raise
                log.warning("progress callback failed: %s", e)
        return ev

    # ------------------------------------------------------------ public API
    def start(self) -> None:
        with self._lock:
            self.status = "running"
            self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._emit("status")

    def start_stage(self, stage: str, total: int | None = None, message: str = "") -> None:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        with self._lock:
            if self.status == "queued":
                self.status = "running"
                self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.stage, self.stage_total, self.stage_done = stage, (total or None), 0
            self._stage_t0 = self._clock()
            self._stage_wait = 0.0
            self.message = message or STAGES[stage]
        self._emit("stage")

    def advance(self, n: int = 1, message: str | None = None) -> None:
        with self._lock:
            self.stage_done += n
            if message:
                self.message = message
        self._emit("progress")

    def end_stage(self) -> None:
        with self._lock:
            if self.stage:
                self._done_stages.add(self.stage)
        self._emit("stage_done")

    def set_download_plan(self, n_requests: int, n_process_units: int | None = None) -> None:
        """Size the download/process stages once the retrieval plan is known."""
        with self._lock:
            self.weights["download"] = n_requests * SECONDS_PER_REQUEST
            if n_process_units is None:  # ~one processed year per 73 chunks, at least one
                n_process_units = 0 if n_requests == 0 else max(1, round(n_requests / 73))
            self.weights["process"] = n_process_units * SECONDS_PER_PROCESS_UNIT

    def warn(self, message: str, kind: str = "warning") -> None:
        w = {"kind": kind, "message": message, "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with self._lock:
            self.warnings.append(w)
        self._emit("warning", warning=w)

    def begin_wait(self, seconds: float, message: str, reason: str = "rate_limit_wait") -> None:
        """A known pause (e.g. FIRMS transaction budget). Emits a warning; ETA is withheld until end_wait."""
        resume = datetime.fromtimestamp(time.time() + seconds, timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._wait = {"reason": reason, "seconds": round(float(seconds), 1), "resume_at": resume}
            self._wait_t0 = self._clock()
        self.warn(message, kind=reason)

    def end_wait(self) -> None:
        with self._lock:
            if self._wait is None:
                return
            self._stage_wait += self._clock() - (self._wait_t0 or self._clock())
            self._wait = self._wait_t0 = None
        self._emit("progress")

    def complete(self) -> None:
        with self._lock:
            self._done_stages.update(STAGE_ORDER)
            self.status, self._percent = "completed", 100.0
            self.stage, self.stage_total = "finalize", None
            self.message = "Analysis complete"
            self.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._emit("completed")

    def fail(self, error_type: str, message: str, recoverable: bool) -> None:
        with self._lock:
            self.status = "failed"
            self.error = {"type": error_type, "message": message, "recoverable": recoverable,
                          "failed_stage": self.stage}
            self.message = f"Failed: {message}"
            self.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._emit("failed", error=self.error)

    def snapshot(self) -> dict:
        with self._lock:
            last = self.events[-1] if self.events else None
            return {"job_id": self.job_id, "status": self.status, "stage": self.stage,
                    "stage_label": STAGES.get(self.stage) if self.stage else None,
                    "progress_percent": round(self._percent, 1),
                    "stage_progress": last["stage_progress"] if last else None,
                    "indeterminate": last["indeterminate"] if last else False,
                    "eta_seconds": last["eta_seconds"] if last else None,
                    "waiting": dict(self._wait) if self._wait else None,
                    "message": self.message, "warnings": list(self.warnings), "error": self.error,
                    "started_at": self.started_at, "finished_at": self.finished_at,
                    "last_event_seq": self._seq}


class NullTracker(ProgressTracker):
    """Tracker used when the caller does not ask for progress (still records warnings)."""
