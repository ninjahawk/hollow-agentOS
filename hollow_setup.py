"""
Hollow AgentOS — interactive setup wizard.
Clean Rich-based CLI. No TUI framework.
Called by hollow.py when setup is needed.
"""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.text import Text

_IS_WIN = platform.system() == "Windows"
if not _IS_WIN:
    import termios
    import tty

ROOT = Path(__file__).parent.resolve()
CONFIG_EXAMPLE = ROOT / "config.example.json"
CONFIG_PATH    = ROOT / "config.json"
ENV_PATH       = ROOT / ".env"

C = Console(highlight=False)

# ── Colors ─────────────────────────────────────────────────────────────────────
TEAL   = "#00e5c0"
WHITE  = "#ffffff"
DIM    = "#4a4a6a"
MUTED  = "#6e6e8e"
FAINT  = "#2a2a3a"
GREEN  = "#00c896"
YELLOW = "#f0b429"
RED    = "#f05050"
PIPE   = "#2a2a3a"


# ── Box-drawing helpers ────────────────────────────────────────────────────────

def _pipe(text: str = "", indent: int = 0) -> None:
    prefix = " " * indent
    C.print(f"[{PIPE}]│[/]  {prefix}{text}")


def _step(title: str, width: int = 64) -> None:
    """◇  Title ──────────────────────────────────────────╮"""
    dashes = "─" * max(4, width - len(title) - 6)
    C.print(f"[{PIPE}]│[/]")
    C.print(f"[{TEAL}]◇[/]  [{WHITE}]{title}[/{WHITE}]  [{FAINT}]{dashes}╮[/]")


def _box_line(text: str, width: int = 64) -> None:
    """│  text                                            │"""
    C.print(f"[{PIPE}]│[/]  {text}")


def _box_close(width: int = 64) -> None:
    """├──────────────────────────────────────────────────╯"""
    dashes = "─" * (width - 2)
    C.print(f"[{PIPE}]├{dashes}╯[/]")


def _answered(question: str, answer: str) -> None:
    """◇  Question?  ›  answer"""
    C.print(f"[{PIPE}]│[/]")
    C.print(f"[{TEAL}]◇[/]  [{MUTED}]{question}[/]  [{FAINT}]›[/]  [{WHITE}]{answer}[/]")


def _blank() -> None:
    C.print(f"[{PIPE}]│[/]")


# ── Raw key input ──────────────────────────────────────────────────────────────

def _read_key() -> str:
    """Read a single keypress. Returns 'up', 'down', 'enter', or the char."""
    if _IS_WIN:
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):    return "enter"
        if ch == "\x03":          raise KeyboardInterrupt
        if ch == "q":             raise KeyboardInterrupt
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            if ch2 == "H":  return "up"
            if ch2 == "P":  return "down"
        return ch
    else:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                rest = sys.stdin.read(2)
                if rest == "[A":  return "up"
                if rest == "[B":  return "down"
                return "esc"
            if ch in ("\r", "\n"):  return "enter"
            if ch == "\x03":        raise KeyboardInterrupt
            if ch == "q":           raise KeyboardInterrupt
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _select(options: list[dict], key: str = "name") -> int:
    """Arrow-key menu. Returns selected index."""
    idx = 0

    def _draw(clear: bool = False) -> None:
        if clear:
            # Move cursor up by (lines drawn last time)
            lines = len(options) * 2
            C.print(f"\x1b[{lines}A\x1b[J", end="")
        for i, opt in enumerate(options):
            if i == idx:
                C.print(f"[{PIPE}]│[/]  [{TEAL}]❯[/]  [{WHITE}]{opt[key]}[/]   [{TEAL}]{opt.get('badge','').upper()}[/]")
            else:
                C.print(f"[{PIPE}]│[/]  [{FAINT}]·[/]  [{DIM}]{opt[key]}[/]   [{FAINT}]{opt.get('badge','').upper()}[/]")
            if i == idx and opt.get("description"):
                C.print(f"[{PIPE}]│[/]     [{MUTED}]{opt['description']}[/]")
            else:
                _blank()

    _draw()
    while True:
        k = _read_key()
        if k == "up":
            idx = max(0, idx - 1)
            _draw(clear=True)
        elif k == "down":
            idx = min(len(options) - 1, idx + 1)
            _draw(clear=True)
        elif k == "enter":
            return idx


def _confirm(question: str) -> bool:
    """Y/n inline confirm."""
    _blank()
    C.print(f"[{PIPE}]│[/]  [{MUTED}]{question}[/]  [{FAINT}]([/{FAINT}][{TEAL}]Y[/][{FAINT}]/n)[/] ", end="")
    ch = _read_key().lower()
    result = ch != "n"
    C.print(f"[{WHITE}]{'Yes' if result else 'No'}[/]")
    return result


def _input_text(prompt: str, password: bool = False) -> str:
    """Single-line text input."""
    C.print(f"[{PIPE}]│[/]  [{MUTED}]{prompt}[/]", end="  ")
    if password:
        import getpass
        val = getpass.getpass("")
    else:
        val = input()
    return val.strip()


# ── Detection helpers ──────────────────────────────────────────────────────────

def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _docker_running() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _ollama_running() -> bool:
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434", timeout=2)
        return True
    except Exception:
        return False


def _detect_gpu() -> tuple[bool, str]:
    if _has_cmd("nvidia-smi"):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                name, mem = r.stdout.strip().splitlines()[0].split(",", 1)
                return True, f"{name.strip()}  ·  {mem.strip()}"
        except Exception:
            pass
    if _has_cmd("rocm-smi"):
        return True, "AMD GPU (ROCm)"
    if platform.system() == "Darwin":
        try:
            r = subprocess.run(["system_profiler", "SPDisplaysDataType"],
                               capture_output=True, text=True, timeout=5)
            if "Metal" in r.stdout:
                return True, "Apple Silicon (Metal)"
        except Exception:
            pass
    return False, ""


def _model_installed(model_id: str) -> bool:
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
        return model_id.split(":")[0] in r.stdout
    except Exception:
        return False


def _api_healthy() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:7777/health", timeout=2) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception:
        return False


def _pkg_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


# ── Installation helpers ───────────────────────────────────────────────────────

def _install_docker() -> tuple[bool, str]:
    sys_platform = platform.system()
    if sys_platform == "Windows":
        if _has_cmd("winget"):
            r = subprocess.run(
                ["winget", "install", "-e", "--id", "Docker.DockerDesktop",
                 "--accept-package-agreements", "--accept-source-agreements", "-h"],
                capture_output=True, text=True, timeout=300,
            )
            if r.returncode == 0:
                docker_exe = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
                if os.path.exists(docker_exe):
                    subprocess.Popen([docker_exe])
                for _ in range(45):
                    if _docker_running():
                        return True, ""
                    time.sleep(2)
                return True, "slow_start"
            return False, r.stderr[:200]
        else:
            import webbrowser
            webbrowser.open("https://docs.docker.com/desktop/install/windows-install/")
            return False, "opened_browser"
    elif sys_platform == "Darwin":
        import webbrowser
        webbrowser.open("https://docs.docker.com/desktop/install/mac-install/")
        return False, "opened_browser"
    else:
        import webbrowser
        webbrowser.open("https://docs.docker.com/engine/install/")
        return False, "opened_browser"


def _install_ollama() -> tuple[bool, str]:
    sys_platform = platform.system()
    if sys_platform == "Windows":
        if _has_cmd("winget"):
            r = subprocess.run(
                ["winget", "install", "-e", "--id", "Ollama.Ollama",
                 "--accept-package-agreements", "--accept-source-agreements", "-h"],
                capture_output=True, text=True, timeout=180,
            )
            if r.returncode == 0:
                subprocess.Popen(["ollama", "serve"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(15):
                    if _ollama_running():
                        return True, ""
                    time.sleep(2)
                return True, "slow_start"
            return False, r.stderr[:200]
        else:
            try:
                import urllib.request, tempfile
                installer = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
                urllib.request.urlretrieve("https://ollama.com/download/OllamaSetup.exe", installer)
                subprocess.run([installer, "/S"], check=True, timeout=120)
                subprocess.Popen(["ollama", "serve"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(15):
                    if _ollama_running():
                        return True, ""
                    time.sleep(2)
                return True, "slow_start"
            except Exception as e:
                return False, str(e)
    elif sys_platform == "Darwin":
        if _has_cmd("brew"):
            r = subprocess.run(["brew", "install", "ollama"],
                               capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                subprocess.Popen(["ollama", "serve"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(10):
                    if _ollama_running():
                        return True, ""
                    time.sleep(2)
                return True, "slow_start"
        import webbrowser
        webbrowser.open("https://ollama.com/download")
        return False, "opened_browser"
    else:
        try:
            r = subprocess.run(
                ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                capture_output=True, text=True, timeout=180,
            )
            if r.returncode == 0:
                subprocess.Popen(["ollama", "serve"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(10):
                    if _ollama_running():
                        return True, ""
                    time.sleep(2)
                return True, "slow_start"
            return False, r.stderr[:200]
        except Exception as e:
            return False, str(e)


def _write_config(model_id: str) -> None:
    if not CONFIG_EXAMPLE.exists():
        raise FileNotFoundError(f"config.example.json not found at {CONFIG_EXAMPLE}")
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text())
            config["ollama"]["default_model"] = model_id
            CONFIG_PATH.write_text(json.dumps(config, indent=2))
            return
        except Exception:
            pass
    config = json.loads(CONFIG_EXAMPLE.read_text())
    config["ollama"]["default_model"] = model_id
    config["api"]["token"] = secrets.token_urlsafe(24)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def _write_env(api_key: Optional[str]) -> None:
    lines = []
    creds = Path.home() / ".claude" / ".credentials.json"
    lines.append(f"CLAUDE_CREDENTIALS_FILE={creds}" if creds.exists() else "CLAUDE_CREDENTIALS_FILE=")
    if api_key and api_key not in ("CLAUDE_CODE", "") and api_key.startswith("sk-"):
        lines.append(f"ANTHROPIC_API_KEY={api_key}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def _ensure_dirs() -> None:
    for d in ["memory", "workspace", "workspace/wrappers", "workspace/sandbox",
              "workspace/bin", "logs", "store/data"]:
        (ROOT / d).mkdir(parents=True, exist_ok=True)


def _pull_model(model_id: str) -> None:
    proc = subprocess.Popen(
        ["ollama", "pull", model_id],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if proc.stdout:
        for _ in proc.stdout:
            pass
    proc.wait()


def _start_containers() -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
        if r.returncode == 0:
            return True, ""
        combined = r.stdout + r.stderr
        if "nvidia" in combined.lower() or "gpu" in combined.lower():
            compose_text = (ROOT / "docker-compose.yml").read_text()
            patched = re.sub(
                r"(?s)\s*# GPU acceleration.*?capabilities: \[gpu\]\s*", "\n",
                compose_text,
            )
            tmp = ROOT / ".docker-compose-nogpu.yml"
            tmp.write_text(patched)
            try:
                r2 = subprocess.run(
                    ["docker", "compose", "-f", str(tmp), "up", "-d"],
                    cwd=ROOT, capture_output=True, text=True, timeout=300,
                )
                return r2.returncode == 0, r2.stderr[:200]
            finally:
                tmp.unlink(missing_ok=True)
        return False, r.stderr[:300]
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except Exception as e:
        return False, str(e)


# ── Models ─────────────────────────────────────────────────────────────────────

MODELS = [
    {
        "id":          "qwen3.5:9b",
        "name":        "Qwen 3.5  ·  9B",
        "badge":       "recommended",
        "description": "Best emergent behavior. Requires NVIDIA GPU with 8GB+ VRAM.",
        "requirements":"NVIDIA GPU · 8GB+ VRAM · ~5.2 GB",
    },
    {
        "id":          "qwen3.5:4b",
        "name":        "Qwen 3.5  ·  4B",
        "badge":       "balanced",
        "description": "Good behavior with lower hardware requirements.",
        "requirements":"4GB+ VRAM or fast CPU · ~2.6 GB",
    },
    {
        "id":          "llama3.2:3b",
        "name":        "Llama 3.2  ·  3B",
        "badge":       "cpu friendly",
        "description": "Runs on any machine. Simpler behavior patterns.",
        "requirements":"CPU only · 4GB RAM · ~2.0 GB",
    },
    {
        "id":          "gemma3:4b",
        "name":        "Gemma 3  ·  4B",
        "badge":       "alternative",
        "description": "Google model. Different reasoning style.",
        "requirements":"4GB+ VRAM or CPU · ~3.3 GB",
    },
]


# ── Setup flow ─────────────────────────────────────────────────────────────────

def run_setup() -> None:
    os.chdir(ROOT)

    # ── Header ──────────────────────────────────────────────────────────────────
    C.print()
    C.print(f"  [{TEAL}]hollow agentOS[/]", highlight=False)
    C.print(f"  [{FAINT}]{'─' * 38}[/]", highlight=False)
    C.print()
    C.print(f"[{PIPE}]┌  Hollow setup[/]")
    _blank()

    # ── Step 1: System check ─────────────────────────────────────────────────────
    _step("System check")
    _blank()

    checks = {}

    # Docker
    C.print(f"[{PIPE}]│[/]  [{DIM}]checking Docker…[/]", end="\r")
    if _docker_running():
        _box_line(f"[{GREEN}]✓[/]  Docker Desktop      running")
        checks["docker"] = "ok"
    elif _has_cmd("docker"):
        _box_line(f"[{YELLOW}]⚠[/]  Docker Desktop      installed, not running")
        checks["docker"] = "not_running"
    else:
        _box_line(f"[{RED}]✗[/]  Docker Desktop      not installed")
        checks["docker"] = "missing"

    # Ollama
    C.print(f"[{PIPE}]│[/]  [{DIM}]checking Ollama…[/]", end="\r")
    if _ollama_running():
        _box_line(f"[{GREEN}]✓[/]  Ollama              running")
        checks["ollama"] = "ok"
    elif _has_cmd("ollama"):
        _box_line(f"[{YELLOW}]⚠[/]  Ollama              installed, not running")
        checks["ollama"] = "not_running"
    else:
        _box_line(f"[{RED}]✗[/]  Ollama              not installed")
        checks["ollama"] = "missing"

    # GPU
    has_gpu, gpu_desc = _detect_gpu()
    if has_gpu:
        _box_line(f"[{GREEN}]✓[/]  GPU                 {gpu_desc[:45]}")
    else:
        _box_line(f"[{YELLOW}]⚠[/]  GPU                 none detected — pick a CPU model")

    _box_close()

    # Fix missing/not-running items
    if checks.get("docker") == "not_running":
        _blank()
        _box_line(f"[{YELLOW}]Docker is installed but not running.[/]")
        _box_line(f"[{MUTED}]Start Docker Desktop, then re-run hollow onboarding.[/]")
        _blank()
        C.print(f"[{PIPE}]└[/]")
        C.print()
        return

    if checks.get("docker") == "missing":
        _blank()
        if _confirm("Docker Desktop is required. Install it now?"):
            _box_line(f"[{TEAL}]Installing Docker Desktop…[/]")
            _box_line(f"[{MUTED}]This may take a few minutes and will ask for admin permission.[/]")
            _blank()
            ok, err = _install_docker()
            if ok or err == "slow_start":
                _box_line(f"[{GREEN}]✓[/]  Docker Desktop installed.")
                checks["docker"] = "ok"
            elif err == "opened_browser":
                _box_line(f"[{YELLOW}]Opened the download page in your browser.[/]")
                _box_line(f"[{MUTED}]Install Docker, then re-run hollow onboarding.[/]")
                _blank()
                C.print(f"[{PIPE}]└[/]")
                C.print()
                return
            else:
                _box_line(f"[{RED}]Install failed: {err[:60]}[/]")
                C.print(f"[{PIPE}]└[/]")
                C.print()
                return
        else:
            _box_line(f"[{MUTED}]Docker is required. Re-run hollow onboarding when ready.[/]")
            C.print(f"[{PIPE}]└[/]")
            C.print()
            return

    if checks.get("ollama") == "not_running":
        _blank()
        _box_line(f"[{TEAL}]Starting Ollama…[/]")
        subprocess.Popen(["ollama", "serve"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(10):
            if _ollama_running():
                break
            time.sleep(1)
        if _ollama_running():
            _box_line(f"[{GREEN}]✓[/]  Ollama started.")
            checks["ollama"] = "ok"
        else:
            _box_line(f"[{YELLOW}]Ollama didn't start in time. Try running 'ollama serve' manually.[/]")

    if checks.get("ollama") == "missing":
        _blank()
        if _confirm("Ollama is required. Install it now?"):
            _box_line(f"[{TEAL}]Installing Ollama…[/]")
            _blank()
            ok, err = _install_ollama()
            if ok or err == "slow_start":
                _box_line(f"[{GREEN}]✓[/]  Ollama installed.")
                checks["ollama"] = "ok"
            elif err == "opened_browser":
                _box_line(f"[{YELLOW}]Opened the download page in your browser.[/]")
                _box_line(f"[{MUTED}]Install Ollama, then re-run hollow onboarding.[/]")
                C.print(f"[{PIPE}]└[/]")
                C.print()
                return
            else:
                _box_line(f"[{RED}]Install failed: {err[:60]}[/]")
                C.print(f"[{PIPE}]└[/]")
                C.print()
                return
        else:
            _box_line(f"[{MUTED}]Ollama is required. Re-run hollow onboarding when ready.[/]")
            C.print(f"[{PIPE}]└[/]")
            C.print()
            return

    _answered("System check", "ready")

    # ── Step 2: Model selection ───────────────────────────────────────────────────
    _step("Choose a model")
    _blank()
    if has_gpu:
        _box_line(f"[{MUTED}]GPU  {gpu_desc}[/]")
    else:
        _box_line(f"[{YELLOW}]No GPU — pick a CPU-friendly model.[/]")
    _box_line(f"[{MUTED}]Downloads automatically. Use ↑ ↓ to move, Enter to select.[/]")
    _blank()

    model_idx = _select(MODELS)
    chosen = MODELS[model_idx]

    _blank()
    _box_close()
    _answered("Model", f"{chosen['name']}  [{MUTED}]{chosen['requirements']}[/]")

    # ── Step 3: API key ───────────────────────────────────────────────────────────
    api_key: Optional[str] = None

    creds_path = Path.home() / ".claude" / ".credentials.json"
    if creds_path.exists():
        try:
            creds = json.loads(creds_path.read_text())
            if creds.get("claudeAiOauth", {}).get("accessToken"):
                api_key = "CLAUDE_CODE"
        except Exception:
            pass

    if api_key == "CLAUDE_CODE":
        _answered("API key", f"[{GREEN}]Claude Code detected ✓[/]")
    else:
        _step("API key  (optional)")
        _blank()
        _box_line(f"[{MUTED}]Lets agents route complex tasks to Claude Sonnet or Haiku.[/]")
        _box_line(f"[{MUTED}]Hollow works without it. Press Enter to skip.[/]")
        _blank()
        raw = _input_text("sk-ant-...  or Enter to skip", password=True)
        _blank()
        _box_close()
        if raw.startswith("sk-"):
            api_key = raw
            _answered("API key", "saved")
        else:
            _answered("API key", "skipped — local model only")

    # ── Step 4: Launch ────────────────────────────────────────────────────────────
    _step("Starting Hollow")
    _blank()

    def log(msg: str) -> None:
        _box_line(msg)

    log(f"[{DIM}]Writing config…[/]")
    _write_config(chosen["id"])

    log(f"[{DIM}]Writing .env…[/]")
    _write_env(api_key)

    log(f"[{DIM}]Creating directories…[/]")
    _ensure_dirs()

    if not _model_installed(chosen["id"]):
        log(f"[{TEAL}]Downloading {chosen['id']}  [{DIM}]{chosen['requirements']}[/]")
        log(f"[{MUTED}]This may take a few minutes…[/]")
        _pull_model(chosen["id"])
        log(f"[{GREEN}]✓[/]  {chosen['id']} ready")
    else:
        log(f"[{GREEN}]✓[/]  {chosen['id']} already downloaded")

    if not _model_installed("nomic-embed-text"):
        log(f"[{TEAL}]Downloading nomic-embed-text  [{DIM}]~274 MB[/]")
        _pull_model("nomic-embed-text")
        log(f"[{GREEN}]✓[/]  nomic-embed-text ready")

    log(f"[{DIM}]Starting containers…[/]")
    ok, err = _start_containers()
    if not ok:
        log(f"[{RED}]✗[/]  Docker compose failed: {err[:80]}")
        _blank()
        _box_close()
        C.print(f"[{PIPE}]└[/]")
        C.print()
        return

    log(f"[{GREEN}]✓[/]  Containers started")
    log(f"[{DIM}]Waiting for API…[/]")

    for _ in range(30):
        if _api_healthy():
            break
        time.sleep(2)

    if _api_healthy():
        log(f"[{GREEN}]✓[/]  API is up")
    else:
        log(f"[{YELLOW}]⚠[/]  API slow to start — check http://localhost:7777/health")

    _blank()
    _box_close()

    # ── Done ──────────────────────────────────────────────────────────────────────
    _blank()
    C.print(f"[{PIPE}]◇  [{TEAL}]Hollow is alive.[/]")
    _blank()
    _pipe(f"[{MUTED}]hollow          open monitor[/]")
    _pipe(f"[{MUTED}]hollow stop     stop containers[/]")
    _pipe(f"[{MUTED}]hollow status   check health[/]")
    _blank()
    C.print(f"[{PIPE}]└[/]")
    C.print()

    # Launch monitor
    time.sleep(1)
    os.execv(sys.executable, [sys.executable, str(ROOT / "thoughts.py")])


# ── Textual shim (hollow.py imports HollowSetupApp) ───────────────────────────

class HollowSetupApp:
    """Thin shim so hollow.py's _run_setup() still works."""
    def run(self) -> None:
        run_setup()
