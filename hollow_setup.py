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
DIM    = "#9090b0"    # readable secondary text
MUTED  = "#707090"    # hints and labels
FAINT  = "#505070"    # pipes and non-selected items
GREEN  = "#00c896"
YELLOW = "#f0b429"
RED    = "#f05050"
PIPE   = "#505070"    # │ characters


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


_SKIP = "SKIP"
_BACK = "BACK"


def _select(options: list[dict], key: str = "name"):
    """Arrow-key menu.
    Returns: index int, _SKIP, or _BACK.
    """
    idx = 0

    def _draw(clear: bool = False) -> None:
        if clear:
            lines = len(options) * 2
            C.print(f"\x1b[{lines}A\x1b[J", end="")
        for i, opt in enumerate(options):
            if i == idx:
                C.print(f"[{PIPE}]│[/]  [{TEAL}]❯[/]  [{WHITE}]{opt[key]}[/]   [{TEAL}]{opt.get('badge','').upper()}[/]")
                if opt.get("description"):
                    C.print(f"[{PIPE}]│[/]     [{DIM}]{opt['description']}[/]")
                else:
                    _blank()
            else:
                C.print(f"[{PIPE}]│[/]     [{MUTED}]{opt[key]}[/]   [{FAINT}]{opt.get('badge','').upper()}[/]")
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
        elif k == "s":
            return _SKIP
        elif k == "b":
            return _BACK


def _confirm(question: str):
    """Y/n/b confirm.
    Returns: True, False, or _BACK.
    """
    _blank()
    C.print(
        f"[{PIPE}]│[/]  [{MUTED}]{question}[/]  "
        f"[{FAINT}]([/][{TEAL}]Y[/][{FAINT}]/n/b)[/] ",
        end="",
    )
    ch = _read_key().lower()
    if ch == "b":
        C.print(f"[{MUTED}]← back[/]")
        return _BACK
    result = ch != "n"
    C.print(f"[{WHITE}]{'Yes' if result else 'No'}[/]")
    return result


def _input_text(prompt: str, password: bool = False):
    """Single-line text input. Returns string or _BACK if user types 'b' alone."""
    C.print(f"[{PIPE}]│[/]  [{MUTED}]{prompt}[/]  [{FAINT}](b = back)[/]", end="  ")
    if password:
        import getpass
        val = getpass.getpass("")
    else:
        val = input()
    val = val.strip()
    if val.lower() == "b":
        return _BACK
    return val


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


# ── Security warning ───────────────────────────────────────────────────────────

def _security_warning() -> bool:
    """Show security notice. Returns False if user wants to exit."""
    _clear()
    _header()
    _step("Security")
    _blank()
    _box_line(f"[{WHITE}]Security notice — please read.[/]")
    _blank()
    _box_line(f"[{DIM}]Hollow runs three autonomous agents on your machine. They pick[/]")
    _box_line(f"[{DIM}]their own goals, write and hot-load their own Python tools, and[/]")
    _box_line(f"[{DIM}]execute shell commands inside Docker — without asking first.[/]")
    _blank()
    _box_line(f"[{MUTED}]Agents can:[/]")
    _box_line(f"[{DIM}]  ·  Run shell commands inside the container[/]")
    _box_line(f"[{DIM}]  ·  Read and write files under /agentOS/[/]")
    _box_line(f"[{DIM}]  ·  Synthesize Python tools and hot-load them without review[/]")
    _box_line(f"[{DIM}]  ·  Queue requests (invoke_claude) for you to implement[/]")
    _blank()
    _box_line(f"[{MUTED}]Agents cannot (by default):[/]")
    _box_line(f"[{DIM}]  ·  Access your host filesystem outside Docker bind mounts[/]")
    _box_line(f"[{DIM}]  ·  Make outbound internet requests[/]")
    _box_line(f"[{DIM}]  ·  Write core system files (daemon, audit log)[/]")
    _blank()
    _box_line(f"[{DIM}]The API runs on localhost:7777. Do not expose it to the internet[/]")
    _box_line(f"[{DIM}]— there is no authentication layer.[/]")
    _blank()
    _box_line(f"[{YELLOW}]This is a research platform, not a hardened production system.[/]")
    _box_line(f"[{YELLOW}]Run it on hardware you control. Keep the API token private.[/]")
    _blank()
    _box_close()
    result = _confirm("I understand — continue?")
    if result is _BACK or result is False:
        _blank()
        C.print(f"[{PIPE}]└[/]")
        return False
    return True


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
                for i in range(45):
                    if _docker_running():
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        return True, ""
                    sys.stdout.write(
                        f"\r    waiting for Docker to start… {(i + 1) * 2}s  "
                    )
                    sys.stdout.flush()
                    time.sleep(2)
                sys.stdout.write("\n")
                sys.stdout.flush()
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
                # winget updates the system PATH but the current process won't
                # see it — add Ollama's install location manually
                if not _has_cmd("ollama"):
                    for candidate in [
                        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama"),
                        os.path.join(os.environ.get("PROGRAMFILES", ""), "Ollama"),
                    ]:
                        if os.path.isdir(candidate):
                            os.environ["PATH"] = os.environ["PATH"] + os.pathsep + candidate
                            break
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


def _pull_model(model_id: str) -> bool:
    """Pull a model via ollama. Returns True on success, False on failure."""
    proc = subprocess.Popen(
        ["ollama", "pull", model_id],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1,
    )
    if proc.stdout:
        for raw in proc.stdout:
            for line in raw.replace("\r", "\n").split("\n"):
                line = line.strip()
                if line:
                    sys.stdout.write(f"\r    {line:<72}")
                    sys.stdout.flush()
    code = proc.wait()
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()
    return code == 0


def _start_containers(has_gpu: bool = True) -> tuple[bool, str]:
    def _nogpu_compose() -> tuple[bool, str]:
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

    try:
        if not has_gpu:
            return _nogpu_compose()

        r = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
        if r.returncode == 0:
            return True, ""
        combined = r.stdout + r.stderr
        if "nvidia" in combined.lower() or "gpu" in combined.lower():
            return _nogpu_compose()
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
        "description": "Best quality and reasoning. Needs NVIDIA GPU with 8GB+ VRAM.",
        "requirements":"NVIDIA GPU · 8GB+ VRAM · ~5.2 GB",
    },
    {
        "id":          "qwen3.5:4b",
        "name":        "Qwen 3.5  ·  4B",
        "badge":       "balanced",
        "description": "Good quality with lower hardware requirements.",
        "requirements":"4GB+ VRAM or fast CPU · ~2.6 GB",
    },
    {
        "id":          "llama3.2:3b",
        "name":        "Llama 3.2  ·  3B",
        "badge":       "cpu friendly",
        "description": "Runs on any machine. Good for getting started.",
        "requirements":"CPU only · 4GB RAM · ~2.0 GB",
    },
    {
        "id":          "gemma3:4b",
        "name":        "Gemma 3  ·  4B",
        "badge":       "alternative",
        "description": "Google model. Different approach to goal reasoning.",
        "requirements":"4GB+ VRAM or CPU · ~3.3 GB",
    },
]


# ── Setup flow ─────────────────────────────────────────────────────────────────

def _clear() -> None:
    os.system("cls" if _IS_WIN else "clear")


def _header() -> None:
    C.print()
    C.print(f"[{TEAL}] _  _  ___  _    _    _____  __  __[/]")
    C.print(f"[{TEAL}]| || |/ _ \\| |  | |  / _ \\ \\ \\  / /[/]")
    C.print(f"[{TEAL}]| __ | (_) | |__| |_| (_) \\ \\/\\/ /[/]")
    C.print(f"[{TEAL}]|_||_|\\___/|____|____|\\___/ \\_/\\_/ [/]")
    C.print()
    C.print(f"[{PIPE}]┌  Hollow setup[/]")
    _blank()


def _nav_hint(*parts: str) -> None:
    """Print a navigation hint row inside the current box."""
    joined = "    ".join(parts)
    _box_line(f"[{FAINT}]{joined}[/]")


def run_setup() -> None:
    os.chdir(ROOT)

    # ── Temp directory check ───────────────────────────────────────────────────────
    if _IS_WIN:
        try:
            import tempfile
            temp = Path(tempfile.gettempdir()).resolve()
            ROOT.resolve().relative_to(temp)
            # We're inside a temp folder — almost certainly ran from inside the zip
            _clear()
            _header()
            _step("Extract the zip first")
            _blank()
            _box_line(f"[{YELLOW}]Hollow is running from a temporary folder.[/]")
            _box_line(f"[{DIM}]This usually means you ran install.bat from inside the zip[/]")
            _box_line(f"[{DIM}]without extracting it first. Config and agent data written[/]")
            _box_line(f"[{DIM}]here will be lost when Windows cleans up this folder.[/]")
            _blank()
            _box_line(f"[{MUTED}]How to fix:[/]")
            _box_line(f"[{DIM}]  1.  Close this window.[/]")
            _box_line(f"[{DIM}]  2.  Right-click the zip → Extract All.[/]")
            _box_line(f"[{DIM}]  3.  Open the extracted folder.[/]")
            _box_line(f"[{DIM}]  4.  Double-click install.bat.[/]")
            _blank()
            _box_close()
            result = _confirm("Continue from temp folder anyway?")
            if result is _BACK or result is False:
                C.print(f"[{PIPE}]└[/]")
                C.print()
                return
        except ValueError:
            pass  # Not in temp, good

    # ── Security warning (step 0 — always first) ──────────────────────────────────
    if not _security_warning():
        C.print()
        return

    # ── Step 1: System check (no back) ────────────────────────────────────────────
    def do_system_check():
        _clear()
        _header()
        _step("System check")
        _blank()
        checks: dict = {}

        C.print(f"[{PIPE}]│[/]  [{DIM}]checking…[/]", end="\r")

        if _IS_WIN:
            build = sys.getwindowsversion().build
            if build < 19041:
                _box_line(f"[{RED}]✗[/]  Windows              build {build} — too old")
                _blank()
                _box_close()
                _blank()
                _pipe(f"[{YELLOW}]Docker requires Windows 10 build 19041+ or Windows 11.[/]")
                _pipe(f"[{MUTED}]Update Windows first, then run hollow.py again.[/]")
                _blank()
                C.print(f"[{PIPE}]└[/]")
                return None, None, None

        if _docker_running():
            _box_line(f"[{GREEN}]✓[/]  Docker Desktop      running")
            checks["docker"] = "ok"
        elif _has_cmd("docker"):
            _box_line(f"[{YELLOW}]⚠[/]  Docker Desktop      installed, not running")
            checks["docker"] = "not_running"
        else:
            _box_line(f"[{RED}]✗[/]  Docker Desktop      not installed")
            checks["docker"] = "missing"

        if _ollama_running():
            _box_line(f"[{GREEN}]✓[/]  Ollama              running")
            checks["ollama"] = "ok"
        elif _has_cmd("ollama"):
            _box_line(f"[{YELLOW}]⚠[/]  Ollama              installed, not running")
            checks["ollama"] = "not_running"
        else:
            _box_line(f"[{RED}]✗[/]  Ollama              not installed")
            checks["ollama"] = "missing"

        has_gpu, gpu_desc = _detect_gpu()
        if has_gpu:
            _box_line(f"[{GREEN}]✓[/]  GPU                 {gpu_desc[:45]}")
        else:
            _box_line(f"[{YELLOW}]⚠[/]  GPU                 none detected — pick a CPU model")

        _box_close()

        # Handle not-running / missing
        if checks.get("docker") == "not_running":
            _blank()
            _box_line(f"[{TEAL}]Starting Docker Desktop…[/]")
            try:
                if _IS_WIN:
                    docker_exe = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
                    if os.path.exists(docker_exe):
                        subprocess.Popen([docker_exe])
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", "-a", "Docker"])
            except Exception:
                pass
            started = False
            for i in range(45):
                if _docker_running():
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    _box_line(f"[{GREEN}]✓[/]  Docker started.")
                    checks["docker"] = "ok"
                    started = True
                    break
                sys.stdout.write(f"\r    waiting for Docker… {(i + 1) * 2}s  ")
                sys.stdout.flush()
                time.sleep(2)
            if not started:
                sys.stdout.write("\n")
                sys.stdout.flush()
                _blank()
                _pipe(f"[{YELLOW}]Docker is taking too long to start.[/]")
                _pipe(f"[{MUTED}]Open Docker Desktop manually, wait for it to finish[/]")
                _pipe(f"[{MUTED}]loading, then run hollow.py again.[/]")
                _blank()
                C.print(f"[{PIPE}]└[/]")
                return None, None, None

        if checks.get("docker") == "missing":
            _blank()
            r = _confirm("Docker Desktop is required. Install it now?")
            if r is _BACK or r is False:
                _pipe(f"[{MUTED}]Docker is required. Run hollow onboarding when ready.[/]")
                C.print(f"[{PIPE}]└[/]")
                return None, None, None
            _box_line(f"[{TEAL}]Installing Docker Desktop…[/]")
            _box_line(f"[{MUTED}]May take a few minutes and will ask for admin permission.[/]")
            ok, err = _install_docker()
            if ok:
                _box_line(f"[{GREEN}]✓[/]  Docker Desktop installed and running.")
                checks["docker"] = "ok"
            elif err == "slow_start":
                sys.stdout.write("\n")
                sys.stdout.flush()
                _blank()
                _pipe(f"[{YELLOW}]Docker installed but didn't start in time.[/]")
                _pipe(f"[{MUTED}]Windows often needs a restart to finish Docker's WSL2 setup.[/]")
                _pipe(f"[{MUTED}]Restart your computer, then run hollow.py again.[/]")
                _pipe(f"[{MUTED}]Setup will pick up where it left off.[/]")
                _blank()
                C.print(f"[{PIPE}]└[/]")
                return None, None, None
            else:
                _box_line(f"[{YELLOW}]Opened download page. Install Docker, then re-run.[/]")
                C.print(f"[{PIPE}]└[/]")
                return None, None, None

        if checks.get("ollama") == "not_running":
            _blank()
            _box_line(f"[{TEAL}]Starting Ollama…[/]")
            subprocess.Popen(["ollama", "serve"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(15):
                if _ollama_running():
                    break
                time.sleep(1)
            if _ollama_running():
                _box_line(f"[{GREEN}]✓[/]  Ollama started.")
                checks["ollama"] = "ok"
            else:
                _blank()
                _pipe(f"[{YELLOW}]Ollama didn't start in time.[/]")
                _pipe(f"[{MUTED}]Try running 'ollama serve' in a terminal, then run[/]")
                _pipe(f"[{MUTED}]hollow.py again.[/]")
                _blank()
                C.print(f"[{PIPE}]└[/]")
                return None, None, None

        if checks.get("ollama") == "missing":
            _blank()
            r = _confirm("Ollama is required. Install it now?")
            if r is _BACK or r is False:
                _pipe(f"[{MUTED}]Ollama is required. Run hollow onboarding when ready.[/]")
                C.print(f"[{PIPE}]└[/]")
                return None, None, None
            _box_line(f"[{TEAL}]Installing Ollama…[/]")
            ok, err = _install_ollama()
            if ok or err == "slow_start":
                _box_line(f"[{GREEN}]✓[/]  Ollama installed.")
                checks["ollama"] = "ok"
            else:
                _box_line(f"[{YELLOW}]Opened download page. Install Ollama, then re-run.[/]")
                C.print(f"[{PIPE}]└[/]")
                return None, None, None

        return checks, has_gpu, gpu_desc

    checks, has_gpu, gpu_desc = do_system_check()
    if checks is None:
        C.print()
        return

    _answered("System check", "ready")

    # ── Steps 2-3 with back support ───────────────────────────────────────────────
    # completed_answers: list of (question, answer) to reprint when going back
    completed = [("System check", "ready")]

    chosen: Optional[dict] = None
    api_key: Optional[str] = None

    # Detect Claude Code credentials once
    creds_path = Path.home() / ".claude" / ".credentials.json"
    auto_api = None
    try:
        if creds_path.exists():
            creds_data = json.loads(creds_path.read_text())
            if creds_data.get("claudeAiOauth", {}).get("accessToken"):
                auto_api = "CLAUDE_CODE"
    except Exception:
        pass

    step = 2  # start at model selection
    while step <= 3:

        # ── Step 2: Model ──────────────────────────────────────────────────────
        if step == 2:
            _clear()
            _header()
            for q, a in completed:
                _answered(q, a)

            _step("Choose a model")
            _blank()
            if has_gpu:
                _box_line(f"[{MUTED}]GPU  {gpu_desc}[/]")
            else:
                _box_line(f"[{YELLOW}]No GPU — pick a CPU-friendly model.[/]")
            _blank()
            _nav_hint("↑ ↓  move", "Enter  select", "S  skip", "Q  quit")
            _blank()

            result = _select(MODELS)
            _blank()
            _box_close()

            if result is _BACK:
                # Can't go back past step 1 (auto-runs), so stay on step 2
                continue
            elif result is _SKIP:
                chosen = None
                ans = f"[{MUTED}]skipped — keeping current config[/]"
            else:
                chosen = MODELS[result]
                ans = f"{chosen['name']}  [{MUTED}]{chosen['requirements']}[/]"

            _answered("Model", ans)
            completed_2 = completed + [("Model", ans)]
            step = 3

        # ── Step 3: API key ────────────────────────────────────────────────────
        if step == 3:
            if auto_api == "CLAUDE_CODE":
                api_key = "CLAUDE_CODE"
                _answered("API key", f"[{GREEN}]Claude Code detected ✓[/]")
                completed = completed_2 + [("API key", "Claude Code ✓")]
                step = 4
                continue

            _clear()
            _header()
            for q, a in completed_2:
                _answered(q, a)

            _step("API key  (optional)")
            _blank()
            _box_line(f"[{MUTED}]Lets agents use Claude Sonnet/Haiku for complex tasks.[/]")
            _box_line(f"[{MUTED}]Hollow works without it.[/]")
            _blank()
            _nav_hint("Enter  skip", "B  back", "Q  quit")
            _blank()

            raw = _input_text("sk-ant-...  or Enter to skip", password=True)
            _blank()
            _box_close()

            if raw is _BACK:
                step = 2
                continue

            if raw.startswith("sk-"):
                api_key = raw
                ans3 = "saved"
            else:
                api_key = None
                ans3 = "skipped — local only"

            _answered("API key", ans3)
            completed = completed_2 + [("API key", ans3)]
            step = 4

    # ── Step 4: Launch ────────────────────────────────────────────────────────────
    _step("Starting Hollow")
    _blank()

    def log(msg: str) -> None:
        _box_line(msg)

    model_id = chosen["id"] if chosen else None
    if model_id:
        log(f"[{DIM}]Writing config…[/]")
        _write_config(model_id)
    else:
        log(f"[{MUTED}]Keeping existing config.[/]")

    log(f"[{DIM}]Writing .env…[/]")
    _write_env(api_key)

    log(f"[{DIM}]Creating directories…[/]")
    _ensure_dirs()

    if model_id and not _model_installed(model_id):
        log(f"[{TEAL}]Downloading {model_id}  [{DIM}]{chosen['requirements']}[/]")
        log(f"[{MUTED}]This may take a few minutes…[/]")
        if _pull_model(model_id):
            log(f"[{GREEN}]✓[/]  {model_id} ready")
        else:
            log(f"[{RED}]✗[/]  Download failed — is Ollama running?")
            log(f"[{MUTED}]Run 'ollama serve' then re-run hollow.py to retry.[/]")
            _blank()
            _box_close()
            C.print(f"[{PIPE}]└[/]")
            C.print()
            return
    elif model_id:
        log(f"[{GREEN}]✓[/]  {model_id} already downloaded")

    if not _model_installed("nomic-embed-text"):
        log(f"[{TEAL}]Downloading nomic-embed-text  [{DIM}]~274 MB[/]")
        if _pull_model("nomic-embed-text"):
            log(f"[{GREEN}]✓[/]  nomic-embed-text ready")
        else:
            log(f"[{RED}]✗[/]  nomic-embed-text download failed.[/]")
            log(f"[{MUTED}]Run 'ollama serve' then re-run hollow.py to retry.[/]")
            _blank()
            _box_close()
            C.print(f"[{PIPE}]└[/]")
            C.print()
            return

    log(f"[{DIM}]Starting containers…[/]")
    ok, err = _start_containers(has_gpu)
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

    # ── What now ──────────────────────────────────────────────────────────────────
    _clear()
    _header()
    _step("You're live")
    _blank()
    _box_line(f"[{WHITE}]Three agents are now running.[/]")
    _box_line(f"[{DIM}]A scout, an analyst, and a builder. They'll name themselves.[/]")
    _box_line(f"[{DIM}]They pick their own goals. You don't need to do anything.[/]")
    _blank()
    _box_line(f"[{MUTED}]The monitor is about to open. Here's what you'll see:[/]")
    _blank()
    _box_line(f"[{DIM}]  ·  Goals as agents choose them[/]")
    _box_line(f"[{DIM}]  ·  Tool calls and results in real time[/]")
    _box_line(f"[{DIM}]  ·  Stressors rising when agents aren't making real progress[/]")
    _blank()
    _box_line(f"[{DIM}]When an agent wants something it can't do itself, it files an[/]")
    _box_line(f"[{DIM}]invoke_claude request. That's for you to review and decide[/]")
    _box_line(f"[{DIM}]whether to build. You'll see it appear in the log.[/]")
    _blank()
    if _IS_WIN:
        _box_line(f"[{MUTED}]python hollow.py          reopen the monitor[/]")
        _box_line(f"[{MUTED}]launch.bat                start agents and monitor[/]")
        _box_line(f"[{MUTED}]stop.bat                  stop agents, clear VRAM[/]")
        _box_line(f"[{MUTED}]python hollow.py status   check health[/]")
        _box_line(f"[{MUTED}]python hollow.py setup    re-run this wizard[/]")
    else:
        _box_line(f"[{MUTED}]python3 hollow.py          reopen the monitor[/]")
        _box_line(f"[{MUTED}]python3 hollow.py stop     stop agents[/]")
        _box_line(f"[{MUTED}]python3 hollow.py status   check health[/]")
        _box_line(f"[{MUTED}]python3 hollow.py setup    re-run this wizard[/]")
    _blank()
    _box_close()
    _blank()
    C.print(f"[{PIPE}]│[/]  [{FAINT}]press any key to open the monitor[/]")
    _blank()
    C.print(f"[{PIPE}]└[/]")
    C.print()
    _read_key()

    os.execv(sys.executable, [sys.executable, str(ROOT / "thoughts.py")])
