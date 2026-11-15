# EXECUTE NOW — Phase 0: Create skeleton and install tools ONLY

You are operating in a directory containing the FireHarmonics source pack. No website code is requested yet.

## Objective
Prepare a reproducible workspace, clone the existing backend integration branch, install Claude Code skills/MCP and *safe foundational development tools*, inspect their availability, and stop. No frontend implementation.

### Step 1 — Examine bundled material
- Read `CLAUDE.md` and all `/docs/*.md`, inspect `design/mockups/` and `design/assets/`, check the asset manifest.
- Verify the 5 mockups and 11 source images exist, and report if anything is missing.
- Preserve exact JPEGs and descriptive filenames.

### Step 2 — Prepare empty directories
- Use the already existing `workspace/frontend/` directory skeleton. Create any missing empty directories but **do not add source files** (except `.gitkeep` where necessary).
- Do not make React components, pages, configuration files, JSON examples, or CSS yet.
- Keep references and source images outside of runtime source.

### Step 3 — Clone repository safely
- Check if `workspace/backend/FireHarmonics` already exists. If not:
  `git clone --branch feature/backend-ml-integration --single-branch https://github.com/Motoqvin/FireHarmonics.git workspace/backend/FireHarmonics`
- Verify commit `9f86410e5cc0c7d66336e2fe5c1ceebfe1f1e6b8` if still the branch head; if different, report without resetting.
- Read actual `FRONTEND_INTEGRATION.md`, `docs/ML_INTEGRATION.md`, and the current API models. Summarise exact endpoint/field names in a **setup report only**.
- Never change, commit, or push to this repository during setup.

### Step 4 — Tools and skills (verify before install, avoid duplicates)
- Verify `git`, `node` >= 20, `npm`/`npx`, `python` in supported version, and `claude` CLI.
- Install Anthropic's `frontend-design` skill in Claude Code using current documented method if missing.
- Install Anthropic's `webapp-testing` skill if still published and available; otherwise report unavailable and use Playwright MCP.
- Connect Figma plugin: `claude plugin install figma@claude-plugins-official`; authorize interactively if needed. If plugin fails, documented alternative: `claude mcp add --transport http figma https://mcp.figma.com/mcp`.
- Install Playwright MCP: `claude mcp add playwright npx @playwright/mcp@latest` if missing.
- For debugging/test discipline, consider `obra/superpowers` but **do not install unreviewed third-party code blindly**. Inspect the repository, permissions, and current official instructions first.
- Use `/mcp`, `claude mcp list`, plugin status, and skill listing to verify results where supported. Never claim Figma connection without authentication.
- Figma Community globe URL: https://www.figma.com/community/file/1611332293521806958 . Inspect reuse/export rights and availability; do not assume the Community file is an installable React/Three.js component. Do not recreate or integrate it yet.

### Step 5 — Libraries and dependencies
- Read `docs/STACK_AND_DEPENDENCIES.md`.
- Decide frontend package manager and compatible versions (React + TypeScript + Vite recommended unless the repo already dictates otherwise). Prepare a proposed, version-aware dependency list for: Three.js/react-three-fiber/drei, routing, charts, accessible controls, motion, data-fetching, export PDF/CSV, Playwright, TypeScript/lint tools.
- **Do not initialize frontend project or create package manifests yet**: the user explicitly requested empty scaffold only. Do not run `npm install` without a project manifest; that would create implementation files. You may use read-only package metadata queries and download CLI tool installers only where appropriate.
- Existing backend: optional clean Python environment and `pip install -r requirements-dev.txt`, only if needed for verification and if version/security constraints permit. Do not train models or run live NASA requests.
- If user intent of 'download all libraries' conflicts with the no-file/empty-scaffold constraint, stop at a validated install plan and explicitly say that actual npm dependency installation requires generating a `package.json` and lockfile; do **not** silently cross the boundary.

### Step 6 — Final report and stop
Provide:
1. Directory tree and source asset count.
2. Git branch and short HEAD.
3. Tool versions and skill/MCP installation status (installed / needs auth / blocked).
4. Backend API docs located.
5. Dependency list and exact commands planned for phase 1.
6. Missing assets, credentials, blockers (especially Figma/Node).
7. Explicit confirmation: NO website UI or backend code created/changed; NO commits/pushes.

**Do not begin implementing Home or any other page until the user explicitly authorizes phase 1.**
