# Project context — FireHarmonics

- Public-facing name: **FireHarmonics** (earlier technical name FireLens).
- Challenge: NASA Space Apps Challenge 2026 — Harmonization of MODIS and VIIRS Hot Spots.
- Purpose: global location-based historical satellite hotspot analysis and experimental next-month forecasting.
- Intended flow: search location -> rotate globe to coordinates -> backend async analysis of a **fixed 25 km radius** -> summary -> expandable report.
- Data: NASA FIRMS, including MODIS/VIIRS instrument data. FIRMS is a provider, not a separate satellite sensor.
- ML: XGBoost classifier (experimental; held-out tests not better than seasonal baselines), seasonal baseline (validated fallback in existing selection logic), Isolation Forest, Getis-Ord Gi*, trends/anomalies. Distinguish hotspot detection probability from ignition probability.
- Harmonization: only some regional precomputed count products validated. Never imply reliable worldwide or FRP harmonization.
- Existing backend integration was reportedly published by another Claude session in `feature/backend-ml-integration` (`9f86410`). 66 backend and 83 ML tests passed offline. Live NASA verification had not been run; do not call live integration verified.
- There is a previously exposed NASA API key in older public Git history; do not reproduce it and ask owner to revoke it.
- S-NPP dataset was reported to stop delivering after 2026-11-02; inspect current backend documentation before describing availability.
- New UI must faithfully reproduce original JPEG layouts while adding agreed features. This pack is **not** authorization to implement the UI yet.
