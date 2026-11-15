# FireHarmonics phase-0 OPTIONAL commands (PowerShell). Read CLAUDE_SETUP_TASK.md first.
# Run only commands you understand, from the root of the extracted source package.
# These commands are examples, not automatically executed on unzip.

$ErrorActionPreference = 'Stop'
Write-Host 'Checking prerequisites...'
git --version
node --version
npm --version
python --version
claude --version

if (!(Test-Path 'workspace/backend/FireHarmonics')) {
  git clone --branch feature/backend-ml-integration --single-branch https://github.com/Motoqvin/FireHarmonics.git workspace/backend/FireHarmonics
} else {
  Write-Host 'Backend already present; skipping clone.'
}

Write-Host 'Review and authorize these optional tool installs with Claude:'
Write-Host 'npx skills add anthropics/skills --skill frontend-design'
Write-Host 'npx skills add anthropics/skills --skill webapp-testing'
Write-Host 'claude plugin install figma@claude-plugins-official'
Write-Host 'claude mcp add playwright npx @playwright/mcp@latest'
Write-Host 'Stop here; no frontend setup/build has been performed.'
