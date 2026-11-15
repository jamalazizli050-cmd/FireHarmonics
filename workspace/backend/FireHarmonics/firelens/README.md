# FireLens: MODIS & VIIRS hotspot pipeline

FireLens is a NASA Space Apps Challenge 2026 project for the challenge *Harmonization of MODIS and VIIRS Hot Spots*.

The repository contains:

- **Data pipeline.** Retrieves NASA FIRMS active-fire detections from MODIS and VIIRS, validates them, stores them as Parquet, and aggregates them onto a shared global grid.
- **Harmonization prototype.** Turns S-NPP VIIRS into an Aqua-MODIS-equivalent record, validated on held-out years and held-out regions.
- **Pattern discovery.** Seasonality, trends, anomalies and hotspots.
- **Next-month forecast.** A fire-occurrence forecast, limited to the region where it was validated.
- **JSON exports.** Files for backend integration.
- **Universal location engine.** `firelens.inference.analyze_location(lat, lon, radius_km, target_month)` analyses any point on Earth: automatic cached FIRMS retrieval, patterns, and a next-month forecast. It also offers background jobs with progress (`start_analysis` / `get_job_status` / `get_job_result`). See [docs/GLOBAL_INFERENCE_API.md](docs/GLOBAL_INFERENCE_API.md).

> Every record is a satellite *hotspot detection*: a pixel flagged as containing thermal anomalies at overpass time. One wildfire produces many detections, and raw MODIS and VIIRS counts are not comparable one-to-one; raw VIIRS shows about 5–8× more detections.
> Harmonization is **validated only for detection counts in California and Zambia/Angola, and only over the jointly observed domain**. Everything else is labelled partial or not validated. See [reports/checkpoints/03_harmonization_2012_2021.md](reports/checkpoints/03_harmonization_2012_2021.md).

## Results

| Topic | Where |
|---|---|
| Overnight summary | [OVERNIGHT_REPORT.md](OVERNIGHT_REPORT.md) |
| Harmonization validation | [checkpoint 3 (2012–2021)](reports/checkpoints/03_harmonization_2012_2021.md); [checkpoint 1 (2018–2021, superseded)](reports/checkpoints/01_harmonization_2018_2021.md) |
| Forecast | [checkpoint 2](reports/checkpoints/02_forecast_california_2012_2022.md) |
| API integration | [docs/API_INTEGRATION_SPEC.md](docs/API_INTEGRATION_SPEC.md); files in `exports/api_v1/` |
| Universal location engine | [docs/GLOBAL_INFERENCE_API.md](docs/GLOBAL_INFERENCE_API.md), [GLOBAL_ENGINE_REPORT.md](GLOBAL_ENGINE_REPORT.md) |
| Phase-1 exploratory analysis | [reports/technical_report.md](reports/technical_report.md), [notebooks/01_exploratory_analysis.ipynb](notebooks/01_exploratory_analysis.ipynb) |

## Project layout

```
firelens/
├── src/firelens/
│   ├── config.py          # settings; MAP_KEY loaded from .env via python-dotenv
│   ├── logging_utils.py   # logging with secret redaction
│   ├── catalog.py         # FIRMS sources, platforms, regions, bounding boxes
│   ├── firms_client.py    # FIRMS API client: retries, rate limiting, typed errors
│   ├── acquisition.py     # chunked, cached, incremental historical downloads
│   ├── cleaning.py        # parsing, common schema, QC flags, monthly Parquet storage
│   ├── coverage.py        # temporal coverage / missing-data indicators
│   ├── grid.py            # shared global grid + monthly aggregation
│   ├── harmonization.py   # overlap-period diagnostics (phase 1)
│   ├── swath.py           # NASA CMR granule footprints -> scan position / observation time
│   ├── matching.py        # VIIRS fire clusters vs Aqua-MODIS pixels in comparable overpasses
│   ├── harmonize.py       # detection-probability + GLM model, FRP calibration, metrics, conformal intervals
│   ├── experiments.py     # held-out-year / held-out-region validation, saved models
│   ├── validation_status.py   # validated / partial / not_validated rule
│   ├── forecast.py        # leakage-free features, XGBoost + isotonic calibration, baselines, validation
│   ├── patterns.py        # seasonality, Theil-Sen/Mann-Kendall, anomalies, Gi*, Isolation Forest
│   ├── ml_pipeline.py     # orchestration for patterns and forecast
│   ├── api_export.py      # JSON exports for backend integration (writes exports/api_v1/ only)
│   ├── inference.py       # analyze_location(): universal location analysis + default job API
│   ├── location_data.py   # cache-aware FIRMS retrieval for any point (tiles, dedup lock, coverage)
│   ├── location_features.py   # disc series + leakage-free features (shared by training and inference)
│   ├── location_patterns.py   # disc-level statistics, seasonality, trends, anomalies, hotspots
│   ├── global_model.py    # pooled disc forecaster: training, validation, method-selection rule
│   ├── progress.py        # work-based progress events (stages, percent, ETA)
│   ├── jobs.py            # background JobManager: polling, callbacks, async stream, persistence
│   ├── *_figures.py       # harmonization / forecast / pattern figures
│   ├── io.py              # Parquet I/O (pyarrow, falls back to polars)
│   ├── plots.py           # figure styling
│   ├── pipeline.py        # end-to-end steps
│   └── __main__.py        # CLI: python -m firelens ...
├── scripts/            # notebook builder, download scripts, run_forecast, forecast_significance, run_patterns_and_export
├── notebooks/          # exploratory analysis (executed, with outputs)
├── tests/              # pytest suite (offline; HTTP is mocked)
├── docs/               # API integration spec
├── exports/api_v1/     # JSON payloads for the backend
├── data/
│   ├── raw/        # verbatim FIRMS CSV responses + manifest.jsonl per source/region
│   ├── processed/  # detections/, grid/, qc/, harmonization/ (matched clusters)
│   ├── models/     # harmonization/*.joblib, forecast/xgb_next_month.*, global/global_disc_forecaster.*, archive/
│   ├── jobs/       # persisted job records and results (JobManager)
│   └── cache/      # data availability, FIRMS missing dates, CMR granule footprints
└── reports/        # technical report, checkpoints/, figures/, results/
```

## Setup

You need Python 3.10 or newer. The project was developed and tested on Python 3.15 under Windows.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Create your `.env` from the template and add your FIRMS MAP_KEY. A free key is available at https://firms.modaps.eosdis.nasa.gov/api/map_key/.

```bash
cp .env.example .env     # then edit NASA_FIRMS_MAP_KEY=...
```

`.env` is git-ignored. The key is never written to logs: request URLs are redacted, and urllib3 debug logging is suppressed.

> **Parquet engine note:** on some managed Windows machines, Application Control blocks pyarrow's `_parquet` DLL. When that happens, `firelens.io` falls back to **polars** (`pip install polars`). The files are standard Parquet either way.

## Running the pipeline

```bash
python -m firelens availability          # date ranges per FIRMS product (live)
python -m firelens status                # MAP_KEY transaction usage

# Full run for the California test case: download -> process -> aggregate -> analyze
python -m firelens run --region california --start 2018-01-01 --end 2021-12-31

# Or step by step
python -m firelens download  --region california --start 2018-01-01 --end 2021-12-31
python -m firelens process   --region california --start 2018-01-01 --end 2021-12-31
python -m firelens aggregate --region california --start 2018-01-01 --end 2021-12-31 --res 0.25
python -m firelens analyze   --region california --start 2018-01-01 --end 2021-12-31

# Global retrieval (use 1-day chunks; a global VIIRS day is about 70k rows / 5.5 MB)
python -m firelens download --region world --start 2020-09-01 --end 2020-09-01 --chunk-days 1

# Any bounding box: west,south,east,north
python -m firelens download --region=-85,-57,-32,14 --start 2020-09-01 --end 2020-09-10

# Exploratory notebook (rebuilds and executes it)
python scripts/build_notebook.py

# Harmonization, forecast, patterns, API export (needs the 5 regions downloaded for 2012-2022;
# see scripts/download_regions.sh and scripts/download_extension.sh)
python -m firelens harmonize --start 2012-01-01 --end 2021-12-31 --n-boot 10
python scripts/run_forecast.py
python scripts/forecast_significance.py
python scripts/run_patterns_and_export.py

# Universal location engine: train the pooled model (cached data only), then analyse any point
python scripts/train_global_model.py
python -c "from firelens.inference import analyze_location; print(analyze_location(37.7, -119.5)['prediction'])"
python scripts/run_global_engine_checks.py      # six real test locations as background jobs

# Tests
python -m pytest -q
```

The default sources are `MODIS_SP` and `VIIRS_SNPP_SP`, the Standard Processing products. Any documented FIRMS source works via `--sources`, for example `VIIRS_NOAA20_SP` or `MODIS_NRT`.

## How acquisition works

- **Endpoint.** The only data endpoint used is `/api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north | world}/{DAY_RANGE 1..5}/{YYYY-MM-DD}`. It was checked against the FIRMS API documentation and live responses on 2026-11-13. Requests are clipped to the product's date range from `/api/data_availability/`.
- **Chunking.** Requests are split into epoch-aligned chunks of up to 5 days. A new date range therefore reuses chunks that are already cached.
- **Caching.** Each chunk's CSV response is stored **verbatim** in `data/raw/`. Every chunk, including failed ones, is logged in `manifest.jsonl` with its row count, SHA-256 and retrieval time.
- **Incremental downloads.**
  - Standard Processing chunks are downloaded once.
  - A chunk that extends past the product's current `max_date` is marked incomplete and fetched again later.
  - NRT chunks are refreshed until they settle.
- **Errors.**
  - Network errors, HTTP 5xx and HTTP 429 are retried with exponential backoff.
  - HTTP 400 responses (`Invalid MAP_KEY.`, `Invalid source.`, `Invalid day range...`) fail immediately with typed exceptions.
- **Rate limits.** FIRMS allows 5000 transactions per 10 minutes, and a large request counts as several transactions. A 1-day global VIIRS request used about 70. The client therefore polls `/mapserver/mapkey_status/` and pauses at 80% of the limit.
- **Full archive.** The Area API supports every date in the SP archive (MODIS from 2000-11-01, VIIRS S-NPP from 2012-01-20). For a bulk global download over many years, the FIRMS archive download page (https://firms.modaps.eosdis.nasa.gov/download/) is the documented alternative. It delivers files by email after an Earthdata/email request, and is not automated here.

## Data model

Processed detections share one schema across sensors:

- **Identity:** `source`, `sensor`, `platform`
- **Location and time:** `latitude`, `longitude`, `acq_datetime_utc`
- **Measurements:** `brightness_k`, `frp_mw`, `scan_km`, `track_km`, `pixel_area_km2`, `nominal_resolution_m`
- **Confidence:** `confidence_raw`, `confidence_pct` (MODIS only), and `confidence_class` (low/nominal/high). For MODIS the class comes from the C6.1 user-guide thresholds 0–29, 30–79 and 80–100.
- **Other attributes:** `fire_type`, `daynight`, `version`
- **Traceability:** `raw_file`, `raw_row`

QC flags (`qc_*`) mark problems but never remove rows:

- **Exclude a record from aggregation** (`qc_usable = False`): parse error, invalid coordinates, invalid time, unknown platform, exact duplicate, outside the bounding box.
- **Informational only:** missing or negative FRP, invalid confidence, implausible pixel size, outside the request window, non-vegetation `type`.

Grid products live in `data/processed/grid/<region>/`:

- `grid_monthly_*.parquet`: per sensor × 0.25° cell × month, with counts, confidence subsets, FRP sum and median, active days and overpass granules.
- `coverage_monthly_*.parquet`: observed / operational platform-days. Terra+Aqua and the FIRMS missing-days list are taken into account.
- `harmonization_prep_*.parquet`: wide MODIS/VIIRS cell-month table. Counts are **0 when the sensor observed and detected nothing**, and **NaN when it did not observe**. It is labelled `harmonization_status = preliminary_aggregation_unharmonized`.
