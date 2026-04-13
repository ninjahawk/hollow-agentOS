# Hollow AgentOS — Snapshot Restore Script
# Copies bundled Cedar/Helix/Titan agent state into the running hollow-api container.
# Run after install.bat has fully completed.

param()

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$SnapshotDir = Join-Path $ScriptDir "snapshot"

function _info($msg) { Write-Host "  $msg" -ForegroundColor Cyan }
function _ok($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function _err($msg)  { Write-Host "  ERR $msg" -ForegroundColor Red; exit 1 }
function _warn($msg) { Write-Host "  !   $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  Hollow AgentOS — Snapshot Restore" -ForegroundColor Magenta
Write-Host "  Cedar / Helix / Titan — April 13 2026, ~23:00 UTC" -ForegroundColor DarkGray
Write-Host "  Post-patch build: toolchain fixes + emergent behavior session" -ForegroundColor DarkGray
Write-Host ""

# Check snapshot exists
if (-not (Test-Path $SnapshotDir)) {
    _err "snapshot/ folder not found next to restore.ps1. Is the zip fully extracted?"
}

# Check Docker
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) { _err "Docker not found. Run install.bat first." }

# Check container is running
_info "Checking hollow-api container..."
$running = docker inspect hollow-api --format "{{.State.Status}}" 2>$null
if ($running -ne "running") {
    _err "hollow-api container is not running. Run install.bat first, wait for it to start, then run restore.bat."
}
_ok "Container is running"

# Copy memory
_info "Restoring agent memory (~93 MB)..."
docker cp "$SnapshotDir\memory\." hollow-api:/agentOS/memory/
if ($LASTEXITCODE -ne 0) { _err "Failed to copy memory into container." }
_ok "Memory restored"

# Copy workspace
_info "Restoring agent workspace..."
docker cp "$SnapshotDir\workspace\." hollow-api:/agentOS/workspace/
if ($LASTEXITCODE -ne 0) { _err "Failed to copy workspace into container." }
_ok "Workspace restored"

# Copy agents (patched runtime + agent-written production files)
_info "Restoring agent runtime files..."
docker cp "$SnapshotDir\agents\." hollow-api:/agentOS/agents/
if ($LASTEXITCODE -ne 0) { _err "Failed to copy agents into container." }
_ok "Agent runtime restored"

# Copy core (agent-written capabilities in /agentOS/core/capabilities/)
_info "Restoring core capabilities..."
docker cp "$SnapshotDir\core\." hollow-api:/agentOS/core/
if ($LASTEXITCODE -ne 0) { _err "Failed to copy core into container." }
_ok "Core capabilities restored"

# Restart container so agents pick up restored state
_info "Restarting container to load restored state..."
docker restart hollow-api | Out-Null
if ($LASTEXITCODE -ne 0) { _err "Failed to restart container." }
_ok "Container restarted"

Write-Host ""
Write-Host "  ─────────────────────────────────────────────" -ForegroundColor Green
Write-Host "   Restore complete." -ForegroundColor Green
Write-Host "   Agents Cedar, Helix, and Titan are resuming." -ForegroundColor Green
Write-Host "   Snapshot: April 13 2026 ~23:00 UTC (post-patch)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "   Dashboard  →  http://localhost:7778" -ForegroundColor Cyan
Write-Host "   API        →  http://localhost:7777" -ForegroundColor Cyan
Write-Host "  ─────────────────────────────────────────────" -ForegroundColor Green
Write-Host ""
Read-Host "  Press Enter to exit"
