# Frontend integration guide

How the FireHarmonics React frontend (3D globe, location summary panel, Location
Analysis Report) talks to this backend. Every value the frontend shows comes from
these responses. The frontend never computes statistics or probabilities itself.

* Base URL (local): `http://127.0.0.1:8000`. Interactive schema: `/docs`; OpenAPI: `/openapi.json`.
* Result schema version: `result.schema_version == "2.0"`.
* Full example result: [docs/examples/analysis_result.synthetic.json](docs/examples/analysis_result.synthetic.json).
  It was produced by the real engine and the production model, but from
  **synthetic test detections** (one per day, July-September, at 45°N 10°E).
  Use it for shapes only, never as data.
* Engine details and limitations: [docs/ML_INTEGRATION.md](docs/ML_INTEGRATION.md).

## 1. Conventions

| Item | Convention |
| --- | --- |
| Coordinates | WGS 84 decimal degrees (`EPSG:4326`); latitude -90..90, longitude -180..180 |
| Radius | km; the frontend uses a fixed `25` |
| Distance | great-circle (haversine), mean Earth radius 6371.0088 km |
| Dates | `YYYY-MM-DD` (UTC acquisition dates) |
| Months | `YYYY-MM` in series; ML seasonal tables use `"Jan"`..`"Dec"`; descriptive `seasonal_patterns.peak_month` is an integer 1-12 |
| Timestamps | ISO 8601 with offset, e.g. `2026-11-14T22:32:00.101210+00:00` |
| Counts | `detections` = satellite hotspot pixels, **not fires** |
| FRP | megawatts (`*_mw`) |
| Probability | 0..1 or `null`. `null` means "not issued"; never show it as 0 %. |
| Missing data | `null`. Unobserved months have `observed: false` and `detections: null`; they are not zero. |
| Availability | Each block has a `status`. Render the explanation when it is not `ok`/`available`. |

The provider is always NASA FIRMS (a distribution service). The instrument is
MODIS, VIIRS or OLI, and the platform Terra/Aqua, Suomi NPP, NOAA-20/21 or
Landsat. See `result.provenance`.

## 2. Endpoints the frontend uses

| Purpose | Method and path |
| --- | --- |
| Engine/model readiness (show "AI unavailable" if needed) | `GET /api/v1/model` |
| Start a location analysis | `POST /api/v1/analyses` |
| Poll job status | `GET /api/v1/analyses/{job_id}` |
| Live progress (SSE) | `GET /api/v1/analyses/{job_id}/events` |
| Final result | `GET /api/v1/analyses/{job_id}/result` |
| JSON / CSV export | `GET /api/v1/analyses/{job_id}/export?format=json` or `?format=csv&table=...` |
| Retry a failed job | `POST /api/v1/analyses/{job_id}/retry` |
| Saved result for a point (never downloads) | `GET /api/v1/locations/analysis?latitude=..&longitude=..&radius_km=25` |
| Example locations | `GET /api/v1/regions` |
| Harmonization availability | `GET /api/v1/harmonization` |
| Regional harmonized series | `GET /api/v1/harmonization/regions/{region_id}` |
| Data sources | `GET /api/v1/sources` |
| Liveness | `GET /health` |

## 3. Starting an analysis

```http
POST /api/v1/analyses
Content-Type: application/json

{
  "latitude": 37.7,
  "longitude": -119.5,
  "radius_km": 25,
  "location_name": "Yosemite, California",
  "source": "VIIRS_NOAA20_SP",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "ml_mode": "download",
  "target_month": null,
  "history_years": 10
}
```

| Field | Default | Notes |
| --- | --- | --- |
| `latitude`, `longitude` | required | |
| `radius_km` | 25 | Keep 25. Must be 1-200 unless `ml_mode` is `off`. |
| `location_name` | null | Label echoed in `result.location.name`; not used in caching |
| `source` | `VIIRS_NOAA20_NRT` | Series for the **descriptive** block and the raw-sensor view (see section 7) |
| `start_date`/`end_date` or `lookback_days` | last 30 days | Temporal filter for the descriptive block; at most 3650 days |
| `ml_mode` | `download` | `download`, `cache_only` (fast, never downloads) or `off` |
| `target_month` | next month | `YYYY-MM`, current month to +12 months |
| `history_years` | 10 | 3-14; history window of the ML analysis |

Response **202** (captured):

```json
{
 "status_code": 202,
 "job_id": "39153bae-0ddb-4662-81fc-ac037ad3d5c8",
 "status": "queued",
 "progress": 0,
 "stage": "queued",
 "eta_seconds": null,
 "nasa_pause_seconds": null,
 "cache_hit": false,
 "attempt": 1,
 "error": null,
 "message": null,
 "result_status": null,
 "links": {
  "status": "/api/v1/analyses/39153bae-...",
  "events": "/api/v1/analyses/39153bae-.../events",
  "result": "/api/v1/analyses/39153bae-.../result",
  "retry": "/api/v1/analyses/39153bae-.../retry",
  "export": "/api/v1/analyses/39153bae-.../export"
 },
 "request": { "...": "the validated request, with defaults filled in" }
}
```

If a fresh saved result exists, the 202 already has `status: "completed"`,
`progress: 100` and `cache_hit: true`. Go straight to the result.

## 4. Progress: polling or SSE

**Polling.** Call `GET /api/v1/analyses/{job_id}` every 1-2 s until `status` is
`completed` or `failed`.

| Field | Use in UI |
| --- | --- |
| `status` | `queued`, `running`, `paused` (NASA rate-limit wait), `completed`, `failed` |
| `progress` | 0-100, monotonic, from completed work; 100 only when completed |
| `stage` | Machine name. Descriptive: `checking_availability`, `downloading`, `waiting_for_nasa`, `parsing`, `analyzing` (or `loading_archive` when building on an imported archive). ML: `ml_starting`, `ml_plan`, `ml_wait_download_slot`, `ml_download`, `ml_process`, `ml_load`, `ml_patterns`, `ml_predict`, `ml_finalize`. Then `assembling`, `completed`. |
| `message` | Human-readable stage text, e.g. `"Downloading 220 FIRMS request chunks (5 days each)"` |
| `eta_seconds` | Show only when not `null`; never invent one |
| `nasa_pause_seconds` | Show "Waiting for NASA (N s)" when not `null` |
| `result_status` | After completion: `complete`, `partial` or `descriptive_only` |

A new location can take 20-30 minutes, mostly NASA downloads. A cached one
takes seconds.

**SSE.** Subscribe with `new EventSource('/api/v1/analyses/{job_id}/events')`.

```text
id: 2
event: progress
data: {"status_code": 200, "job_id": "39153bae-...", "status": "running", "progress": 1, "stage": "checking_availability", ...}
```

* Event names: `progress`, `completed`, `failed`. Each `data` is the same JSON as
  the status endpoint, without the result.
* IDs are numeric and persistent. Reconnect with `Last-Event-ID` (EventSource
  does this automatically) or `?after_id=N`.
* A `: heartbeat` comment is sent every 15 s; the stream closes after the
  terminal event. Then fetch `/result`.

## 5. Reading the result

`GET /api/v1/analyses/{job_id}/result`

| HTTP | Body | Frontend action |
| --- | --- | --- |
| 200 | `{status_code, job_id, status: "completed", result}` | Render the report |
| 202 | `{status, progress, result: null}` + `Retry-After: 2` | Keep waiting |
| 409 | `{status: "failed", error: {code, detail, recoverable, status_code}}` | Show `detail`; offer Retry if `recoverable` |
| 404 | `{detail: "Analysis job not found"}` | Unknown job |

Top-level result keys: `schema_version`, `status`, `location`, `source`,
`provenance`, `coverage`, `detection_count`, `historical_activity`,
`seasonal_patterns`, `long_term_trends`, `anomalies`, `hotspots`,
`prediction`, `ml_analysis`, `satellite_analysis`, `overview`, `components`,
`warnings`.

`components` tells the report which sections have data:

```json
"components": {"descriptive": "available", "ml_analysis": "ok", "forecast": "ok", "harmonization": "unavailable"}
```

| Component value | Meaning |
| --- | --- |
| `ml_analysis`: `ok` / `partial` | Engine ran; `partial` = some days unobserved (see `ml_analysis.data_coverage`) |
| `ml_analysis`: `insufficient_data` | Engine ran but the record is too short for a forecast |
| `ml_analysis`: `failed` / `unavailable` / `not_run` | No ML content; show `ml_analysis.explanation` and `error` |
| `forecast`: `ok` | `prediction.probability` is a number |
| `forecast`: anything else | `prediction.probability` is `null`; show `prediction.explanation` |
| `harmonization`: `regional_only` / `unavailable` | See section 7 |

### Two data series in one result

| Block | Series |
| --- | --- |
| `historical_activity`, `seasonal_patterns`, `long_term_trends`, `anomalies`, `hotspots`, `detection_count` (top level) | The requested `source` over the requested period (the **descriptive** block) |
| `ml_analysis.*` and `prediction` | Always S-NPP VIIRS (SP, then NRT) over `history_years` (the **ML** block) |

Label every chart with its series (`result.provenance` for descriptive and
`ml_analysis.series` for ML). Never add counts across the two.

## 6. Field mapping per dashboard component

### Globe and location summary panel

| UI element | Field |
| --- | --- |
| Marker / circle | `location.latitude`, `location.longitude`, `location.radius_km` |
| Title | `location.name` (falls back to coordinates) |
| Detection points (descriptive) | `hotspots[]`: `latitude`, `longitude`, `detections`, `max_frp_mw`, `first_date`, `last_date` (0.01° cells, top 100) |
| Recurring hotspot cells (ML) | `ml_analysis.hotspots[]`: `cell_lat`, `cell_lon`, `cell_size_deg`, `total_detections`, `recurring`, `gi_star_z`, `gi_star_hotspot_99` |
| Summary chips | `overview` (below) |

### Overview metrics

`result.overview` holds values copied from the blocks below. Nothing in it is
computed specially for the overview.

| Metric | Field |
| --- | --- |
| Status badge | `overview.status` |
| Detections in selected period | `overview.descriptive.detection_count`, `overview.descriptive.period`, `overview.descriptive.source` |
| 10-year detections (observed months) | `overview.ml.total_detections_observed_months`, `overview.ml.period` |
| Mean detections per year | `overview.ml.mean_annual_detections` (complete years only) |
| Peak fire month | `overview.ml.peak_month` (e.g. `"Sep"`) |
| Trend | `overview.ml.trend_direction`, `overview.ml.trend_pct_per_year`, `overview.ml.trend_significant` |
| Anomalous months | `overview.ml.anomaly_count` |
| Hotspot cells | `overview.ml.gi_star_hotspot_cells` |
| Forecast headline | `overview.forecast.probability`, `.method`, `.target_month`, `.relative_activity` |
| Harmonization badge | `overview.harmonization_status` |

### Burning Activity Calendar

| View | Field |
| --- | --- |
| Daily calendar (selected period, requested source) | `historical_activity.daily_counts[]`: `{date, detections}`. Days appear only when covered. |
| Monthly grid (ML record) | `ml_analysis.historical_activity.monthly[]`: `{month, detections, frp_sum_mw, observed, coverage_fraction, products}`. Grey out `observed: false`. |
| Calendar-month climatology | `ml_analysis.seasonal_patterns.by_calendar_month[]`: `{month, years_observed, years_with_detections, fraction_of_years_with_detections, median_detections, mean_detections}` |
| Season highlight | `ml_analysis.seasonal_patterns.core_season_months_80pct`, `.peak_month`, `.monthly_share_of_annual_detections` |

### Historical Trends

| View | Field |
| --- | --- |
| Annual bars (ML) | `ml_analysis.historical_activity.annual_totals_complete_years` (`{"2017": 2247, ...}`) |
| Trend line and text | `ml_analysis.long_term_trends`: `status`, `method`, `theil_sen_pct_per_year`, `ci95_pct_per_year`, `mann_kendall_tau`, `p_value`, `significant_at_0.05`, `direction`, `years` |
| Monthly series | `ml_analysis.historical_activity.monthly[]` |
| Selected-period counts (requested source) | `historical_activity.monthly_counts[]`, `historical_activity.annual_counts[]` (`complete` flag), `long_term_trends` |

Show "not statistically significant" when `significant_at_0.05` is false.

### AI Pattern Discovery

| View | Field |
| --- | --- |
| Seasonal pattern | `ml_analysis.seasonal_patterns` (`status`, `peak_month`, `core_season_months_80pct`, `lowest_activity_months`) |
| Anomalous months | `ml_analysis.anomalies[]`: `{month, detections, same_month_median_other_years, robust_z, direction, method}`; method details in `ml_analysis.anomaly_detection` |
| Unusual cell-months (Isolation Forest) | `ml_analysis.anomaly_detection.cell_month_outliers[]` and `.cell_month_outliers_method` |
| Recurring hotspots (Gi*) | `ml_analysis.hotspots[]`, `ml_analysis.hotspot_analysis` |
| Limitations | `ml_analysis.limitations[]` |

### AI Forecast

| UI element | Field |
| --- | --- |
| Probability | `prediction.probability` (0..1 or `null`) |
| Plain-language target | `prediction.target_definition` |
| Target month / lead | `prediction.target_month`, `prediction.lead_months`, `prediction.last_observed_month` |
| Method badge | `prediction.method`: `seasonal_baseline`, `xgboost_pooled` or `unavailable` |
| Why this method | `prediction.method_reason` (or `prediction.explanation`) |
| Compared with normal | `prediction.relative_activity.category` (`below_normal`, `near_normal`, `above_normal`, `no_past_activity_this_month`), `.ratio_to_seasonal_norm`, `.recent_observed_months` |
| Seasonal norm | `prediction.seasonal_norm` (`frequency`, `years_observed`, `years_with_detections`) |
| Reliability | `prediction.uncertainty.empirical_reliability` (held-out frequency in this probability bin) |
| Validation evidence | `prediction.validation_evidence` |
| Model comparison | `prediction.model_comparison.xgboost_pooled` (`used: false` means comparison only) and `.seasonal_baseline` |
| Model card | `prediction.model`; global readiness from `GET /api/v1/model` |
| No forecast | When `probability` is `null`, show `prediction.explanation`. If present, also show `prediction.reason` and `prediction.observed_rate_upper_bound_95`. |

Captured `prediction` excerpt (synthetic input; out-of-domain point):

```json
{
 "status": "ok",
 "probability": 0.1,
 "method": "seasonal_baseline",
 "target_month": "2026-12",
 "lead_months": 2,
 "last_observed_month": "2026-10",
 "target_definition": "Probability of at least one usable S-NPP VIIRS vegetation-fire detection within 25 km of the point during 2026-12 (satellite detection, not an individual wildfire ignition).",
 "explanation": "pooled XGBoost did not pass the held-out validation rule for locations outside the five training regions at lead 2 (Brier skill vs seasonal baseline not significantly > 0); seasonal baseline used",
 "model_comparison": {
  "xgboost_pooled": {"probability": 0.0412, "used": false, "validated_for_this_location_and_lead": false},
  "seasonal_baseline": {"probability": 0.1, "variant": "seasonal_raw", "used": true}
 }
}
```

### Satellite Analysis

`result.satellite_analysis`:

```json
{
 "provider": {"name": "NASA FIRMS", "role": "data distribution service (not a sensor)"},
 "descriptive_series": {"product": "VIIRS_NOAA20_NRT", "instrument": "VIIRS", "platforms": ["NOAA-20"], "processing": "near_real_time", "nominal_resolution_m": 375, "product_kind": "raw_detections"},
 "forecast_series": {"products": ["VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"], "instrument": "VIIRS", "platforms": ["Suomi NPP"], "used": true},
 "views": {
  "raw_modis": {"status": "separate_request", "source": "MODIS_SP", "detection_count": null},
  "raw_viirs": {"status": "this_result", "source": "VIIRS_NOAA20_NRT", "detection_count": 1},
  "harmonized": {"status": "unavailable", "location_scale": "not_validated", "explanation": "..."}
 },
 "modis_viirs_mixed": false,
 "warnings": ["Raw MODIS (1 km) and VIIRS (375 m) detection counts are not comparable and are never summed.", "..."]
}
```

Also use `ml_analysis.harmonization` (engine note and regional status) and
`ml_analysis.data_coverage` (products, availability, unobserved months,
retrieval statistics, attribution).

## 7. Switching Raw MODIS / Raw VIIRS / Harmonized

Each analysis describes **one** FIRMS source. To switch views:

| View | What to do |
| --- | --- |
| Raw VIIRS | `POST /api/v1/analyses` with a VIIRS `source` (`VIIRS_NOAA20_SP`/`_NRT`, `VIIRS_NOAA21_*`, `VIIRS_SNPP_*`) |
| Raw MODIS | `POST /api/v1/analyses` with `source: "MODIS_SP"` (archive) or `"MODIS_NRT"` (recent). Use `ml_mode: "cache_only"` or `"off"` to avoid repeating the ML stage. Its results don't depend on `source`. |
| Harmonized | Only if `satellite_analysis.views.harmonized.status == "regional_only"`: `GET` its `series_url` |

`views.raw_modis` and `views.raw_viirs` show which view the current result
covers (`this_result`) and which needs another request (`separate_request`).

Harmonized data is **regional only**. It is a MODIS-equivalent monthly series
for 2012-2021, for five training regions. It covers the jointly observed domain
and is not validated at the 25 km scale.

```http
GET /api/v1/harmonization/regions/zambia_angola
```

```json
{
 "status": "regional_only",
 "location_scale": "not_validated",
 "region_id": "zambia_angola",
 "domain": "jointly observed: VIIRS fire clusters that Aqua-MODIS also observed within 20 min, vegetation fires only",
 "prediction_type": "held-out: each year predicted by a model trained on the other years",
 "validation": {"count": {"status": "validated", "...": "..."}, "frp": {"status": "not_validated", "...": "..."}},
 "interval_method": "split-conformal (nominal 90%)",
 "data": [
  {"month": "2012-01", "viirs_raw": {"count": 4.0, "frp_mw": 26.78},
   "modis_equivalent": {"count": 0.242512, "count_interval_90": [0.062695, 0.28924], "frp_mw": 9.796992, "frp_interval_90": [3.307264, 11.218831]},
   "modis_aqua_observed": {"count": 0.0, "frp_mw": 0.0}}
 ]
}
```

Display `validation.count.status` and `validation.frp.status` next to the
chart. FRP is not fully validated in any region. `GET /api/v1/harmonization`
lists all five regions and their statuses.

### Harmonized MODIS+VIIRS calendar, season timing and current conditions

`ml_analysis.unified_calendar` (FireLens, local overlap calibration) is the burning-activity calendar from 2002:
Aqua MODIS before 2012 and S-NPP VIIRS × `ratio` from 2012 (`monthly[]`: `month`, `value`, `source`
`modis_aqua` | `viirs_snpp_scaled`, `interval_95`, raw `modis_aqua` / `viirs_snpp`). `ratio`, `ratio_ci95`, `overlap`
and `validation` (leave-one-year-out on 2012-2021: `median_abs_pct_error_annual` vs `..._raw_viirs`,
`monthly_correlation_modis_vs_viirs`, `by_year[]`) describe the calibration; always show `label` and `limitations`.
`unified_calendar.season` gives typical `onset` / `peak` / `end` months and `current.onset_status`
(`earlier_than_usual`, `later_than_usual`, `as_usual`, `not_started_later_than_usual`).
`ml_analysis.current_conditions` compares the last 14 days of NOAA-20 NRT detections with the same window in past
S-NPP years (`category`: `highest_on_record`, `well_above_normal`, `above_normal`, `near_normal`, `below_normal`,
`quiet_as_usual`, `isolated_detections`). Both blocks have a `status`; render `explanation` when it is not `ok`.
CSV: `?format=csv&table=calendar`. `radius_km` may be 10, 25, 50 or 100 km (the forecast model's training discs).

### Normalized view (where no harmonized series exists)

`satellite_analysis.views.normalized` is a **relative activity index**, not a harmonization: each month's detections
divided by the mean monthly detections of the same series over its observed months (`1.0` = an average month). It is
computed for the ML record (S-NPP VIIRS) and, with at least 12 complete months, for the requested source. Label it
with `label` ("Normalized — not harmonized, not validated") and show `definition` and `limitations`.

```json
"normalized": {"status": "available", "method": "relative_activity_index", "validated": false,
 "label": "Normalized — not harmonized, not validated",
 "series": [{"series": "ml_record", "instrument": "VIIRS", "status": "available",
             "reference_period": {"start": "2023-11", "end": "2026-10", "months": 36},
             "reference_mean_monthly_detections": 7.6667,
             "monthly": [{"month": "2024-07", "detections": 31, "index": 4.0435},
                         {"month": "2026-11", "detections": null, "index": null}]}]}
```

`status` is `insufficient_data` when no series has 12 usable months; a series with no detections has
`status: "no_activity"`. Unobserved months have `index: null`, never 0.

`GET /api/v1/harmonized` (older endpoint) returns **raw detections** of a saved
analysis. It is labelled `harmonization_status: "not_harmonized"`; don't use it
for the Harmonized view.

## 8. Exports

| Export | Request |
| --- | --- |
| JSON (full report) | `GET /api/v1/analyses/{job_id}/export?format=json`, an attachment with `{job_id, request, result}` |
| CSV | `GET /api/v1/analyses/{job_id}/export?format=csv&table=TABLE` |
| PDF | Render on the frontend from the JSON result (no backend PDF) |

CSV tables: `daily`, `monthly`, `annual`, `hotspots` (descriptive, requested
source); `ml_monthly`, `ml_seasonal`, `ml_anomalies`, `ml_hotspots` (ML,
S-NPP VIIRS). Booleans are `true`/`false`, missing values are empty cells and
nested values are JSON. An ML table returns **409** when the ML stage did not
run. Captured `ml_monthly` start:

```text
month,detections,frp_sum_mw,observed,coverage_fraction,products
2023-11,0,0.0,true,1.0,VIIRS_SNPP_SP
```

## 9. Errors

All handled errors have `{status_code, detail, recoverable}`. Schema-validation 422s also have `errors` (field-level details).

| HTTP | When | UI |
| --- | --- | --- |
| 422 | Invalid coordinates, dates, `target_month` (format, past, > 12 months), `history_years`, radius > 200 with ML, unknown field | Fix the input |
| 429 | Queue full (32 active jobs) or NASA pause still active on retry; has `Retry-After` | Wait and retry |
| 500 | `NASA_FIRMS_MAP_KEY` missing (`recoverable: true`) or internal error | Configuration problem |
| 502 / 504 | NASA error or timeout | Retry later |
| 409 | Result of a failed job; retry of a non-recoverable job; export of an unfinished job or missing table | See `detail` |

ML failures do **not** fail the job. They give `result.status: "partial"`,
`ml_analysis.status: "failed"`, `ml_analysis.error: {code, detail, recoverable}`,
`prediction.probability: null` and a `warnings` entry starting `ML engine:`.
Engine error codes: `firms_auth`, `firms_rate_limited`, `network`,
`firms_request_rejected`, `data_unavailable`, `model_unavailable`,
`invalid_request`, `internal_error`, `engine_not_installed`, `engine_disabled`.
Partial results are not cached, so posting the same request again retries.

## 10. Recommended frontend flow

1. On load: `GET /api/v1/model`. If `available` is false, show forecast
   sections as unavailable (analyses still return descriptive data).
2. User picks a point: optionally `GET /api/v1/locations/analysis` for an
   instant saved result.
3. `POST /api/v1/analyses` with `radius_km: 25` and `location_name`.
4. Follow `links.events` (SSE) or poll `links.status`; show `progress`,
   `message`, `eta_seconds` and `nasa_pause_seconds`.
5. On `completed`: `GET links.result`; drive each section from `components`.
6. On `failed`: show `error.detail`; offer `POST links.retry` when `recoverable`.
7. Exports: `links.export` with `format`/`table`.

Always display `warnings[]` and `ml_analysis.limitations[]` in the report.
Phrase the forecast as "probability of at least one satellite hotspot
detection", not as a fire or ignition probability.
