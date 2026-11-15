# FireLens technical report: MODIS / VIIRS hotspot pipeline and harmonization readiness

*NASA Space Apps Challenge 2026: Harmonization of MODIS and VIIRS Hot Spots.*
*Report date 2026-11-13. Every number below comes from data retrieved from NASA FIRMS on that date and processed by this repository.*

## 1. Summary

- **Pipeline status.** A working pipeline now retrieves NASA FIRMS Standard Processing hotspots for MODIS (Terra+Aqua, C6.1) and VIIRS (Suomi NPP, 375 m). It validates and stores them as Parquet, then aggregates them to a shared global 0.25° monthly grid with coverage indicators. It ran on **4 years of California data (715,461 detections)** and **one global day (76,009 detections)**.
- **Data quality.** No downloaded record failed validation. Every record is retained and traceable to the raw NASA response.
- **Counts.** VIIRS reports **9.3× more detections than Aqua-MODIS** in the same region and months (95% CI 8.7–10.2). The ratio grows with fire activity (log-log slope 1.12, CI 1.05–1.15), so **no single constant can convert counts between the sensors**.
- **Fire radiative power (FRP).** FRP is far more comparable. For fire clusters seen by both sensors within 20 minutes, the median VIIRS/MODIS FRP ratio is **1.08** (Spearman ρ = 0.91, n = 5,311 clusters).
- **Why the sensors differ.** The main difference is sensitivity to small fires. Aqua-MODIS detects **0.6%** of VIIRS fire clusters below 2 MW but **93%** above 250 MW. This FRP-dependent detection probability is the recommended basis for harmonization (section 6).
- **Status.** Nothing in this repository is yet a harmonized product. Grid tables are labelled `preliminary_aggregation_unharmonized`.

## 2. Data sources and API usage

| Item | Value (verified 2026-11-13) |
|---|---|
| API | FIRMS Area API `/api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north or world}/{1..5}/{YYYY-MM-DD}` |
| MODIS product | `MODIS_SP`, Collection 6.1 (`version` 6.03 in the data), Terra + Aqua, ~1 km at nadir. Available 2000-11-01 → 2026-08-31 |
| VIIRS product | `VIIRS_SNPP_SP` (`version` 2 in the data), Suomi NPP, 375 m at nadir. Available 2012-01-20 → 2026-07-31 |
| Overlap period | 2012-01-20 → 2026-07-31, about 14.5 years |
| Rate limit | 5000 transactions per 10 min per MAP_KEY. Large requests count as several transactions |
| Observed cost | 601 requests (5-day California chunks) used over 4,000 transactions, about 7 each. A 1-day `world` request cost about 30–35 |

The FIRMS archive page lists the S-NPP 375 m record as ending **2026-11-02**. That makes a VIIRS NOAA-20/NOAA-21 cross-calibration necessary for continuity. Those sources are already supported by the pipeline (`VIIRS_NOAA20_SP`, `VIIRS_NOAA21_SP`).

## 3. What was retrieved and processed

| Dataset | Window | Requests / chunks | Raw rows | Rows in window | Usable |
|---|---|---|---|---|---|
| MODIS_SP, California bbox (-124.5, 32.5, -114.0, 42.1) | 2018-01-01 → 2021-12-31 | 293 chunks, 0 failed | 130,244 | 130,239 | 130,239 |
| VIIRS_SNPP_SP, California bbox | 2018-01-01 → 2021-12-31 | 293 chunks, 0 failed | 585,318 | 585,222 | 585,222 |
| MODIS_SP, world | 2020-09-01 | 1 | 6,723 | 6,723 | 6,723 |
| VIIRS_SNPP_SP, world | 2020-09-01 | 1 | 69,286 | 69,286 | 69,286 |

A few raw rows fall outside the window: 5 MODIS and 96 VIIRS rows dated 2022-01-01 to 2022-01-04. The last epoch-aligned 5-day chunk extends past the requested end date. These rows stay in `data/raw` and are not processed for this window.

On disk: 63 MB of raw CSV and 22 MB of Parquet (106 files).

The California **bounding box** also covers parts of Oregon, Nevada, Arizona, Baja California and ocean. It is not the state boundary.

### Validation results (California, 2018–2021)

| Check | MODIS | VIIRS | Handling |
|---|---|---|---|
| Parse errors / invalid coordinates / invalid date-time | 0 / 0 / 0 | 0 / 0 / 0 | excluded from aggregation if present |
| Unknown platform / exact duplicates / outside bbox / outside request window | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | excluded from aggregation if present |
| Missing / negative FRP | 0 / 0 | 0 / 0 | flagged |
| FRP = 0 MW | 146 | 1,273 | kept |
| Invalid confidence value | 0 | 0 | flagged |
| Implausible pixel size | 0 | 0 | flagged |
| Non-vegetation `type` (volcano, static land source, offshore) | 1,415 (1.1%) | 29,865 (5.1%) | kept, counted separately as `n_vegetation` |
| Brightness: VIIRS I4 maximum | – | exactly 367.0 K | consistent with I4 saturation; brightness is not usable as a fire-intensity measure for saturated pixels |
| VIIRS I4 brightness < 300 K | – | 46,325 (7.9%) | kept; not investigated further |

Correctness of the pipeline was verified as follows:

- **Unit tests (41, all passing).** They cover API errors (invalid key, bad request, 5xx retry, 429 back-off, network failure, unexpected body), chunking, caching and incremental downloads, parsing, every QC flag, month routing without loss, grid indexing, and the zero-versus-missing distinction.
- **Real-data reconciliation.** Raw and processed row counts were checked against each other, as in the table above.
- **Secret handling.** A scan found the MAP_KEY only in `.env`; no log, output or source file contains it.

## 4. Coverage and missing observations

- **Platform-days.** Every platform-day in the window was retrieved: 2,922 Terra+Aqua and 1,461 S-NPP. The coverage fraction is 1.0 in all 48 months for both sensors.
- **FIRMS missing-days list.** This list comes from the feed behind https://firms.modaps.eosdis.nasa.gov/api/missing_data/. It contains **no** Terra, Aqua or S-NPP dates inside 2018–2021. Across the full record it lists 57 Terra, 26 Aqua and 80 S-NPP days, which the coverage module accounts for.
- **Days without detections.** Days with zero detections in the bbox are not missing data: MODIS had 90 such days and VIIRS 7. The grid tables keep this distinction, with **0** for "observed, nothing detected" and **NaN** for "not observed".

## 5. Differences found between the sensors

### 5.1 Activity totals

| Year | MODIS detections | VIIRS detections | MODIS FRP sum (GW) | VIIRS FRP sum (GW) |
|---|---|---|---|---|
| 2018 | 25,417 | 103,464 | 3,222.9 | 1,814.8 |
| 2019 | 6,439 | 28,845 | 541.3 | 351.7 |
| 2020 | 51,866 | 246,837 | 7,346.8 | 4,540.8 |
| 2021 | 46,517 | 206,076 | 6,894.7 | 4,076.8 |

Both sensors show the same seasonal cycle (Jul–Oct peak) and the same peak day, 2020-09-09. Year-to-year variation is about 8×.

Terra and Aqua each contribute about half of the MODIS detections (Terra 51.9%). Comparing VIIRS with *Terra+Aqua* therefore compares about 4 MODIS overpasses per day with about 2 VIIRS overpasses. **Aqua-only is the like-for-like reference**, because Aqua and S-NPP both overpass at about 13:30 and 01:30 local time. This choice changes the conclusions:

| Ratio VIIRS / MODIS (sum over 2018–2021, month-block bootstrap 95% CI) | vs Terra+Aqua | vs Aqua only |
|---|---|---|
| Detection count | 4.49 (4.24–4.72) | **9.34 (8.67–10.23)** |
| Detection count, nominal+high confidence only | 4.59 (4.30–4.84) | 9.50 (8.79–10.37) |
| Summed FRP | 0.60 (0.55–0.70) | **1.14 (1.03–1.35)** |
| Monthly count ratio, median (min–max over 48 months) | 4.42 (2.82–6.67) | 9.26 (4.59–21.96) |
| Monthly FRP ratio, median (min–max) | 0.79 (0.44–1.60) | 1.50 (0.83–3.41) |

### 5.2 Per-detection properties

| Property | MODIS | VIIRS S-NPP |
|---|---|---|
| Median pixel area (scan × track) | 1.43 km² (max 9.6) | 0.202 km² (max 0.624) |
| Median / mean / p95 FRP per detection | 40.6 / 138.3 / 548.7 MW | 4.77 / 18.4 / 79.6 MW |
| Confidence classes (low / nominal / high) | 6.4% / 38.4% / 55.2% | 4.5% / 87.6% / 7.9% |
| Night-time share of detections | 46.1% | 63.4% |

MODIS confidence is a 0–100 score, binned here using the C6.1 user-guide thresholds. VIIRS confidence is a categorical label produced by a different algorithm. **The classes do not mean the same thing across sensors** and should not be used as a harmonization variable without further study.

### 5.3 Spatial agreement (0.25° grid, Aqua-MODIS vs VIIRS, months both observed)

- **Cell-months with detections:** 10,619. Of these, **33.1%** have detections from both sensors, **64.1% from VIIRS only** and **2.8% from MODIS only**.
- **Cells over the 4 years:** 784 cells had any MODIS detection, 896 any VIIRS detection, and 754 both.
- **Where both detect:** the log-log slope of VIIRS on MODIS is **1.12 (1.05–1.15)** for counts and **0.89 (0.85–0.92)** for summed FRP. Spearman ρ, including zeros, is 0.42 for counts and 0.55 for FRP.

### 5.4 Near-coincident fire clusters (Aqua-MODIS vs S-NPP VIIRS)

**Method.**

- Detections from both sensors are linked when their footprints overlap (centre distance ≤ half the sum of the pixel diagonals, plus 0.5 km) and their times differ by at most Δt.
- Connected components of these links form **fire clusters**: adjacent hot pixels in one overpass, not wildfire incidents.
- A cluster counts toward the statistics only if the other sensor had any detection within 100 km and Δt. This is a proxy for co-observation, because FIRMS does not publish non-detections.

| Δt window | eligible clusters | both | MODIS only | VIIRS only | P(VIIRS detects \| MODIS cluster) | P(MODIS detects \| VIIRS cluster) | median VIIRS/MODIS pixel ratio | median VIIRS/MODIS FRP ratio | FRP Spearman ρ | median \|Δt\| |
|---|---|---|---|---|---|---|---|---|---|---|
| 10 min | 8,008 | 2,524 | 154 | 5,330 | 0.942 | 0.321 | 3.36 | 1.11 | 0.915 | 6 min |
| **20 min** | **15,550** | **5,311** | **380** | **9,859** | **0.933** | **0.350** | **3.0** | **1.08** | **0.906** | 11 min |
| 30 min | 22,076 | 7,778 | 789 | 13,509 | 0.908 | 0.365 | 3.0 | 1.05 | 0.898 | 15 min |

The conclusions do not depend on the choice of window.

Probability that Aqua-MODIS also detects a VIIRS cluster, by the VIIRS cluster's summed FRP (Δt ≤ 20 min):

| VIIRS cluster FRP (MW) | <2 | 2–5 | 5–10 | 10–20 | 20–50 | 50–100 | 100–250 | ≥250 |
|---|---|---|---|---|---|---|---|---|
| VIIRS clusters | 2,779 | 2,733 | 2,206 | 1,899 | 1,922 | 1,059 | 1,079 | 1,493 |
| P(MODIS detects) | 0.006 | 0.053 | 0.179 | 0.342 | 0.543 | 0.714 | 0.843 | 0.933 |

**Interpretation.**

1. When both sensors see the same fire at nearly the same time, their FRP estimates agree closely (median ratio 1.08, ρ ≈ 0.91).
2. Fires missed by Aqua-MODIS are overwhelmingly small. About half the VIIRS clusters (51%) are under 10 MW, and MODIS detects only 0.6–18% of them.
3. These two facts together explain the aggregate statistics. VIIRS has far more detections because it resolves each fire into more, smaller pixels (about 3 VIIRS pixels per MODIS pixel in co-detected clusters) and because it detects many small fires that MODIS misses. Its total FRP is only modestly higher (×1.14 vs Aqua), because small fires carry little FRP.
4. The VIIRS-to-MODIS count ratio therefore depends on the fire-size distribution, which varies by month, region and biome. This is why the monthly count ratio ranges from 4.6 to 22.

## 6. Recommended harmonization approach

Ranked by scientific defensibility given the evidence above:

1. **Calibrate on Aqua vs S-NPP, using the full 2012–2026 overlap and global coverage.** The two have matched overpass times. Treat Terra separately: its overpass time differs, and both Terra and Aqua are in extended missions with drifting orbits, which must be checked from overpass-time metadata before a long record is built.
2. **Harmonize in FRP space, not count space.** Monthly gridded FRP (or fire radiative energy) agrees within roughly 10–15% where both sensors observe. The residual difference is concentrated in small fires.
3. **Build a "MODIS-equivalent" VIIRS series using an FRP-dependent detection-probability model.** Fit P(MODIS detects | cluster FRP, view angle, day/night, biome, cloud context) on near-coincident pairs. Then apply it to VIIRS clusters, or keep only VIIRS clusters above an empirically matched FRP threshold, to simulate what MODIS would have recorded. The result can be checked directly against observed MODIS in the overlap period.
   - Conversely, extending MODIS to VIIRS sensitivity requires modelling the unobserved small-fire component. That is harder to validate, so it is a secondary goal.
4. **For count-based indicators, use resolution-matched units.** Count unique 1 km cells or fire clusters per overpass rather than native pixels, and model the remaining ratio as a function of activity level (since the slope ≠ 1). Never use a single global scale factor.
5. **Validate out of sample.** Use month-block and region-block cross-validation, and report uncertainty, which the code already does with a month-block bootstrap. Repeat sections 5.3–5.4 for contrasting fire regimes before trusting any coefficients: savanna (Africa), deforestation (Amazon), boreal, agricultural burning (South Asia).
6. **Plan VIIRS continuity.** Cross-calibrate NOAA-20 (from 2018-04-01) and NOAA-21 (from 2023-03-01) against S-NPP, whose 375 m record ends 2026-11-02 according to FIRMS. Use the same cluster method.

## 7. Limitations

- **Region and period.** The analysis covers one region (a bbox around California, a Mediterranean-climate fire regime) and four years. The coefficients above are **not** general. The global day verifies retrieval only; its VIIRS/MODIS count ratio of 10.3 (vs Terra+Aqua) already differs from California's 4.5.
- **Co-observation proxy.** FIRMS publishes detections only. "Other sensor observed this location" is approximated by "other sensor detected something within 100 km and Δt". Clouds, smoke, swath edges, and differences in view angle and pixel growth are not modelled. True detection probabilities need swath and cloud-mask data, such as MOD14/MYD14 and VNP14IMG granules.
- **Heuristic parameters.** The cluster-linking parameters (footprint-overlap rule + 0.5 km tolerance, Δt) are heuristics. Results were stable across Δt = 10/20/30 min, but tolerance sensitivity was not tested.
- **Counts include all FIRMS `type` values.** Comparisons used all detection types; vegetation-only counts (`n_vegetation`) are available. Non-vegetation types make up 5.1% of VIIRS and 1.1% of MODIS records in this window.
- **Coverage granularity.** Coverage is tracked at day granularity. Partial-day outages are not detected unless FIRMS lists the day as missing. The missing-days feed is the data source of an official FIRMS page rather than a versioned API, so it is treated as optional.
- **Duplicate checks.** Duplicates are checked within each monthly file. Rows dated outside their request window are flagged and excluded, which prevents cross-month double counting (tested).
- **Global throughput.** Global scale through the Area API is limited by transaction cost. A rough estimate from the measured ~30–35 transactions per global day-request is about 185k transactions for the S-NPP record, which is several hours at a safe 4,000 transactions per 10 minutes. MODIS, with more days, would take longer. For the bulk global archive, the FIRMS archive download (email-delivered files, https://firms.modaps.eosdis.nasa.gov/download/) is the documented alternative. Bulk ingestion of archive files is not yet implemented.
- **Environment.** This Windows machine blocks pyarrow's Parquet DLL by Application Control, so Parquet I/O ran through the polars fallback. The full `jupyter` metapackage has no Python 3.15 wheels (pyyaml); the notebook is executed with `nbclient` + `ipykernel`.

## 8. Next steps

1. **Scale the overlap study.** Run Aqua vs S-NPP for one fire season in each of 4–6 contrasting regions (`--region west,south,east,north`), then globally for selected years. Ingest FIRMS archive files for bulk years (new reader for the archive CSVs in `acquisition`).
2. **Add explanatory variables to the cluster analysis.** Use scan/track as a view-angle proxy, day/night, land cover and latitude, and fit the detection-probability model with cross-validation.
3. **Create the first harmonized product.** Produce a MODIS-equivalent monthly 0.25° FRP and cluster-count grid from VIIRS for 2012–2026, validate it against Aqua-MODIS, and only then extend the record.
4. **Plan the ML phase.** Pattern discovery and XGBoost forecasting should train on the harmonized product with coverage masks, not on raw counts. Use the existing `harmonization_prep` tables only for prototyping features.

## Appendix: reproducing these results

```bash
python -m firelens run --region california --start 2018-01-01 --end 2021-12-31
python -m firelens download --region world --start 2020-09-01 --end 2020-09-01 --chunk-days 1
python -m firelens process  --region world --start 2020-09-01 --end 2020-09-01
python scripts/build_notebook.py
python -m pytest -q
```

Machine-readable results are in `reports/results/harmonization_diagnostics_california_2018-01-01_2021-12-31_0.25deg.json` and `reports/results/eda_summary.json`. Figures are in `reports/figures/`.
