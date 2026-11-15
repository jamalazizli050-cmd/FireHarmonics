# Checkpoint 2: next-month fire-occurrence forecast (California)

Run: `python scripts/run_forecast.py`, completed 2026-11-14 ~04:10 local, exit 0. Significance: `python scripts/forecast_significance.py`.
Outputs: `reports/results/forecast/` (`forecast_metrics.json`, `forecast_significance.json`, `heldout_predictions.parquet`, `forecast_2023-01.parquet`), `reports/figures/forecast/f01–f04`, `data/models/forecast/`.

## Scope decision

Only California passed harmonization validation for both counts and FRP (checkpoint 1), so the deployed forecaster covers **California only**. The other four regions appear only in a transfer diagnostic (metrics only, no predictions exported).

**Update after checkpoint 3 (2012–2021).** California is now validated for counts only; Zambia/Angola counts are also validated. The decision stands:
- The forecast target is VIIRS detection occurrence, which depends on count-type information.
- California counts are the only harmonization result validated in both runs.
- The California model did worse than local climatology in Zambia/Angola (see below).

A Zambia/Angola-specific forecaster is listed as an optional next step in `OVERNIGHT_REPORT.md`.

## Setup

- **Target:** P(≥1 S-NPP VIIRS vegetation-fire detection in a 0.25° cell in month t+1). A single sensor keeps the label consistent; no MODIS–VIIRS mixing.
- **Data:** VIIRS S-NPP SP, 2012-01 to 2022-12, 1,638 cells. Rows need 12 months of history, so the first usable year is 2013.
- **Features:** location, seasonality, own-cell lags and rolling means, climatology computed only from earlier years, months since last fire, neighbour lags, and regional lags. All shifted, so only past information is used; a perturbation test in `tests/test_ml.py` guards against leakage. No weather or vegetation features (not acquired).
- **Model:** XGBoost with isotonic calibration fitted on a separate year.

| Split | Train | Calibrate | Test |
|---|---|---|---|
| Temporal | 2013–2018 | 2019 | 2020–2022 |
| Spatial blocks (2°, 4 folds) | 2013–2018, other blocks | 2019, other blocks | 2020–2022, held-out blocks |

## Held-out results (test years 2020–2022, n = 58,968 cell-months, base rate 12.3%)

| Model | ROC-AUC | PR-AUC | Brier | ECE |
|---|---|---|---|---|
| **XGBoost calibrated, temporal** | **0.925** | **0.674** | **0.0636** | 0.014 |
| XGBoost calibrated, spatial blocks | 0.923 | 0.670 | 0.0642 | 0.015 |
| Seasonal climatology | 0.916 | 0.647 | 0.0672 | 0.019 |
| Historical frequency | 0.915 | 0.637 | 0.0680 | 0.013 |
| Persistence | 0.741 | 0.354 | 0.0832 | 0.008 |

On cells that had any fire before 2019 (n = 34,632, base rate 20.7%), the temporal split scores ROC-AUC 0.875 / PR-AUC 0.682 / Brier 0.105. Seasonal climatology scores 0.859 / 0.655 / 0.111.

**Per year (temporal):** XGBoost beats both baselines on ROC-AUC, PR-AUC and Brier in each of 2020, 2021 and 2022. The same holds for the spatial-block split.

**Month-block bootstrap (300 resamples), XGBoost minus best baseline:**

| Split | Metric | Mean gain | 95% CI | Resamples where XGBoost is better |
|---|---|---|---|---|
| Temporal | ROC-AUC | +0.0096 | [0.0066, 0.0126] | 100% |
| Temporal | Brier reduction | 0.0037 | [0.0024, 0.0048] | 100% |
| Spatial blocks | ROC-AUC | +0.0070 | [0.0038, 0.0100] | 100% |
| Spatial blocks | Brier reduction | 0.0030 | [0.0018, 0.0041] | 100% |

**Interpretation.** The improvement is real and consistent, but **small**: about +0.01 AUC and about 5% lower Brier. Most of the skill comes from where and when fires usually happen. The top feature by gain is `hist_freq`, and a seasonal climatology alone reaches AUC 0.916. Calibration is good (ECE 0.014).

## Transfer diagnostic (not deployed)

The California-trained model was applied unchanged to the other regions, 2020–2022. It is worse than a local seasonal climatology in Punjab (AUC 0.770 vs 0.903), Rondônia (0.842 vs 0.907) and Zambia/Angola (0.918 vs 0.969, ECE 0.138). It is better only in Yakutia (0.861 vs 0.738). This supports limiting the deliverable to California.

## Next-month forecast

`forecast_2023-01.parquet` covers 1,638 California cells, with mean P = 0.095. Each cell also carries the seasonal-climatology baseline. The grid is the full bounding box, so offshore and out-of-state cells are included; they carry near-zero probability.
