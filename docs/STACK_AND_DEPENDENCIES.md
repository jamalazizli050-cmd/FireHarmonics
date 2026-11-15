# Proposed implementation stack & tool acquisition (not yet initialized)

**These are recommendations for a later implementation phase, not packages bundled in ZIP or already installed.**

- React + TypeScript + Vite (unless existing repo already specifies other stack).
- `three`, `@react-three/fiber`, `@react-three/drei`: possible 3D globe implementation; inspect Figma globe compatibility first.
- `react-router-dom`: routes if needed.
- `@tanstack/react-query`: API state, polling/retries.
- `recharts` OR `visx`: dashboard charts, only one primary chart toolkit to avoid duplication.
- `framer-motion` or its current `motion` package: transitions, respecting reduced motion.
- `lucide-react`: icons.
- `react-hook-form` + `zod`: contact validation if appropriate.
- `jspdf` + `html2canvas` (or well-supported equivalent): client PDF; accessibility/performance review required.
- Playwright test runner + Playwright MCP: visual and functional browser tests.
- `typescript`, ESLint, formatting tools.

## Claude Code tooling
- Official Figma MCP plugin: `claude plugin install figma@claude-plugins-official` (needs sign-in).
- Playwright MCP: `claude mcp add playwright npx @playwright/mcp@latest`.
- Frontend skill: `npx skills add anthropics/skills --skill frontend-design` (verify current repo and target agent).
- Web app testing skill: `npx skills add anthropics/skills --skill webapp-testing` (verify availability).
- Optional Superpowers by obra: inspect first, install only if approved/trusted.

**Phase 0 prohibition:** do not write package.json, initialize Vite, install actual frontend npm packages, or add starter code without a separate authorization. A basic folder skeleton and tools/MCP are the only allowed changes in this phase.

## Official references
- https://developers.figma.com/docs/figma-mcp-server/remote-server-installation/
- https://playwright.dev/mcp/clients/claude-code
- https://github.com/anthropics/skills
- https://github.com/obra/superpowers
- https://www.figma.com/community/file/1611332293521806958
