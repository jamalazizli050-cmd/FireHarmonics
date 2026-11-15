# ML engine integration (FireLens)

[Back to README](../README.md)

This page describes how the FastAPI backend runs the FireLens ML engine, what
it needs, and what its outputs do and do not mean. It documents the code as
implemented, not the original design.

## 1. Execution flow

```
POST /api/v1/analyses                         (FireDetectionAPP/main.py)
  -> AnalysisService.submit                   (jobs.py: validate, cache lookup, queue)
  -> worker thread (ThreadPoolExecutor, 2)    (never the FastAPI event loop)
       1. descriptive stage                   NASA FIRMS Area API for the requested `source`
                                              -> analytics.describe (counts, simple stats)
       2. ML stage (unless ml_mode="off")     ml_engine.FireLensEngine.analyze
            -> firelens.inference.validate_request
            -> firelens.inference.run_analysis
                 plan/download S-NPP VIIRS (SP, then NRT) into FireLens's tile cache
                 patterns (seasonality, Theil-Sen trend, robust-z anomalies,
                 Isolation Forest cell-months, Getis-Ord Gi* hotspots)
                 forecast (validated method gate: XGBoost or seasonal baseline)
            -> json_safe()  (NumPy/pandas -> JSON; NaN/inf -> null)
       3. assemble                            jobs.assemble -> AnalysisResult validation
  -> SQLite job record + events               (storage.py; SSE reads the events)
GET /api/v1/analyses/{id}[/events|/result|/export]
```

FireLens progress events (work-based, never timers) are mapped onto the job:
the descriptive stage uses 0-40 %, FireLens 40-98 %, assembly 99 %, and 100 %
only together with `status: "completed"`. Stage names are prefixed `ml_`
(`ml_plan`, `ml_download`, `ml_process`, `ml_load`, `ml_patterns`, `ml_predict`,
`ml_finalize`). `eta_seconds` is FireLens's measured download estimate or `null`.
During a FIRMS transaction-budget pause the job shows `status: "paused"` and
`nasa_pause_seconds`.

Only `FireDetectionAPP/ml_engine.py` imports FireLens. The engine's scientific
code is unchanged.

## 2. Where FireLens comes from

FireLens lives in `./firelens` (source in `firelens/src/firelens`). It was
committed in `4d3776c` and removed from `main` in `bbea94b`. The working copy
was restored from `4d3776c` unchanged, except that `firelens/pyproject.toml` and
`firelens/requirements.txt` now list `scikit-learn`, `xgboost` and `joblib`,
which the trained model needs but which were missing.

The backend adds `$FIRELENS_ROOT/src` (default `./firelens/src`) to `sys.path`.
No `pip install -e` is needed. When FireLens can't be imported, the backend still
runs: analyses return `ml_analysis.status: "unavailable"` and
`prediction.probability: null`.

## 3. Required artifacts

| File | Used for | Required |
| --- | --- | --- |
| `firelens/data/models/global/global_disc_forecaster.joblib` | Pickled `GlobalForecaster` (XGBoost booster + isotonic calibrators) | Yes |
| `firelens/data/models/global/global_disc_forecaster.json` | Features, method gate, validation and reliability tables | Yes |
| `firelens/data/models/global/global_disc_forecaster.ubj` | Raw booster copy; not read at inference | No |
| `firelens/reports/results/harmonization/validation_status.json` | Regional harmonization status | For harmonization status |
| `firelens/exports/api_v1/regions.json`, `exports/api_v1/harmonized/*.json` | Precomputed regional harmonized series | For `/api/v1/harmonization` |

The model was serialized with **scikit-learn 1.9.1** and **XGBoost 3.4.1**. Both
are pinned. It loads without version warnings, and its 28 feature names match
the metadata and the booster. `GET /api/v1/model` reports `features_consistent`.

If the model files are missing, `GET /api/v1/model` returns `available: false`.
Each analysis then completes with `status: "partial"`,
`ml_analysis.error.code: "model_unavailable"` and no probability.

FireLens's NASA data cache (`firelens/data/raw`, `processed`, `cache`, about
1.6 GB for the original project) is not in Git. It is recreated on first use.

## 4. Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `NASA_FIRMS_MAP_KEY` | none | NASA key. Used by both the backend and FireLens. Never returned or logged. |
| `FIREHARMONICS_ML_ENGINE` | `firelens` | `disabled` turns the ML stage off globally. |
| `FIRELENS_ROOT` | `./firelens` | FireLens checkout (needs `src/`, `data/models/`, `exports/`, `reports/`). |
| `FIRELENS_DATA_DIR` | `$FIRELENS_ROOT/data` | FireLens data and **model** folder (`models/global` must be inside it). |
| `FIRELENS_REPORTS_DIR` | `$FIRELENS_ROOT/reports` | Harmonization validation status. |
| `FIREHARMONICS_DATA_DIR` | `FireDetectionAPP/.data` | Backend SQLite store. |
| `FIREHARMONICS_CACHE_TTL_SECONDS` | `3600` | Freshness of saved NASA-based results. |
| `FIRELENS_DOWNLOAD_WORKERS` | `4` | Parallel FIRMS Area API requests (1-8). Request starts stay spaced and the transaction-budget guard still applies; `1` = sequential. |
| `FIRELENS_COUNTRY_ARCHIVE` | `1` | Fill completed years of `VIIRS_SNPP_SP` from the FIRMS yearly per-country archives (no API transactions). `0` disables it. |

## 5. Request fields that control the engine

| Field | Default | Rule |
| --- | --- | --- |
| `ml_mode` | `download` | `download`: fetch missing S-NPP data. `cache_only`: never download (missing days are unobserved). `off`: descriptive only. |
| `target_month` | next month (UTC) | `YYYY-MM`; from the current month up to 12 months ahead |
| `history_years` | `10` | 3-14 |
| `radius_km` | `25` | 1-200 when `ml_mode` is not `off` (500 for descriptive-only) |
| `location_name` | `null` | Display label only; not part of the cache key |

A new location needs hundreds of 5-day FIRMS requests. FIRMS allows 5000
transactions per 10 minutes, and FireLens pauses for up to 600 s when the budget
runs low. Expect 20-30 minutes for a new 10-year location and seconds for a
cached one. Nearby points share 1-degree cache tiles.

### Faster history download (country archives + parallel requests)

For completed years, FireLens first fills missing `VIIRS_SNPP_SP` chunks from NASA FIRMS's yearly per-country
files (`https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/<YEAR>/viirs-snpp_<YEAR>_<Country>.csv`), which
cost no Area API transactions (`firelens/src/firelens/country_archive.py`). Country-year files are kept in
`$FIRELENS_DATA_DIR/cache/firms_country/` as Parquet, so later tiles in the same country need no network.

* The tile's countries come from `firms_country_cells.json` (Natural Earth 1:50m, 1-degree cells, 0.25-degree margin;
  generator: `firelens/tools/build_firms_country_cells.py`). Tiles touching a territory without a FIRMS file, open
  ocean, the current year and NRT data always use the Area API.
* Before writing, the busiest chunks (default 4) are compared with the Area API; any difference rejects the archive
  for that tile. The outcome is reported in `ml_analysis.data_coverage.retrieval.country_archive`.
* Verified on 2026-11-15: a UK tile filled from the archive was identical to the Area API cache in all 670 chunks
  (4,408 detections, 2016-2025). Zambia 2024 (61 MB) downloads and converts in about 5 s.

The remaining Area API chunks are fetched with `FIRELENS_DOWNLOAD_WORKERS` parallel requests.

**Cache size.** Country-year Parquet files take roughly 0.5-2 GB per large fire country (MODIS 2002- and VIIRS 2012-).
Keep `FIRELENS_DATA_DIR` outside cloud-synced folders. `firelens/tools/prewarm_archives.py` downloads the files for
demo locations in advance (no MAP_KEY needed).

### MODIS+VIIRS calendar (`firelens/src/firelens/unified_calendar.py`)

Aqua MODIS (2002-07 on) and S-NPP VIIRS (2012 on) from the same yearly archives, FireLens QC, vegetation type 0,
within the disc. Local ratio `r = sum(MODIS)/sum(VIIRS)` on 2012-2021 (before Aqua's orbit drift), 95 % CI by year
bootstrap, leave-one-year-out validation against raw VIIRS. Measured on 2026-11-15: Zambia/Angola r = 0.177
(CI 0.168-0.186), annual error 3 % vs 463 % for raw VIIRS, monthly r = 0.97; Yosemite r = 0.089, 30 % vs 1,236 %,
monthly r = 0.96. Season timing (10 % / 90 % of a fire-year) and current conditions (NOAA-20 NRT, last 14 days, vs the
same window in past S-NPP years) are computed in the same module. Not the FireLens regional harmonization model.

## 6. Forecast method selection

The forecast is the probability of **at least one usable S-NPP VIIRS
vegetation-fire detection within the radius during `target_month`**. It is a
satellite-detection probability, not a probability of a wildfire ignition.

The method is fixed by the pre-registered gate stored in the model metadata.
For lead L (target month minus last fully observed month):

* inside a training region (California, Zambia/Angola, Rondonia, Yakutia,
  Punjab): `xgboost_pooled` only if its temporal hold-out Brier skill against
  the best baseline is above 0 and the bootstrap 95 % lower bound is above 0;
* elsewhere: `xgboost_pooled` only if the leave-one-region-out Brier skill is
  above 0 in at least 4 of 5 regions and the pooled lower bound is above 0;
* otherwise, or for leads above 3: `seasonal_baseline` (the disc's
  same-calendar-month climatology, raw or isotonic-calibrated).

**In the installed model XGBoost passed for no region and no lead.** Every
forecast therefore uses `seasonal_baseline`. The XGBoost probability is still
reported under `prediction.model_comparison.xgboost_pooled` with `used: false`.
Pooled XGBoost should not be presented as better than the regional seasonal
baselines.

No probability is issued (`probability: null`, `method: "unavailable"`) when:

* fewer than 36 observed months, or fewer than 3 observed years of the target
  calendar month (`status: "insufficient_data"`);
* there are no detections within 100 km in the record (`reason:
  "no_fire_detections_within_100km"`). The engine then reports a 95 % upper bound
  on the monthly rate instead;
* the lead is outside 1-12 months;
* the engine failed or is unavailable (`status: "failed"` / `"unavailable"`).

A missing probability is never 0 %.

## 7. Result status and failure semantics

| `result.status` | Meaning |
| --- | --- |
| `complete` | Descriptive and ML stages ran; FireLens coverage was complete |
| `partial` | The ML stage failed, was unavailable, or had failed/skipped downloads. Descriptive statistics are still genuine. See `components` and `warnings`. |
| `descriptive_only` | `ml_mode: "off"` or an archive import |

The job itself is `failed` only when the descriptive stage fails (NASA errors,
invalid period). Partial results from engine failures or failed downloads are
**not cached**, so a new POST retries them. `POST .../retry` applies only to
failed jobs.

## 8. Scientific limitations (also returned in `ml_analysis.limitations`)

* Counts are satellite hotspot detections (375 m pixels at two overpasses a day),
  not fires, burned area or ignitions.
* Clouds, smoke and canopy hide fires; no cloud correction is applied.
* No weather, fuel or land-cover data are used.
* The pooled model is validated only in five regions and is not globally validated.
* The ML stage always uses S-NPP VIIRS, whatever `source` the request names.
  The descriptive block uses the requested source. The two are never summed.
* **S-NPP observation delivery ended on 2026-11-02.** The last fully observed
  month is now 2026-10, so lead times grow each month. Once the target month is
  more than 12 months after 2026-10, FireLens issues no forecast. Moving to
  NOAA-20/21 would need retraining and revalidation; that is a project decision.
* MODIS/VIIRS harmonization exists only as precomputed regional series
  (2012-2021, five regions, jointly observed domain). Counts are validated in
  California and Zambia/Angola only. FRP is not fully validated anywhere. It is
  not validated at the 25 km location scale.

## 9. Python environment note

On the development machine, Windows Smart App Control blocks SciPy 1.18.1's
`_flapack` DLL for Python 3.15, the only SciPy build for 3.15. scikit-learn
cannot load as a result. pandas and pyarrow load fine there. The same SciPy
version for **Python 3.13** loads normally, so the verified environment is
Python 3.13.17 in a virtual environment. Security settings were not changed.
See [Setup](SETUP.md).
