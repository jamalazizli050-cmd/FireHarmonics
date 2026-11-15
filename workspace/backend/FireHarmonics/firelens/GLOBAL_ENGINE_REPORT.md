# FireLens universal location engine: report

**Date:** 2026-11-14

**Status:** working worldwide prototype. It is **not** a globally validated fire model.

**Interface:** `docs/GLOBAL_INFERENCE_API.md`

## 1. Summary

`firelens.inference.analyze_location(lat, lon, radius_km=25, target_month=None)` analyses any point on Earth. A non-blocking job API sits on top of it. For a new point the engine:

1. downloads the missing NASA FIRMS S-NPP VIIRS record, rate-limited and cached by 1° tile;
2. separates months with no detections from months with no data;
3. returns historical activity, seasonality, trends, anomalies and hotspots;
4. returns a next-months forecast with method, evidence and limitations.

**Validation outcome.** A pooled XGBoost model was trained on 1,264 discs in the five project regions. On held-out years it was only slightly better than a seasonal baseline inside those regions, and not significantly so. In regions it had not seen, it was significantly worse. The selection rule, written before the results were read, therefore picks the **seasonal baseline for every region and lead**. The engine reports XGBoost's number only as a transparency field.

**Real end-to-end check.** All six test locations ran on live FIRMS data:
- 3,042 requests, 0 failures, 7 automatic 10-minute rate-limit pauses;
- 0 duplicate downloads;
- the open-ocean point correctly returns `insufficient_data` and no probability;
- re-running all six from the cache took **9 s** with **0** requests and identical results.

**Tests:** 83 passed (offline, FIRMS mocked).

## 2. What was built

| Component | File |
|---|---|
| Public entry points: `analyze_location`, `start_analysis`, `get_job_status`, `get_job_result`, request validation, result assembly | `src/firelens/inference.py` |
| Retrieval: tiles, cache index, planner, dedup lock, coverage, observation loading, vegetation and static-source filters | `src/firelens/location_data.py` |
| Disc time series and forecasting features, shared by training and inference | `src/firelens/location_features.py` |
| Location-scale patterns: history, seasonality, trend, anomalies, Gi* hotspots, Isolation Forest outliers | `src/firelens/location_patterns.py` (reuses `patterns.py`) |
| Pooled model: training table, fit, validation, selection rule, save/load | `src/firelens/global_model.py` |
| Training script | `scripts/train_global_model.py` |
| Serialized model | `data/models/global/global_disc_forecaster.{joblib,ubj,json}` |
| Progress tracking (work-based percent, stage events, ETA rule, rate-limit waits) | `src/firelens/progress.py` |
| Job manager (thread pool, polling, subscribe, async stream, JSON persistence, retry) | `src/firelens/jobs.py` |
| Six-location real check | `scripts/run_global_engine_checks.py` → `reports/results/global/locations/` |
| Tests | `tests/test_global_engine.py` (27 tests) and the existing 56 |

No frontend, HTTP server or teammate backend code was written or changed. The only change to an existing module is backward compatible: `acquisition.download` gained an optional `on_chunk` callback.

## 3. Data design

**One sensor series.** S-NPP VIIRS 375 m: `VIIRS_SNPP_SP` (standard) to 2026-07-31, then `VIIRS_SNPP_NRT` (near-real-time) to 2026-11-02.
- MODIS and VIIRS are never mixed.
- The project's MODIS-equivalent harmonisation was validated only for regional counts in two regions, not at disc scale, so the engine reports it but does not use it.

**Zero vs missing.**
- A day is observed only if it was retrieved, lies inside product availability and is not on FIRMS's missing-data list for Suomi NPP.
- A month with at least 90% observed days is a real observation (0 detections means a real zero). Other months are `null`.
- Example: FIRMS lists all 31 days of May 2026 as missing for S-NPP, so May 2026 is unobserved at every location. July–August 2022 and July 2024 are partially missing and below the 90% threshold.

**Vegetation filter.**
- SP keeps FIRMS `type = 0`.
- NRT has no `type`, so NRT detections are dropped in 0.01° cells where SP flagged a static source.
- Central England shows why this matters: 1,534 non-vegetation SP detections and 49 NRT detections at known static sources were excluded, against 152 vegetation detections kept.

**Cache.**
- The analysis disc is `max(radius, 100 km)` plus 5 km, snapped outward to a 1° grid. Discs are split at the antimeridian and use full longitude near the poles.
- Each 5-day request chunk is served from any cached region whose bounding box contains the tile, including the five project regions for 2012–2022. Only chunks found nowhere are downloaded.
- One process-wide lock plus a re-plan after taking it guarantees no chunk is downloaded twice by concurrent jobs.

## 4. Forecast model and validation

**Target.** P(≥ 1 usable S-NPP vegetation-fire detection within the radius in the target month), for leads 1–3 months. The default request is lead 2, because the current month is incomplete.

**Training data.**
- 1,264 discs (radii 10/25/50/100 km) on a 0.5° lattice inside the five regions, 2012–2022.
- 439,872 rows with leads 1–3 stacked.
- Regions are weighted equally.
- Features use only months ≤ t − lead; a leakage test enforces this.

**Model.** XGBoost (400 trees, depth 5) with isotonic calibration.

**Design selection** used only an inner split: train ≤ 2016, calibrate 2017, select on 2018–2019. Four variants were compared:

| Variant | Inner score (mean Brier) |
|---|---|
| with longitude | 0.08338 |
| with longitude + baseline margin | 0.08370 |
| without longitude | 0.08296 |
| **without longitude + baseline margin** (chosen) | **0.08254** |

**Test evaluation:** train ≤ 2018, calibrate 2019, test 2020–2022 (34 months). Confidence intervals are 95% month-block bootstrap (500 resamples).

**Temporal hold-out, all regions pooled:**

| Lead | XGBoost Brier / AUC | Seasonal baseline Brier / AUC | BSS vs best baseline [95% CI] |
|---|---|---|---|
| 1 | 0.0829 / 0.957 | 0.0849 / 0.953 | +0.024 [−0.005, +0.051] |
| 2 | 0.0849 / 0.955 | 0.0849 / 0.953 | ≈ 0 |

**Per region (in-domain gate), BSS vs the best baseline:**

| Region | Lead 1 | Lead 2 | Lead 3 |
|---|---|---|---|
| California | +0.016 [−0.002, 0.034] | −0.002 [−0.016, 0.013] | −0.004 [−0.017, 0.008] |
| Zambia/Angola* | +0.016 [−0.075, 0.093] | −0.013 [−0.111, 0.079] | −0.047 [−0.159, 0.057] |
| Rondônia | −0.005 [−0.075, 0.061] | −0.016 [−0.082, 0.044] | −0.011 [−0.074, 0.044] |
| Yakutia | +0.074 [−0.045, 0.208] | +0.020 [−0.068, 0.106] | +0.017 [−0.033, 0.073] |
| Punjab | −0.059 [−0.137, 0.004] | −0.084 [−0.165, −0.021] | −0.067 [−0.134, −0.013] |

\* For Zambia/Angola the best baseline is the isotonic-calibrated seasonal baseline; elsewhere it is the raw seasonal baseline.

**Leave-one-region-out** (out-of-domain gate: train on four regions, test on the fifth, 2020–2022):

| Lead | Regions where XGBoost wins | Pooled BSS [95% CI] |
|---|---|---|
| 1 | 1 of 5 (Rondônia) | −0.054 [−0.084, −0.022] |
| 2 | 1 of 5 (Rondônia) | −0.081 [−0.109, −0.053] |
| 3 | 1 of 5 (Rondônia) | −0.085 [−0.112, −0.058] |

**Selection rule** (in `global_model.decide`, fixed before results were read):
- **In-domain:** XGBoost if BSS > 0 *and* the CI lower bound > 0.
- **Out-of-domain:** XGBoost if it wins in ≥ 4 of 5 regions *and* the pooled lower bound > 0.
- **Leads > 3:** baseline.
- **Result:** the seasonal baseline everywhere (15 in-domain region-lead cells and 3 out-of-domain leads).

**Why the baseline is hard to beat.** At disc scale, whether any detection occurs in a month is dominated by the fire season. The seasonal baseline already reaches AUC 0.953 at a base rate of 0.45. The model's top features by gain are `any_last`, `ctx_clim_same_month`, `clim_mean_log`, `month_cos`, `log_area_km2` and `p_seasonal_raw`, which are mostly climatology. Weather, fuel and land-cover inputs, which are not used, are the obvious next step.

**Disclosure.**
- The first training run (`logs/train_global_run1_plain_variants.out`) evaluated two variants on the 2020–2022 test years before inner design selection existed.
- Design selection was then moved to the inner split and run once on the test years.
- The first run's rule outcome was also the seasonal baseline everywhere (18 of 18 decisions), so this did not change the result. The test years were still seen more than once.

**Uncertainty.**
- No per-location interval is given, because none is empirically supported.
- Each forecast instead carries the held-out reliability of its probability bin. Example: for Zambia, 2,397 held-out forecasts in bin [0.3, 0.5) had a mean forecast of 0.43 and an observed frequency of 0.48.

## 5. Six-location real check (2026-11-14)

All points use radius 25 km and a 10-year history; the target month is 2026-12 (lead 2).

| Location | Why | Status | Requests | Detections (mean / yr) | Peak / core season | Trend (Theil-Sen, 95% CI) | Dec 2026 probability |
|---|---|---|---|---|---|---|---|
| California, Sierra Nevada (37.7, −119.5) | training region | ok | 281 | 13,581 (1,580) | Aug / Jul–Oct | −46%/yr [−73, +49], n.s. | 0.42 (norm 4/10 years) |
| Zambia (−13.0, 24.0) | different climate | ok | 281 | 22,448 (2,270) | Sep / Jul–Oct | +1.6%/yr [−1.1, +3.5], n.s. | 0.41 (4/10) |
| Yakutia (62.0, 125.0) | boreal | ok | 281 | 11,951 (1,706) | Jul / Jul–Sep | −55%/yr [−91, +38], n.s. | 0.02 (0/10) |
| Northern Australia (−14.5, 132.5) | outside the 5 regions | ok | 733 | 8,951 (976) | Jun / Apr–Aug, Nov | −5%/yr [−20, +6], n.s. | 0.99 |
| Central England (52.5, −1.5) | low fire | ok | 733 | 152 (13) | Nov | 0%/yr [−20, +30], n.s. | 0.25 |
| South Pacific (−30.0, −140.0) | open ocean | insufficient_data | 733 | 0 | — | — | none (95% upper bound on the monthly rate: 0.26) |

**Notes on the results.**

- **Patterns match known fire regimes.**
  - Zambia: a stable dry-season regime, inter-annual CV small, 2,000–2,500 detections each year.
  - California and Yakutia: dominated by single extreme years. California had 5,036 detections in 2020 and 6 in 2019. Yakutia had 7,514 in 2021 and 0 in 2023 and 2025.
  - Because of those extreme years, their large negative trends are not significant and should not be read as declines.
- **Anomalies are flagged where expected.** Examples: Yakutia July 2021 (5,581 detections), Zambia August 2024 (1,337), several high fire seasons in California.
- **Yakutia in December.** No detection in any of 10 Decembers. The probability of 0.023 comes only from the shrinkage prior (the disc's overall fire frequency), so `relative_activity.category` is `no_past_activity_this_month`. That category was added after this check; an earlier version returned `null`.
- **Central England.**
  - 152 vegetation detections in ten years. The engine treats this as real low activity, not missing data.
  - The November peak (2–7 detections in most Novembers) is not explained by the data. Bonfire-night fires or residual non-vegetation sources are possible. The NRT static-source filter only removes sources the SP product flagged.
  - With values this small, the "peak" is descriptive only.
- **Northern Australia** is outside the training regions. It uses the out-of-domain rule (seasonal baseline). Its December probability is high because 10 of 10 past Decembers had detections, an observed frequency, not a model extrapolation.
- **Ocean.** 115 observed months with 0 detections within 100 km. The engine issues no probability. It explains that water, ice, rock or an unmonitored surface cannot be told apart from a very rare fire regime, and gives the exact binomial upper bound instead.
- **XGBoost transparency field.** California and Zambia show the same XGBoost value (0.4813). That is one plateau of the isotonic calibrator, which outputs a step function. The value is not used.

**Performance.**

| Measure | Value |
|---|---|
| Time per request (excluding pauses) | 0.66–0.80 s |
| Rate-limit pauses | 10 minutes each; about one per 430 requests in this session |
| New location inside a project region | 281 requests, 3 min with no pause |
| New location elsewhere | 733 requests, 20–30 min with 1–2 pauses |
| Processing | 6–26 s |
| Patterns + prediction | 1–3 s |
| Cached re-run | 1–3 s per location, 9 s for all six including process start; 0 requests, identical probabilities |

All six jobs ran through one `JobManager` with two workers. The real event logs (`*_events.jsonl`) show:
- one job downloading while the next waited in `wait_download_slot`;
- monotonic progress;
- 100% only at completion;
- `rate_limit_wait` warnings during the pauses.

**A defect found by the real run and fixed afterwards.** During a pause, the status kept the last ETA (for example 129 s). After the pause, the ETA counted the 600 s pause as download time (731 s).
- Events now carry `waiting = {reason, seconds, resume_at}`.
- `eta_seconds` is `null` while waiting.
- Waits are excluded from the measured rate.
- A new test covers this.

## 6. Tests

`pytest`: **83 passed** in about 41 s (offline; FIRMS HTTP mocked with a tiny model). The engine tests cover:

- **Requests:** request validation and the default target month.
- **Tiles:** normal, antimeridian and pole tiles.
- **Disc counts and features:** exact disc counts; feature leakage.
- **Progress:** monotonic, 100 only at completion, ETA only after 3 units, indeterminate stages, faulty callbacks tolerated, rate-limit waits.
- **End to end:** download, cache reuse with 0 requests on the second call, JSON validity, exact counts.
- **Forecast paths:** XGBoost path when the rule allows it; no detections → `insufficient_data`; downloads disabled → `partial`/`insufficient_data`.
- **Jobs:** concurrent jobs without duplicate downloads; streaming; persistence across a manager restart; interrupted job and retry; explicit, recoverable auth failure that does not leak the key; unfinished job raises.

## 7. Limitations

- **Not globally validated.** Evidence comes from five regions only. Out-of-region skill was estimated by leave-one-region-out within those regions, and the seasonal baseline won it.
- **Detections, not fires.** Counts are 375 m hotspot pixels at the ~01:30 and ~13:30 overpasses. They are not corrected for cloud, smoke or canopy.
- **Sensor coverage.** S-NPP only, from 2012-01-20. FIRMS-listed gaps, such as May 2026, become unobserved months. NOAA-20/21 are not merged, to keep one consistent series.
- **NRT static-source filter.** It relies on SP flags; new industrial sources after the SP record ends are not removed.
- **No land/water mask.** The `global-land-mask` package was not installed. Ocean, ice and desert are recognised only through "no detection within 100 km". A coastal point gets land-based statistics for the land part of its disc.
- **No covariates.** No weather, fuel or land-cover inputs.
- **Download time and budget.** A new location costs 20–30 minutes and a large share of the MAP_KEY transaction budget.
- **Job persistence.** Job state persists across restarts (tested). Live callbacks do not, and the download lock is per process.
- **Small samples.** Hotspot Gi* is descriptive, at 99%, with no multiple-testing correction. With few detections, seasonality and anomalies are descriptive only.

## 8. Reproduce

```bash
python scripts/train_global_model.py                   # about 1.5 min on the cached regional data (measured 2026-11-14)
python scripts/run_global_engine_checks.py             # real six-location check (downloads)
python scripts/run_global_engine_checks.py --cached    # re-run from the cache
python -m pytest -q
```

The MAP_KEY is read from `firelens/.env` only. It does not appear in any code, log, document or output file; this was checked with a search over the project.
