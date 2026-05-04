#Requires -Version 5.1
<#
.SYNOPSIS
    Hollow AgentOS — Windows bootstrapper.

.DESCRIPTION
    Installs Python, Docker Desktop, and Ollama if missing, then hands off
    to the interactive setup wizard (hollow.py) which handles everything else:
    model selection, API key, config, and launching the agents.

    Run by double-clicking install.bat (which calls this file).
    Requires an internet connection and ~8 GB of free disk space.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Paths ─────────────────────────────────────────────────────────────────────
$HollowDir   = $PSScriptRoot                          # wherever this script lives
$ConfigSrc   = Join-Path $HollowDir "config.example.json"
$ConfigDest  = Join-Path $HollowDir "config.json"
$LaunchBat   = Join-Path $HollowDir "launch.bat"
$Desktop     = [Environment]::GetFolderPath("Desktop")
$Shortcut    = Join-Path $Desktop "Hollow AgentOS.lnk"

# ── Colors ────────────────────────────────────────────────────────────────────
function _ok($msg)   { Write-Host "  [ok] $msg"      -ForegroundColor Green  }
function _info($msg) { Write-Host "  [..] $msg"      -ForegroundColor Cyan   }
function _warn($msg) { Write-Host "  [!!] $msg"      -ForegroundColor Yellow }
function _err($msg)  { Write-Host "  [XX] $msg"      -ForegroundColor Red    }
function _head($msg) { Write-Host "`n  $msg"         -ForegroundColor White  }

Clear-Host
Write-Host @"

   _  _  ___  _    _    _____  __  __
  | || |/ _ \| |  | |  / _ \ \ \  / /
  | __ | (_) | |__| |_| (_) \ \/\/ /
  |_||_|\___/|____|____\___/ \_/\_/

  hollow agentOS — installer

"@ -ForegroundColor White

# ── Winget availability ───────────────────────────────────────────────────────
$HasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)

# ── Helper: wait for a command to become available ────────────────────────────
function Wait-Command($name, $seconds = 60) {
    $t = 0
    while (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Start-Sleep 2; $t += 2
        if ($t -ge $seconds) { return $false }
    }
    return $true
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Docker Desktop
# ─────────────────────────────────────────────────────────────────────────────
_head "Step 1/6 — Docker Desktop"

$dockerCmd  = Get-Command docker -ErrorAction SilentlyContinue
$dockerRunning = $false
if ($dockerCmd) {
    try {
        docker info 2>&1 | Out-Null
        $dockerRunning = $true
    } catch { }
}

if ($dockerRunning) {
    _ok "Docker is already running"
} else {
    # Check if Docker Desktop is installed but not running
    $ddInstalled = Test-Path "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (-not $ddInstalled) {
        _info "Docker Desktop not found — installing…"
        if ($HasWinget) {
            winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements -h
        } else {
            _warn "winget not available. Opening Docker download page…"
            Start-Process "https://docs.docker.com/desktop/install/windows-install/"
            _warn "Install Docker Desktop, then re-run this script."
            Read-Host "  Press Enter to exit"
            exit 1
        }
        $ddInstalled = $true
    }

    if ($ddInstalled) {
        _info "Starting Docker Desktop…"
        Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
        _info "Waiting for Docker to become ready (this can take ~60s on first launch)…"
        $ready = Wait-Command "docker" 90
        if ($ready) {
            # Docker CLI is present but daemon might still be starting
            $attempts = 0
            while ($attempts -lt 20) {
                try { docker info 2>&1 | Out-Null; break } catch { }
                Start-Sleep 3; $attempts++
            }
        }
        try {
            docker info 2>&1 | Out-Null
            _ok "Docker Desktop is running"
        } catch {
            _err "Docker Desktop did not start in time."
            _warn "Please start Docker Desktop manually, then re-run this script."
            Read-Host "  Press Enter to exit"
            exit 1
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Ollama
# ─────────────────────────────────────────────────────────────────────────────
_head "Step 2/6 — Ollama"

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    # Also check the default Windows install location
    $ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $ollamaExe) {
        $env:PATH += ";$env:LOCALAPPDATA\Programs\Ollama"
        $ollamaCmd = $ollamaExe
    }
}

if ($ollamaCmd) {
    _ok "Ollama is already installed"
} else {
    _info "Ollama not found — installing…"
    if ($HasWinget) {
        winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements -h
        $env:PATH += ";$env:LOCALAPPDATA\Programs\Ollama"
    } else {
        _info "Downloading Ollama installer…"
        $ollamaInstaller = Join-Path $env:TEMP "OllamaSetup.exe"
        Invoke-WebRequest "https://ollama.com/download/OllamaSetup.exe" -OutFile $ollamaInstaller
        Start-Process $ollamaInstaller -ArgumentList "/S" -Wait
        $env:PATH += ";$env:LOCALAPPDATA\Programs\Ollama"
        Remove-Item $ollamaInstaller -ErrorAction SilentlyContinue
    }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        _err "Ollama install failed or PATH not updated — please restart and re-run."
        Read-Host "  Press Enter to exit"
        exit 1
    }
    _ok "Ollama installed"
}

# Make sure the Ollama service is running
try {
    $resp = Invoke-WebRequest "http://localhost:11434" -TimeoutSec 3 -ErrorAction SilentlyContinue
} catch {
    _info "Starting Ollama service…"
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep 4
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Python
# ─────────────────────────────────────────────────────────────────────────────
_head "Step 3/3 — Python"

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
}

if (-not $pythonCmd) {
    _info "Python not found — installing via winget…"
    if ($HasWinget) {
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements -h
        $env:PATH += ";$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    }
}

if (-not $pythonCmd) {
    _err "Python not found. Install Python 3.12+ from https://python.org then re-run install.bat."
    Read-Host "  Press Enter to exit"
    exit 1
}

_ok "Python ready"

# Install textual if missing (needed by hollow.py wizard)
_info "Checking Python dependencies…"
& $pythonCmd.Source -m pip install "textual>=8.0.0" -q --disable-pip-version-check
_ok "Dependencies ready"

# ── Hand off to the interactive setup wizard ──────────────────────────────────
Write-Host ""
Write-Host "  Prerequisites installed. Starting Hollow setup wizard..." -ForegroundColor Cyan
Write-Host ""
Start-Sleep 1

$env:HOLLOW_DIR = $HollowDir
Set-Location $HollowDir
& $pythonCmd.Source hollow.py
