#Requires -Version 5.1
<#
.SYNOPSIS
    Hollow AgentOS — one-shot installer for Windows.

.DESCRIPTION
    Installs Docker Desktop and Ollama if missing, pulls the required LLM
    models, starts the AgentOS stack with docker compose, and launches the
    live monitor TUI — all from a single script.

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
# STEP 3 — Pull LLM models
# ─────────────────────────────────────────────────────────────────────────────
_head "Step 3/6 — LLM models"

$models = @("qwen2.5:9b", "nomic-embed-text")
foreach ($model in $models) {
    # Check if model already exists
    $list = (ollama list 2>&1) -join " "
    $modelBase = $model.Split(":")[0]
    if ($list -match [regex]::Escape($modelBase)) {
        _ok "$model already downloaded"
    } else {
        _info "Pulling $model (this downloads several GB — grab a coffee)…"
        ollama pull $model
        _ok "$model ready"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Claude auth
# ─────────────────────────────────────────────────────────────────────────────
_head "Step 4/7 — Claude auth"

$EnvFile = Join-Path $HollowDir ".env"
$ClaudeCredentials = Join-Path $env:USERPROFILE ".claude\.credentials.json"

if (Test-Path $ClaudeCredentials) {
    try {
        $creds = Get-Content $ClaudeCredentials -Raw | ConvertFrom-Json
        $token = $creds.claudeAiOauth.accessToken
        if ($token) {
            # Write .env with the credentials file path so Docker mounts it
            # The container re-reads it fresh on each call, so token refresh
            # by Claude Code is picked up automatically.
            $envContent = "CLAUDE_CREDENTIALS_FILE=$ClaudeCredentials"
            Set-Content $EnvFile $envContent -Encoding UTF8
            _ok "Claude credentials found — agents will use your extra usage credits"
        } else {
            _warn "Claude credentials file found but no access token — falling back to Ollama"
            "CLAUDE_CREDENTIALS_FILE=" | Set-Content $EnvFile -Encoding UTF8
        }
    } catch {
        _warn "Could not read Claude credentials — falling back to Ollama"
        "CLAUDE_CREDENTIALS_FILE=" | Set-Content $EnvFile -Encoding UTF8
    }
} else {
    _info "Claude Code not detected — checking for Anthropic API key"

    if (-not (Test-Path $EnvFile)) {
        "CLAUDE_CREDENTIALS_FILE=" | Set-Content $EnvFile -Encoding UTF8
    }

    # Check if API key already in .env
    $envContent = if (Test-Path $EnvFile) { Get-Content $EnvFile -Raw } else { "" }
    if ($envContent -match "ANTHROPIC_API_KEY=.+") {
        _ok "ANTHROPIC_API_KEY already set in .env"
    } else {
        Write-Host ""
        Write-Host "  Claude API access unlocks high-quality tool wrapping (Sonnet/Haiku)." -ForegroundColor Cyan
        Write-Host "  Without it, Hollow uses local Ollama models (lower quality)." -ForegroundColor DarkGray
        Write-Host "  Get a key at: console.anthropic.com — or press Enter to skip." -ForegroundColor DarkGray
        Write-Host ""
        $apiKey = Read-Host "  Anthropic API key (sk-ant-...) or Enter to skip"
        if ($apiKey -and $apiKey.StartsWith("sk-")) {
            Add-Content $EnvFile "ANTHROPIC_API_KEY=$apiKey" -Encoding UTF8
            _ok "API key saved — agents will use Claude for tool wrapping"
        } else {
            _info "Skipped — agents will use local Ollama (you can add ANTHROPIC_API_KEY to .env later)"
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Config
# ─────────────────────────────────────────────────────────────────────────────
_head "Step 5/7 — Configuration"

if (Test-Path $ConfigDest) {
    _ok "config.json already exists — keeping it"
} else {
    if (-not (Test-Path $ConfigSrc)) {
        _err "config.example.json not found in $HollowDir"
        Read-Host "  Press Enter to exit"
        exit 1
    }
    # Copy and replace the default token with a secure random one
    $config = Get-Content $ConfigSrc -Raw | ConvertFrom-Json
    $token  = [System.Web.HttpUtility]::UrlEncode([System.Convert]::ToBase64String(
        [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(24)
    )).Replace("%2B", "+").Replace("%2F", "/").Replace("%3D", "=").Replace("+", "").Replace("/", "").Replace("=", "")
    $config.api.token = $token
    $config | ConvertTo-Json -Depth 10 | Set-Content $ConfigDest -Encoding UTF8
    _ok "config.json created with a unique API token"
}

# Ensure runtime directories exist before docker compose tries to mount them
foreach ($dir in @("memory", "workspace", "workspace\wrappers", "workspace\sandbox",
                    "workspace\bin", "logs", "store\data")) {
    $path = Join-Path $HollowDir $dir
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}
_ok "Runtime directories ready"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Start the AgentOS stack
# ─────────────────────────────────────────────────────────────────────────────
_head "Step 6/7 — Starting AgentOS"

Set-Location $HollowDir

# Try to pull the pre-built image first (fast — no compile step).
# Fall back to building from source if the registry is unreachable or the
# image hasn't been published yet (e.g., running a fork before first CI push).
_info "Pulling pre-built image from GHCR…"
$pulled = $false
try {
    docker compose pull api 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    $pulled = ($LASTEXITCODE -eq 0)
} catch { }

if (-not $pulled) {
    _warn "Could not pull pre-built image — building from source instead (this takes a few minutes)…"
    docker compose up -d --build 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
} else {
    _ok "Image pulled"
    _info "Starting containers…"
    docker compose up -d 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
}

# Wait for the API health check
_info "Waiting for API to become healthy…"
$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-WebRequest "http://localhost:7777/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($r.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    Start-Sleep 2
}

if ($healthy) {
    _ok "API is up at http://localhost:7777"
} else {
    _warn "API did not respond yet — it may still be starting. Check: http://localhost:7777/health"
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Python TUI setup + desktop shortcut
# ─────────────────────────────────────────────────────────────────────────────
_head "Step 7/7 — Monitor TUI"

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

if ($pythonCmd) {
    _info "Installing monitor dependencies…"
    $monReqs = Join-Path $HollowDir "requirements-monitor.txt"
    & $pythonCmd.Source -m pip install -q -r $monReqs
    _ok "Monitor dependencies installed"
} else {
    _warn "Python not found. Install Python 3.12+ from python.org, then run launch.bat."
}

# Create desktop shortcut and Start Menu entry
if (Test-Path $LaunchBat) {
    $wsh = New-Object -ComObject WScript.Shell

    # Desktop shortcut
    $lnk = $wsh.CreateShortcut($Shortcut)
    $lnk.TargetPath       = $LaunchBat
    $lnk.WorkingDirectory = $HollowDir
    $lnk.Description      = "Open Hollow AgentOS live monitor"
    $lnk.Save()
    _ok "Desktop shortcut created: 'Hollow AgentOS'"

    # Start Menu shortcut
    $StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    $StartMenuLnk = Join-Path $StartMenuDir "Hollow AgentOS.lnk"
    $lnk2 = $wsh.CreateShortcut($StartMenuLnk)
    $lnk2.TargetPath       = $LaunchBat
    $lnk2.WorkingDirectory = $HollowDir
    $lnk2.Description      = "Open Hollow AgentOS live monitor"
    $lnk2.Save()
    _ok "Start Menu shortcut created: 'Hollow AgentOS'"
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host @"

  ─────────────────────────────────────────────────
   Setup complete.
   API    →  http://localhost:7777
   Docs   →  http://localhost:7777/docs
   Dashboard → http://localhost:7778
  ─────────────────────────────────────────────────

"@ -ForegroundColor Green

# Launch the TUI
if ($pythonCmd) {
    _info "Launching live monitor…"
    Start-Sleep 1
    $env:HOLLOW_DIR = $HollowDir
    Set-Location $HollowDir
    & $pythonCmd.Source monitor.py
} else {
    _warn "To open the monitor later, double-click 'launch.bat' or 'Hollow AgentOS' on your Desktop."
    Read-Host "  Press Enter to exit"
}
