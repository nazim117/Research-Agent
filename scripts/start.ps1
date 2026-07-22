# start.ps1 -- one-command startup helper for local Windows development.
#
# Mirrors the README's "Quick Start" steps exactly, just automated:
#   1. Copy .env.example -> .env if it doesn't exist yet (every var has a
#      working default; this just gives you a file to edit later).
#   2. Start the Docker-based infra: qdrant, embeddings, searxng, mcp-server.
#      (mcp-server runs in Docker here for zero-Go-toolchain convenience --
#      if you're actively developing mcp-server itself, run it natively via
#      "go run ./cmd/server" instead, same as the VS Code "Run All" task.)
#   3. Start chat-agent natively in its own window (hot reload via --reload).
#   4. Start the dashboard natively in its own window (hot reload via Vite).
#
# Usage: double-click start.cmd, or run "powershell -File scripts\start.ps1"
# from the repo root or from anywhere -- it resolves paths off its own location.

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "== Research Agent startup helper ==" -ForegroundColor Cyan

# 1. .env
$envPath = Join-Path $repoRoot ".env"
$envExamplePath = Join-Path $repoRoot ".env.example"
if (-not (Test-Path $envPath)) {
    Copy-Item $envExamplePath $envPath
    Write-Host "Created .env from .env.example (edit it later for Jira/GitHub/cloud LLM credentials, or use the dashboard's Settings page instead)." -ForegroundColor Yellow
}

# 2. Docker infra
Write-Host "`nStarting Docker infra: qdrant, embeddings, searxng, mcp-server..." -ForegroundColor Cyan
docker compose up qdrant embeddings searxng mcp-server -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "docker compose failed - is Docker Desktop running? Continuing anyway in case you're running these services another way." -ForegroundColor Red
}

# 3. chat-agent (native, for hot reload)
$chatAgentDir = Join-Path $repoRoot "services\chat-agent"
$venvPython = Join-Path $chatAgentDir "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "`nSetting up chat-agent's Python virtual environment (first run only)..." -ForegroundColor Cyan
    python -m venv (Join-Path $chatAgentDir "venv")
    & (Join-Path $chatAgentDir "venv\Scripts\pip.exe") install -r (Join-Path $chatAgentDir "requirements.txt")
}
Write-Host "Starting chat-agent in a new window..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-ExecutionPolicy", "Bypass", "-NoExit", "-Command",
    "Set-Location '$chatAgentDir'; . .\venv\Scripts\Activate.ps1; uvicorn main:app --host 0.0.0.0 --port 8080 --reload"
)

# 4. dashboard (native, for hot reload)
$dashboardDir = Join-Path $repoRoot "dashboard"
if (-not (Test-Path (Join-Path $dashboardDir "node_modules"))) {
    Write-Host "`nInstalling dashboard dependencies (first run only)..." -ForegroundColor Cyan
    Push-Location $dashboardDir
    npm install
    Pop-Location
}
Write-Host "Starting the dashboard in a new window..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-ExecutionPolicy", "Bypass", "-NoExit", "-Command",
    "Set-Location '$dashboardDir'; npm run dev"
)

Write-Host "`nResearch Agent is starting up:" -ForegroundColor Green
Write-Host "  Dashboard:  http://localhost:5173  (first run opens the Setup Wizard)"
Write-Host "  chat-agent: http://localhost:8080"
Write-Host "  mcp-server: http://localhost:8083"
Write-Host "  qdrant:     http://localhost:6333"
Write-Host "  embeddings: http://localhost:8082  (first run downloads its model, ~1 min)"
Write-Host "  searxng:    http://localhost:8085"
