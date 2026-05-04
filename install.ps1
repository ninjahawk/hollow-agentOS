#Requires -Version 5.1
<#
.SYNOPSIS
    Hollow AgentOS - Windows bootstrapper.

.DESCRIPTION
    Ensures Python is installed then launches the interactive setup wizard.
    The wizard handles Docker, Ollama, model selection, and startup.
    Run by double-clicking install.bat.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$HollowDir = $PSScriptRoot
$HasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)

function _ok($msg)   { Write-Host ('  [ok] ' + $msg) -ForegroundColor Green  }
function _info($msg) { Write-Host ('  [..] ' + $msg) -ForegroundColor Cyan   }
function _warn($msg) { Write-Host ('  [!!] ' + $msg) -ForegroundColor Yellow }
function _err($msg)  { Write-Host ('  [XX] ' + $msg) -ForegroundColor Red    }

Clear-Host
Write-Host ''
Write-Host '  hollow agentOS' -ForegroundColor Cyan
Write-Host ''

# ── Python ────────────────────────────────────────────────────────────────────
_info 'Checking for Python...'

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
}

if (-not $pythonCmd) {
    _info 'Python not found - installing via winget...'
    if ($HasWinget) {
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements -h
        $py312     = $env:LOCALAPPDATA + '\Programs\Python\Python312'
        $py312s    = $py312 + '\Scripts'
        $env:PATH  = $env:PATH + ';' + $py312 + ';' + $py312s
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    }
}

if (-not $pythonCmd) {
    _err 'Could not install Python automatically.'
    _warn 'Install Python 3.12+ from https://python.org/downloads'
    _warn 'Then double-click install.bat again.'
    Start-Process 'https://python.org/downloads'
    Read-Host '  Press Enter to exit'
    exit 1
}

_ok 'Python ready'

# ── Rich (wizard UI dependency) ───────────────────────────────────────────────
_info 'Installing wizard dependencies...'
$pyExe = if ($pythonCmd.Source) { $pythonCmd.Source } else { 'python' }
& $pyExe -m pip install 'rich' -q --disable-pip-version-check
_ok 'Ready'

# ── Launch setup wizard ───────────────────────────────────────────────────────
Write-Host ''
_info 'Starting Hollow setup wizard...'
Write-Host ''
Start-Sleep 1

$env:HOLLOW_DIR = $HollowDir
Set-Location $HollowDir
& $pyExe hollow.py
