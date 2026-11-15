# API reference

[Back to README](../README.md)

Run the backend using [Setup](SETUP.md).
Frontend developers: start with [FRONTEND_INTEGRATION.md](../FRONTEND_INTEGRATION.md).
The ML engine is described in [ML integration](ML_INTEGRATION.md).

Local address: `http://127.0.0.1:8000`.

Open <http://127.0.0.1:8000/docs> to try the endpoints.
Select an endpoint, then **Try it out**, then **Execute**.

## Main flow: analyse a map point

### 1. Start the analysis

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyses \
  -H 'Content-Type: application/json' \
  -d '{"latitude":-13,"longitude":24,"radius_km":25}'
```

Expected status: **202**.
Copy `job_id` from the response.

The response also includes links to status, events, result, and retry.
The download runs in a background worker.

Optional body fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `source` | `VIIRS_NOAA20_NRT` | NASA dataset |
| `lookback_days` | `30` | Days ending at the latest available date |
| `start_date` | Not set | First requested day, as `YYYY-MM-DD` |
| `end_date` | Not set | Last requested day, as `YYYY-MM-DD` |
| `ml_mode` | `download` | FireLens stage: `download`, `cache_only` (never downloads) or `off` |
| `target_month` | Next month (UTC) | Forecast month `YYYY-MM`, current month to 12 months ahead |
| `history_years` | `10` | History of the ML analysis, 3-14 years |
| `location_name` | Not set | Display label echoed in `result.location.name` |

An explicit `start_date` replaces the lookback calculation.
The maximum period is 3650 days. Future dates are rejected.
Radius must be greater than zero and at most 500 km, or at most 200 km when `ml_mode` is not `off`.

The period fields apply to the descriptive statistics of `source`.
The ML stage always uses S-NPP VIIRS over `history_years`; see [ML integration](ML_INTEGRATION.md).

Availability can shorten the requested period.
The result states both the requested and downloaded dates.

### 2. Check progress

Replace `JOB_ID` with the ID you copied.

```bash
curl http://127.0.0.1:8000/api/v1/analyses/JOB_ID
```

Poll every 1–2 seconds until `status` is `completed` or `failed`.

| Field | Meaning |
| --- | --- |
| `status` | `queued`, `running`, `paused`, `completed`, or `failed` |
| `progress` | Progress from 0 to 100, weighted by completed download requests and processing stages |
| `stage` | Current operation, such as `downloading` or `analyzing` |
| `eta_seconds` | Estimate from observed request times, or `null` when unknown |
| `nasa_pause_seconds` | NASA retry pause, or `null` |
| `cache_hit` | Whether a saved result was reused |
| `error` | Failure details, including `recoverable`, or `null` |
| `message` | Human-readable stage description, or `null` |
| `result_status` | After completion: `complete`, `partial` or `descriptive_only` |

ML stages are prefixed `ml_` (for example `ml_download`, `ml_patterns`, `ml_predict`).

Progress is not a prediction of exact runtime.
NASA limits and the selected period affect the duration.

For a live SSE stream instead of polling:

```bash
curl -N http://127.0.0.1:8000/api/v1/analyses/JOB_ID/events
```

SSE event names are `progress`, `completed`, and `failed`.
Each event contains a job-status JSON object and a numeric event ID.

Reconnect with `Last-Event-ID` or `?after_id=EVENT_ID` to read newer events.
The stream closes after the terminal event.

### 3. Read the result

```bash
curl -i http://127.0.0.1:8000/api/v1/analyses/JOB_ID/result
```

| HTTP status | Meaning |
| --- | --- |
| 200 | Completed; read `result` |
| 202 | Still working; `result` is `null` |
| 409 | Failed; read `error` |
| 404 | The job does not exist |

## What the result contains

| Field inside `result` | Meaning |
| --- | --- |
| `schema_version` | `"2.0"` for results produced by this version |
| `status` | `complete`, `partial` (ML failed/unavailable or incomplete data) or `descriptive_only` |
| `location` | Centre, radius, `name`, coordinate reference and distance method |
| `source`, `provenance` | Requested FIRMS product: provider, instrument, platforms, processing, requested and observed period |
| `coverage` | Dates, origin, and completeness of the descriptive data |
| `detection_count` | Unique detections inside the circle and period |
| `historical_activity` | Daily, monthly, and annual counts |
| `historical_activity.mean_annual_detections` | Mean across complete calendar years, or `null` |
| `seasonal_patterns.peak_month` | Month number 1-12, or `null`; needs two complete years |
| `long_term_trends` | Annual linear count trend; needs three complete years |
| `anomalies` | Monthly count outliers; needs 12 complete months |
| `hotspots` | Up to 100 cells with the most detections; each cell is 0.01 degrees |
| `prediction` | FireLens forecast: `status`, `probability` (0-1 or `null`), `method` (`seasonal_baseline`, `xgboost_pooled` or `unavailable`), `target_month`, `explanation`, plus the engine's evidence fields |
| `ml_analysis` | FireLens patterns for S-NPP VIIRS: monthly history, seasonality, trend, anomalies, Isolation Forest outliers, Gi* hotspots, data coverage, limitations |
| `satellite_analysis` | Provider vs instrument, which raw views this result covers, harmonization availability |
| `overview` | Headline values copied from the blocks above |
| `components` | Availability status of each part |
| `warnings` | Coverage and interpretation limits; ML problems start with `ML engine:` |

The top-level statistics (`historical_activity` to `hotspots`) are the
descriptive analysis of the requested `source` and period. `ml_analysis` and
`prediction` come from the FireLens engine and always use S-NPP VIIRS.

The NASA bounding box is filtered to the exact requested radius.
Duplicate observations across downloaded batches are removed.

Counts describe satellite detections, not distinct fires.
Clouds, sensor coverage, and processing changes affect those counts.
Trends are descriptive; anomaly scores are not adjusted for seasonality.

A recent-data request usually cannot support multi-year statistics.
Those sections return `insufficient_data` and an explanation.
A missing probability is not zero probability.

The forecast is the probability of at least one satellite hotspot detection
in the circle during the target month. It is not a wildfire-ignition probability.

## Export a result

```bash
curl -OJ 'http://127.0.0.1:8000/api/v1/analyses/JOB_ID/export?format=json'
curl -OJ 'http://127.0.0.1:8000/api/v1/analyses/JOB_ID/export?format=csv&table=ml_monthly'
```

Tables: `daily`, `monthly`, `annual`, `hotspots`, `ml_monthly`, `ml_seasonal`,
`ml_anomalies`, `ml_hotspots`. A table that does not exist in the result returns **409**.

## Retry a failed analysis

Use this only when `error.recoverable` is `true`:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyses/JOB_ID/retry
```

Expected status: **202**. The same job ID is reused.
Previously downloaded batches can be reused while fresh.

A non-recoverable or unfinished job returns **409**.
If NASA requested a longer pause, retry returns **429** with `Retry-After`.

The worker automatically retries transient failures up to twice per request.
It respects NASA's retry delay. Pauses over 60 seconds are left for manual retry.

## Read saved results without downloading

| GET path | Contents |
| --- | --- |
| `/api/v1/locations/analysis?latitude=-13&longitude=24&radius_km=25` | Latest saved analysis for that exact location, radius, and source |
| `/api/v1/regions` | Five example map locations and their saved-data status |
| `/api/v1/regions/{id}/patterns` | Saved result for one example location |
| `/api/v1/regions/{id}/activity` | Alias for that region's saved result |
| `/api/v1/harmonized?latitude=-13&longitude=24` | Saved **raw** detections (normalized fields plus original row); not harmonized |
| `/api/v1/analyses` | Recent jobs; optional `limit`, up to 100 |

Location queries accept the same fields as the analysis request.
The default source is NOAA-20. Optional dates select matching saved coverage.
Without dates, the location endpoint returns the latest saved period.

Harmonized rows support `offset` and `limit`; the maximum page size is 10000.
Coordinates and FRP are normalized to numbers. Original fields are in `raw`.

These endpoints never download data.
An empty cache returns `status: "insufficient_data"`.
A saved response includes its timestamp and a `stale` flag where applicable.

The five regions have metadata only until analysed or imported.
To prepare a demo, POST each region's coordinates and radius to `/api/v1/analyses`.
Wait for completion before presenting it.

## Import historical archives

Read supported formats and limits at `GET /api/v1/archives`.

The [NASA archive page](https://firms.modaps.eosdis.nasa.gov/download/) requires
Earthdata or email-code authentication. NASA processes the export and emails
instructions. This backend does not automate that login or request process.

Download the export yourself, then upload it:

```bash
curl -X POST \
  'http://127.0.0.1:8000/api/v1/archives/import?latitude=-13&longitude=24&radius_km=25&source=VIIRS_NOAA20_SP&start_date=2020-01-01&end_date=2023-12-31&coverage_complete=true' \
  -F 'file=@fires.csv'
```

Replace `fires.csv` with your downloaded file.
Set the source, location, and dates to match the export.

Expected status: **201**, with the calculated `result`.
The saved result is available through the location and region endpoints.

Imports are descriptive only (`result.status: "descriptive_only"`).
A later `POST /api/v1/analyses` with the same location, source and dates reuses
the import instead of downloading it, and adds the ML stage when the engine is available.

Supported files:

- CSV.
- JSON lists, objects containing `rows` or `data`, and Point GeoJSON.
- ZIP files containing CSV/JSON/GeoJSON or one point shapefile dataset.

A shapefile ZIP needs matching `.shp` and `.dbf` files.
Use WGS84 longitude/latitude coordinates; projected coordinates are unsupported.
Archive attribute names are normalized to lowercase.
Detections require `latitude`, `longitude`, and `acq_date`.
Optional `acq_time` uses `HHMM`.

Limits: 20 MiB uploaded, 100 MiB expanded ZIP, 100 files in a ZIP,
and 200000 input rows. The maximum analysis period is 3650 days.

`coverage_complete` defaults to **false**.
Set it to true only for a complete export of the declared period and area.
This declaration comes from the uploader and is recorded in `coverage`.
The backend cannot infer complete coverage from detection dates alone.
Incomplete imports do not produce annual means or seasonal/trend estimates.

## Direct NASA data

| GET path | Contents |
| --- | --- |
| `/api/v1/fires` | Raw parsed CSV rows |
| `/api/v1/availability` | Available dates by source |
| `/api/v1/sources` | Supported dataset identifiers and NASA documentation links |
| `/fires`, `/`, `/test` | Aliases for `/api/v1/fires` |
| `/availability` | Alias for `/api/v1/availability` |

Fire parameters: `source`, `area`, `day_range` from 1 to 5, and optional `date`.
`area` is `world` or `west,south,east,north`.
Omit `date` for the latest data.

```bash
curl 'http://127.0.0.1:8000/api/v1/fires?area=23,-14,25,-12&day_range=1'
```

All original CSV columns are preserved. Their raw values remain strings.
Empty or header-only CSV returns HTTP 200 with `count: 0` and `rows: []`.
Availability accepts `source=ALL` to list all products.

These requests follow the [Area API](https://firms.modaps.eosdis.nasa.gov/api/area/)
and [Data Availability API](https://firms.modaps.eosdis.nasa.gov/api/data_availability/).
The old fixed date and S-NPP default have been replaced with latest data and NOAA-20.

## Health and model status

`GET /health` returns HTTP 200 when the backend can respond.
It does not call NASA. Use `/availability` to check the NASA connection.

`GET /api/v1/model` and `/api/v1/models` report the FireLens engine:
whether the package imports, whether the trained model loads, its features,
the method each region/lead uses, and whether a NASA key is configured.
`status` is `available`, `degraded` (model missing or unloadable),
`unavailable` (package missing) or `disabled`.
The key itself is never returned.

## Harmonization

| GET path | Contents |
| --- | --- |
| `/api/v1/harmonization` | Which harmonized products exist, with count and FRP validation status per region |
| `/api/v1/harmonization/regions/{region_id}` | Precomputed monthly MODIS-equivalent series (2012-2021) for one of `california`, `zambia_angola`, `rondonia`, `yakutia`, `punjab` |

These are regional, held-out FireLens products for the jointly observed domain.
They are not validated at the 25 km location scale. FRP is not fully validated anywhere.

## Errors

Handled errors include `status_code`, `detail`, and `recoverable`.

| HTTP status | Meaning |
| --- | --- |
| 409 | Failed-job result, invalid retry, or export of an unfinished job or missing table |
| 413 | Archive too large |
| 415 | Unsupported archive format |
| 422 | Invalid request (including `target_month`, `history_years`, radius > 200 km with ML) or invalid imported detections |
| 429 | Job queue full or NASA retry pause still active |
| 500 | Missing NASA key or an internal failure |
| 502 | Upstream HTTP, connection, or parsing failure |
| 504 | NASA request timed out |

## Local storage and workers

The queue accepts up to 32 active jobs, with two worker threads.
Run one Uvicorn process. Multiple Uvicorn workers sharing this store are unsupported.

Jobs, events, cached batches, and results live in `FireDetectionAPP/.data/analyses.sqlite3`.
This folder is excluded from Git.
Completed results survive reloads. Interrupted jobs become recoverable failures.

Recent-data cache freshness defaults to one hour.
Imported archive results stay available without automatic expiry.
Saved-location endpoints may return old results with `stale: true`.
A new POST refreshes an expired matching NASA analysis.

Optional settings in `.env`:

```dotenv
FIREHARMONICS_DATA_DIR=FireDetectionAPP/.data
FIREHARMONICS_CACHE_TTL_SECONDS=3600
FIREHARMONICS_ML_ENGINE=firelens
FIRELENS_ROOT=firelens
```

ML failures and failed FireLens downloads give a completed job with
`result.status: "partial"`. Such results are not cached, so a new POST retries them.

Restart the backend after changing `.env`.
Upstream URLs and error bodies are excluded from responses to keep the NASA key private.
