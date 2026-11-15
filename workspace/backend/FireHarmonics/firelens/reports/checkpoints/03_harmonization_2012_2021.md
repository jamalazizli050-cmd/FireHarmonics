# Checkpoint 3: harmonization re-validated on 2012–2021 (10 held-out years)

Run: `python -m firelens harmonize --start 2012-01-01 --end 2021-12-31 --n-boot 10`, completed 2026-11-14 06:02 local, exit 0. Statuses were then recomputed with `validation_status.save_status`, using the same rule as checkpoint 1.

Outputs: `reports/results/harmonization/`, `reports/figures/harmonization/`, `data/models/harmonization/`. The 2018–2021 run is archived in `reports/results/archive/harmonization_2018_2021/`, `reports/results/archive/harmonization_figures_2018_2021/` and `data/models/archive/harmonization_2018_2021/`.

## Why rerun

The 2018–2021 validation (checkpoint 1) had only 4 held-out years per region. The MODIS and VIIRS 2012–2017 data were downloaded overnight, which gives 10 held-out years and 10-year training sets for the held-out-region tests.

## Validation status: 2012–2021 vs 2018–2021

| Region | Counts 2012–21 | FRP 2012–21 | Overall 2012–21 | Overall 2018–21 (checkpoint 1) |
|---|---|---|---|---|
| california | **validated** (held-out region improved; 8/10 years) | partial (held-out region: total −22%; 8/10 years) | **validated for counts only** | validated |
| zambia_angola | **validated** (improved; 8/10) | not validated (not better than constant; 7/10) | **validated for counts only** | validated for FRP only |
| rondonia | not validated (not better than constant; 4/10) | partial (improved; 6/10) | partial | partial |
| yakutia | not validated (total +27%; 7/10) | not validated (marginal; 5/10) | **not validated** | partial |
| punjab | partial (improved; 7/10) | partial (improved; 3/10) | partial | partial |

Years counted "ok" are those with an improved or marginal verdict. The rule needs at least 75% of held-out years ok AND an improved held-out-region (LORO) verdict.

**Main finding: California counts are the only result that held up in both runs.**
- California FRP failed the held-out-region total-bias criterion in the longer run. Its median error was still lower (×1.23 vs ×1.30), but the total came out at 0.784, outside the 0.8–1.25 band.
- Zambia/Angola changed from FRP-validated to counts-validated.
- Status flips like these show that validation on 4 years was not enough to judge stability. The 10-year statuses supersede checkpoint 1.

## Held-out-region (LORO) numbers, jointly observed domain

Median monthly error factor = 10^median|log10(pred/obs)|.

| Region | Counts: raw VIIRS | Counts: constant | Counts: model (total ratio) | FRP: raw | FRP: constant | FRP: model (total ratio) |
|---|---|---|---|---|---|---|
| california | 6.25× | 1.26× | **1.12×** (0.97) | 1.30× | 1.30× | 1.23× (0.78) |
| zambia_angola | 5.11× | 1.53× | **1.40×** (0.81) | 1.40× | 1.25× | 1.29× (0.85) |
| rondonia | 5.77× | 1.19× | 1.25× (1.03) | 1.52× | 1.51× | **1.36×** (0.89) |
| yakutia | 7.64× | 1.51× | 1.32× (1.27) | 1.39× | 1.31× | 1.29× (1.21) |
| punjab | 6.33× | 1.41× | **1.34×** (1.00) | 2.56× | 2.31× | **1.31×** (1.22) |

Raw VIIRS counts overstate Aqua-MODIS counts by 5–8× in every region. Any harmonization (even a constant factor) removes most of that. The model's added value over the constant factor is consistent only in California and Zambia/Angola (counts) and in Punjab (FRP).

**Detection model** (held-out years, median AUC): California 0.881, Yakutia 0.863, Rondônia 0.844, Zambia/Angola 0.805, Punjab 0.745.

**Uncertainty:** empirical coverage of the nominal 90% split-conformal intervals on held-out years is 0.87–0.89 for counts and 0.86–0.89 for FRP. That is slightly under nominal and closer to it than the 0.81–0.90 seen in 2018–2021.

**Unreliable cases:** 715 held-out months are flagged in `unreliable_months.csv`:
- 247 from held-out-year (LOYO) folds;
- 219 from held-out-region (LORO) folds;
- 249 from the California-transfer folds.

Flags: count or FRP off by more than 2×, or observation outside the 90% interval. 74% of flagged months have fewer than 20 observed Aqua-MODIS pixels (vs 36% of all held-out months; median 6 vs 44 pixels). Low-activity months, where small counts make ratios unstable, are the main source of unreliable estimates.

## Geometry caveat (Yakutia)

At 60–65° N the swath geometry is weaker:
- 96.5% of Aqua-MODIS and 98.8% of VIIRS detections are located inside a granule, vs ≥99.8% in the other regions;
- the day/night direction rule agrees with neighbour-granule orientation in 91.6% of VIIRS granules, vs ≥99.9% elsewhere.

Some Yakutia comparability flags are therefore wrong, which is a plausible contributor to its failed validation. The issue was already present in 2018–2021. It is not fixed.

## Full-domain product

Unchanged conclusion: the full-domain MODIS-equivalent (all VIIRS clusters, not only those Aqua also observed) is **not validated**. Held-out-region total ratios: California 1.25, Yakutia 1.46, Punjab 1.04, Rondônia 1.14, Zambia/Angola 0.93.
