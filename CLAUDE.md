# Claude Code instructions — FireHarmonics

**Current phase: 0 — scaffolding and tool acquisition ONLY.**

Read in this order:
1. `START_HERE.md`
2. `setup/CLAUDE_SETUP_TASK.md` (the task to execute now)
3. `docs/PROJECT_CONTEXT.md`
4. `docs/SCREEN_SPECIFICATIONS.md`
5. `docs/ASSET_CATALOG.md`
6. `docs/API_REQUIREMENTS.md`
7. `docs/STACK_AND_DEPENDENCIES.md`
8. `docs/ACCEPTANCE_CHECKLIST.md`

## Nonnegotiables
- Do not build any frontend components, HTML pages, React app, sample UI, test UI, placeholder mock data, or backend adapters during phase 0.
- Do not run `npm create vite`, `create-next-app`, scaffold generators that create source code, or modify working backend files.
- Do not modify/rewrite ML models or train models.
- Do not commit, push, merge, rebase, or open PRs.
- Never print secrets or put them in files, terminal logs, or screenshots. Do not pull secrets from history.
- Preserve bundled original images byte-for-byte. Create derivatives only later and with clear purpose.
- Do not invent API endpoints. Inspect the cloned `FRONTEND_INTEGRATION.md` first.
- If package installation or auth is blocked, report it; do not pretend success.
- If instructions say 'install dependencies', do not create a frontend package.json yet: record proposed dependency choices in a setup report and optionally prefetch packages only when a clean, reproducible method exists. Actual frontend project initialization belongs to a separately approved phase.
- The frontend should eventually show XGBoost prominently as **experimental, not validated**, with the seasonal baseline as comparison. Never relabel XGBoost as the backend's validated primary method.
- Source imagery belongs in `design/`; implementation code will eventually live under `workspace/frontend/`.
