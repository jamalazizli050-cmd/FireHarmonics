# FireLens global inference API (Python)

FireLens exposes one importable engine that analyses **any point on Earth**:

- historical fire activity;
- seasonal and long-term patterns;
- anomalies and recurring hotspots;
- a next-month forecast with its method, evidence and limitations.

It is a Python module, not an HTTP service. The backend calls it directly and can wrap it in SSE or WebSocket endpoints.

| You want | Call |
|---|---|
| One blocking call | `firelens.inference.analyze_location(...)` |
| Non-blocking job + polling | `start_analysis(...)`, then `get_job_status(job_id)` and `get_job_result(job_id)` |
| Push progress (SSE/WebSocket) | `progress_callback=`, `JobManager.subscribe(...)` or `async for e in JobManager.stream_events(...)` |

Both paths run the same code (`firelens.inference.run_analysis`) and return the same JSON-serialisable dict.

> **Scope.** This is a working worldwide *prototype*, not a globally validated fire model. Numbers are satellite **hotspot detections**, not fires or ignitions. The forecast method for each location is chosen by a held-out validation rule; at present that rule selects the **seasonal baseline** everywhere (see §6).

## 1. Quick start

```python
from firelens.inference import analyze_location

r = analyze_location(37.7, -119.5)                     # radius 25 km, target = next calendar month
r["prediction"]["probability"], r["prediction"]["method"]
```

Background job:

```python
from firelens.inference import start_analysis, get_job_status, get_job_result
from firelens.jobs import JobNotFinished, JobFailed

job_id = start_analysis(-13.0, 24.0, radius_km=50)     # returns immediately
get_job_status(job_id)          # {"status": "running", "stage": "download", "progress_percent": 31.4, ...}
try:
    result = get_job_result(job_id)                    # or get_job_result(job_id, timeout=600) to wait
except JobNotFinished:
    ...                                                # still queued/running
except JobFailed as e:
    e.error                     # {"type": "network", "message": "...", "recoverable": true, "failed_stage": "download"}
```

**Requirements.**

- Run from the project's virtual environment (`pip install -e .`).
- New locations need `NASA_FIRMS_MAP_KEY` in `firelens/.env`.
- The trained model must exist: `data/models/global/global_disc_forecaster.*`, created by `python scripts/train_global_model.py`.
- A location that is already fully cached runs without a key.

## 2. Parameters

| Parameter | Type | Default | Allowed | Notes |
|---|---|---|---|---|
| `latitude` | float | (required) | −90…90 | |
| `longitude` | float | (required) | −180…180 | Discs crossing the antimeridian or a pole are handled. |
| `radius_km` | float | 25 | 1…200 | Disc on the sphere (great-circle distance). |
| `target_month` | `"YYYY-MM"` or `None` | next calendar month | current month … current month + 12 | Past months are rejected; use `historical_activity` for those. |
| `history_years` | int | 10 | 3…14 | History requested before the current month. S-NPP data start 2012-01-20. |
| `allow_download` | bool | True | | `False` means cache only: missing days become unobserved months, and the result is marked `partial` or `insufficient_data`. |
| `progress_callback` | callable(dict) | None | | `analyze_location`. The job API uses `callback=` on `start_analysis`. |
| `settings` | `firelens.config.Settings` | from `.env` | | For tests or alternative data directories. |

Invalid input raises `firelens.inference.InvalidRequestError` (a `ValueError`) *before* any work. With the job API it is raised by `start_analysis`, and no job is created.

## 3. What happens for a new location

1. The analysis needs every detection within `max(radius, 100 km)` of the point; the 100 km disc is the regional context.
2. That disc's bounding box is snapped to a 1° grid. Nearby requests therefore share one cache *tile*.
3. **Data source.** The series is a single consistent sensor, S-NPP VIIRS 375 m:
   - `VIIRS_SNPP_SP` up to its last available day;
   - `VIIRS_SNPP_NRT` after that.

   MODIS and VIIRS counts are never mixed.
4. **Cache reuse.** For each 5-day FIRMS request chunk, the planner looks for any cached region containing the tile, for example the five project regions for 2012–2022 or earlier tiles. Only chunks found nowhere are downloaded.
5. **Download cost** (measured on 2026-11-14, see `GLOBAL_ENGINE_REPORT.md`).
   - A brand-new location needs about 73 requests per year of history; the default 10 years is 733 requests.
   - Requests took 0.66–0.80 s each, so 733 requests are about 10 minutes of transfer.
   - In that run the FIRMS budget guard paused for 10 minutes about once every 430 requests. A new location therefore took **20–30 minutes** of download in practice.
   - Inside a project region, 2012–2022 are already cached, so only 281 requests (2023 onwards) were needed: about 3 minutes without a pause.
   - A second call for the same 1° tile makes no requests and returns in **1–3 s**.
6. **Coverage.**
   - A day counts as observed only if it was retrieved, lies inside the product's availability window, and is not on FIRMS's missing-data list for Suomi NPP.
   - A month is **observed** when ≥ 90% of its days are observed. Observed months with no detections are real zeros. Other months are `null`, never 0.
   - The current month is always partial, so it is unobserved.
7. **Vegetation filter.**
   - `VIIRS_SNPP_SP` keeps FIRMS `type = 0` (vegetation fire).
   - `VIIRS_SNPP_NRT` has no `type` column. For NRT, detections are dropped in 0.01° cells where the SP product flagged a static source (volcano, industrial, offshore) within the window.

**Rate limits.** All jobs in a process share one FIRMS client and one download lock. The client:

- throttles requests;
- polls the MAP_KEY transaction counter and pauses at 80% of the 5,000-per-10-minutes limit;
- retries 429 and 5xx responses with back-off.

Pauses of 30 s or more are reported as `rate_limit_wait` warnings. While such a pause lasts, progress events carry `waiting` and `eta_seconds` is `null` (§8).

## 4. Output schema (`schema_version` "1.0")

Top-level keys are always present.

| Key | Content |
|---|---|
| `status` | `"ok"`: complete data and a forecast. `"partial"`: a forecast exists, but some days could not be retrieved. `"insufficient_data"`: no probability is issued; see `prediction.reason`. |
| `location` | `latitude`, `longitude`, `radius_km`, `cache_tiles` |
| `request` | the normalised request, including `request_date_utc` |
| `historical_activity` | Monthly series (`detections`/`frp_sum_mw` are `null` when unobserved), annual totals for complete years, mean/median per year, detections per 1000 km² per year, max month, mean FRP per detection, night share |
| `seasonal_patterns` | Calendar-month stats (years observed, years with detections, median/mean), share of annual detections per month, `peak_month`, `core_season_months_80pct`, `lowest_activity_months`, `status` |
| `long_term_trends` | Theil-Sen % per year on log10 annual totals with 95% CI, Mann-Kendall τ and p, `significant_at_0.05`, interannual CV; needs ≥ 5 complete years |
| `anomalies` | Months with robust \|z\| ≥ 3 vs the same calendar month in other years (≥ 4 other years) *and* a departure ≥ 1% of mean annual detections |
| `anomaly_detection` | Test metadata plus `cell_month_outliers` (Isolation Forest on 0.05° cell-months, when ≥ 100 exist) |
| `hotspots` | Top 0.05° cells inside the disc: totals, mean per year, years active, `recurring`, Gi* z, `gi_star_hotspot_99` |
| `hotspot_analysis` | Grid size, number of hotspot cells, share of detections in Gi* hotspots, method |
| `prediction` | See §5 |
| `data_coverage` | Products and date ranges, observed/unobserved months (with coverage fraction), last observed month, retrieval counts for this call, cache sources, exclusions, `complete`, attribution |
| `harmonization` | `modis_viirs_mixed: false`, the project region (if any) and its regional harmonisation status, with an explanation |
| `warnings` | Run-time warnings (failed chunks, downloads disabled, rate-limit waits) |
| `limitations` | Standing and location-specific limitations, as plain strings |

All values are JSON builtins. NaN is converted to `null`, and the engine checks `json.dumps(result, allow_nan=False)` before returning.

## 5. `prediction`

| Field | Meaning |
|---|---|
| `status` | `"ok"` or `"insufficient_data"` (with `reason`) |
| `target_month`, `last_observed_month`, `lead_months` | Lead = target − last fully observed month. Default requests are usually lead 2, because the current month is incomplete. |
| `probability` | P(≥ 1 usable S-NPP VIIRS vegetation-fire detection within the radius during the target month) |
| `method` | `"seasonal_baseline"` or `"xgboost_pooled"` |
| `method_reason` | Why that method was chosen for this location and lead |
| `seasonal_norm` | Observed share of past years with ≥ 1 detection in that calendar month, plus median/mean detections |
| `relative_activity` | `ratio_to_seasonal_norm` and `category`, plus the last 3 observed months vs their same-month medians |
| `validation_evidence` | Held-out metrics of the chosen method in the matching setting, and XGBoost's Brier skill vs the baseline |
| `uncertainty.empirical_reliability` | Among held-out forecasts in the same probability bin, how often a detection occurred. No per-location interval is given, because none is empirically supported. |
| `model_comparison` | Both probabilities, with flags for which was used and whether XGBoost was validated there. **Show only `probability` as the forecast.** |

`relative_activity.category` is:

- `below_normal` if the ratio < 0.67;
- `above_normal` if the ratio > 1.5;
- `near_normal` otherwise, or when both probabilities are below 0.02;
- `no_past_activity_this_month` when no past year had a detection in that calendar month. The ratio is then undefined, and the probability comes only from the disc's overall fire frequency (the shrinkage prior). Yakutia in December is an example.

**`insufficient_data` reasons:**

| Reason | Meaning |
|---|---|
| `no_observed_months` | Nothing is cached or retrievable. |
| `insufficient observed history: …` | Fewer than 36 observed months, or fewer than 3 observed years of the target calendar month. |
| `no_fire_detections_within_100km` | Zero detections in the whole context disc. Open water, ice, bare rock or an unmonitored surface cannot be told apart from a very rare fire regime. A one-sided 95% upper bound on the monthly rate is given instead of a probability. |
| `target is N months after the last observed month` | The data are too stale for the 12-month horizon. |

## 6. Forecast methods and the selection rule

Two methods are available.

- **Seasonal baseline.** The share of past years with fire in the same calendar month, shrunk towards the disc's overall monthly fire frequency with 2 pseudo-years. For some regions an isotonic-calibrated variant is used instead.
- **Pooled XGBoost.**
  - Trained on discs of 10, 25, 50 and 100 km in the five project regions, 2012–2022, with leads 1–3.
  - Features:
    - latitude, |latitude|, log disc area;
    - month and hemisphere-adjusted season encodings;
    - lags, rolling means, expanding frequency, same-month climatology, months since last fire;
    - 100 km context activity.
  - All features use only months ≤ t − lead.
  - It is boosted from the baseline logit and calibrated with isotonic regression.

The selection rule was fixed in code before the results were read (`firelens/global_model.py`):

- **Inside a training region:** use XGBoost if its temporal hold-out (2020–2022) Brier skill vs the best baseline is > 0, with a month-block bootstrap lower bound > 0.
- **Anywhere else:** use XGBoost only if it beats the baseline in ≥ 4 of 5 leave-one-region-out folds, with a pooled bootstrap lower bound > 0.
- **Leads above 3:** always the baseline.

**Current outcome:** the rule selects the seasonal baseline for every region and lead. See `GLOBAL_ENGINE_REPORT.md` for the numbers.

## 7. Job API

`firelens.inference.start_analysis / get_job_status / get_job_result` use one process-wide `JobManager` (`firelens.jobs.default_manager()`). A backend that wants its own settings or job directory can create one directly:

```python
from firelens.jobs import JobManager
jm = JobManager(settings=None, max_workers=2, store_dir=None, persist=True)   # store_dir defaults to data/jobs
```

| Method | Returns / raises |
|---|---|
| `start_analysis(lat, lon, radius_km=25, target_month=None, *, history_years=None, allow_download=True, callback=None)` | Job ID (str), at once. Invalid input raises `InvalidRequestError` and creates no job. |
| `get_job_status(job_id)` | Snapshot dict: `status`, `stage`, `stage_label`, `progress_percent`, `stage_progress`, `indeterminate`, `eta_seconds`, `waiting`, `message`, `warnings`, `error`, `started_at`, `finished_at`, `last_event_seq`, `request`, `created_at`, `retry_of`, `restored_from_disk`, `result_available`. Unknown ID raises `JobNotFound`. |
| `get_job_result(job_id, timeout=None)` | The result dict (§4). Raises `JobNotFinished` while queued/running, or `JobFailed` with `.error`. With `timeout`, waits up to that many seconds first. |
| `get_events(job_id, after_seq=0)` | Events with `seq > after_seq`, for polling clients that resume from the last seen event |
| `subscribe(job_id, callback, replay=True)` | Registers `callback(event)` and returns `unsubscribe()`. Past events are replayed atomically with registration, so none are lost or duplicated. Callbacks run on the worker thread. |
| `async stream_events(job_id, after_seq=0)` | Async iterator for asyncio back-ends; ends after `completed` or `failed` |
| `retry_job(job_id, allow_download=None)` | New job ID for the same request (`retry_of` links them). Chunks already downloaded are reused. |
| `list_jobs()`, `shutdown(wait=True)` | |

**Statuses:** `queued` → `running` → `completed` or `failed`. At most `max_workers` jobs run at once; the rest stay `queued`.

**Concurrency and downloads.**
- All jobs in a process share one FIRMS client and one download lock, so only one job downloads at a time. That job is in stage `download`; others that need new data wait in `wait_download_slot` (indeterminate).
- After taking the lock a job re-plans against the cache, so a chunk that another job has just fetched is not fetched again (`data_coverage.retrieval.chunks_reused_from_concurrent_job`).
- Jobs whose data are already cached never wait for the lock and run in parallel.

**Errors.** `error = {"type", "message", "recoverable", "failed_stage"}`. Messages never contain the MAP_KEY.

| `type` | Cause | `recoverable` |
|---|---|---|
| `firms_auth` | MAP_KEY missing or rejected | true (fix `.env`, then `retry_job`) |
| `firms_rate_limited` | FIRMS kept refusing after retries | true |
| `network` | Timeout or connection failure | true |
| `firms_request_rejected` | FIRMS rejected the request itself | false |
| `data_unavailable` | No product covers the requested period | true |
| `missing_file` | Model or cache file missing (for example, the model was not trained) | true |
| `interrupted` | The process stopped while the job was queued or running | true |
| `invalid_request` | Raised synchronously by `start_analysis`; not stored as a job | false |
| `internal_error` | Anything else (a bug) | false |

A failed chunk does not fail the job. The affected days become unobserved, `status` becomes `partial` and a warning is added.

**Persistence (implemented and tested in `tests/test_global_engine.py`).**
- Job records are written to `data/jobs/<job_id>.json` on creation, on every stage change and warning, and on completion or failure. A completed job's result is stored as `<job_id>.result.json`.
- A new `JobManager` in a new process loads that directory. Status, the stored tail of events and the results of completed jobs are available again (`restored_from_disk: true`).
- Jobs that were `queued` or `running` when the process stopped are marked `failed` with type `interrupted`. Call `retry_job` to run them again.
- **Not persisted:** live callbacks and streams (clients reconnect and pass `after_seq`), and the download lock, which is per process. Two *processes* analysing the same new tile at the same moment can both download it, so run one engine process or route analyses through one worker.
- `persist=False` keeps everything in memory.

## 8. Progress events

Every event has the same fields:

| Field | Meaning |
|---|---|
| `job_id`, `seq` | `seq` increases by 1 per event within a job |
| `type` | `status`, `stage` (a stage started), `progress`, `stage_done`, `warning`, `completed`, `failed` |
| `time`, `status` | UTC ISO time; the job status at that moment |
| `stage`, `stage_label`, `stage_index`, `n_stages` | One of 9 stages: validate, plan, wait_download_slot, download, process, load, patterns, predict, finalize |
| `progress_percent` | 0–100, monotonic, at most 99.0 until `completed`, then exactly 100.0 |
| `stage_progress` | `{completed, total}` units for a determinate stage (FIRMS request chunks in `download`, processed years in `process`), otherwise `null` |
| `indeterminate` | `true` while a stage of unknown length runs; no fraction is invented |
| `eta_seconds` | Only in `download`, after at least 3 requests: measured seconds per request (excluding known waits) × remaining requests, plus the nominal later stages. Otherwise `null`. |
| `waiting` | `{reason, seconds, resume_at}` during a FIRMS budget pause or retry back-off of 30 s or more, otherwise `null`. `eta_seconds` is `null` while waiting. |
| `message` | Human-readable line |
| `warning` / `error` | Present on `warning` and `failed` events |

**How the percentage is made.**
- Each stage takes a share of the bar proportional to its expected work.
- The download share is `requests × 2.5 s` and the process share is `years × 2 s`, both known once the plan is made. Small fixed shares cover the other stages.
- Within download and process, the fraction is the real number of completed units.
- For a fully cached location, download and process have zero weight, so the bar moves through the fixed stages only.

**Real events** from the Zambia job in the 2026-11-14 run, trimmed to the fields listed:

```json
{"seq": 6,   "type": "stage",    "status": "running", "stage": "wait_download_slot", "progress_percent": 1.3,  "stage_progress": null, "indeterminate": true,  "eta_seconds": null, "message": "Waiting for the shared FIRMS download slot"}
{"seq": 8,   "type": "stage",    "status": "running", "stage": "download", "progress_percent": 1.3,  "stage_progress": {"completed": 0, "total": 281},   "indeterminate": false, "eta_seconds": null, "message": "Downloading 281 FIRMS request chunks (5 days each)"}
{"seq": 10,  "type": "progress", "status": "running", "stage": "download", "progress_percent": 1.3,  "stage_progress": {"completed": 2, "total": 281},   "indeterminate": false, "eta_seconds": null, "message": "VIIRS_SNPP_SP: ok (0 rows)"}
{"seq": 147, "type": "warning",  "status": "running", "stage": "download", "progress_percent": 48.2, "stage_progress": {"completed": 138, "total": 281}, "message": "VIIRS_SNPP_SP: ok (2 rows)", "warning": {"kind": "rate_limit_wait", "message": "Waiting 600s for the FIRMS transaction budget / retry back-off"}}
{"seq": 292, "type": "stage",    "status": "running", "stage": "process",  "progress_percent": 97.9, "stage_progress": {"completed": 0, "total": 5}, "indeterminate": false, "eta_seconds": null}
{"seq": 301, "type": "stage",    "status": "running", "stage": "patterns", "progress_percent": 99.0, "stage_progress": null, "indeterminate": true, "eta_seconds": null, "message": "Computing historical statistics and patterns"}
{"seq": 307, "type": "completed","status": "completed","stage": "finalize","progress_percent": 100.0,"stage_progress": null, "indeterminate": false, "eta_seconds": null, "message": "Analysis complete"}
```

This run happened before the `waiting` field existed, and it showed two ETA errors:
- event 147 still carried the ETA from before the pause (129 s);
- the first ETA after the pause (731 s) counted the 600 s pause as download time.

Both are now fixed:
- during a pause the event reads `"eta_seconds": null, "waiting": {"reason": "rate_limit_wait", "seconds": 600.0, "resume_at": "…"}`;
- a `progress` event marks the end of the wait;
- the ETA after it excludes the pause.

The test `test_rate_limit_wait_withholds_eta_and_is_excluded_from_rate` covers this.

## 9. Wiring into SSE or WebSocket (example only)

FireLens contains no web server. These sketches show how a backend can bridge the events; adapt them to your framework.

```python
# SSE with an asyncio framework (e.g. a FastAPI/Starlette StreamingResponse body)
import json
from firelens.jobs import default_manager

async def sse(job_id: str, last_event_id: int = 0):
    async for ev in default_manager().stream_events(job_id, after_seq=last_event_id):
        yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
```

```python
# WebSocket
async def ws_handler(websocket, job_id: str):
    async for ev in default_manager().stream_events(job_id):
        await websocket.send_json(ev)
    await websocket.send_json({"type": "result", "result": default_manager().get_job_result(job_id)})
```

```python
# Thread-based servers: subscribe and push into your own queue
unsubscribe = default_manager().subscribe(job_id, my_queue.put)
```

Polling clients call `get_job_status(job_id)` every 1–2 s, or `get_events(job_id, after_seq=last_seq)`, and fetch `get_job_result(job_id)` once `result_available` is true. Note that `get_job_result` raises `JobFailed` for a failed job.

## 10. Example output (real, trimmed)

Zambia (−13.0, 24.0), 25 km, requested 2026-11-14:

```json
{"status": "ok",
 "location": {"latitude": -13.0, "longitude": 24.0, "radius_km": 25.0, "cache_tiles": ["23,-14,25,-12"]},
 "prediction": {
  "status": "ok", "target_month": "2026-12", "last_observed_month": "2026-10", "lead_months": 2,
  "probability": 0.41, "method": "seasonal_baseline", "baseline_variant": "seasonal_calibrated",
  "method_reason": "pooled XGBoost did not pass the held-out validation rule for locations inside training region zambia_angola at lead 2 (Brier skill vs seasonal baseline not significantly > 0); seasonal baseline used",
  "seasonal_norm": {"calendar_month": "December", "years_observed": 10, "years_with_detections": 4, "frequency": 0.4, "median_detections": 0.0, "mean_detections": 0.8},
  "relative_activity": {"ratio_to_seasonal_norm": 1.025, "category": "near_normal"},
  "validation_evidence": {"setting": "temporal hold-out (2020-2022) in zambia_angola", "lead_evaluated": 2,
    "method_metrics_heldout": {"n": 3264, "base_rate": 0.700, "brier": 0.0629, "roc_auc": 0.962},
    "xgboost_vs_best_baseline_brier_skill": {"bss": -0.013, "ci95": [-0.111, 0.079], "n_months": 34}},
  "uncertainty": {"empirical_reliability": {"forecast_bin": [0.3, 0.5], "n_heldout_forecasts": 2397, "observed_frequency_heldout": 0.4806, "mean_forecast_heldout": 0.4256}}},
 "data_coverage": {"months_observed": 115, "months_unobserved": 6, "last_observed_month": "2026-10",
  "products": [{"product": "VIIRS_SNPP_SP", "first_day": "2016-11-01", "last_day": "2026-07-31"},
               {"product": "VIIRS_SNPP_NRT", "first_day": "2026-08-01", "last_day": "2026-11-02"}]},
 "harmonization": {"modis_viirs_mixed": false, "project_region": "zambia_angola"}}
```

Open ocean (−30.0, −140.0): no probability is issued.

```json
{"status": "insufficient_data", "probability": null, "method": null,
 "reason": "no_fire_detections_within_100km",
 "explanation": "No usable vegetation-fire detection within 100 km in 115 observed months. The satellite record cannot distinguish a non-flammable or unmonitored surface (open water, ice, bare rock, dense urban area) from a fire regime too rare to observe, so no probability is issued.",
 "observed_rate_upper_bound_95": {"value": 0.2589, "definition": "one-sided 95% upper bound on the per-month rate for December from 0 events in 10 observed years (exact binomial)"}}
```

The full outputs for the six test locations are in `reports/results/global/locations/`.

## 11. Displaying results honestly

- Show `prediction.probability` together with its `target_definition`. Never show `model_comparison.xgboost_pooled.probability` as a forecast.
- Show `method` and `method_reason`. When `status` is `insufficient_data`, show `reason` and `explanation`, not a number.
- Say "satellite fire detections", not "fires".
- Surface `warnings` and `status: "partial"`.
- Do not present results outside the five project regions as validated. The out-of-region evidence is leave-one-region-out *within* those five regions.
- Attribution: data from NASA FIRMS (S-NPP VIIRS 375 m); see `data_coverage.attribution`.
