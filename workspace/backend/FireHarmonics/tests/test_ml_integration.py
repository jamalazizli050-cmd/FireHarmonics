"""Backend <-> FireLens integration tests (offline and deterministic).

The real FireLens engine and the *production* trained model are used. Only the
network is replaced: NASA FIRMS answers with synthetic detections from a fake
HTTP session (FireLens side) and a patched ``httpx.get`` (descriptive side).
Synthetic detections are test fixtures, not data presented as observations.
"""
import json
import math
import os
import re
import shutil
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import numpy as np
import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

from FireDetectionAPP import main
from FireDetectionAPP.jobs import AnalysisService
from FireDetectionAPP.ml_engine import (DEFAULT_FIRELENS_ROOT, DisabledEngine, EngineError, FireLensEngine,
                                        json_safe, normalize_prediction)
from FireDetectionAPP.schemas import AnalysisResult, Prediction

FIRELENS = DEFAULT_FIRELENS_ROOT
pytestmark = pytest.mark.skipif(not (FIRELENS / "src" / "firelens").is_dir(), reason="FireLens package not present")

TODAY = datetime.now(timezone.utc).date()
SP_END = TODAY - timedelta(days=100)
NRT_END = TODAY - timedelta(days=12)
SECRET = "TESTKEY0123456789"
AREA_RE = re.compile(r"/api/area/csv/[^/]+/(?P<src>[A-Z0-9_]+)/(?P<area>[^/]+)/(?P<days>\d)/(?P<date>[\d-]+)")
VIIRS_HEADER = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,"
                "version,bright_ti5,frp,daynight")
POINT = {"latitude": 45.0, "longitude": 10.0, "radius_km": 25}


# ------------------------------------------------------------------ fake NASA
def firms_response(text, status=200):
    response = requests.Response()
    response.status_code, response._content, response.encoding = status, text.encode(), "utf-8"
    response.headers["content-type"] = "text/plain"
    return response


class FakeFirms:
    """Synthetic FIRMS Area API: one detection per day of Jul-Sep at (45, 10)."""

    def __init__(self, fire_months=(7, 8, 9)):
        self.fire_months, self.calls, self.lock, self.fail = fire_months, [], threading.Lock(), None
        self.headers = {}

    def get(self, url, timeout=None):
        if self.fail is not None:
            raise self.fail
        match = AREA_RE.search(url)
        assert match, "unexpected FIRMS URL"
        with self.lock:
            self.calls.append((match["src"], match["area"], match["date"]))
        sp = match["src"].endswith("_SP")
        rows = [VIIRS_HEADER + (",type" if sp else "")]
        first = date.fromisoformat(match["date"])
        for offset in range(int(match["days"])):
            day = first + timedelta(days=offset)
            if day.month in self.fire_months:
                rows.append(f"45.00000,10.00000,330.1,0.4,0.4,{day},1312,N,VIIRS,n,2,295.0,5.5,D" + (",0" if sp else ""))
        return firms_response("\n".join(rows) + "\n")


def descriptive_nasa(url, **kwargs):
    if "data_availability" in url:
        return httpx.Response(200, text="data_id,min_date,max_date\nVIIRS_NOAA20_NRT,2020-01-01,2025-12-31\n"
                                        "MODIS_SP,2000-11-01,2025-12-31\n")
    return httpx.Response(200, text="latitude,longitude,acq_date,acq_time,satellite,frp\n45,10,2024-01-02,0930,N20,12.5\n")


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def firelens_settings(tmp_path):
    assert FireLensEngine().available   # puts firelens/src on sys.path, exactly as the backend does
    from firelens.config import Settings
    from firelens.io import write_parquet
    settings = Settings(map_key=SECRET, data_dir=tmp_path / "fl" / "data", reports_dir=tmp_path / "fl" / "reports",
                        min_request_interval_s=0.0, backoff_base_s=0.01, max_retries=1, status_check_every=10_000)
    settings.cache_dir.mkdir(parents=True)
    pd.DataFrame({"data_id": ["VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"], "min_date": ["2012-01-20", str(SP_END + timedelta(days=1))],
                  "max_date": [str(SP_END), str(NRT_END)]}).to_csv(settings.cache_dir / "data_availability.csv", index=False)
    write_parquet(pd.DataFrame({"date": [date(2000, 1, 1)], "platform": ["Terra"]}),
                  settings.cache_dir / "firms_missing_dates.parquet")
    models = settings.data_dir / "models" / "global"
    models.mkdir(parents=True)
    for suffix in (".joblib", ".json"):   # the production artifacts, unchanged
        shutil.copy(FIRELENS / "data" / "models" / "global" / f"global_disc_forecaster{suffix}", models)
    status = settings.reports_dir / "results" / "harmonization"
    status.mkdir(parents=True)
    shutil.copy(FIRELENS / "reports" / "results" / "harmonization" / "validation_status.json", status)
    return settings


@pytest.fixture
def firms(monkeypatch):
    from firelens import location_data
    from firelens.firms_client import FirmsClient
    fake = FakeFirms()
    monkeypatch.setattr(location_data, "shared_client",
                        lambda settings: FirmsClient(settings, session=fake, sleep=lambda seconds: None))
    return fake


@pytest.fixture
def api(tmp_path, monkeypatch, firelens_settings, firms):
    """FastAPI client whose service uses the real FireLens engine on isolated settings."""
    def build(engine=None, workers=2):
        service = AnalysisService(str(tmp_path / "backend"), engine=engine or FireLensEngine(settings=firelens_settings),
                                  workers=workers)
        monkeypatch.setattr(main, "create_service", lambda: service)
        return service
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", "test-key")
    monkeypatch.setattr(main.httpx, "get", descriptive_nasa)
    clients = []

    def client(engine=None, workers=2):
        build(engine, workers)
        test_client = TestClient(main.app)
        test_client.__enter__()
        clients.append(test_client)
        return test_client
    yield client
    for test_client in clients:
        test_client.__exit__(None, None, None)


def run_job(client, payload, timeout=180):
    response = client.post("/api/v1/analyses", json=payload)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/analyses/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def result_of(client, job):
    response = client.get(f"/api/v1/analyses/{job['job_id']}/result")
    assert response.status_code == 200, response.text
    return response.json()["result"]


# ------------------------------------------------------------------ engine and model
def test_firelens_imports_and_production_model_loads():
    state = FireLensEngine().status(load_model=True)
    assert state["available"] and state["package"]["importable"]
    model = state["model"]
    assert model["loadable"] and model["features_consistent"] and model["n_features"] == 28
    # The pre-registered gate never selects pooled XGBoost in the shipped model.
    gate = model["method_gate"]
    assert {m for leads in gate["in_domain"].values() for m in leads.values()} == {"seasonal_baseline"}
    assert set(gate["out_of_domain"].values()) == {"seasonal_baseline"}
    assert SECRET not in json.dumps(state)


def test_missing_package_reports_unavailable(tmp_path, monkeypatch):
    engine = FireLensEngine(root=tmp_path / "absent")
    real = __import__("importlib").import_module

    def blocked(name, *args):
        if name.startswith("firelens"):
            raise ImportError("No module named 'firelens'")
        return real(name, *args)
    monkeypatch.setattr("FireDetectionAPP.ml_engine.importlib.import_module", blocked)
    state = engine.status()
    assert not state["available"] and state["status"] == "unavailable" and not state["package"]["importable"]
    with pytest.raises(EngineError) as error:
        engine.analyze(**POINT)
    assert error.value.code == "engine_not_installed"


# ------------------------------------------------------------------ serialization
def test_json_safe_handles_numpy_pandas_and_non_finite_values():
    value = {"f": np.float64(1.5), "i": np.int64(3), "b": np.bool_(True), "nan": float("nan"), "inf": np.float32("inf"),
             "ts": pd.Timestamp("2024-01-02T03:04:05Z"), "period": pd.Period("2024-07", "M"), "nat": pd.NaT,
             "arr": np.array([1.0, np.nan]), "day": date(2024, 1, 1), 1: (np.int32(2),)}
    safe = json_safe(value)
    text = json.dumps(safe, allow_nan=False)
    assert safe["f"] == 1.5 and safe["i"] == 3 and safe["b"] is True
    assert safe["nan"] is None and safe["inf"] is None and safe["nat"] is None and safe["arr"] == [1.0, None]
    assert safe["period"] == "2024-07" and safe["ts"].startswith("2024-01-02T03:04:05") and safe["1"] == [2]
    assert "NaN" not in text and "Infinity" not in text


@pytest.mark.parametrize("path", sorted(path for path in (FIRELENS / "reports" / "results" / "global" / "locations").glob("*.json")
                                        if not path.stem.startswith("summary")), ids=lambda path: path.stem)
def test_normalization_of_saved_real_engine_outputs(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw = raw.get("result", raw)
    prediction = Prediction.model_validate(normalize_prediction(raw["prediction"]))
    if raw["prediction"]["status"] == "ok":
        assert prediction.probability == raw["prediction"]["probability"]
        assert prediction.method == raw["prediction"]["method"] == "seasonal_baseline"
    else:
        assert prediction.probability is None and prediction.method == "unavailable"


# ------------------------------------------------------------------ request validation
@pytest.mark.parametrize("change", [{"radius_km": 300}, {"target_month": "2001-01"}, {"target_month": "2026-13"},
                                    {"target_month": "12/2026"}, {"history_years": 2}, {"history_years": 15},
                                    {"ml_mode": "always"}, {"location_name": "x" * 161}])
def test_invalid_ml_inputs_rejected_before_any_work(api, firms, change):
    client = api()
    response = client.post("/api/v1/analyses", json={**POINT, **change})
    assert response.status_code == 422, response.text
    assert firms.calls == []


def test_target_month_beyond_horizon_rejected(api):
    client = api()
    year = TODAY.year + 2
    assert client.post("/api/v1/analyses", json={**POINT, "target_month": f"{year}-01"}).status_code == 422


# ------------------------------------------------------------------ end to end with the real engine
def test_end_to_end_real_engine_with_production_model(api, firms):
    client = api()
    job = run_job(client, {**POINT, "history_years": 3, "location_name": "Po Valley test point"})
    assert job["status"] == "completed", job
    assert job["result_status"] in {"complete", "partial"}
    result = result_of(client, job)
    AnalysisResult.model_validate(result)
    json.dumps(result, allow_nan=False)
    assert result["schema_version"] == "2.0" and result["location"]["name"] == "Po Valley test point"
    assert result["location"]["crs"].startswith("EPSG:4326")

    ml = result["ml_analysis"]
    assert ml["status"] in {"ok", "partial"} and ml["engine"]["name"] == "firelens"
    monthly = {row["month"]: row for row in ml["historical_activity"]["monthly"]}
    july = [row for month, row in monthly.items() if month.endswith("-07") and row["observed"]]
    assert july and all(row["detections"] == 31 for row in july)          # one synthetic detection per day
    assert all(row["detections"] is None for row in monthly.values() if not row["observed"])  # missing != zero
    assert ml["seasonal_patterns"]["peak_month"] in {"Jul", "Aug"}
    assert ml["data_coverage"]["retrieval"]["chunks_downloaded_this_call"] > 0

    prediction = result["prediction"]
    assert prediction["status"] == "ok" and 0 < prediction["probability"] < 1
    assert prediction["method"] == "seasonal_baseline"                     # out-of-domain gate, production model
    assert prediction["model"]["name"] == "global_disc_forecaster"
    comparison = prediction["model_comparison"]
    assert comparison["xgboost_pooled"]["used"] is False
    assert comparison["seasonal_baseline"]["probability"] == prediction["probability"]
    assert "not an individual wildfire ignition" in prediction["target_definition"]
    assert prediction["target_month"] == job["request"]["target_month"] or job["request"]["target_month"] is None

    satellites = result["satellite_analysis"]
    assert satellites["provider"]["name"] == "NASA FIRMS" and satellites["modis_viirs_mixed"] is False
    assert satellites["views"]["raw_viirs"]["status"] == "this_result"
    assert satellites["views"]["raw_modis"]["status"] == "separate_request"
    assert satellites["views"]["harmonized"]["status"] == "unavailable"     # (45, 10) is outside the five regions
    assert result["components"] == {"descriptive": "available", "ml_analysis": ml["status"], "forecast": "ok",
                                    "harmonization": "unavailable"}
    assert result["overview"]["forecast"]["probability"] == prediction["probability"]
    assert SECRET not in json.dumps(result)

    # Progress came from real stages, was monotonic, and reached 100 only on completion.
    events = [snapshot for _, snapshot in main.app.state.service.store.events(job["job_id"], 0)]
    progress = [event["progress"] for event in events]
    assert progress == sorted(progress) and progress[-1] == 100
    assert all(p < 100 for event, p in zip(events, progress) if event["status"] != "completed")
    stages = {event["stage"] for event in events}
    assert {"downloading", "ml_plan", "ml_download", "ml_patterns", "ml_predict", "completed"} <= stages

    sse = client.get(f"/api/v1/analyses/{job['job_id']}/events")
    assert "event: completed" in sse.text and "ml_download" in sse.text and SECRET not in sse.text


def test_cache_reuse_and_deterministic_results(api, firms):
    client = api()
    payload = {**POINT, "history_years": 3}
    first = run_job(client, payload)
    calls = len(firms.calls)
    second = client.post("/api/v1/analyses", json=payload).json()
    assert second["cache_hit"] and second["status"] == "completed" and len(firms.calls) == calls
    assert result_of(client, second)["prediction"] == result_of(client, first)["prediction"]
    # A new target month misses the backend cache but FireLens reuses its tile cache (no new downloads).
    target = f"{TODAY.year + TODAY.month // 12:04d}-{TODAY.month % 12 + 1:02d}"
    later = (pd.Period(target, "M") + 1).strftime("%Y-%m")
    third = run_job(client, {**payload, "target_month": later})
    assert len(firms.calls) == calls
    result = result_of(client, third)
    assert result["ml_analysis"]["data_coverage"]["retrieval"]["chunks_downloaded_this_call"] == 0
    assert result["prediction"]["target_month"] == later


def test_cache_only_mode_never_downloads_and_issues_no_probability(api, firms):
    client = api()
    job = run_job(client, {**POINT, "history_years": 3, "ml_mode": "cache_only"})
    assert job["status"] == "completed" and firms.calls == []
    result = result_of(client, job)
    assert result["ml_analysis"]["data_coverage"]["retrieval"]["downloads_skipped"] is True
    assert result["prediction"]["probability"] is None and result["prediction"]["method"] == "unavailable"
    assert result["prediction"]["status"] == "insufficient_data"


def test_no_detections_is_insufficient_data_not_zero(api, firms):
    firms.fire_months = ()
    client = api()
    result = result_of(client, run_job(client, {**POINT, "history_years": 3}))
    prediction = result["prediction"]
    assert prediction["probability"] is None and prediction["status"] == "insufficient_data"
    assert prediction["reason"] == "no_fire_detections_within_100km"
    assert 0 < prediction["observed_rate_upper_bound_95"]["value"] < 1
    assert result["status"] == "complete"            # a valid scientific outcome, not a failure


def test_missing_model_is_explicit_and_not_cached(api, firms, firelens_settings):
    shutil.rmtree(firelens_settings.data_dir / "models")
    client = api()
    state = client.get("/api/v1/model").json()
    assert state["available"] is False and state["engine"]["model"]["artifact_present"] is False
    payload = {**POINT, "history_years": 3, "start_date": "2024-01-01", "end_date": "2024-01-05"}
    job = run_job(client, payload)
    assert job["status"] == "completed" and job["result_status"] == "partial"
    result = result_of(client, job)
    assert result["ml_analysis"]["status"] == "failed"
    assert result["ml_analysis"]["error"]["code"] == "model_unavailable"
    assert result["prediction"] == {**result["prediction"], "probability": None, "method": "unavailable", "status": "failed"}
    assert result["detection_count"] == 1           # genuine descriptive statistics survive
    assert any(note.startswith("ML engine: failed") for note in result["warnings"])
    assert client.post("/api/v1/analyses", json=payload).json()["cache_hit"] is False


def test_nasa_failure_inside_engine_is_scrubbed(api, firms):
    firms.fail = requests.ConnectionError(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{SECRET}/x failed")
    client = api()
    job = run_job(client, {**POINT, "history_years": 3})
    result = result_of(client, job)
    # FireLens records each failed chunk as an unobserved period; the result must say so, not look complete.
    retrieval = result["ml_analysis"]["data_coverage"]["retrieval"]
    assert retrieval["chunks_failed_this_call"] > 0 and retrieval["chunks_downloaded_this_call"] == 0
    assert result["status"] == "partial" and job["result_status"] == "partial"
    assert result["prediction"]["probability"] is None and result["prediction"]["method"] == "unavailable"
    assert any("chunks failed" in note for note in result["warnings"])
    assert SECRET not in json.dumps(job) and SECRET not in json.dumps(result)
    # Not cached: once NASA recovers, the same request downloads again and yields a forecast.
    firms.fail = None
    retried = result_of(client, run_job(client, {**POINT, "history_years": 3}))
    assert retried["prediction"]["status"] == "ok" and 0 < retried["prediction"]["probability"] < 1


def test_concurrent_requests_complete_without_duplicate_downloads(api, firms):
    client = api(workers=2)
    payloads = [{**POINT, "history_years": 3}, {**POINT, "history_years": 3, "radius_km": 10}]
    ids = [client.post("/api/v1/analyses", json=payload).json()["job_id"] for payload in payloads]
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        jobs = [client.get(f"/api/v1/analyses/{job_id}").json() for job_id in ids]
        if all(job["status"] == "completed" for job in jobs):
            break
        assert not any(job["status"] == "failed" for job in jobs), jobs
        time.sleep(0.05)
    else:
        raise AssertionError("concurrent analyses did not finish")
    assert len(firms.calls) == len(set(firms.calls))   # FireLens's download lock prevents duplicates


# ------------------------------------------------------------------ modes, sources, harmonization, exports
def test_ml_off_and_disabled_engine_return_explicit_status(api, firms):
    client = api()
    off = result_of(client, run_job(client, {**POINT, "ml_mode": "off", "radius_km": 300}))
    assert off["status"] == "descriptive_only" and off["ml_analysis"]["status"] == "not_run"
    assert off["prediction"]["status"] == "not_run" and off["prediction"]["probability"] is None
    client = api(engine=DisabledEngine())
    disabled = result_of(client, run_job(client, POINT))
    assert disabled["status"] == "partial" and disabled["ml_analysis"]["status"] == "unavailable"
    assert disabled["prediction"]["status"] == "unavailable" and disabled["prediction"]["probability"] is None
    assert firms.calls == []


def test_modis_source_is_labelled_as_modis_and_inside_training_region(api, firms):
    client = api(engine=DisabledEngine())
    result = result_of(client, run_job(client, {"latitude": -13.0, "longitude": 24.0, "source": "MODIS_SP",
                                                "start_date": "2024-01-01", "end_date": "2024-01-05"}))
    assert result["provenance"]["instrument"] == "MODIS" and result["provenance"]["provider"] == "NASA FIRMS"
    views = result["satellite_analysis"]["views"]
    assert views["raw_modis"]["status"] == "this_result" and views["raw_viirs"]["status"] == "separate_request"
    assert views["harmonized"]["status"] == "regional_only" and views["harmonized"]["region_id"] == "zambia_angola"
    assert views["harmonized"]["location_scale"] == "not_validated"


def test_harmonization_endpoints_expose_only_genuine_regional_products(api):
    client = api(engine=DisabledEngine())
    overview = client.get("/api/v1/harmonization").json()
    assert overview["status"] == "regional_only" and len(overview["regions"]) == 5
    assert all(region["frp_status"] != "validated" for region in overview["regions"])
    series = client.get("/api/v1/harmonization/regions/zambia_angola").json()
    assert series["location_scale"] == "not_validated" and len(series["data"]) == 120
    assert {"viirs_raw", "modis_equivalent"} <= set(series["data"][0])
    for bad in ("unknown", "..%2Fsecrets", "CALIFORNIA"):
        assert client.get(f"/api/v1/harmonization/regions/{bad}").status_code == 404
    raw = client.get("/api/v1/harmonized", params=POINT).json()
    assert raw["harmonization_status"] == "not_harmonized" and raw["product"] == "raw_detections"


def test_exports(api, firms):
    client = api()
    job = run_job(client, {**POINT, "history_years": 3})
    exported = client.get(f"/api/v1/analyses/{job['job_id']}/export")
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    assert exported.json()["result"]["prediction"]["method"] == "seasonal_baseline"
    csv_text = client.get(f"/api/v1/analyses/{job['job_id']}/export", params={"format": "csv", "table": "ml_monthly"}).text
    header, *rows = csv_text.strip().splitlines()
    assert header.startswith("month,detections") and len(rows) >= 36
    assert ",true," in csv_text and "True" not in csv_text
    off = run_job(client, {**POINT, "ml_mode": "off"})
    assert client.get(f"/api/v1/analyses/{off['job_id']}/export",
                      params={"format": "csv", "table": "ml_monthly"}).status_code == 409


def test_archive_import_is_reused_by_an_ml_request(api, firms):
    client = api()
    params = {**POINT, "start_date": "2024-01-01", "end_date": "2024-01-31"}
    csv_bytes = b"latitude,longitude,acq_date,acq_time,frp\n45,10,2024-01-07,0930,4.5\n"
    imported = client.post("/api/v1/archives/import", params=params, files={"file": ("a.csv", csv_bytes, "text/csv")})
    assert imported.status_code == 201 and imported.json()["result"]["status"] == "descriptive_only"
    with patch.object(main.httpx, "get", side_effect=AssertionError("descriptive NASA must not be called")):
        job = run_job(client, {**params, "history_years": 3})
    result = result_of(client, job)
    assert result["coverage"]["source_kind"] == "archive_import" and result["detection_count"] == 1
    assert result["ml_analysis"]["status"] in {"ok", "partial"} and result["prediction"]["status"] == "ok"
