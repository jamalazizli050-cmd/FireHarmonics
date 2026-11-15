import json
import os
import tempfile
import threading
import time
import unittest
from datetime import date
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from FireDetectionAPP import main
from FireDetectionAPP.jobs import AnalysisService
from FireDetectionAPP.ml_engine import DisabledEngine
from FireDetectionAPP.schemas import AnalysisRequest
from FireDetectionAPP.storage import now

PAYLOAD = {"latitude": -13.0, "longitude": 24.0, "radius_km": 25,
           "start_date": "2024-01-01", "end_date": "2024-01-12"}
CSV = "latitude,longitude,acq_date,acq_time,satellite,frp\n-13,24,2024-01-02,0930,N20,12.5\n-13,24,2024-01-02,0930,N20,12.5\n-13,24.3,2024-01-02,0930,N20,5\n"


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"FIREHARMONICS_DATA_DIR": self.folder, "NASA_FIRMS_MAP_KEY": "test-key",
                                                     "FIREHARMONICS_ML_ENGINE": "disabled"}))
        self.get = self.enterContext(patch.object(main.httpx, "get", side_effect=self.respond))
        self.client = self.enterContext(TestClient(main.app))
        self.manager = main.app.state.service

    def respond(self, url, **kwargs):
        if "data_availability" in url:
            return httpx.Response(200, text="data_id,min_date,max_date\nVIIRS_NOAA20_NRT,2020-01-01,2025-12-31\n")
        return httpx.Response(200, text=CSV)

    def wait(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/v1/analyses/{job_id}")
            self.assertEqual(response.status_code, 200)
            if response.json()["status"] in {"completed", "failed"}:
                return response.json()
            time.sleep(0.01)
        self.fail("Analysis did not finish")

    def submit(self, payload=None):
        response = self.client.post("/api/v1/analyses", json=payload or PAYLOAD)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status_code"], 202)
        return response.json()["job_id"]

    def test_background_work_returns_before_nasa_and_pending_result_is_202(self):
        gate = threading.Event()
        reached = threading.Event()
        self.addCleanup(gate.set)

        def blocked(url, **kwargs):
            reached.set()
            if not gate.wait(3):
                raise RuntimeError("Test did not release NASA worker")
            return self.respond(url, **kwargs)

        self.get.side_effect = blocked
        started = time.monotonic()
        job_id = self.submit()
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(reached.wait(1))
        response = self.client.get(f"/api/v1/analyses/{job_id}/result")
        self.assertEqual(response.status_code, 202)
        self.assertIsNone(response.json()["result"])
        self.assertEqual(self.client.get("/health").status_code, 200)
        gate.set()
        self.assertEqual(self.wait(job_id)["status"], "completed")

    def test_batches_radius_filter_deduplication_and_persistent_cache(self):
        job_id = self.submit()
        self.assertEqual(self.wait(job_id)["status"], "completed")
        requests = [call.args[0] for call in self.get.call_args_list if "/area/" in call.args[0]]
        self.assertEqual(len(requests), 3)
        self.assertTrue(requests[0].endswith("/5/2024-01-01"))
        self.assertTrue(requests[1].endswith("/5/2024-01-06"))
        self.assertTrue(requests[2].endswith("/2/2024-01-11"))
        result = self.client.get(f"/api/v1/analyses/{job_id}/result").json()["result"]
        self.assertEqual(result["detection_count"], 1)
        self.assertIsNone(result["prediction"]["probability"])
        self.assertIsNone(result["historical_activity"]["mean_annual_detections"])
        self.assertEqual(result["seasonal_patterns"]["status"], "insufficient_data")
        self.assertEqual(result["coverage"]["raw_row_count"], 9)
        self.get.reset_mock()
        cached = self.submit()
        self.assertTrue(self.wait(cached)["cache_hit"])
        self.get.assert_not_called()
        location = self.client.get("/api/v1/locations/analysis", params=PAYLOAD)
        self.assertEqual(location.status_code, 200, location.text)
        self.assertEqual(location.json()["result"]["detection_count"], 1)
        other = AnalysisService(self.folder, engine=DisabledEngine())
        try:
            self.assertEqual(other.store.job(job_id)["status"], "completed")
            self.assertEqual(other.store.cached(AnalysisRequest(**PAYLOAD))["result"]["detection_count"], 1)
        finally:
            other.close()

    def test_sse_terminal_events_and_reconnection(self):
        job_id = self.submit()
        self.assertEqual(self.wait(job_id)["status"], "completed")
        response = self.client.get(f"/api/v1/analyses/{job_id}/events")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn("event: completed", response.text)
        ids = [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]
        self.assertEqual(ids, sorted(set(ids)))
        terminal = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")][-1]
        self.assertEqual(terminal["progress"], 100)
        self.assertNotIn("test-key", response.text)
        replay = self.client.get(f"/api/v1/analyses/{job_id}/events", headers={"Last-Event-ID": str(ids[-1])})
        self.assertEqual(replay.text, "")

    def test_nasa_pause_is_reported_and_automatic_retry_succeeds(self):
        calls = 0

        def limited(url, **kwargs):
            nonlocal calls
            if "/area/" in url:
                calls += 1
                if calls == 1:
                    return httpx.Response(429, text="test-key", headers={"Retry-After": "1"})
            return self.respond(url, **kwargs)

        self.get.side_effect = limited
        with patch.object(self.manager.stop, "wait", return_value=False) as wait:
            job_id = self.submit()
            self.assertEqual(self.wait(job_id)["status"], "completed")
            wait.assert_called_once_with(1)
        events = self.manager.store.events(job_id, 0)
        paused = [snapshot for _, snapshot in events if snapshot["status"] == "paused"]
        self.assertEqual(paused[0]["nasa_pause_seconds"], 1)
        self.assertIsNone(paused[0]["eta_seconds"])

    def test_recoverable_failure_and_manual_retry(self):
        self.get.side_effect = httpx.ConnectError("secret upstream URL test-key")
        with patch.object(self.manager.stop, "wait", return_value=False):
            job_id = self.submit()
            job = self.wait(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertTrue(job["error"]["recoverable"])
        self.assertNotIn("test-key", json.dumps(job))
        failed = self.client.get(f"/api/v1/analyses/{job_id}/result")
        self.assertEqual(failed.status_code, 409)
        self.get.side_effect = self.respond
        response = self.client.post(f"/api/v1/analyses/{job_id}/retry")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.wait(job_id)["status"], "completed")
        self.assertEqual(self.wait(job_id)["attempt"], 2)
        self.assertEqual(self.client.post(f"/api/v1/analyses/{job_id}/retry").status_code, 409)

    def test_retry_reuses_successful_download_batches(self):
        downloaded = []

        def failing_second_batch(url, **kwargs):
            if "/area/" in url:
                downloaded.append(url)
                if url.endswith("/5/2024-01-06"):
                    raise httpx.ConnectError("private URL test-key")
            return self.respond(url, **kwargs)

        self.get.side_effect = failing_second_batch
        with patch.object(self.manager.stop, "wait", return_value=False):
            job_id = self.submit()
            self.assertEqual(self.wait(job_id)["status"], "failed")
        first_url = downloaded[0]
        self.get.reset_mock()
        self.get.side_effect = self.respond
        self.assertEqual(self.client.post(f"/api/v1/analyses/{job_id}/retry").status_code, 202)
        self.assertEqual(self.wait(job_id)["status"], "completed")
        self.assertNotIn(first_url, [call.args[0] for call in self.get.call_args_list])

    def test_long_nasa_retry_after_is_not_shortened(self):
        self.get.side_effect = lambda *args, **kwargs: httpx.Response(429, headers={"Retry-After": "120"})
        job_id = self.submit()
        job = self.wait(job_id)
        self.assertEqual(job["error"]["retry_after_seconds"], 120)
        self.assertEqual(self.get.call_count, 1)
        retry = self.client.post(f"/api/v1/analyses/{job_id}/retry")
        self.assertEqual(retry.status_code, 429)
        self.assertGreater(int(retry.headers["Retry-After"]), 60)

    def test_invalid_inputs_rejected_without_nasa(self):
        for change in ({"latitude": 91}, {"longitude": -181}, {"radius_km": 0},
                       {"source": "wrong"}, {"lookback_days": 3651},
                       {"start_date": "2024-02-01", "end_date": "2024-01-01"},
                       {"start_date": "2999-01-01"}, {"extra_field": 1}):
            with self.subTest(change=change):
                response = self.client.post("/api/v1/analyses", json={**PAYLOAD, **change})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["status_code"], 422)
        self.get.assert_not_called()

    def test_cold_cache_regions_model_and_unknown_ids_never_download(self):
        self.assertEqual(self.client.get("/api/v1/locations/analysis", params=PAYLOAD).json()["status"], "insufficient_data")
        regions = self.client.get("/api/v1/regions").json()["regions"]
        self.assertEqual(len(regions), 5)
        self.assertTrue(all(region["status"] == "insufficient_data" for region in regions))
        self.assertEqual(self.client.get("/api/v1/regions/southern-africa/patterns").json()["status"], "insufficient_data")
        self.assertEqual(self.client.get("/api/v1/regions/missing/patterns").status_code, 404)
        for path in ("/api/v1/analyses/missing", "/api/v1/analyses/missing/events", "/api/v1/analyses/missing/result"):
            self.assertEqual(self.client.get(path).status_code, 404)
        model = self.client.get("/api/v1/model").json()
        self.assertEqual(model["status"], "disabled")   # tests run with FIREHARMONICS_ML_ENGINE=disabled
        self.assertFalse(model["available"])
        self.assertIsNone(model["prediction"]["probability"])
        self.get.assert_not_called()

    def test_no_overlap_fails_without_retry_or_area_requests(self):
        job_id = self.submit({**PAYLOAD, "start_date": "2010-01-01", "end_date": "2010-01-02"})
        job = self.wait(job_id)
        self.assertEqual(job["error"]["code"], "insufficient_data")
        self.assertFalse(job["error"]["recoverable"])
        self.assertEqual(self.client.post(f"/api/v1/analyses/{job_id}/retry").status_code, 409)
        self.assertEqual(self.get.call_count, 1)

    def test_restart_marks_unfinished_jobs_recoverable(self):
        stamp = now()
        self.manager.store.save_job({"job_id": "interrupted", "status": "running", "stage": "downloading",
                                     "created_at": stamp, "updated_at": stamp, "progress": 20})
        restarted = AnalysisService(self.folder, engine=DisabledEngine())
        try:
            record = restarted.store.job("interrupted")
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["error"]["code"], "interrupted")
            self.assertTrue(record["error"]["recoverable"])
        finally:
            restarted.close()


if __name__ == "__main__":
    unittest.main()


class CancelTests(AnalysisTests):
    def test_cancel_running_job_stops_it_and_completed_job_cannot_be_cancelled(self):
        gate = threading.Event()
        reached = threading.Event()
        self.addCleanup(gate.set)

        def blocked(url, **kwargs):
            reached.set()
            gate.wait(3)
            return self.respond(url, **kwargs)

        self.get.side_effect = blocked
        job_id = self.submit()
        self.assertTrue(reached.wait(1))
        response = self.client.post(f"/api/v1/analyses/{job_id}/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        gate.set()
        job = self.wait(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["stage"], "cancelled")
        self.assertEqual(job["error"]["code"], "cancelled")
        self.assertTrue(job["error"]["recoverable"])
        self.assertEqual(self.client.post(f"/api/v1/analyses/{job_id}/cancel").status_code, 409)
        # a cancelled job can be retried and then completes normally
        self.get.side_effect = self.respond
        self.assertEqual(self.client.post(f"/api/v1/analyses/{job_id}/retry").status_code, 202)
        self.assertEqual(self.wait(job_id)["status"], "completed")

    def test_cancel_queued_job_never_runs(self):
        job_id = self.submit()
        self.manager.cancel(job_id) if self.client.get(f"/api/v1/analyses/{job_id}").json()["status"] == "queued" else None
        job = self.wait(job_id)
        self.assertIn(job["status"], {"completed", "failed"})
        if job["status"] == "failed":
            self.assertEqual(job["error"]["code"], "cancelled")

    def test_cancel_unknown_job_is_404(self):
        self.assertEqual(self.client.post("/api/v1/analyses/nope/cancel").status_code, 404)
