# Checkpoint 1: harmonization validation, 5 regions, 2018–2021

> **Superseded by [checkpoint 3](03_harmonization_2012_2021.md).** With 10 held-out years (2012–2021), several statuses changed. California is validated for counts only. Kept for the record; the files are in `reports/results/archive/harmonization_2018_2021/`.

Run: `python -m firelens harmonize --n-boot 10`, completed 2026-11-14 ~02:28 local, exit 0.
Outputs: `reports/results/harmonization/`, `reports/figures/harmonization/`, `data/models/harmonization/`.

## Validation status (rule in `src/firelens/validation_status.py`)

| Region | Counts | FRP | Overall |
|---|---|---|---|
| california | validated (held-out region improved; 4/4 held-out years) | validated (improved; 4/4) | **validated** |
| zambia_angola | not validated (held-out region: total biased; 2/4 years) | validated (improved; 3/4) | **validated for FRP only** |
| rondonia | partial (held-out region improved; 1/4 years) | partial (improved; 1/4) | partial |
| yakutia | partial (held-out region marginal; 3/4 years) | partial (improved; 2/4) | partial |
| punjab | partial (held-out region marginal; 3/4 years) | partial (improved; 1/4) | partial |

## Key held-out numbers (leave-one-region-out, jointly observed domain)

"Median monthly error factor" means 10^median|log10(pred/obs)|.

| Region | Counts: raw VIIRS | Counts: constant | Counts: model | FRP: raw | FRP: constant | FRP: model |
|---|---|---|---|---|---|---|
| california | 6.5× | 1.31× | **1.14×** | 1.33× | 1.29× | **1.23×** |
| zambia_angola | 5.8× | 1.56× | **1.40×** (total −21%) | 1.44× | 1.37× | **1.24×** |
| rondonia | 5.2× | 1.30× | **1.23×** | 1.48× | 1.57× | **1.42×** |
| yakutia | 7.7× | 1.44× | 1.41× (total +25%) | 1.42× | 1.35× | **1.15×** |
| punjab | 6.5× | 1.37× | 1.35× | 2.50× | 2.37× | **1.24×** |

## Not validated

The **full-domain** MODIS-equivalent (all VIIRS clusters vs all Aqua pixels) is not validated. Its held-out-region totals are biased by up to +30% in California and +45% in Yakutia. VIIRS's wider swath samples more overpasses than Aqua, and the model does not yet correct for that difference.
