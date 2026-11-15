# Phase 0 setup report — FireHarmonics (2026-11-14)

Status: **Phase 0 complete. Stopped before implementation.** Phase 1 needs explicit approval.

## 1. Directory tree and source assets

```
FireHarmonics_Claude_Source_Pack/
├─ CLAUDE.md, START_HERE.md
├─ .claude/                          (Claude Code tooling only, see section 3)
│  ├─ settings.local.json            enabled plugins: frontend-design, figma (local scope)
│  └─ skills/webapp-testing/         Anthropic skill, pinned copy
├─ design/
│  ├─ ASSET_MANIFEST.json
│  ├─ mockups/   01-home, 02-interactive-globe, 03-about, 04-contact, 05-faq (.jpeg)
│  └─ assets/    11 .jpeg illustrations
├─ docs/         7 files (unchanged)
├─ setup/        task files + this report
└─ workspace/
   ├─ backend/FireHarmonics/         git clone (section 2)
   ├─ frontend/{public/assets,src/{components,lib,pages,styles},tests}/  .gitkeep only
   ├─ references/  .gitkeep
   └─ scripts/     .gitkeep
```

- 5/5 mockups and 11/11 assets are present. **All 16 SHA-256 hashes and byte sizes match** `ASSET_MANIFEST.json`. No image was opened for writing.
- The frontend skeleton already existed and was complete, so nothing was added. It contains only `.gitkeep` files: no `package.json`, lockfile, `node_modules`, config or source.

## 2. Backend clone

| Item | Value |
| --- | --- |
| Path | `workspace/backend/FireHarmonics` |
| Branch | `feature/backend-ml-integration` (single-branch clone) |
| HEAD | `9f86410` = `9f86410e5cc0c7d66336e2fe5c1ceebfe1f1e6b8`, **matches** the expected commit |
| Commit | "feat: integrate FireLens ML engine with FastAPI backend" (2026-11-14) |
| Working tree | clean (`git status --porcelain` empty); nothing modified, committed or pushed |

## 3. Tools, skills and MCP

| Tool | Version / state | Status |
| --- | --- | --- |
| git | 2.56.0.windows.4 | ✅ present |
| Node.js | **v26.13.1 LTS**, installed this session via `winget install OpenJS.NodeJS.LTS` (official OpenJS MSI, hash verified by winget) | ✅ installed (was missing) |
| npm / npx | 11.20.0 / 11.20.0 | ✅ |
| Python | 3.15.2 | ⚠️ present, but see the backend note below |
| `claude` CLI | 2.1.325. Only the VS Code extension's bundled binary exists (`~/.vscode/extensions/anthropic.claude-code-2.1.325-win32-x64/resources/native-binary/claude.exe`). There is **no `claude` on PATH**. | ⚠️ works by full path |
| Browser for Playwright | Microsoft Edge (system). No Chrome, no Playwright-managed browsers. | ✅ via `--browser msedge` |
| **frontend-design** skill | plugin `frontend-design@anthropic-plugin-directory` v1.1.1, local scope | ✅ installed, enabled |
| **webapp-testing** skill | still published in `anthropics/skills`. Copied the 6 reviewed files from commit `1780c5e5` to `.claude/skills/webapp-testing/` (provenance in `.source`). This avoided the 12-skill `example-skills` bundle and the third-party `npx skills` CLI. | ✅ installed. Its Python scripts need `pip install playwright`, which is **not** installed. Prefer the Playwright MCP / `@playwright/test`. |
| **Figma** plugin | `figma@anthropic-plugin-directory` v2.2.157, local scope. MCP `plugin:figma:figma` → `https://mcp.figma.com/mcp` | ✅ **Connected and authenticated** (verified with `whoami`; Starter plan, View seat, so MCP rate limits are low) |
| **Playwright MCP** | `cmd /c npx -y @playwright/mcp@0.0.85 --browser msedge`, **project scope (`.mcp.json`)** with `C:\Program Files\nodejs` prepended to PATH in `env`, pinned instead of `@latest` | ✅ Approved and connected; Edge launch verified (`about:blank`) |
| obra/superpowers | inspected, **not installed** (see below) | ⏸ awaiting approval |

Command note: in this Claude Code build the built-in marketplace is named `anthropic-plugin-directory`. The documented `figma@claude-plugins-official` returned "marketplace not found", so the equivalent `figma@anthropic-plugin-directory` was used. Both list the same upstream source (figma/mcp-server-guide @ `f2789420`).

**superpowers review:** MIT licence, v7.0.2, listed in the official plugin marketplace at pinned SHA `22f0b463`. It ships 15 workflow skills (TDD, systematic-debugging, writing-plans, code review, git worktrees, etc.). It also installs a **SessionStart hook** (`hooks/run-hook.cmd session-start`) that runs on every startup, clear and compact and injects instructions. Some of its skills push git-worktree, branch-finishing and commit workflows, which conflict with this project's no-commit rule. It is not installed. If wanted, install with `claude plugin install superpowers@anthropic-plugin-directory --scope local` after reading `hooks/session-start`.

**Figma Community globe** (https://www.figma.com/community/file/1611332293521806958): the page returns **HTTP 403** to automated fetches. The title, author, licence, contents and price could not be determined. Nothing is assumed about reuse rights or code assets. Check it in a browser, or through the Figma MCP after sign-in.

## 4. Backend API contract (from `FRONTEND_INTEGRATION.md`, `docs/ML_INTEGRATION.md`, `FireDetectionAPP/schemas.py`, `main.py`)

Base URL `http://127.0.0.1:8000`; OpenAPI at `/openapi.json`; `result.schema_version == "2.0"`.

| Purpose | Endpoint |
| --- | --- |
| Model readiness | `GET /api/v1/model` (alias `/api/v1/models`) → `available` |
| Start analysis | `POST /api/v1/analyses` → 202 with `job_id`, `links{status,events,result,retry,export}` |
| Status (poll 1-2 s) | `GET /api/v1/analyses/{job_id}` |
| SSE | `GET /api/v1/analyses/{job_id}/events`: events `progress`/`completed`/`failed`, `Last-Event-ID` or `?after_id=N`, 15 s heartbeat |
| Result | `GET /api/v1/analyses/{job_id}/result`: 200 / 202 + `Retry-After` / 409 failed / 404 |
| Export | `GET /api/v1/analyses/{job_id}/export?format=json` or `?format=csv&table=` `daily`, `monthly`, `annual`, `hotspots`, `ml_monthly`, `ml_seasonal`, `ml_anomalies`, `ml_hotspots` (an ML table returns 409 if the ML stage didn't run). **PDF is frontend-only.** |
| Retry | `POST /api/v1/analyses/{job_id}/retry` (failed and recoverable jobs only) |
| Saved result | `GET /api/v1/locations/analysis?latitude=&longitude=&radius_km=25` |
| Example regions | `GET /api/v1/regions` |
| Harmonization | `GET /api/v1/harmonization`, `GET /api/v1/harmonization/regions/{region_id}` (regional only) |
| Sources | `GET /api/v1/sources`; liveness `GET /health` |
| Do **not** use for Harmonized view | `GET /api/v1/harmonized` (older endpoint; returns raw detections labelled `not_harmonized`) |

**Request fields** (`AnalysisRequest`): `latitude`, `longitude`, `radius_km` (default 25; 1-200 with ML), `location_name` (≤160), `source` (default `VIIRS_NOAA20_NRT`), `start_date`/`end_date` or `lookback_days` (≤3650), `ml_mode` (`download` | `cache_only` | `off`), `target_month` (`YYYY-MM`, current to +12), `history_years` (3-14, default 10).
**Source enum:** `LANDSAT_NRT`, `MODIS_NRT`, `MODIS_SP`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA20_SP`, `VIIRS_NOAA21_NRT`, `VIIRS_NOAA21_SP`, `VIIRS_SNPP_NRT`, `VIIRS_SNPP_SP`.
**Job:** `status` ∈ `queued`, `running`, `paused`, `completed`, `failed`; `progress` 0-100; `stage`; `message`; `eta_seconds`; `nasa_pause_seconds`; `cache_hit`; `result_status` ∈ `complete`, `partial`, `descriptive_only`.
**Result keys:** `schema_version`, `status`, `location`, `source`, `provenance`, `coverage`, `detection_count`, `historical_activity`, `seasonal_patterns`, `long_term_trends`, `anomalies`, `hotspots`, `prediction`, `ml_analysis`, `satellite_analysis`, `overview`, `components`, `warnings`.
**Two series:** the descriptive blocks use the requested `source`. `ml_analysis` and `prediction` always use S-NPP VIIRS. Never sum the two.

**XGBoost display contract:** `prediction.method` ∈ `seasonal_baseline`, `xgboost_pooled`, `unavailable`. In the installed model **XGBoost passed validation for no region and no lead**, so every forecast uses `seasonal_baseline`. The XGBoost value lives at `prediction.model_comparison.xgboost_pooled.{probability, used:false, validated_for_this_location_and_lead:false}`, and the baseline at `.seasonal_baseline`. The UI should foreground the XGBoost value as an *experimental comparison* next to the validated baseline. `probability: null` means "not issued" and is never shown as 0 %.

**Facts that matter for Phase 1:**
- **No CORS middleware** in `FireDetectionAPP/main.py`. Use a Vite dev-server proxy (`/api` and `/health` → `127.0.0.1:8000`) rather than modifying the backend.
- S-NPP delivery **ended 2026-11-02**; last fully observed month is 2026-10, so forecast leads grow monthly.
- A new location takes 20-30 min (NASA downloads); cached results take seconds. Requires `NASA_FIRMS_MAP_KEY` (a 500 error if missing). No key was requested or used.
- `docs/examples/analysis_result.synthetic.json` is for response shapes only, never for display data.

**Backend Python env: not created.** The backend is verified on **Python 3.13.17**. Its docs say Windows Smart App Control blocks SciPy 1.18.1 on Python 3.15, which is the only Python installed here. To run the backend locally later: install Python 3.13, then `py -3.13 -m venv .venv` and `pip install -r requirements-lock.txt` inside `workspace/backend/FireHarmonics`. No tests or models were run.

## 5. Proposed Phase 1 dependencies (npm registry, read-only query, 2026-11-14)

Package manager: **npm** (ships with Node 26; no extra tool). Node 26.13.1 meets every engine constraint below.

| Purpose | Package | Version | Note |
| --- | --- | --- | --- |
| Build | `vite`, `@vitejs/plugin-react` | 8.3.8, 6.3.0 | |
| UI | `react`, `react-dom` | 19.3.1 | R3F requires react `<19.4` |
| Language | `typescript` | **6.0.3** | TS 7.0.2 is latest, but `typescript-eslint` 8.72 requires `<6.1` |
| 3D | `three`, `@react-three/fiber`, `@react-three/drei`, `@types/three` | 0.187.0, 9.8.2, 10.7.9, 0.187.0 | |
| Routing | `react-router` | 8.5.1 | v8 merged `react-router-dom`; requires Node ≥22.22 ✅ |
| API state | `@tanstack/react-query` | 5.107.1 | SSE via native `EventSource` (no library) |
| Charts | `recharts` | 3.10.1 | one toolkit only (visx not added) |
| Motion | `motion` | 14.14.0 | the current name for framer-motion; respects `useReducedMotion` |
| Icons | `lucide-react` | 1.73.0 | |
| Accessible primitives | `@radix-ui/react-tabs`, `@radix-ui/react-accordion` | 1.2.1, 1.3.1 | About tabs, FAQ accordion |
| Forms | `react-hook-form`, `zod`, `@hookform/resolvers` | 7.92.0, 4.6.7, 7.0.6 | |
| PDF | `jspdf` + `html-to-image` | 4.2.1, 1.11.13 | `html2canvas-pro` 2.6.4 is an alternative (needs Node ≥24) |
| Contact email | `@emailjs/browser` | 5.0.3 | provider still to be decided (public key only on client) |
| Test | `@playwright/test`, `@axe-core/playwright`, `vitest`, `@testing-library/react` | 1.65.1, 4.14.0, 5.1.0, 16.3.3 | |
| Lint / format | `eslint`, `typescript-eslint`, `eslint-plugin-react-hooks`, `prettier`, `@types/react` | 10.15.0, 8.72.2, 7.1.1, 3.10.1, 19.3.0 | |

CSV is served by the backend export, so no CSV library is needed.

**Planned Phase 1 commands (not run).** Initializing the project creates `package.json` and a lockfile, so this needs Phase 1 approval.

```powershell
cd workspace/frontend
npm init -y          # then hand-write package.json/vite config (no scaffold generator, per CLAUDE.md)
npm install --save-exact react@19.3.1 react-dom@19.3.1 three@0.187.0 @react-three/fiber@9.8.2 @react-three/drei@10.7.9 react-router@8.5.1 @tanstack/react-query@5.107.1 recharts@3.10.1 motion@14.14.0 lucide-react@1.73.0 @radix-ui/react-tabs@1.2.1 @radix-ui/react-accordion@1.3.1 react-hook-form@7.92.0 zod@4.6.7 @hookform/resolvers@7.0.6 jspdf@4.2.1 html-to-image@1.11.13
npm install --save-dev --save-exact vite@8.3.8 @vitejs/plugin-react@6.3.0 typescript@6.0.3 @types/react@19.3.0 @types/three@0.187.0 @playwright/test@1.65.1 @axe-core/playwright@4.14.0 vitest@5.1.0 @testing-library/react@16.3.3 eslint@10.15.0 typescript-eslint@8.72.2 eslint-plugin-react-hooks@7.1.1 prettier@3.10.1
npx playwright install chromium   # optional; Edge is already usable
```

## 6. Blockers and pending items

1. ~~Figma auth pending~~ **Resolved 2026-11-15:** Figma is authenticated and Playwright MCP is approved. Note: a server added from Git Bash is keyed `C:/…`, but the VS Code session uses `c:/…`, so local-scope MCP entries didn't load. Project scope (`.mcp.json`) avoids this.
2. **Figma globe licence unknown** (403 to automated fetch). Needs a manual check.
3. **`claude` not on PATH.** Optional fix: the native installer (`irm https://claude.ai/install.ps1 | iex`) or add the extension's `native-binary` folder to PATH.
4. **Python 3.13 needed** to run the backend locally (3.15 is blocked by Smart App Control for SciPy).
5. **NASA FIRMS key needed** before any live test. The old key exposed in public git history must be revoked by the owner. It was not looked up or reproduced.
6. Superpowers: awaiting your decision (section 3).
7. Contact email provider and team data are not yet supplied.

## 7. Confirmation

- **No** website UI, components, pages, CSS, config, mock data, `package.json` or lockfile created. **No** scaffold generator run.
- **No** backend file changed (clean tree at `9f86410`), **no** models run or trained.
- **No** commits, pushes, merges, rebases or PRs.
- **No** secrets read, printed or stored.
- All 16 bundled JPEGs are byte-identical to the manifest.
- Changes outside the pack: Node.js installed system-wide; `~/.claude.json` gained the local-scope Playwright MCP entry; plugin cache under `~/.claude/plugins`; npm cache holds `@playwright/mcp@0.0.85`.
