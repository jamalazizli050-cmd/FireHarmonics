# FireLens overnight report (2026-11-13/14)

Every number below was produced by code in this repository on real FIRMS / NASA CMR data and can be traced to the files listed in §5. The detailed write-ups are the three checkpoints:

- [01: harmonization 2018–2021](reports/checkpoints/01_harmonization_2018_2021.md) (superseded by 03)
- [02: forecast](reports/checkpoints/02_forecast_california_2012_2022.md)
- [03: harmonization 2012–2021](reports/checkpoints/03_harmonization_2012_2021.md)

## Bottom line

- **Harmonization works for detection counts in California and Zambia/Angola, and nowhere else yet.** In both regions:
  - it beats raw VIIRS by a wide margin (median monthly error ×6.2 → ×1.12 in California);
  - it beats a constant correction factor on held-out years and when the region itself is held out.
- **FRP harmonization is not stable.** California FRP passed on 2018–2021 but failed the total-bias criterion on 2012–2021 (held-out-region total 0.78× observed).
- **Yakutia is not validated.** Rondônia and Punjab are partial. Every result applies only to the domain both satellites observed within 20 minutes. The full-domain MODIS-equivalent series remains **not validated**: held-out-region totals are 0.93–1.46× the Aqua totals, up to +46% too high.
- **Forecast (California only)** beats seasonal-climatology and historical-frequency baselines in every held-out year (2020, 2021, 2022) and on held-out 2° spatial blocks. The gain is real but **small**: +0.01 ROC-AUC, about 5% lower Brier. Applied to the other four regions it was worse than local climatology in three of them, so no forecasts are published there.
- **API.** A proposal spec ([docs/API_INTEGRATION_SPEC.md](docs/API_INTEGRATION_SPEC.md)) with real JSON examples, plus static export files in `exports/api_v1/`. No backend code was touched.
- **Tests:** 56 / 56 passing. **Git:** the folder is not a git repository, so nothing was committed (and nothing pushed or published).

## 1. Completed work

### Phase 1: data extension and harmonization validation

1. **Finished the Yakutia and Punjab 2018–2021 downloads.** They had been interrupted by the reboot and resumed from their manifests.
2. **Extended the record for all five regions** (California, Zambia/Angola, Rondônia, Yakutia, Punjab):
   - S-NPP VIIRS SP: 2012-01-20 → 2017-12-31, plus 2022;
   - MODIS SP: 2012 → 2017;
   - 0 failed chunks out of 4,740 extension chunks (each a 1–5-day request). The client paused for the FIRMS transaction limit (5000 / 10 min) when needed.

   | Region | VIIRS records 2012–2022 | MODIS records 2012–2021 |
   |---|---|---|
   | California | 969,082 | 208,396 |
   | Zambia/Angola | 2,565,036 | 583,548 |
   | Rondônia | 537,508 | 120,041 |
   | Yakutia | 1,115,702 | 262,274 |
   | Punjab | 1,236,780 | 226,579 |

   All records passed QC. Nothing was dropped silently; QC flags are kept.
3. **Pre-fetched swath footprints from NASA CMR** for 2012–2017 (Aqua MYD03, S-NPP VNP03IMG): 60 region-years, public API, no FIRMS budget used.
4. **Re-ran harmonization on 2012–2021** with held-out years (LOYO, 10 per region) and held-out regions (LORO), plus a California-trained transfer test. The 2018–2021 run is archived, not overwritten.
5. **Checked statuses with the fixed rule.** The rule is in `validation_status.py` and was fixed before results were inspected. The `harmonize` CLI now writes `validation_status.json` itself.
6. **Before/after figures** (11 in `reports/figures/harmonization/`), an unreliable-month list, and saved models (`data/models/harmonization/*.joblib` with `manifest.json`).

### Phase 2: pattern discovery and forecasting

1. **Patterns** for 5 regions, 2012–2021, on independent series (S-NPP VIIRS, Aqua-MODIS, and the MODIS-equivalent where counts are validated):
   - seasonal profiles;
   - Theil–Sen + Mann–Kendall trends with a check that both sensors agree;
   - robust monthly anomalies (|z| ≥ 3 **and** a material departure; flagged "robust" only when ≥ 2 series agree);
   - Getis–Ord Gi* hotspots;
   - Isolation Forest on cell-months.
2. **XGBoost next-month forecast** of P(≥1 S-NPP VIIRS vegetation-fire detection in a 0.25° cell):
   - features shifted so only past information is used; a perturbation test guards against leakage;
   - isotonic calibration on a separate year;
   - temporal and spatial-block hold-outs;
   - three baselines;
   - month-block bootstrap CIs;
   - a transfer diagnostic for the other regions;
   - a saved model and a 2023-01 forecast.

### Phase 3: API integration

1. **`api_export.export_all`** writes `exports/api_v1/`: regions, activity, harmonized, patterns, forecast, model cards. It only writes files in this repository.
2. **Validation status travels in the data.** Each series carries its validation status, and the full-domain MODIS-equivalent is always labelled `not_validated`.
3. **[docs/API_INTEGRATION_SPEC.md](docs/API_INTEGRATION_SPEC.md)** covers endpoints, real JSON examples, error cases, and what the UI must show about confidence and limitations.
4. **`tests/test_api_export.py`** checks the schema, that forecasts appear only for validated regions, strict JSON (no NaN), and that no secret is present.

### Fixes made along the way (found while checking results)

- **Pattern window.** Series were trimmed to their first/last non-zero month, which dropped real winter zeros, and with them 2012 and 2021, from the Yakutia trends. They now use the full window.
- **Anomaly magnitude.** The anomaly test flagged a handful of detections in normally empty winter months. It now also requires a material departure; a regression test was added.
- **Bias wording.** The verdict "total biased by >25%" did not match the rule (ratio outside 0.8–1.25). Code and result files were relabelled; no numbers changed.
- **Model calibration year.** An export text said the deployed forecast was calibrated on 2019. It is actually refitted on 2013–2021 and calibrated on 2022 (2019 applies only to the validation model). Corrected.
- **FRP field name.** A field named "median FRP" was actually a mean. Renamed.

## 2. Harmonization validation results (2012–2021)

Held-out region (LORO), jointly observed domain. Median monthly error factor = 10^median|log10(pred/obs)|; "ratio" = predicted / observed total.

| Region | Counts: raw VIIRS → model (ratio) | Counts status | FRP: raw → model (ratio) | FRP status | Overall |
|---|---|---|---|---|---|
| California | ×6.25 → **×1.12** (0.97) | **validated** (8/10 years) | ×1.30 → ×1.23 (0.78) | partial | **counts only** |
| Zambia/Angola | ×5.11 → **×1.40** (0.81) | **validated** (8/10) | ×1.40 → ×1.29 (0.85) | not validated | **counts only** |
| Rondônia | ×5.77 → ×1.25 (1.03) | not validated (4/10) | ×1.52 → ×1.36 (0.89) | partial | partial |
| Yakutia | ×7.64 → ×1.32 (1.27) | not validated (7/10) | ×1.39 → ×1.29 (1.21) | not validated | not validated |
| Punjab | ×6.33 → ×1.34 (1.00) | partial (7/10) | ×2.56 → ×1.31 (1.22) | partial | partial |

- **Comparison with 2018–2021 (checkpoint 1).** Statuses changed for California FRP, both Zambia targets and Yakutia. Four held-out years were not enough to judge stability; the 10-year statuses supersede the old ones. Only California counts were validated in both runs.
- **Uncertainty.** The nominal 90% split-conformal intervals cover 86–89% of held-out months, slightly under nominal.
- **Detection model AUC** (held-out years, median): California 0.88, Yakutia 0.86, Rondônia 0.84, Zambia/Angola 0.81, Punjab 0.75.
- **Coverage.** 32–39% of VIIRS clusters are "comparable" (Aqua observed the same spot within 20 minutes). Of comparable MODIS pixels, 89–97% match a VIIRS cluster; Punjab is the outlier at 71%.
- **Unreliable months.** 715 held-out months are flagged (error > 2× or outside the interval). 74% of them have fewer than 20 Aqua pixels, so low-activity months are the main weak spot.

## 3. Forecasting performance (California, held-out 2020–2022)

| Model | ROC-AUC | PR-AUC | Brier | ECE |
|---|---|---|---|---|
| **XGBoost (calibrated), held-out years** | **0.925** | **0.674** | **0.0636** | 0.014 |
| XGBoost (calibrated), held-out 2° blocks | 0.923 | 0.670 | 0.0642 | 0.015 |
| Seasonal climatology | 0.916 | 0.647 | 0.0672 | 0.019 |
| Historical frequency | 0.915 | 0.637 | 0.0680 | 0.013 |
| Persistence | 0.741 | 0.354 | 0.0832 | 0.008 |

n = 58,968 cell-months, base rate 12.3%.

- **Consistency.** XGBoost is better in every test year on both splits.
- **Bootstrap (300 month-block resamples).** XGBoost minus seasonal climatology: ROC-AUC +0.0096 [0.0066, 0.0126]; Brier reduction 0.0037 [0.0024, 0.0048]. XGBoost was better in 100% of resamples.
- **Calibration** is good: see the reliability diagram, `f01`.
- **Source of skill.** Most predictability comes from where and when fires usually burn; the top feature is the historical burn frequency.
- **Transfer diagnostic** (not deployed). California model vs local seasonal climatology, ROC-AUC:

  | Region | California model | Local climatology |
  |---|---|---|
  | Punjab | 0.77 | 0.90 |
  | Rondônia | 0.84 | 0.91 |
  | Zambia/Angola | 0.92 | 0.97 |
  | Yakutia | 0.86 | 0.74 |

  The California model is worse in three of four regions, which confirms the decision to publish California only.

## 4. Tests

```
python -m pytest -q   ->   56 passed in 2.3 s
```

- **New tonight (54 → 56):**
  - `test_api_export.py`: export schema, scoping, strict JSON, no secrets;
  - `test_anomaly_requires_material_departure`.
- **Data-level checks run tonight:**
  - **Key scan:** across 9,269 files in the project (data, logs, exports and models included), the MAP_KEY appears only in `.env`.
  - **Status file:** `validation_status.json` was recomputed and matches the rule applied to `summary.csv`.
  - **Determinism:** the bootstrap script reproduces the same CIs with its fixed seed.
- **Failures:** none.

## 5. Files produced

| What | Path |
|---|---|
| Checkpoints | `reports/checkpoints/01_…`, `02_…`, `03_…` |
| Harmonization results | `reports/results/harmonization/` (`summary.csv`, `folds.json`, `monthly_predictions.csv`, `unreliable_months.csv`, `matching_diagnostics.json`, `validation_status.json`) |
| Harmonization figures | `reports/figures/harmonization/h01–h08` |
| Harmonization models | `data/models/harmonization/{region}.joblib`, `pooled.joblib`, `manifest.json` |
| 2018–2021 archive | `reports/results/archive/`, `data/models/archive/` |
| Forecast results | `reports/results/forecast/` (`forecast_metrics.json`, `forecast_significance.json`, `heldout_predictions.parquet`, `forecast_2023-01.parquet`) |
| Forecast figures | `reports/figures/forecast/f01_reliability`, `f02_skill_by_year`, `f03_transfer_diagnostic`, `f04_forecast_map_2023-01` |
| Forecast models | `data/models/forecast/xgb_next_month.{ubj,joblib,json}` (deployed), `xgb_next_month_validation.*` (validated model) |
| Patterns | `reports/results/patterns/` (`patterns.json`, `regional_anomalies.csv`, `regional_monthly_series.csv`, `hotspots_gi_star.parquet`, `isolation_forest_*.parquet`) |
| Pattern figures | `reports/figures/patterns/p01_seasonal_profiles`, `p02_annual_trends`, `p03_hotspots` |
| API | `docs/API_INTEGRATION_SPEC.md`, `exports/api_v1/` (regions, activity, harmonized, patterns, forecast, model_cards) |
| New code | `src/firelens/{api_export,forecast_figures,patterns_figures}.py`; edits to `patterns.py`, `ml_pipeline.py`, `harmonize.py`, `__main__.py` |
| New scripts | `scripts/run_forecast.py`, `scripts/forecast_significance.py`, `scripts/run_patterns_and_export.py` |
| Logs | `logs/download_extension.txt`, `harmonize_2012_2021.txt`, `forecast_run.txt`, `patterns_export.txt`, `firelens.log` |

## Pattern findings (robust = seen by both sensors)

- **Seasonality:** sharp and identical in both sensors.
  - California: Jul–Oct, peak Aug.
  - Zambia/Angola: Jun–Sep.
  - Rondônia: Aug–Oct.
  - Yakutia: Jul–Sep.
  - Punjab: two crop-residue peaks, May and Oct–Nov. The May peak is much stronger in VIIRS (23% vs 11% of annual detections), consistent with small fires that MODIS misses.
- **Trends 2012–2021:**
  - **Rondônia is the only significant trend:** +7.6%/yr VIIRS (p = 0.009) and +6.6%/yr MODIS (p = 0.03).
  - California +17%/yr in both sensors, not significant (p = 0.22), driven by 2020–2021.
  - Zambia/Angola is flat and very stable (CV 5%).
  - Punjab is flat.
  - Yakutia is dominated by extreme years (2021 ≈ 5× the mean).
- **Robust anomalies:**

  | Region | High | Low |
  |---|---|---|
  | California | Dec 2017; Sep–Oct 2020; Sep 2021; Mar 2020 (VIIRS + MODIS-equivalent) | Aug 2019; Nov 2014 (MODIS-based series) |
  | Yakutia | May 2013 | Jul–Aug 2015, Aug 2016 |
  | Rondônia | — | Jun and Aug 2013 |
  | Zambia/Angola, Punjab | none robust | none robust |

- **Hotspots:** Gi* 99% hotspots hold 66% of California's detections in 8% of its cells (western Sierra Nevada and the Klamath / northern Coast Ranges). Punjab: 46% in 19% of cells. Zambia/Angola: activity is spread out (10%).
- **Isolation Forest:** unusual cell-months concentrate in record years: California 2020–2021 (117 of 220), Yakutia 2021 (35 of 50).

## 6. Known limitations

1. **Validation scope.** Harmonization is validated only for counts, only in two regions, and only over the jointly observed domain. FRP is not validated anywhere on the 10-year record.
2. **Full-domain sampling bias.** VIIRS's wider swath sees fires on overpasses that Aqua misses. The model does not correct for this, so full-domain MODIS-equivalent totals are 0.93–1.46× the Aqua totals.
3. **High-latitude geometry.** In Yakutia, 3.5% of Aqua and 1.2% of VIIRS detections cannot be located in a granule, and the orientation rule agrees only 91.6% of the time (≥ 99.9% elsewhere). This is a plausible contributor to Yakutia's failure; not fixed.
4. **Punjab matching.** Only 71% of comparable Punjab MODIS pixels match a VIIRS cluster (vs 89–97% elsewhere). Small agricultural fires and haze are likely causes; not investigated.
5. **Clouds, smoke and gaps.** These are not modelled. "Not detected" means not detected, not "no fire".
6. **Low-activity months** produce most unreliable estimates (§2).
7. **Forecast inputs.** The forecast uses no weather, fuel or vegetation inputs (none were acquired). It cannot anticipate weather-driven extreme months, and its gain over climatology is small.
8. **Forecast scope.** The forecast covers California only and is validated on 2020–2022. The deployed model is refitted on 2013–2021 and calibrated on 2022 with the validated settings; that refit itself has no separate hold-out.
9. **Forecast grid.** The grid is the California bounding box, not the state boundary (it includes ocean and parts of Nevada/Mexico).
10. **Trends** cover 10 years only. California's +17%/yr is not significant and depends on two extreme years.
11. **Environment.** The local Parquet engine pyarrow is blocked by Windows Application Control; I/O falls back to polars automatically. Python 3.15 lacks some wheels (no Jupyter metapackage; nbclient is used instead).
12. **Δt choice.** Δt = 20 min was chosen on California 2018–2021 (20 and 30 min both improved in 4/4 years; 10 min was mixed). It was not re-tuned on 2012–2021.

## 7. What needs your attention

1. **Accept or reject the scope.** The honest headline is "MODIS-equivalent detection counts, validated in 2 of 5 test regions; FRP not validated". If the challenge needs FRP, the next step is a region-aware FRP model (FRP bias varies from 0.78 to 1.22 between regions).
2. **Version control.** This folder is not a git repo. When you're ready: `git init` and commit. `.gitignore` already excludes `.env`, raw/processed data, the cache and logs. Models (4 MB) and exports (0.7 MB) are not excluded; decide whether to commit them.
3. **OneDrive sync.** `data/` holds about 1.5 GB (raw 617 MB, processed 841 MB) inside a OneDrive folder. Consider excluding `data/` from sync.
4. **Backend review.** The endpoint paths in the spec are suggestions. The backend team should confirm them and how files are loaded. Nothing in their code was changed.
5. **Optional next experiments** (none started, to stay within budget):

   | Experiment | Cost |
   |---|---|
   | Fix high-latitude swath orientation, then rerun Yakutia | Code only |
   | Correct the full-domain sampling bias with overpass-coverage weights | Code only |
   | Train a Zambia/Angola-specific forecaster (its counts are now validated) and run held-out-region validation with California | About 5 min compute |
   | Add ERA5 weather features to the forecast | New data source; needs your decision |
