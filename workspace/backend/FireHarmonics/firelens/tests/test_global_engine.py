"""Offline tests for the universal location engine (HTTP mocked, tiny model)."""
from __future__ import annotations

import asyncio
import json
import re
import threading
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from firelens import global_model as G
from firelens import inference as INF
from firelens import location_data as LD
from firelens import location_features as LF
from firelens.catalog import BBox
from firelens.firms_client import FirmsClient
from firelens.io import write_parquet
from firelens.jobs import JobFailed, JobManager, JobNotFinished
from firelens.progress import ProgressTracker

from conftest import FakeSession, make_response

TODAY = datetime.now(timezone.utc).date()
SP_END = TODAY - timedelta(days=100)
NRT_END = TODAY - timedelta(days=12)
VIIRS_HEADER = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,"
                "version,bright_ti5,frp,daynight")
AREA_RE = re.compile(r"/api/area/csv/[^/]+/(?P<src>[A-Z0-9_]+)/(?P<area>[^/]+)/(?P<days>\d)/(?P<date>[\d-]+)")


# ------------------------------------------------------------------ fixtures
def fire_route(lat0, lon0, fire_months=(7, 8, 9), per_day=1, calls=None, fail=None):
    """Synthetic FIRMS: ``per_day`` detections near (lat0, lon0) on each day of fire months."""
    lock = threading.Lock()

    def route(url):
        m = AREA_RE.search(url)
        if not m:
            raise AssertionError(f"unexpected URL {url}")
        with lock:
            if calls is not None:
                calls.append((m["src"], m["area"], m["date"]))
        if fail is not None:   # a 4xx Response is falsy
            return fail
        src, d0, n = m["src"], date.fromisoformat(m["date"]), int(m["days"])
        sp = src.endswith("_SP")
        rows = [VIIRS_HEADER + (",type" if sp else "")]
        for k in range(n):
            d = d0 + timedelta(days=k)
            if d.month in fire_months:
                for j in range(per_day):
                    rows.append(f"{lat0 + 0.01 * j:.5f},{lon0 + 0.01 * j:.5f},330.1,0.4,0.4,{d},1312,N,VIIRS,n,2,"
                                f"295.0,5.5,D" + (",0" if sp else ""))
        return make_response(200, "\n".join(rows) + "\n")
    return route


@pytest.fixture
def engine(settings, monkeypatch, tmp_path):
    """Settings with a fresh availability table, missing-dates table and a tiny global model."""
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"data_id": ["VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"], "min_date": ["2012-01-20", str(SP_END + timedelta(days=1))],
                  "max_date": [str(SP_END), str(NRT_END)]}).to_csv(settings.cache_dir / "data_availability.csv", index=False)
    write_parquet(pd.DataFrame({"date": [date(2000, 1, 1)], "platform": ["Terra"]}),
                  settings.cache_dir / "firms_missing_dates.parquet")
    _tiny_model(settings, xgb_out_of_domain=False)
    state = {"calls": [], "route": fire_route(45.0, 10.0)}

    def fake_client(s):
        return FirmsClient(s, session=FakeSession(route=lambda u: state["route"](u)), sleep=lambda x: None)

    def recording(u):
        return state["route"](u)
    monkeypatch.setattr(LD, "shared_client", fake_client)
    state["set_route"] = lambda r: state.__setitem__("route", r)
    return settings, state


def _tiny_model(settings, xgb_out_of_domain: bool):
    rng = np.random.default_rng(0)
    months = pd.period_range("2012-01", "2016-12", freq="M")
    c = rng.poisson(1.0, (30, len(months))).astype(float)
    t = []
    for L in (1, 2, 3):
        f = LF.build_features(c, c * 3, c * 5, months, rng.uniform(-50, 50, 30), rng.uniform(-100, 100, 30),
                              np.full(30, 25.0), L)
        df = LF.to_frame(f, months)
        t.append(df)
    t = pd.concat(t, ignore_index=True)
    t["weight"] = 1.0
    feats = [f for f in LF.FEATURES_ALL if f != "lon"]
    model = G.fit(t[t.year <= 2015], t[t.year == 2016], feats, margin=True)
    gate_ood = {str(L): {"method": "xgboost_pooled" if xgb_out_of_domain else "seasonal_baseline",
                         "baseline_variant": "seasonal_raw"} for L in (1, 2, 3)}
    gate_in = {r: {str(L): {"method": "seasonal_baseline", "baseline_variant": "seasonal_raw"} for L in (1, 2, 3)}
               for r in G.HARMONIZATION_REGIONS}
    meta = {"model": G.MODEL_NAME, "design_variant": "test", "training_regions": G.HARMONIZATION_REGIONS,
            "training_period": ["2012-01-01", "2016-12-31"],
            "method_gate": {"in_domain": gate_in, "out_of_domain": gate_ood},
            "validation": {"temporal": {}, "loro": {"all": {str(L): {"seasonal_raw": {"brier": 0.1}} for L in (1, 2, 3)}}},
            "reliability": {"out_of_domain": {str(L): {"seasonal_raw": [{"bin": [0, 1.0], "n": 10, "mean_forecast": 0.5,
                                                                         "observed_frequency": 0.5}]} for L in (1, 2, 3)}}}
    G.save(settings, model, json.loads(json.dumps(meta)))


def area_calls(state):
    return [c for c in state["calls"]]


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("kw", [dict(latitude=91, longitude=0), dict(latitude=0, longitude=-181),
                                dict(latitude=float("nan"), longitude=0), dict(latitude="x", longitude=0),
                                dict(latitude=0, longitude=0, radius_km=0.5), dict(latitude=0, longitude=0, radius_km=500),
                                dict(latitude=0, longitude=0, target_month="2026-13"),
                                dict(latitude=0, longitude=0, target_month="12/2026"),
                                dict(latitude=0, longitude=0, target_month="2001-01"),
                                dict(latitude=0, longitude=0, target_month="2099-01"),
                                dict(latitude=0, longitude=0, history_years=1),
                                dict(latitude=True, longitude=0)])
def test_invalid_requests_rejected(kw):
    with pytest.raises(INF.InvalidRequestError):
        INF.validate_request(**kw)


def test_default_target_is_next_month():
    r = INF.validate_request(10, 20, today=date(2026, 11, 14))
    assert r["target_month"] == "2026-12" and r["radius_km"] == 25


# ------------------------------------------------------------------ geometry / plan
def test_tiles_normal_antimeridian_pole():
    t = LD.analysis_tiles(45.3, 10.2, 25)
    assert len(t) == 1 and t[0].west <= 10.2 - 1.2 and float(t[0].west).is_integer()
    am = LD.analysis_tiles(0.0, 179.8, 25)
    assert len(am) == 2 and am[0].east == 180 and am[1].west == -180
    pole = LD.analysis_tiles(89.5, 0, 25)
    assert pole[0].west == -180 and pole[0].east == 180 and pole[0].north == 90


def test_disc_series_counts_exact():
    lat = np.array([0.0, 0.0, 0.0, 0.0])
    lon = np.array([0.0, 0.1, 0.5, 2.0])     # 0, ~11 km, ~56 km, ~222 km
    ds = LF.disc_series(lat, lon, np.array([0, 0, 1, 1]), np.ones(4), 0.0, 0.0, [10, 60], 2)
    assert ds["count"].tolist() == [[1, 0], [2, 1]]
    assert ds["ctx"].tolist() == [2, 1]


def test_feature_leakage_guard():
    months = pd.period_range("2012-01", "2016-12", freq="M")
    c = np.random.default_rng(1).poisson(2, (1, len(months))).astype(float)
    for L in (1, 2, 3):
        a = LF.build_features(c, c, c, months, [5], [5], [25], L)
        c2 = c.copy()
        c2[:, 40 - L + 1:] = 99
        b = LF.build_features(c2, c2, c2, months, [5], [5], [25], L)
        for k in LF.FEATURES_ALL:
            assert np.allclose(np.nan_to_num(a[k][:, 40], nan=-1), np.nan_to_num(b[k][:, 40], nan=-1)), (k, L)


# ------------------------------------------------------------------ progress
def test_progress_monotonic_and_100_only_at_completion():
    evs = []
    tr = ProgressTracker("j", callbacks=[evs.append, lambda e: 1 / 0])   # faulty callback is tolerated
    tr.start_stage("plan"); tr.set_download_plan(10); tr.end_stage()
    tr.start_stage("download", total=10)
    for _ in range(10):
        tr.advance()
    tr.end_stage()
    tr.start_stage("patterns"); tr.end_stage()
    assert max(e["progress_percent"] for e in evs) < 100
    tr.complete()
    p = [e["progress_percent"] for e in evs]
    assert p == sorted(p) and p[-1] == 100.0 and evs[-1]["status"] == "completed"
    dl = [e for e in evs if e["stage"] == "download" and e["type"] == "progress"]
    assert dl[0]["eta_seconds"] is None and dl[2]["eta_seconds"] is not None      # ETA only after 3 units
    assert dl[0]["stage_progress"] == {"completed": 1, "total": 10} and not dl[0]["indeterminate"]
    pat = [e for e in evs if e["stage"] == "patterns" and e["type"] == "stage"][0]
    assert pat["indeterminate"] and pat["stage_progress"] is None and pat["eta_seconds"] is None


def test_rate_limit_wait_withholds_eta_and_is_excluded_from_rate(monkeypatch):
    now = [0.0]
    evs = []
    tr = ProgressTracker("j", callbacks=[evs.append], clock=lambda: now[0])
    tr.start_stage("plan"); tr.set_download_plan(10); tr.end_stage()
    tr.start_stage("download", total=10)
    for _ in range(4):
        now[0] += 1.0
        tr.advance()
    assert evs[-1]["eta_seconds"] is not None and evs[-1]["waiting"] is None
    # the FIRMS client's sleep hook reports a 600 s budget pause through the tracker
    monkeypatch.setattr(LD.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    LD._LOCAL.tracker = tr
    try:
        LD._reporting_sleep(600)
    finally:
        LD._LOCAL.tracker = None
    w = [e for e in evs if e["type"] == "warning"][-1]
    assert w["warning"]["kind"] == "rate_limit_wait" and w["eta_seconds"] is None
    assert w["waiting"]["seconds"] == 600 and w["waiting"]["resume_at"]
    assert tr.snapshot()["waiting"] is None                                  # wait is over
    resumed = evs[-1]
    assert resumed["type"] == "progress" and resumed["waiting"] is None
    # measured rate ignores the 600 s pause: 1 s/request x 6 remaining + later stages
    later = sum(tr.weights[s] for s in ("process", "load", "patterns", "predict", "finalize"))
    assert resumed["eta_seconds"] == pytest.approx(6.0 + later, abs=0.2)
    LD._reporting_sleep(1)                                                   # short sleeps: no tracker, no event
    assert evs[-1] is resumed


# ------------------------------------------------------------------ end to end
def test_analyze_new_location_downloads_caches_and_is_json(engine):
    settings, state = engine
    calls = []
    state["set_route"](fire_route(45.0, 10.0, calls=calls))
    evs = []
    r = INF.analyze_location(45.0, 10.0, 25, history_years=3, settings=settings, progress_callback=evs.append)
    json.dumps(r, allow_nan=False)
    assert r["status"] in ("ok", "partial")
    assert calls and {c[0] for c in calls} == {"VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"}
    assert len(calls) == len(set(calls))                                    # no chunk requested twice
    h = r["historical_activity"]
    monthly = {x["month"]: x for x in h["monthly"]}
    # one synthetic detection per day of Jul-Sep at the point
    jul = [k for k, v in monthly.items() if k.endswith("-07") and v["observed"]]
    assert jul and all(monthly[k]["detections"] == 31 for k in jul)
    assert all(v["detections"] == 0 for k, v in monthly.items() if v["observed"] and k[-2:] in ("01", "04", "11"))
    assert r["seasonal_patterns"]["peak_month"] in ("Jul", "Aug")
    assert set(r["seasonal_patterns"]["core_season_months_80pct"]) <= {"Jul", "Aug", "Sep"}
    assert r["data_coverage"]["months_unobserved"] >= 1                       # current month is partial
    assert all(v["detections"] is None for v in h["monthly"] if not v["observed"])
    p = r["prediction"]
    assert p["status"] == "ok" and 0 < p["probability"] < 1 and p["method"] == "seasonal_baseline"
    assert r["harmonization"]["modis_viirs_mixed"] is False
    pr = [e["progress_percent"] for e in evs]
    assert pr == sorted(pr) and pr[-1] == 100 and sum(e["type"] == "progress" and e["stage"] == "download" for e in evs) == len(calls)
    # second call: everything served from the cache
    calls.clear()
    r2 = INF.analyze_location(45.0, 10.0, 25, history_years=3, settings=settings)
    assert calls == [] and r2["data_coverage"]["retrieval"]["chunks_downloaded_this_call"] == 0
    assert r2["historical_activity"]["total_detections_observed_months"] == h["total_detections_observed_months"]


def test_xgboost_path_when_gate_allows(engine):
    settings, state = engine
    _tiny_model(settings, xgb_out_of_domain=True)
    r = INF.analyze_location(45.0, 10.0, 25, history_years=3, settings=settings)
    p = r["prediction"]
    assert p["method"] == "xgboost_pooled" and p["model_comparison"]["xgboost_pooled"]["used"]
    assert p["probability"] == p["model_comparison"]["xgboost_pooled"]["probability"]


def test_no_detections_gives_insufficient_data(engine):
    settings, state = engine
    state["set_route"](fire_route(45.0, 10.0, fire_months=()))
    r = INF.analyze_location(-30.0, -140.0, 25, history_years=3, settings=settings)
    assert r["status"] == "insufficient_data"
    p = r["prediction"]
    assert p["probability"] is None and p["reason"] == "no_fire_detections_within_100km"
    assert 0 < p["observed_rate_upper_bound_95"]["value"] < 1
    assert r["seasonal_patterns"]["status"] in ("no_detections", "insufficient_complete_years")
    json.dumps(r, allow_nan=False)


def test_downloads_disabled_reports_partial(engine):
    settings, state = engine
    r = INF.analyze_location(10.0, 20.0, 25, history_years=3, settings=settings, allow_download=False)
    assert r["data_coverage"]["complete"] is False and r["data_coverage"]["retrieval"]["downloads_skipped"]
    assert r["status"] == "insufficient_data" and r["prediction"]["probability"] is None
    assert any("downloads are disabled" in w for w in r["warnings"])


# ------------------------------------------------------------------ jobs
def test_concurrent_jobs_do_not_duplicate_downloads(engine, tmp_path):
    settings, state = engine
    calls = []
    state["set_route"](fire_route(-12.0, 30.0, calls=calls))
    jm = JobManager(settings, max_workers=2, store_dir=tmp_path / "jobs")
    a = jm.start_analysis(-12.0, 30.0, 25, history_years=3)
    b = jm.start_analysis(-12.0, 30.0, 25, history_years=3)
    assert a != b
    ra, rb = jm.get_job_result(a, timeout=120), jm.get_job_result(b, timeout=120)
    assert len(calls) == len(set(calls)) and len(calls) > 0
    assert ra["historical_activity"]["total_detections_observed_months"] == rb["historical_activity"]["total_detections_observed_months"]
    sa, sb = jm.get_job_status(a), jm.get_job_status(b)
    assert sa["status"] == sb["status"] == "completed" and sa["progress_percent"] == 100
    reused = ra["data_coverage"]["retrieval"]["chunks_reused_from_concurrent_job"] + rb["data_coverage"]["retrieval"]["chunks_reused_from_concurrent_job"]
    downloaded = ra["data_coverage"]["retrieval"]["chunks_downloaded_this_call"] + rb["data_coverage"]["retrieval"]["chunks_downloaded_this_call"]
    assert downloaded == len(calls) and (reused > 0 or 0 in (ra["data_coverage"]["retrieval"]["chunks_downloaded_this_call"],
                                                              rb["data_coverage"]["retrieval"]["chunks_downloaded_this_call"]))
    jm.shutdown()


def test_job_status_events_stream_and_persistence(engine, tmp_path):
    settings, state = engine
    store = tmp_path / "jobs"
    jm = JobManager(settings, max_workers=1, store_dir=store)
    jid = jm.start_analysis(45.0, 10.0, 25, history_years=3)
    st = jm.get_job_status(jid)
    assert st["status"] in ("queued", "running", "completed") and st["job_id"] == jid

    async def consume():
        return [e async for e in jm.stream_events(jid)]
    evs = asyncio.run(consume())
    assert evs[-1]["type"] == "completed" and evs[-1]["progress_percent"] == 100
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    assert [e["progress_percent"] for e in evs] == sorted(e["progress_percent"] for e in evs)
    res = jm.get_job_result(jid)
    jm.shutdown()
    # a new manager (process restart) sees the completed job and its result
    jm2 = JobManager(settings, store_dir=store)
    st2 = jm2.get_job_status(jid)
    assert st2["status"] == "completed" and st2["restored_from_disk"]
    assert jm2.get_job_result(jid) == json.loads(json.dumps(res))
    jm2.shutdown()


def test_interrupted_job_marked_failed_and_retry(engine, tmp_path):
    settings, state = engine
    store = tmp_path / "jobs"
    store.mkdir(parents=True)
    rec = {"job_id": "deadbeef", "request": INF.validate_request(45.0, 10.0, 25, history_years=3),
           "allow_download": True, "created_at": "2026-01-01T00:00:00+00:00", "retry_of": None,
           "snapshot": {"job_id": "deadbeef", "status": "running", "stage": "download", "progress_percent": 12.0},
           "events_tail": []}
    (store / "deadbeef.json").write_text(json.dumps(rec))
    jm = JobManager(settings, store_dir=store)
    st = jm.get_job_status("deadbeef")
    assert st["status"] == "failed" and st["error"]["type"] == "interrupted" and st["error"]["recoverable"]
    with pytest.raises(JobFailed):
        jm.get_job_result("deadbeef")
    new = jm.retry_job("deadbeef")
    assert jm.get_job_result(new, timeout=120)["status"] in ("ok", "partial")
    assert jm.get_job_status(new)["retry_of"] == "deadbeef"
    jm.shutdown()


def test_api_failure_is_explicit_and_recoverable(engine, tmp_path):
    settings, state = engine
    state["set_route"](fire_route(0, 0, fail=make_response(400, "Invalid MAP_KEY.")))
    jm = JobManager(settings, store_dir=tmp_path / "jobs")
    jid = jm.start_analysis(5.0, 5.0, 25, history_years=3)
    with pytest.raises(JobFailed) as ei:
        jm.get_job_result(jid, timeout=60)
    assert ei.value.error["type"] == "firms_auth" and ei.value.error["recoverable"]
    st = jm.get_job_status(jid)
    assert st["status"] == "failed" and st["progress_percent"] < 100 and settings.map_key not in json.dumps(st)
    jm.shutdown()


def test_unfinished_job_result_raises(engine, tmp_path):
    settings, state = engine
    jm = JobManager(settings, max_workers=1, store_dir=tmp_path / "jobs", persist=False)
    gate = threading.Event()
    orig = state["route"]
    state["set_route"](lambda u: (gate.wait(30), orig(u))[1])
    jid = jm.start_analysis(33.0, 33.0, 25, history_years=3)
    with pytest.raises(JobNotFinished):
        jm.get_job_result(jid)
    gate.set()
    jm.get_job_result(jid, timeout=120)
    jm.shutdown()

