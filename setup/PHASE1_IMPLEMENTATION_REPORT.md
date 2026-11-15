# Phase 1 implementation report — FireHarmonics frontend (2026-11-15)

Run instructions, architecture and configuration: `workspace/frontend/README.md`.

## 1. Implemented

| Page / feature | Status |
| --- | --- |
| Home (mockup 01) | Starfield (supplied texture + twinkling stars), decorative rotating 3D Earth, brand title, “Choose your location” combobox with geocoded suggestions directly below (Open-Meteo; typed coordinates also accepted), intro fade sequence, responsive |
| Home → Globe transition | One persistent Three.js canvas: the same Earth glides, shrinks, changes tint and grows extruded country borders while the text fades; location and real coordinates are passed in the URL |
| Interactive Globe (mockup 02) | Drag / wheel / pinch / keyboard rotation and zoom; selection only via search; glowing pin, no radius overlay; fixed 25 km radius; right panel with search, source + period controls, live job status (SSE with polling fallback, progress, stage, ETA, NASA pause), errors with retry, partial-result notice, brief overview |
| Location Analysis Report | Dialog over the globe (focus trap, Esc, focus return): header & coverage, overview, burning-activity calendar (monthly heatmap of the ML record with unobserved months hatched + daily calendar), trends (annual bars, monthly line with brush, Theil–Sen / Mann–Kendall), AI pattern discovery (seasonality, anomalies, Isolation Forest, Gi* hotspots scatter + table), AI forecast, satellite analysis & harmonization (raw MODIS / raw VIIRS / regional harmonized chart when available), exports |
| Forecast presentation | “Experimental XGBoost Forecast” card first, badges Experimental · Not validated · May be inaccurate, value from `model_comparison.xgboost_pooled`; seasonal baseline beside it marked “Selected by backend”; backend method + reason; “Unavailable” when no XGBoost value; detection-not-ignition wording |
| Source/period sync | One `DataControls` component bound to one state in panel and report; changes re-POST `/api/v1/analyses` (`ml_mode: cache_only` after the first run); previous result stays visible, clearly marked, until the new one completes |
| Exports | PDF generated in the browser (jsPDF + html-to-image), JSON and CSV (all 8 tables) via `/api/v1/analyses/{id}/export` |
| About (mockup 03) | Four animated tabs (Overview, NASA Challenge, How It Works, Our Mission), original illustrations. (The Meet the Team section was removed at the owner's request on 2026-11-15.) |
| Contact (mockup 04) | Original layout + illustration; first/last name, email, optional phone with country code, message; validation on touch; honeypot; Formspree delivery (public form ID only, server-side rate limiting/spam filtering by the provider) plus client throttle; sending / success / error; values preserved on failure; without configuration it says the message was **not** sent |
| FAQ (mockup 05) | Ten accurate entries, 2-column grid continuing down the page, multiple open, animated height (reduced-motion fallback), original illustrations, single column on mobile |
| Accessibility | Settings panel: text size (90–150 %), high contrast, reduced motion (system/on/off), colour-blind chart palette; skip link, visible focus, ARIA labels, keyboard globe control; axe: no serious/critical violations on any page or the report (desktop + mobile) |

## 2. Figma

The Figma MCP is authenticated (Starter plan, View seat), but it only accepts `/design/` file keys; Community file
`1611332293521806958` cannot be opened without duplicating it into a team, and its licence could not be verified
(HTTP 403 to automated access). **No Figma code or assets were used.** The Earth is an original Three.js /
`three-globe` implementation with NASA Blue Marble (public domain) textures and Natural Earth borders — see
`workspace/frontend/public/textures/TEXTURES_LICENSE.md`.

## 3. Backend connectivity (real, unmodified backend)

Backend run locally with Python 3.13.17 (`workspace/backend/.venv313`, data dir `workspace/backend/.runtime-data`,
both outside the repo). Verified through the frontend proxy: `/health`, `/api/v1/model` (`available: true`),
`/api/v1/sources`, `/api/v1/harmonization`, `/api/v1/harmonization/regions/california`. `POST /api/v1/analyses`
returns **HTTP 500 “NASA_FIRMS_MAP_KEY is not configured”** because no NASA key was provided; the UI shows this
verbatim, offers retry, and shows no statistics. A full live analysis (SSE progress → result) therefore was **not**
executed against NASA. Report rendering was tested with the backend’s own example result
(`docs/examples/analysis_result.synthetic.json`, real engine / synthetic detections) injected **only in tests** via
Playwright route interception.

## 4. Tests actually executed (2026-11-15)

| Check | Result |
| --- | --- |
| `npm install` (345 packages) | OK |
| `npm run typecheck` | 0 errors |
| `npm run lint` | 0 errors, 6 warnings (react-refresh “only export components”, dev-only HMR hint) |
| `npm test` (Vitest) | 10 / 10 passed |
| `npm run build` | OK (globe chunk ≈ 2.1 MB / 584 KB gzip, lazily loaded) |
| `npx playwright test` (Edge, desktop 1600×1040 + mobile 390×844) | 50 / 50 passed — navigation, overflow, console errors, search→globe, live backend request, report sections, forecast labelling, source/period sync, harmonized gating, CSV/JSON/PDF downloads, contact, FAQ, About, a11y panel, axe scans |
| Visual capture `tests/e2e/visual.spec.ts` | 5 / 5 — screenshots in `workspace/frontend/test-results/visual-final/` compared by eye with `/design/mockups` |
| `npm audit --omit=dev` | 0 vulnerabilities (7 moderate in dev-only lint tooling: `minimatch` via typescript-eslint) |

Bugs found and fixed by testing: three-globe nulling the material colour (render-loop crash), report rendered below
the navigation (portal), Escape lost when a focused select was disabled, metric/label contrast below 4.5:1,
unfocusable scrollable tables, Motion transforms overriding CSS centring.

## 5. Remaining blockers / known differences

1. **NASA FIRMS key** needed for real analyses (set `NASA_FIRMS_MAP_KEY` for the backend). The previously exposed
   key in public git history should be revoked.
2. **Contact delivery** needs a Formspree form ID (`VITE_FORMSPREE_FORM_ID`) and the rate-limit / spam settings in
   that account; until then the form reports “not configured”.
3. ~~Team profiles~~ — section removed at the owner's request.
4. **Figma Community globe** licence unverified; not used.
5. Visual: the Home Earth is a tinted NASA texture rendered in 3D, so it approximates (not duplicates) the
   painted render in mockup 01; illustration backgrounds were removed into transparent PNG derivatives.
6. Backend has no CORS: production hosting must proxy `/api` (as Vite does) or the backend owner must add CORS.
7. `CLAUDE.md` still says “phase 0”; update it if you want future sessions to treat phase 1 as current.

No backend code changed (`git status` clean at `9f86410`), nothing committed or pushed, all 16 design files
byte-identical to `ASSET_MANIFEST.json`.
