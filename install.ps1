#Requires -Version 5.1
<#
.SYNOPSIS
    Hollow AgentOS — Windows bootstrapper.

.DESCRIPTION
    This script only does one thing: make sure Python is installed, then
    hand off to the interactive setup wizard (hollow.py).

    The wizard handles everything else — Docker, Ollama, model selection,
    API keys, config, and starting the agents — with a proper interactive UI.

    Run by double-clicking install.bat (which calls this file).
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$HollowDir = $PSScriptRoot
$HasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)

function _ok($msg)   { Write-Host "  [ok] $msg" -ForegroundColor Green  }
function _info($msg) { Write-Host "  [..] $msg" -ForegroundColor Cyan   }
function _warn($msg) { Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function _err($msg)  { Write-Host "  [XX] $msg" -ForegroundColor Red    }

Clear-Host
Write-Host @"

  hollow agentOS

"@ -ForegroundColor Cyan

# ── Python ────────────────────────────────────────────────────────────────────
_info "Checking for Python..."

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
}

if (-not $pythonCmd) {
    _info "Python not found — installing via winget..."
    if ($HasWinget) {
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements -h
        $env:PATH += ";$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    }
}

if (-not $pythonCmd) {
    _err "Could not install Python automatically."
    _warn "Install Python 3.12+ from https://python.org/downloads"
    _warn "Then double-click install.bat again."
    Start-Process "https://python.org/downloads"
    Read-Host "  Press Enter to exit"
    exit 1
}

_ok "Python ready"

# ── Textual (needed by the wizard UI) ─────────────────────────────────────────
_info "Installing wizard dependencies..."
$pyExe = if ($pythonCmd.Source) { $pythonCmd.Source } else { "python" }
& $pyExe -m pip install "textual>=8.0.0" -q --disable-pip-version-check
_ok "Ready"

# ── Launch the setup wizard ───────────────────────────────────────────────────
Write-Host ""
_info "Starting Hollow setup wizard..."
Write-Host ""
Start-Sleep 1

$env:HOLLOW_DIR = $HollowDir
Set-Location $HollowDir
& $pyExe hollow.py
