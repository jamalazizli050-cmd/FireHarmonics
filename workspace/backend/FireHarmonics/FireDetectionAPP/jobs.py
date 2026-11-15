"""Persistent analysis jobs, bounded workers, real request progress and retry state.

One job = the descriptive analysis of the requested FIRMS source, then (unless
``ml_mode="off"``) the FireLens engine stage. Both run in worker threads, never
on the event loop. Progress comes from completed work only: NASA request batches
for the descriptive stage, and FireLens's own work-based tracker for the ML stage.

An ML failure never yields fabricated output: the job completes with the genuine
descriptive statistics, ``status="partial"``, ``prediction.probability=None`` and
the engine error, and the partial result is not cached (so a new request retries).
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException

from . import analytics, nasa
from .ml_engine import (EngineError, create_engine, empty_ml_block, json_safe, ml_block, normalize_prediction,
                        not_run_prediction)
from .satellites import PROVIDER, normalized_view, satellite_analysis, source_provenance
from .schemas import AnalysisRequest, AnalysisResult
from .storage import Store, fingerprint, next_month, now

TERMINAL = {"completed", "failed"}
CANCELLED_ERROR = {"code": "cancelled", "detail": "The analysis was cancelled (a newer request replaced it).",
                   "recoverable": True, "status_code": 409}


class JobCancelled(Exception):
    """Raised inside a worker when its job was cancelled. FireLens's ProgressTracker re-raises it (abort_job)."""
    abort_job = True
RESULT_SCHEMA_VERSION = "2.0"
DESCRIPTIVE_SHARE = 0.4   # share of the progress bar used by the descriptive stage when the ML stage follows
ML_START = 40
log = logging.getLogger(__name__)


class AnalysisService:
    def __init__(self, folder, *, cache_ttl=3600, workers=2, engine=None):
        self.store = Store(folder)
        self.cache_ttl = cache_ttl
        self.engine = engine if engine is not None else create_engine()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analysis")
        self.cancelled: set[str] = set()
        for job in self.store.jobs():
            if job["status"] not in TERMINAL:
                self.update(job["job_id"], status="failed", stage="interrupted", eta_seconds=None, nasa_pause_seconds=None,
                            error={"code": "interrupted", "detail": "The backend restarted. Retry the analysis.", "recoverable": True, "status_code": 503})

    def close(self):
        self.stop.set()
        # A running FireLens stage cannot be interrupted; shutdown waits for it to finish.
        self.executor.shutdown(wait=True, cancel_futures=True)

    def require_job(self, job_id):
        job = self.store.job(job_id)
        if job is None:
            raise HTTPException(404, "Analysis job not found")
        return job

    def update(self, job_id, **changes):
        with self.lock:
            job = self.require_job(job_id)
            job.update(changes, updated_at=now())
            self.store.save_job(job)
            return job

    def fresh(self, cached):
        if not cached:
            return False
        if cached["result"]["coverage"]["source_kind"] == "archive_import":
            return True
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["cached_at"])).total_seconds()
        return age <= self.cache_ttl

    # ------------------------------------------------------------ ML helpers
    def ml_will_run(self, request):
        return request.ml_mode != "off" and self.engine.available

    def check_ml_request(self, request):
        """Relative rules the engine enforces, checked before any work is queued."""
        if request.ml_mode == "off":
            return
        if not 1 <= request.radius_km <= 200:
            raise HTTPException(422, "radius_km must be between 1 and 200 when ml_mode is not 'off'")
        if request.target_month:
            today = datetime.now(timezone.utc).date()
            year, month = map(int, request.target_month.split("-"))
            ahead = (year - today.year) * 12 + month - today.month
            if ahead < 0:
                raise HTTPException(422, "target_month is in the past; past months are in historical_activity")
            if ahead > 12:
                raise HTTPException(422, "target_month is beyond the 12-month forecast horizon")

    def archive_base(self, request):
        """A descriptive archive import that a request with ML can build on."""
        if request.ml_mode == "off":
            return None
        cached = self.store.cached(request.model_copy(update={"ml_mode": "off"}))
        if cached and cached["result"]["coverage"].get("source_kind") == "archive_import":
            return cached
        return None

    def skipped_ml(self, request):
        if request.ml_mode == "off":
            return None
        state = self.engine.status(load_model=False)
        return {"state": "unavailable", "reason": state.get("reason") or "The ML engine is unavailable."}

    # ------------------------------------------------------------ submission
    def submit(self, request):
        self.check_ml_request(request)
        ml_runs = self.ml_will_run(request)   # may import FireLens; done outside the lock
        with self.lock:
            if self.stop.is_set():
                raise HTTPException(503, "Backend is shutting down")
            active = sum(job["status"] not in TERMINAL for job in self.store.jobs())
            if active >= 32:
                raise HTTPException(429, "The analysis queue is full. Retry later.", headers={"Retry-After": "10"})
            cached = self.store.cached(request)
            hit = self.fresh(cached)
            result = None
            if hit:
                result = cached["result"]
                result["location"] = {**result["location"], "name": request.location_name}
            else:
                archive = self.archive_base(request)
                if archive and not ml_runs:
                    result, hit = self.assemble(request, archive["result"], self.skipped_ml(request)), True
                elif not archive:
                    nasa.get_map_key()
            stamp = now()
            job_id = str(uuid4())
            prefix = f"/api/v1/analyses/{job_id}"
            job = {"job_id": job_id, "status": "completed" if hit else "queued", "progress": 100 if hit else 0,
                   "stage": "completed" if hit else "queued", "eta_seconds": 0 if hit else None, "nasa_pause_seconds": None,
                   "created_at": stamp, "updated_at": stamp, "request": request.model_dump(mode="json"),
                   "cache_hit": hit, "error": None, "attempt": 1, "message": None,
                   "result_status": result.get("status") if hit else None,
                   "links": {"status": prefix, "events": f"{prefix}/events", "result": f"{prefix}/result",
                             "retry": f"{prefix}/retry", "export": f"{prefix}/export"},
                   "result": result}
            self.store.save_job(job)
            if not hit:
                self.executor.submit(self.run, job_id)
            return job

    def retry(self, job_id):
        with self.lock:
            job = self.require_job(job_id)
            if job["status"] != "failed" or not job["error"] or not job["error"]["recoverable"]:
                raise HTTPException(409, "Only failed jobs with recoverable=true can be retried")
            not_before = job["error"].get("retry_not_before")
            if not_before:
                remaining = (datetime.fromisoformat(not_before) - datetime.now(timezone.utc)).total_seconds()
                if remaining > 0:
                    raise HTTPException(429, "NASA requested a longer pause before retrying.",
                                        headers={"Retry-After": str(int(remaining) + 1)})
            if sum(item["status"] not in TERMINAL for item in self.store.jobs()) >= 32:
                raise HTTPException(429, "The analysis queue is full. Retry later.")
            if not self.archive_base(AnalysisRequest.model_validate(job["request"])):
                nasa.get_map_key()
            self.cancelled.discard(job_id)
            job = self.update(job_id, status="queued", stage="queued", progress=0, eta_seconds=None,
                              nasa_pause_seconds=None, error=None, result=None, cache_hit=False, message=None,
                              result_status=None, attempt=job.get("attempt", 1) + 1)
            self.executor.submit(self.run, job_id)
            return job

    def cancel(self, job_id):
        """Cancel a queued or running job. A running job stops at its next progress checkpoint."""
        with self.lock:
            job = self.require_job(job_id)
            if job["status"] in TERMINAL:
                raise HTTPException(409, "Only queued or running analyses can be cancelled")
            self.cancelled.add(job_id)
            if job["status"] == "queued":
                job = self.mark_cancelled(job_id)
            return job

    def mark_cancelled(self, job_id):
        return self.update(job_id, status="failed", stage="cancelled", eta_seconds=None, nasa_pause_seconds=None,
                           error=dict(CANCELLED_ERROR), message="Cancelled")

    def check_cancel(self, job_id):
        if job_id in self.cancelled:
            raise JobCancelled()

    def call_nasa(self, job_id, operation, *args):
        for attempt in range(3):
            self.check_cancel(job_id)
            if self.stop.is_set():
                raise nasa.FIRMSException(503, "The backend stopped. Retry the analysis.", recoverable=True, code="interrupted")
            try:
                return operation(*args)
            except nasa.FIRMSException as exc:
                delay = exc.retry_after or 2 ** attempt
                if not exc.recoverable or attempt == 2 or delay > 60:
                    raise
                job = self.require_job(job_id)
                stage = job["stage"]
                self.update(job_id, status="paused", stage="waiting_for_nasa", nasa_pause_seconds=delay, eta_seconds=None)
                self.stop.wait(delay)
                self.update(job_id, status="running", stage=stage, nasa_pause_seconds=None)

    # ------------------------------------------------------------ worker
    def run(self, job_id):
        if job_id in self.cancelled and self.require_job(job_id)["status"] in TERMINAL:
            self.cancelled.discard(job_id)   # cancelled while queued: already marked
            return
        try:
            job = self.update(job_id, status="running", stage="checking_availability", progress=1)
            request = AnalysisRequest.model_validate(job["request"])
            ml_runs = self.ml_will_run(request)
            archive = self.archive_base(request)
            if archive:
                rows, descriptive = archive["detections"], archive["result"]
                self.update(job_id, stage="loading_archive", progress=ML_START if ml_runs else 96)
            else:
                rows, descriptive = self.descriptive(job_id, request, DESCRIPTIVE_SHARE if ml_runs else 1.0)
            self.check_cancel(job_id)
            ml = self.run_ml(job_id, request) if ml_runs else self.skipped_ml(request)
            self.check_cancel(job_id)
            self.update(job_id, stage="assembling", progress=99, eta_seconds=None, nasa_pause_seconds=None, status="running")
            result = self.assemble(request, descriptive, ml)
            if cacheable(ml):
                self.store.save_dataset(request, rows, result)
            self.update(job_id, status="completed", stage="completed", progress=100, eta_seconds=0,
                        nasa_pause_seconds=None, error=None, result=result, result_status=result["status"],
                        message="Analysis complete" if result["status"] != "partial" else
                        "Completed with partial results; see result.components")
        except JobCancelled:
            self.cancelled.discard(job_id)
            self.mark_cancelled(job_id)
        except HTTPException as exc:
            delay = getattr(exc, "retry_after", None)
            self.update(job_id, status="failed", stage="failed", eta_seconds=None, nasa_pause_seconds=None,
                        error={"code": getattr(exc, "code", "analysis_error"), "detail": str(exc.detail),
                               "recoverable": getattr(exc, "recoverable", False), "status_code": exc.status_code,
                               "retry_after_seconds": delay,
                               "retry_not_before": (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat() if delay else None})
        except Exception as exc:
            # Raw exceptions can contain a private upstream URL. Never persist or log their text.
            log.error("Analysis %s failed unexpectedly (%s)", job_id, type(exc).__name__)
            self.update(job_id, status="failed", stage="failed", eta_seconds=None, nasa_pause_seconds=None,
                        error={"code": "internal_error", "detail": "The analysis failed unexpectedly.", "recoverable": True, "status_code": 500})

    def descriptive(self, job_id, request, scale):
        """Download (or reuse cached batches of) the requested FIRMS source and describe it."""
        pc = lambda value: max(1, round(value * scale))
        first, last = self.call_nasa(job_id, nasa.available_window, request.source.value)
        today = datetime.now(timezone.utc).date()
        requested_end = request.end_date or min(last, today)
        requested_start = request.start_date or requested_end - timedelta(days=request.lookback_days - 1)
        if (requested_end - requested_start).days >= 3650:
            raise HTTPException(422, "An analysis can cover at most 3650 days")
        start, end = max(first, requested_start), min(last, requested_end)
        if start > end:
            raise nasa.FIRMSException(422, "The requested period has no available NASA data.", code="insufficient_data")
        warnings = []
        if (start, end) != (requested_start, requested_end):
            warnings.append("The requested period was clipped to NASA's available dates. Statistics use the downloaded period only.")
        boxes = analytics.bounding_boxes(request.latitude, request.longitude, request.radius_km)
        batches = []
        cursor = start
        while cursor <= end:
            days = min(5, (end - cursor).days + 1)
            for box in boxes:
                area = nasa.validate_area(",".join(f"{value:.8f}" for value in box))
                batches.append((request.source.value, area, days, cursor))
            cursor += timedelta(days=days)
        raw_rows = []
        started = time.monotonic()
        for index, arguments in enumerate(batches, 1):
            self.check_cancel(job_id)
            self.update(job_id, stage="downloading", progress=pc(5 + int(85 * (index - 1) / len(batches))))
            key = fingerprint([str(value) for value in arguments])
            rows = self.store.chunk(key, self.cache_ttl)
            if rows is None:
                rows = self.call_nasa(job_id, nasa.fetch_area, *arguments)
                self.store.save_chunk(key, rows)
            raw_rows.extend(rows)
            elapsed = time.monotonic() - started
            # ETA covers the descriptive download only; the ML stage's duration is unknown here.
            self.update(job_id, progress=pc(5 + int(85 * index / len(batches))),
                        eta_seconds=round(elapsed / index * (len(batches) - index)) if scale == 1.0 else None)
        self.update(job_id, stage="parsing", progress=pc(92), eta_seconds=None)
        try:
            normalized = analytics.normalize_rows(raw_rows)
        except HTTPException as exc:
            raise nasa.FIRMSException(502, "NASA returned invalid detection rows.", code="invalid_detections") from exc
        rows = analytics.filter_rows(normalized, request, start, end)
        self.update(job_id, stage="analyzing", progress=pc(96))
        coverage = {"start_date": str(start), "end_date": str(end), "complete": True,
                    "requested_start_date": str(requested_start), "requested_end_date": str(requested_end),
                    "source_kind": "area_api", "downloaded_at": now(), "request_count": len(batches),
                    "raw_row_count": len(raw_rows), "unique_rows_before_radius_filter": len(normalized)}
        return rows, analytics.describe(rows, request, coverage, warnings)

    def run_ml(self, job_id, request):
        """Run FireLens in this worker thread, mirroring its progress into the job record."""
        state = {"progress": ML_START, "key": None}
        self.update(job_id, stage="ml_starting", progress=ML_START, eta_seconds=None,
                    message="Starting the FireLens analysis (S-NPP VIIRS)")

        def on_event(event):
            self.check_cancel(job_id)   # raises JobCancelled; FireLens re-raises it and stops
            if event.get("type") in {"completed", "failed"}:
                return   # the backend decides the job's terminal state
            waiting = event.get("waiting")
            progress = min(98, ML_START + int((98 - ML_START) * float(event.get("progress_percent") or 0) / 100))
            progress = max(progress, state["progress"])
            eta = event.get("eta_seconds")
            key = (progress, event.get("stage"), bool(waiting), eta is None)
            if key == state["key"] and event.get("type") not in {"stage", "warning"}:
                return
            state.update(progress=progress, key=key)
            self.update(job_id, status="paused" if waiting else "running", stage=f"ml_{event.get('stage') or 'running'}",
                        progress=progress, eta_seconds=round(eta) if eta is not None else None,
                        nasa_pause_seconds=round(waiting["seconds"]) if waiting else None,
                        message=str(event.get("message") or "")[:200] or None)

        try:
            raw = self.engine.analyze(latitude=request.latitude, longitude=request.longitude,
                                      radius_km=request.radius_km, target_month=request.target_month,
                                      history_years=request.history_years,
                                      allow_download=request.ml_mode == "download", progress_callback=on_event)
            return {"state": "ok", "raw": raw}
        except EngineError as exc:
            return {"state": "failed", "error": {"code": exc.code, "detail": exc.detail, "recoverable": exc.recoverable}}
        except Exception as exc:
            log.error("ML engine failed for job %s (%s)", job_id, type(exc).__name__)
            return {"state": "failed", "error": {"code": "internal_error", "recoverable": True,
                                                 "detail": "The ML engine failed unexpectedly."}}

    # ------------------------------------------------------------ result
    def assemble(self, request, descriptive, ml):
        """Descriptive result + ML outcome -> validated public result. No values are invented."""
        result = {key: value for key, value in descriptive.items()
                  if key not in {"ml_analysis", "satellite_analysis", "overview", "components", "provenance"}}
        result["warnings"] = [note for note in descriptive.get("warnings", []) if not note.startswith("ML engine:")]
        target = request.target_month or next_month()
        if ml is None:
            ml_analysis = empty_ml_block("not_run", "ml_mode='off': only the descriptive analysis was requested.", mode="off")
            prediction = not_run_prediction("not_run", "No forecast was requested (ml_mode='off').")
            status = "descriptive_only"
        elif ml["state"] == "unavailable":
            ml_analysis = empty_ml_block("unavailable", ml["reason"], mode=request.ml_mode)
            prediction = not_run_prediction("unavailable", f"No forecast: {ml['reason']}", target)
            status = "partial"
            result["warnings"].append(f"ML engine: unavailable. {ml['reason']}")
        elif ml["state"] == "failed":
            error = ml["error"]
            ml_analysis = empty_ml_block("failed", "The FireLens analysis failed; descriptive statistics are unaffected.",
                                         mode=request.ml_mode, error=error)
            prediction = not_run_prediction("failed", f"No forecast: the ML engine failed ({error['code']}).", target, error)
            status = "partial"
            result["warnings"].append(f"ML engine: failed ({error['code']}). {error['detail']}")
        else:
            raw = ml["raw"]
            ml_analysis = ml_block(raw, mode=request.ml_mode)
            prediction = normalize_prediction(raw.get("prediction") or {})
            coverage = raw.get("data_coverage") or {}
            # The engine reports "insufficient_data" even when the cause was failed downloads; coverage tells them apart.
            status = "partial" if raw.get("status") == "partial" or not coverage.get("complete") else "complete"
            result["warnings"].extend(f"ML engine: {note}" for note in raw.get("warnings") or [])
            failed = (coverage.get("retrieval") or {}).get("chunks_failed_this_call") or 0
            if failed:
                result["warnings"].append(f"ML engine: {failed} FIRMS request chunks failed; those months are "
                                          "unobserved and the result is not cached. Re-submit to retry.")
        satellites = satellite_analysis(request.source.value, request.latitude, request.longitude,
                                        descriptive["detection_count"], bool(ml and ml["state"] == "ok"),
                                        ml_analysis.get("harmonization"))
        satellites["views"]["normalized"] = normalized_view(ml_analysis, descriptive, request.source.value)
        result.update({
            "schema_version": RESULT_SCHEMA_VERSION, "status": status,
            "location": {**descriptive["location"], "name": request.location_name,
                         "crs": "EPSG:4326 (WGS 84 latitude/longitude, decimal degrees)",
                         "distance_method": "great-circle (haversine), mean Earth radius 6371.0088 km"},
            "provenance": {**source_provenance(request.source.value), "provider_details": PROVIDER,
                           "requested_period": {"start_date": descriptive["coverage"].get("requested_start_date"),
                                                "end_date": descriptive["coverage"].get("requested_end_date")},
                           "observed_period": {"start_date": descriptive["coverage"]["start_date"],
                                               "end_date": descriptive["coverage"]["end_date"]}},
            "prediction": prediction, "ml_analysis": ml_analysis, "satellite_analysis": satellites,
            "components": {"descriptive": "available", "ml_analysis": ml_analysis["status"],
                           "forecast": prediction["status"], "harmonization": satellites["views"]["harmonized"]["status"]},
        })
        result["overview"] = overview(result)
        result = json_safe(result)
        return AnalysisResult.model_validate(result).model_dump(mode="json")

    def import_rows(self, request, raw_rows, complete):
        request = request.model_copy(update={"ml_mode": "off"})   # archive imports are descriptive only
        normalized = analytics.normalize_rows(raw_rows)
        start, end = request.start_date, request.end_date
        rows = analytics.filter_rows(normalized, request, start, end)
        coverage = {"start_date": str(start), "end_date": str(end), "complete": complete,
                    "source_kind": "archive_import", "downloaded_at": now(), "raw_row_count": len(raw_rows),
                    "unique_rows_before_radius_filter": len(normalized), "coverage_declaration": "provided_by_uploader"}
        result = self.assemble(request, analytics.describe(rows, request, coverage), None)
        self.store.save_dataset(request, rows, result)
        return result


def cacheable(ml):
    """Engine failures and failed downloads are not cached, so a new request retries them."""
    if ml is None or ml["state"] == "unavailable":
        return True
    if ml["state"] == "failed":
        return False
    retrieval = (ml["raw"].get("data_coverage") or {}).get("retrieval") or {}
    return not retrieval.get("chunks_failed_this_call")


def overview(result):
    """Headline values for the summary panel, copied from the blocks below (no new statistics)."""
    ml = result["ml_analysis"]
    history = ml.get("historical_activity") or {}
    seasonal = ml.get("seasonal_patterns") or {}
    trend = ml.get("long_term_trends") or {}
    hotspots = ml.get("hotspot_analysis") or {}
    prediction = result["prediction"]
    return {
        "location": {key: result["location"].get(key) for key in ("name", "latitude", "longitude", "radius_km")},
        "status": result["status"],
        "descriptive": {"source": result["source"], "instrument": result["provenance"]["instrument"],
                        "period": result["provenance"]["observed_period"], "detection_count": result["detection_count"],
                        "mean_annual_detections": result["historical_activity"].get("mean_annual_detections"),
                        "peak_month": result["seasonal_patterns"].get("peak_month")},
        "ml": {"status": ml["status"], "series": "S-NPP VIIRS",
               "period": (ml.get("data_coverage") or {}).get("window"),
               "total_detections_observed_months": history.get("total_detections_observed_months"),
               "mean_annual_detections": history.get("mean_annual_detections"),
               "peak_month": seasonal.get("peak_month"),
               "trend_direction": trend.get("direction"), "trend_pct_per_year": trend.get("theil_sen_pct_per_year"),
               "trend_significant": trend.get("significant_at_0.05"),
               "anomaly_count": len(ml["anomalies"]) if isinstance(ml.get("anomalies"), list) else None,
               "gi_star_hotspot_cells": hotspots.get("n_gi_star_hotspot_cells_99")},
        "forecast": {"status": prediction.get("status"), "probability": prediction.get("probability"),
                     "method": prediction.get("method"), "target_month": prediction.get("target_month"),
                     "relative_activity": (prediction.get("relative_activity") or {}).get("category")},
        "harmonization_status": result["satellite_analysis"]["views"]["harmonized"]["status"],
    }
