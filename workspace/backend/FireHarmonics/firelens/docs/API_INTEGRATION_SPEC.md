# FireLens API integration spec (v1)

**Audience:** backend developers serving FireLens results.
**Status:** proposal. FireLens does not ship a server and does not modify backend code. It writes static JSON files to `exports/api_v1/`, produced by `firelens.api_export.export_all`. The backend can serve these files as they are, or load them into its own store. The endpoint paths below are suggestions that map one-to-one onto those files.

Schema identifier in every payload: `"schema": "firelens.api.v1"`. Missing values are JSON `null`, never `NaN`. All months are `YYYY-MM` (UTC). FRP is in MW.

## 1. What the numbers mean (read before integrating)

- **Counts are satellite hotspot detections**, not fires or incidents. One fire can produce many detections, and clouds hide fires from the satellite.
- **MODIS and VIIRS counts are not interchangeable.** Raw VIIRS (375 m) detects about 5–8× more hotspots than Aqua-MODIS (1 km) over the same fires. Never add, subtract or chart the two on one axis as if they were the same quantity.
- **Validation status is part of the data.** Each region and each series carries a `harmonization_status`. The frontend must show it, for example as a badge or a warning, and must not present `partial`, `not_validated` or `not_evaluated` series as calibrated.
- **Forecasts exist only where they were validated** (currently California). For any other region the API must return "not available" (see §4), never a fallback global model.

## 2. Endpoints

| Suggested endpoint | File | Purpose |
|---|---|---|
| `GET /api/v1/regions` | `regions.json` | Region list, bounding boxes, harmonization status, forecast availability |
| `GET /api/v1/regions/{region_id}/activity` | `activity/{region_id}_monthly.json` | Observed monthly series: VIIRS S-NPP, Aqua-MODIS; plus a full-domain MODIS-equivalent series flagged `not_validated` |
| `GET /api/v1/regions/{region_id}/harmonized` | `harmonized/{region_id}_monthly.json` | Validated harmonization product: held-out MODIS-equivalent predictions with 90% intervals and the observed Aqua-MODIS values |
| `GET /api/v1/regions/{region_id}/patterns` | `patterns/{region_id}.json` | Seasonality, trends, anomalies, hotspots, Isolation Forest summary |
| `GET /api/v1/regions/{region_id}/forecast?month=YYYY-MM` | `forecast/{region_id}_{YYYY-MM}.json` | Next-month fire-occurrence probability per 0.25° cell |
| `GET /api/v1/models` | `model_cards.json` | Model confidence, validation results and limitations |

`region_id` is one of: `california`, `zambia_angola`, `rondonia`, `yakutia`, `punjab`.

## 3. Payloads

### 3.1 `GET /api/v1/regions`

Real output (first region shown; 5 in total):

```json
{
 "schema": "firelens.api.v1",
 "regions": [
  {
   "region_id": "california",
   "description": "Bounding box enclosing California (not the state boundary)",
   "bbox": {
    "west": -124.5,
    "south": 32.5,
    "east": -114.0,
    "north": 42.1
   },
   "harmonization_status": "validated_count_only",
   "harmonization_notes": "count: validated (held-out region: improved; held-out years ok: 8/10); frp: partial (held-out region: lower error but total biased (ratio outside 0.8-1.25); held-out years ok: 8/10). Applies to the jointly observed domain; the full-domain series is not validated.",
   "forecast_available": true
  },
  "... 4 more"
 ]
}
```

### 3.2 `GET /api/v1/regions/{region_id}/activity`

One entry per series. `viirs_snpp` and `modis_aqua` are observations. The MODIS series is Aqua only, which keeps it consistent with the harmonization target. `modis_equivalent` appears only for regions whose count harmonization was validated, and it is always `not_validated`: it applies the model to *all* VIIRS clusters, a domain where validation failed.

Real output, `california`, two months per series:

```json
{
 "schema": "firelens.api.v1",
 "region_id": "california",
 "series": {
  "modis_aqua": {
   "unit_count": "fire pixels (detections), vegetation fires only",
   "unit_frp": "MW (sum of per-detection FRP)",
   "harmonization_status": "observed",
   "data": [
    {
     "month": "2012-08",
     "count": 4414.0,
     "frp_mw": 639468.4
    },
    {
     "month": "2012-09",
     "count": 432.0,
     "frp_mw": 44568.6
    },
    "... 118 more"
   ]
  },
  "modis_equivalent": {
   "unit_count": "fire pixels (detections), vegetation fires only",
   "unit_frp": "MW (sum of per-detection FRP)",
   "harmonization_status": "not_validated",
   "note": "Full-domain MODIS-equivalent (all VIIRS clusters). Not validated: when this region was held out, the predicted total count was 1.25x the observed Aqua-MODIS total (VIIRS samples more overpasses than Aqua; not corrected). Use /harmonized for the validated jointly-observed product.",
   "data": [
    {
     "month": "2012-08",
     "count": 5693.088861,
     "frp_mw": 760300.801287
    },
    {
     "month": "2012-09",
     "count": 527.089272,
     "frp_mw": 44266.048265
    },
    "... 118 more"
   ]
  },
  "viirs_snpp": {
   "unit_count": "fire pixels (detections), vegetation fires only",
   "unit_frp": "MW (sum of per-detection FRP)",
   "harmonization_status": "observed",
   "data": [
    {
     "month": "2012-08",
     "count": 36315.0,
     "frp_mw": 716620.04
    },
    {
     "month": "2012-09",
     "count": 3638.0,
     "frp_mw": 43782.23
    },
    "... 118 more"
   ]
  }
 },
 "notes": "Counts are satellite hotspot detections, not fire incidents. Do not add or compare counts across series without harmonization."
}
```

### 3.3 `GET /api/v1/regions/{region_id}/harmonized`

This is the product that was actually validated.

- Each month's MODIS-equivalent value is a **held-out** prediction: the model never saw that year.
- It sits next to the Aqua-MODIS observation for the same month.
- It covers only the domain both sensors observed within 20 minutes.
- `count_interval_90` and `frp_interval_90` are split-conformal intervals; `interval_empirical_coverage_heldout_years` gives their measured coverage.

The frontend should show `validation.overall` and each target's `status`.

Real output, `california`, August–September 2012:

```json
{
 "schema": "firelens.api.v1",
 "region_id": "california",
 "domain": "jointly observed: VIIRS fire clusters that Aqua-MODIS also observed within 20 min, vegetation fires only",
 "prediction_type": "held-out: each year predicted by a model trained on the other years",
 "validation": {
  "count": {
   "status": "validated",
   "heldout_region_verdict": "improved",
   "heldout_years_ok": "8/10",
   "median_monthly_error_factor_model_heldout_region": 1.116525,
   "median_monthly_error_factor_raw_viirs": 6.246377,
   "full_domain_total_ratio_heldout_region": 1.25461
  },
  "frp": {
   "status": "partial",
   "heldout_region_verdict": "lower error but total biased (ratio outside 0.8-1.25)",
   "heldout_years_ok": "8/10",
   "median_monthly_error_factor_model_heldout_region": 1.231488,
   "median_monthly_error_factor_raw_viirs": 1.299796,
   "full_domain_total_ratio_heldout_region": 0.978018
  },
  "overall": "validated_count_only"
 },
 "interval_method": "split-conformal (nominal 90%)",
 "interval_empirical_coverage_heldout_years": {
  "count": 0.881818,
  "frp": 0.891667
 },
 "data": [
  {
   "month": "2012-08",
   "viirs_raw": {
    "count": 11760.0,
    "frp_mw": 228101.11
   },
   "modis_equivalent": {
    "count": 1802.322381,
    "count_interval_90": [
     1437.723584,
     3302.379386
    ],
    "frp_mw": 239887.961435,
    "frp_interval_90": [
     137593.852039,
     410713.048407
    ]
   },
   "modis_aqua_observed": {
    "count": 1778.0,
    "frp_mw": 233644.4
   }
  },
  {
   "month": "2012-09",
   "viirs_raw": {
    "count": 1292.0,
    "frp_mw": 20236.85
   },
   "modis_equivalent": {
    "count": 155.951314,
    "count_interval_90": [
     124.403316,
     285.748215
    ],
    "frp_mw": 22541.231061,
    "frp_interval_90": [
     12929.097371,
     38592.923416
    ]
   },
   "modis_aqua_observed": {
    "count": 182.0,
    "frp_mw": 25252.8
   }
  },
  "... 118 more"
 ]
}
```

### 3.4 `GET /api/v1/regions/{region_id}/patterns`

- **Trends:** `trend_count` is a Theil–Sen slope of log annual totals with a Mann–Kendall test. Present a trend as real only if `significant_at_0.05` is true **and** `trend_agreement.same_sign` is true (both sensors agree).
- **Anomalies:** `regional_anomalies[].robust` is true when two or more independent series flag the same month. `z` is the robust z-score against the same calendar month in other years; negative means unusually low. A month is flagged only if |z| ≥ 3 and the departure is at least 1% of the mean annual total.
- **Hotspots:** `hotspots` uses Getis–Ord Gi* on 0.25° cells (S-NPP VIIRS).
- **Isolation Forest:** `isolation_forest` summarises unusual cell-months.

Real output, `rondonia` (trimmed):

```json
{
 "schema": "firelens.api.v1",
 "region_id": "rondonia",
 "window": [
  "2012-01",
  "2021-12"
 ],
 "notes": [
  "VIIRS S-NPP record starts 2012-01-20; January 2012 is partial for VIIRS."
 ],
 "viirs_snpp": {
  "seasonality": {
   "peak_month": "Sep",
   "core_season_months_80pct": [
    "Aug",
    "Sep",
    "Oct"
   ],
   "n_core_months": 3,
   "n_full_years": 10
  },
  "trend_count": {
   "n_years": 10,
   "years": [
    2012,
    2021
   ],
   "theil_sen_pct_per_year": 7.567747,
   "ci95_pct_per_year": [
    2.21893,
    15.137395
   ],
   "mann_kendall_tau": 0.644444,
   "p_value": 0.009148,
   "significant_at_0.05": true,
   "interannual_cv": 0.313648
  },
  "...": "seasonality.monthly_share, trend_count.annual_totals, seasonality_frp, trend_frp"
 },
 "modis_aqua": "... same structure",
 "regional_anomalies": [
  {
   "month": "2013-06",
   "series": [
    "modis_aqua",
    "viirs_snpp"
   ],
   "robust": true,
   "z": {
    "viirs_snpp": -4.49,
    "modis_aqua": -6.63
   }
  },
  {
   "month": "2013-07",
   "series": [
    "modis_aqua"
   ],
   "robust": false,
   "z": {
    "viirs_snpp": -2.07,
    "modis_aqua": -3.38
   }
  },
  "... 5 more"
 ],
 "trend_agreement": {
  "signs": {
   "viirs_snpp": 1,
   "modis_aqua": 1
  },
  "same_sign": true,
  "any_significant": true
 },
 "hotspots": {
  "n_cells": 256,
  "n_hotspot_cells_gi99": 24,
  "share_activity_in_hotspots": 0.338876,
  "top_cells": [
   {
    "cell_id": 465590,
    "lat": -9.125,
    "lon": -62.375,
    "mean_annual_detections": 1730.4,
    "gi_star_z": 6.626
   },
   "..."
  ]
 },
 "isolation_forest": {
  "n_cell_months": 11562,
  "n_flagged": 116,
  "flagged_by_year": {
   "2012": 6,
   "2013": 2,
   "2014": 7,
   "2015": 11,
   "2016": 3,
   "2017": 10,
   "2018": 6,
   "2019": 26,
   "2020": 17,
   "2021": 28
  },
  "share_flagged_in_regional_anomaly_months": 0.387931,
  "top": [
   {
    "cell_id": 467029,
    "month": "2021-08",
    "lat": -8.834,
    "lon": -62.555,
    "count": 1622,
    "frp": 30900.38,
    "departure": 4.619,
    "iso_score": 0.717
   },
   "..."
  ]
 },
 "region_summary": {
  "bbox_area_km2": 194784,
  "viirs_detections_per_1000km2_per_year": 238.70791,
  "viirs_mean_frp_per_detection_MW": 12.797976,
  "night_share_viirs": 0.371016
 }
}
```

### 3.5 `GET /api/v1/regions/{region_id}/forecast?month=YYYY-MM`

- `p_fire` is the calibrated probability that S-NPP VIIRS records at least one vegetation-fire detection in the 0.25° cell during `forecast_month`.
- `p_seasonal_baseline` is the shrunk same-month climatology, for comparison.
- Cells cover the full bounding box, so offshore cells have probabilities near zero.
- `model.heldout_skill` comes from held-out years 2020–2022.
- `gain_vs_baselines_bootstrap` gives month-block bootstrap 95% CIs of XGBoost minus each baseline. For Brier, a positive value means XGBoost is better.

Real output, `california` 2023-01 (the two highest-probability cells shown):

```json
{
 "schema": "firelens.api.v1",
 "region_id": "california",
 "forecast_month": "2023-01",
 "target": "probability of >=1 S-NPP VIIRS vegetation-fire detection in the 0.25 deg cell during the month",
 "model": {
  "name": "xgb_next_month",
  "calibration": "isotonic",
  "heldout_test_years": [
   2020,
   2021,
   2022
  ],
  "heldout_skill": {
   "temporal": {
    "all_cells": {
     "xgboost_calibrated": {
      "roc_auc": 0.925254,
      "pr_auc": 0.673662,
      "brier": 0.063567,
      "ece": 0.013747,
      "base_rate": 0.123355
     },
     "seasonal_climatology": {
      "roc_auc": 0.915663,
      "pr_auc": 0.646961,
      "brier": 0.067227,
      "ece": 0.018464,
      "base_rate": 0.123355
     },
     "historical_frequency": {
      "roc_auc": 0.914826,
      "pr_auc": 0.636552,
      "brier": 0.067978,
      "ece": 0.012793,
      "base_rate": 0.123355
     }
    }
   },
   "...": "temporal.ever_burned_cells, spatial_blocks.{all_cells, ever_burned_cells}"
  },
  "gain_vs_baselines_bootstrap": {
   "temporal": {
    "auc_vs_seasonal": {
     "mean": 0.00958,
     "ci95": [
      0.00656,
      0.012606
     ],
     "share_xgb_better": 1.0
    },
    "auc_vs_hist": {
     "mean": 0.010458,
     "ci95": [
      0.007573,
      0.013106
     ],
     "share_xgb_better": 1.0
    },
    "brier_vs_seasonal": {
     "mean": 0.003653,
     "ci95": [
      0.00245,
      0.00482
     ],
     "share_xgb_better": 1.0
    },
    "brier_vs_hist": {
     "mean": 0.004425,
     "ci95": [
      0.003275,
      0.005725
     ],
     "share_xgb_better": 1.0
    }
   },
   "...": "spatial_blocks"
  }
 },
 "limitations": [
  "Target is satellite detection (S-NPP VIIRS, vegetation fires), not fire occurrence on the ground; clouds and smoke can hide fires.",
  "Model uses only past detection history, location and season. No weather, fuel or vegetation inputs, so it cannot anticipate anomalous weather-driven fire months.",
  "Validated only for California (held-out years 2020-2022 and held-out 2-degree spatial blocks). Applied to other regions it was worse than a local seasonal climatology in 3 of 4 regions, so no forecasts are provided there.",
  "Skill gain over a seasonal-climatology baseline is small (about +0.01 ROC-AUC, about 5% lower Brier score); most predictability comes from where and when fires usually occur.",
  "The deployed model is refitted on 2013-2021 and calibrated (isotonic) on 2022 with the same settings as the validated model (trained 2013-2018, calibrated 2019, tested 2020-2022). Calibration may drift if fire regimes change."
 ],
 "cells": [
  {
   "cell_id": 705857,
   "lat": 32.625,
   "lon": -115.625,
   "p_fire": 0.951049,
   "p_seasonal_baseline": 0.912587
  },
  {
   "cell_id": 705859,
   "lat": 32.625,
   "lon": -115.125,
   "p_fire": 0.951049,
   "p_seasonal_baseline": 0.916084
  },
  "... 1636 more"
 ]
}
```

### 3.6 `GET /api/v1/models`

Real output (trimmed):

```json
{
 "schema": "firelens.api.v1",
 "harmonization": {
  "california": {
   "count": {
    "status": "validated",
    "heldout_region_verdict": "improved",
    "heldout_years_ok": "8/10",
    "median_monthly_error_factor_model_heldout_region": 1.116525,
    "median_monthly_error_factor_raw_viirs": 6.246377,
    "full_domain_total_ratio_heldout_region": 1.25461
   },
   "frp": {
    "status": "partial",
    "heldout_region_verdict": "lower error but total biased (ratio outside 0.8-1.25)",
    "heldout_years_ok": "8/10",
    "median_monthly_error_factor_model_heldout_region": 1.231488,
    "median_monthly_error_factor_raw_viirs": 1.299796,
    "full_domain_total_ratio_heldout_region": 0.978018
   },
   "status": "validated_count_only"
  },
  "...": "other regions"
 },
 "forecast": {
  "regions": [
   "california"
  ],
  "limitations": [
   "Target is satellite detection (S-NPP VIIRS, vegetation fires), not fire occurrence on the ground; clouds and smoke can hide fires.",
   "Model uses only past detection history, location and season. No weather, fuel or vegetation inputs, so it cannot anticipate anomalous weather-driven fire months.",
   "Validated only for California (held-out years 2020-2022 and held-out 2-degree spatial blocks). Applied to other regions it was worse than a local seasonal climatology in 3 of 4 regions, so no forecasts are provided there.",
   "Skill gain over a seasonal-climatology baseline is small (about +0.01 ROC-AUC, about 5% lower Brier score); most predictability comes from where and when fires usually occur.",
   "The deployed model is refitted on 2013-2021 and calibrated (isotonic) on 2022 with the same settings as the validated model (trained 2013-2018, calibrated 2019, tested 2020-2022). Calibration may drift if fire regimes change."
  ]
 },
 "harmonization_manifest": {
  "created": "2026-11-14T06:02:38.040984+00:00",
  "period": [
   "2012-01-01",
   "2021-12-31"
  ],
  "match_config": {
   "max_dt_min": 20.0,
   "search_dt_min": 100.0,
   "same_pass_min": 3.0,
   "cluster_tol_km": 0.1,
   "match_tol_km": 0.5,
   "vegetation_only": true
  },
  "...": "detector/GLM features, library versions, model files"
 }
}
```

## 4. Errors and unavailable data

Suggested behaviour, consistent with the validation results:

| Situation | HTTP | Body |
|---|---|---|
| Unknown `region_id` | 404 | `{"error": "unknown_region", "region_id": "..."}` |
| Forecast requested for a region with `forecast_available: false` | 404 | `{"error": "forecast_not_available", "region_id": "...", "reason": "forecast model only validated for: california"}` |
| Forecast month not produced | 404 | `{"error": "forecast_month_not_available", "available": ["2023-01"]}` |
| `harmonized` for a region without held-out results | 404 | `{"error": "not_evaluated"}` |

## 5. Confidence and limitations to surface

| What | Where in the payload | What the UI should do |
|---|---|---|
| Harmonization status per region and target | `regions[].harmonization_status`, `harmonized.validation` | Show it as a badge; treat anything other than `validated` as experimental |
| Full-domain MODIS-equivalent | `activity.series.modis_equivalent` | Always flagged `not_validated`; show it only with its note, never as the headline number |
| Interval coverage | `harmonized.interval_empirical_coverage_heldout_years` | Label intervals as ~90%. Measured coverage is 0.86–0.89, slightly under nominal |
| Low-activity months | `harmonized.data[]` with a small `modis_aqua_observed.count` | Ratios are unstable below ~20 pixels/month; 74% of the months flagged unreliable fall in this range |
| Forecast scope | `regions[].forecast_available` | Show forecasts only where true (California); show "not available" elsewhere |
| Forecast skill vs baseline | `forecast.model.heldout_skill`, `gain_vs_baselines_bootstrap` | Present the forecast as "slightly better than seasonal climatology", not as a weather-driven prediction |
| Forecast limitations | `forecast.limitations` | Display verbatim near the map |

## 6. Regenerating the files

```bash
python -m firelens harmonize --start 2012-01-01 --end 2021-12-31 --n-boot 10   # harmonization + validation status
python scripts/run_forecast.py                 # forecast + validation (reports/results/forecast)
python scripts/forecast_significance.py        # per-year skill, bootstrap CIs, forecast figures
python scripts/run_patterns_and_export.py      # patterns + figures + exports/api_v1 (--export-only to skip patterns)
```

Each export file is rewritten in full on every run, so the backend can reload them atomically. No FIRMS MAP_KEY or other secret appears in any export; `tests/test_api_export.py` checks this.
