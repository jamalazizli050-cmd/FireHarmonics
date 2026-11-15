# FireHarmonics — Claude Code source package (2026-11-14)

**Goal of this session: SETUP ONLY.** Do not implement the website, do not generate UI or API code, do not modify the backend, and do not push/commit. Create only the empty directory skeleton (if not already present), install verified tooling, download dependencies and reference material, and report findings. Do not overwrite files.

## Quick start on Windows
1. Extract this ZIP to a local directory (e.g. `C:\Projects\FireHarmonics_Source`). Avoid placing it inside an existing Git repo.
2. Open this root folder in VS Code or Claude Code. Give Claude the instructions in `CLAUDE.md` and `setup/CLAUDE_SETUP_TASK.md`.
3. Allow normal network installation and Figma authentication when requested. There is no need to provide a NASA API key during this setup phase.
4. Claude should clone **only** `feature/backend-ml-integration` into `workspace/backend/FireHarmonics`, inspect `FRONTEND_INTEGRATION.md`, prepare tooling, and stop before implementation.
5. All five JPEG mockups and eleven original JPEG assets are already bundled. `design/ASSET_MANIFEST.json` records original names and SHA-256 checksums.

**Do not execute the future implementation brief now.** The five-page spec is reference material for the next phase.

## Relevant links
- Backend branch: https://github.com/Motoqvin/FireHarmonics/tree/feature/backend-ml-integration
- Integration commit reported by Claude: `9f86410e5cc0c7d66336e2fe5c1ceebfe1f1e6b8`
- Figma Community globe: https://www.figma.com/community/file/1611332293521806958

## Existing packages vs downloads
This ZIP includes documentation, source images, and the empty skeleton. It **does not contain** the upstream Git repository, npm packages, Python virtual environment, binaries, or third-party skill code. The setup instructions ask Claude to retrieve those from their respective sources. Installation requires internet and may require authentication.
