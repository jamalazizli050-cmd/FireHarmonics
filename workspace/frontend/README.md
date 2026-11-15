# FireHarmonics — frontend

React 19 + TypeScript 6 + Vite 8 frontend for the FireHarmonics FastAPI backend
(`../backend/FireHarmonics`, branch `feature/backend-ml-integration`). The only API contract used is
`../backend/FireHarmonics/FRONTEND_INTEGRATION.md`; the backend is not modified.

## Run locally

Prerequisites: Node.js ≥ 22.22 (tested with 26.13.1) and, for real analyses, the backend on `127.0.0.1:8000`.

```powershell
# 1. Backend (separate terminal) — Python 3.13 is required (3.15 cannot load SciPy under Smart App Control)
cd workspace/backend
py -3.13 -m venv .venv313            # once
.venv313\Scripts\python -m pip install -r FireHarmonics/requirements-lock.txt   # once
$env:NASA_FIRMS_MAP_KEY = "<your key>"   # without it, analyses return HTTP 500 (shown honestly in the UI)
$env:FIREHARMONICS_DATA_DIR = "$PWD\.runtime-data"
cd FireHarmonics
..\.venv313\Scripts\python -m uvicorn FireDetectionAPP.main:app --host 127.0.0.1 --port 8000

# 2. Frontend
cd workspace/frontend
npm ci
npm run dev          # http://127.0.0.1:5173  (proxies /api and /health to the backend)
```

Other scripts: `npm run build` (typecheck + production build), `npm run preview` (serves `dist/` on :4173 with the same
proxy), `npm run lint`, `npm run typecheck`, `npm test` (Vitest unit tests), `npm run test:e2e` (Playwright on the
system Microsoft Edge; builds must exist — run `npm run build` first), `npm run assets` (regenerates transparent
illustration derivatives).

### Configuration (`.env.local`, see `.env.example`)

| Variable | Purpose |
| --- | --- |
| `FIREHARMONICS_API_TARGET` | Backend URL for the dev/preview proxy (default `http://127.0.0.1:8000`) |
| `VITE_API_BASE_URL` | Optional absolute API base when the site is served without the proxy (backend has no CORS) |
| `VITE_FORMSPREE_FORM_ID` | Contact form delivery via Formspree (public form ID only). Empty → honest "not configured" message |
| `VITE_GEOCODER_URL` | Place search (default: Open-Meteo geocoding API, no key) |

## Deploying

`npm run build`, copy `dist/` to the server, run the backend (uvicorn on 127.0.0.1:8000 with `NASA_FIRMS_MAP_KEY`
and `FIRELENS_DATA_DIR` outside synced folders) and serve both with `deploy/Caddyfile` (static files + `/api`
proxy, so no CORS is needed). Before a demo, run `firelens/tools/prewarm_archives.py` and analyse the demo places
once so they load in seconds.

## What is where

```
src/
  api/            types.ts (backend schema 2.0), client.ts (typed fetch + exports), useAnalysisJob.ts (POST → SSE → polling → result)
  components/
    globe/        GlobeStage.tsx (one persistent Three.js canvas; Home ⇄ Globe morph), createEarth.ts (three-globe setup)
    report/       ReportOverlay, JobStatus, PanelSummary, ForecastCards, sections/{Descriptive,Model}Sections
    layout/       NavBar, Starfield, AccessibilityPanel
    ui/           LocationSearch (ARIA combobox), DataControls (shared source/period controls), primitives
  pages/          HomePage, GlobePage, AboutPage, ContactPage, FaqPage
  content/        about.tsx, faq.ts
  lib/            format, period, sources, geocode, contact (Formspree), pdf (jsPDF + html-to-image)
  state/          a11y.tsx (text size, contrast, motion, colour-blind palette), location.ts (URL state)
  assets/derived/ transparent PNGs generated from /design/assets (originals untouched; see DERIVED_ASSETS.json)
public/textures/  NASA Blue Marble textures (see TEXTURES_LICENSE.md)
tests/unit/       Vitest      tests/e2e/   Playwright (functional, a11y with axe, visual capture)
```

Original artwork is imported read-only from `/design` through the `@design` alias.

## Data honesty rules implemented

* Every number comes from the API; `null` renders as “—” / “not issued”, never 0. Unobserved months are hatched.
* The descriptive series (requested source) and the ML series (S-NPP VIIRS) are labelled separately and never summed.
* Pooled XGBoost is shown as **Experimental XGBoost Forecast · Experimental · Not validated · May be inaccurate**,
  using `prediction.model_comparison.xgboost_pooled`; the seasonal baseline and the backend-selected method are shown
  beside it. If no XGBoost value is returned the card says “Unavailable”.
* Harmonized data is only offered where `satellite_analysis.views.harmonized.status == "regional_only"`.
  Elsewhere the API's relative activity index (`views.normalized`) can be shown, always labelled
  “Normalized — not harmonized, not validated”.
* Forecasts are described as satellite hotspot-detection probabilities, not wildfire ignition.
