"""
Hollow AgentOS — interactive setup wizard (Textual TUI).
Called by hollow.py when setup is needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Button, Footer, Header, Input, Label,
    ListItem, ListView, LoadingIndicator,
    Markdown, ProgressBar, Static, Switch,
)

ROOT = Path(__file__).parent.resolve()
CONFIG_EXAMPLE = ROOT / "config.example.json"
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"

# ── Available models ──────────────────────────────────────────────────────────

MODELS = [
    {
        "id": "qwen3.5:9b",
        "name": "Qwen 3.5  ·  9B",
        "badge": "RECOMMENDED",
        "badge_color": "bold #00e5c0",
        "description": "Best emergent behavior. Richest stressor vocabulary and reasoning.",
        "requirements": "NVIDIA GPU · 8GB+ VRAM",
        "size": "~5.2 GB",
    },
    {
        "id": "qwen3.5:4b",
        "name": "Qwen 3.5  ·  4B",
        "badge": "BALANCED",
        "badge_color": "bold #7b5ea7",
        "description": "Good behavior with lower hardware needs.",
        "requirements": "4GB+ VRAM  or  fast CPU",
        "size": "~2.6 GB",
    },
    {
        "id": "llama3.2:3b",
        "name": "Llama 3.2  ·  3B",
        "badge": "CPU FRIENDLY",
        "badge_color": "bold #f0b429",
        "description": "Runs on any machine. Simpler behavior patterns.",
        "requirements": "CPU only · 4GB RAM",
        "size": "~2.0 GB",
    },
    {
        "id": "gemma3:4b",
        "name": "Gemma 3  ·  4B",
        "badge": "ALTERNATIVE",
        "badge_color": "bold #6e6e8e",
        "description": "Google's lightweight model. Different reasoning style.",
        "requirements": "4GB+ VRAM  or  CPU",
        "size": "~3.3 GB",
    },
]

# ── Shared CSS ────────────────────────────────────────────────────────────────

HOLLOW_CSS = """
Screen {
    background: #0d0d14;
    color: #e2e2f0;
}

.logo {
    color: #00e5c0;
    text-align: center;
    padding: 1 0 0 0;
}

.tagline {
    color: #7b5ea7;
    text-align: center;
    text-style: italic;
    padding: 0 0 1 0;
}

.step-header {
    color: #00e5c0;
    text-style: bold;
    padding: 1 2 0 2;
}

.step-body {
    padding: 0 2 1 2;
    color: #e2e2f0;
}

.dim {
    color: #6e6e8e;
}

.success {
    color: #00c896;
    text-style: bold;
}

.warning {
    color: #f0b429;
}

.error {
    color: #f05050;
}

.divider {
    border-top: solid #2a2a3e;
    margin: 1 2;
}

.panel {
    border: round #2a2a3e;
    margin: 0 2 1 2;
    padding: 1 2;
    background: #141420;
}

.panel-accent {
    border: round #00e5c0;
    margin: 0 2 1 2;
    padding: 1 2;
    background: #141420;
}

.check-row {
    height: 1;
    margin: 0 0 0 2;
}

.model-item {
    padding: 1 2;
    background: #141420;
    border: round #2a2a3e;
    margin: 0 2 1 2;
    height: auto;
}

.model-item:focus {
    border: round #00e5c0;
    background: #1a1a2e;
}

.model-item.-highlight {
    border: round #00e5c0;
    background: #1a1a2e;
}

.model-name {
    text-style: bold;
    color: #e2e2f0;
}

.model-badge {
    text-style: bold;
}

.model-desc {
    color: #9e9ec0;
}

.model-req {
    color: #6e6e8e;
}

.btn-primary {
    background: #00e5c0;
    color: #0d0d14;
    text-style: bold;
    margin: 0 2;
    border: none;
}

.btn-primary:hover {
    background: #00ffda;
}

.btn-secondary {
    background: #2a2a3e;
    color: #9e9ec0;
    margin: 0 1;
    border: none;
}

.btn-secondary:hover {
    background: #3a3a5e;
    color: #e2e2f0;
}

.progress-label {
    color: #6e6e8e;
    margin: 0 2 0 2;
}

ProgressBar {
    margin: 0 2 1 2;
}

ProgressBar Bar {
    color: #00e5c0;
    background: #2a2a3e;
}

Input {
    background: #141420;
    border: round #2a2a3e;
    color: #e2e2f0;
    margin: 0 2 1 2;
}

Input:focus {
    border: round #00e5c0;
}

.footer-hint {
    color: #6e6e8e;
    text-align: center;
    padding: 1 0;
}

.done-summary {
    color: #6e6e8e;
    padding: 0 2 1 2;
}

.done-tick {
    color: #00c896;
}
"""

LOGO = """\
  ██╗  ██╗ ██████╗ ██╗     ██╗      ██████╗ ██╗    ██╗
  ██║  ██║██╔═══██╗██║     ██║     ██╔═══██╗██║    ██║
  ███████║██║   ██║██║     ██║     ██║   ██║██║ █╗ ██║
  ██╔══██║██║   ██║██║     ██║     ██║   ██║██║███╗██║
  ██║  ██║╚██████╔╝███████╗███████╗╚██████╔╝╚███╔███╔╝
  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝ ╚═════╝  ╚══╝╚══╝"""


# ── System detection helpers ──────────────────────────────────────────────────

def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _docker_running() -> bool:
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=5
        )
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
    """Returns (has_gpu, description)."""
    # NVIDIA
    if _has_cmd("nvidia-smi"):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                line = r.stdout.strip().splitlines()[0]
                name, mem = line.split(",", 1)
                return True, f"{name.strip()}  ·  {mem.strip()}"
        except Exception:
            pass
    # AMD ROCm
    if _has_cmd("rocm-smi"):
        return True, "AMD GPU (ROCm)"
    # macOS Metal
    if platform.system() == "Darwin":
        try:
            r = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=5
            )
            if "Metal" in r.stdout:
                return True, "Apple Silicon (Metal)"
        except Exception:
            pass
    return False, "No GPU detected — CPU only"


def _model_installed(model_id: str) -> bool:
    try:
        r = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=5
        )
        base = model_id.split(":")[0]
        return base in r.stdout
    except Exception:
        return False


def _pkg_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _api_healthy() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(
            "http://localhost:7777/health", timeout=2
        ) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception:
        return False


# ── Welcome screen ────────────────────────────────────────────────────────────

class WelcomeScreen(Screen):
    BINDINGS = [Binding("enter", "continue", "Continue")]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(LOGO, classes="logo")
            yield Static(
                "agentOS  ·  v5.5",
                classes="tagline",
            )
            yield Static("", classes="step-body")
            with Container(classes="panel-accent"):
                yield Static(
                    "Hollow gives AI a persistent home on your computer.\n"
                    "Three agents run continuously — forming goals, building tools,\n"
                    "and developing behavior you didn't program.\n\n"
                    "What they do next is up to them.",
                    classes="step-body",
                )
            yield Static(
                "This setup takes about 5 minutes on a fresh machine.",
                classes="step-body dim",
            )
            yield Static(
                "If you already have Docker and Ollama, it's closer to 30 seconds.",
                classes="step-body dim",
            )
            yield Static("", classes="step-body")
            yield Button("  Begin Setup  →", classes="btn-primary", id="begin")
            yield Static(
                "  Enter  ·  continue        Q  ·  quit",
                classes="footer-hint",
            )

    def action_continue(self) -> None:
        self.app.push_screen(SystemCheckScreen())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "begin":
            self.app.push_screen(SystemCheckScreen())


# ── System check screen ───────────────────────────────────────────────────────

class CheckRow(Widget):
    DEFAULT_CSS = """
    CheckRow {
        height: 1;
        padding: 0 2;
    }
    """

    def __init__(self, label: str, **kwargs):
        super().__init__(**kwargs)
        self._label = label
        self._status = "pending"  # pending | ok | warn | error

    def render(self):
        icons = {
            "pending": ("[dim]○[/]", "[dim]waiting…[/]"),
            "running": ("[bold #00e5c0]◌[/]", "[dim]checking…[/]"),
            "ok":      ("[bold #00c896]✓[/]", "[bold #00c896]ready[/]"),
            "warn":    ("[bold #f0b429]⚠[/]", "[bold #f0b429]warning[/]"),
            "error":   ("[bold #f05050]✗[/]", "[bold #f05050]not found[/]"),
        }
        icon, state = icons.get(self._status, icons["pending"])
        from rich.text import Text
        return Text.from_markup(
            f"  {icon}  {self._label:<30} {state}"
        )

    def set_status(self, status: str, detail: str = "") -> None:
        self._status = status
        if detail:
            icons = {
                "ok": "✓", "warn": "⚠", "error": "✗",
            }
            icon = icons.get(status, "○")
            from rich.text import Text
            # store detail for re-render
            self._detail = detail
        self.refresh()

    def render(self):
        from rich.text import Text
        colors = {
            "pending": "#6e6e8e",
            "running": "#00e5c0",
            "ok":      "#00c896",
            "warn":    "#f0b429",
            "error":   "#f05050",
        }
        icons = {
            "pending": "○",
            "running": "◌",
            "ok":      "✓",
            "warn":    "⚠",
            "error":   "✗",
        }
        detail = getattr(self, "_detail", self._status)
        col = colors.get(self._status, "#6e6e8e")
        icon = icons.get(self._status, "○")
        return Text.from_markup(
            f"  [{col}]{icon}[/]  {self._label:<32} [{col}]{detail}[/]"
        )


def _install_docker() -> tuple[bool, str]:
    """Attempt to install Docker Desktop. Returns (ok, error)."""
    sys_platform = platform.system()
    if sys_platform == "Windows":
        if _has_cmd("winget"):
            r = subprocess.run(
                ["winget", "install", "-e", "--id", "Docker.DockerDesktop",
                 "--accept-package-agreements", "--accept-source-agreements", "-h"],
                capture_output=True, text=True, timeout=300,
            )
            if r.returncode == 0:
                # Start Docker Desktop
                docker_exe = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
                if os.path.exists(docker_exe):
                    subprocess.Popen([docker_exe])
                # Wait up to 90s for Docker to become ready
                for _ in range(45):
                    if _docker_running():
                        return True, ""
                    time.sleep(2)
                return True, "slow_start"
            return False, r.stderr[:200]
        else:
            # No winget — open download page
            import webbrowser
            webbrowser.open("https://docs.docker.com/desktop/install/windows-install/")
            return False, "no_winget"
    elif sys_platform == "Darwin":
        import webbrowser
        webbrowser.open("https://docs.docker.com/desktop/install/mac-install/")
        return False, "manual_mac"
    else:
        import webbrowser
        webbrowser.open("https://docs.docker.com/engine/install/")
        return False, "manual_linux"


def _install_ollama() -> tuple[bool, str]:
    """Attempt to install Ollama. Returns (ok, error)."""
    sys_platform = platform.system()
    if sys_platform == "Windows":
        if _has_cmd("winget"):
            r = subprocess.run(
                ["winget", "install", "-e", "--id", "Ollama.Ollama",
                 "--accept-package-agreements", "--accept-source-agreements", "-h"],
                capture_output=True, text=True, timeout=180,
            )
            if r.returncode == 0:
                # Start ollama serve
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for _ in range(15):
                    if _ollama_running():
                        return True, ""
                    time.sleep(2)
                return True, "slow_start"
            return False, r.stderr[:200]
        else:
            # Download installer directly
            try:
                import urllib.request, tempfile
                installer = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
                urllib.request.urlretrieve(
                    "https://ollama.com/download/OllamaSetup.exe", installer
                )
                subprocess.run([installer, "/S"], check=True, timeout=120)
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                for _ in range(15):
                    if _ollama_running():
                        return True, ""
                    time.sleep(2)
                return True, "slow_start"
            except Exception as e:
                return False, str(e)
    elif sys_platform == "Darwin":
        if _has_cmd("brew"):
            r = subprocess.run(
                ["brew", "install", "ollama"],
                capture_output=True, text=True, timeout=180,
            )
            if r.returncode == 0:
                subprocess.Popen(["ollama", "serve"],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                for _ in range(10):
                    if _ollama_running():
                        return True, ""
                    time.sleep(2)
                return True, "slow_start"
            return False, r.stderr[:200]
        else:
            import webbrowser
            webbrowser.open("https://ollama.com/download")
            return False, "manual_mac"
    else:
        # Linux
        try:
            r = subprocess.run(
                ["bash", "-c",
                 "curl -fsSL https://ollama.com/install.sh | sh"],
                capture_output=True, text=True, timeout=180,
            )
            if r.returncode == 0:
                subprocess.Popen(["ollama", "serve"],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                for _ in range(10):
                    if _ollama_running():
                        return True, ""
                    time.sleep(2)
                return True, "slow_start"
            return False, r.stderr[:200]
        except Exception as e:
            return False, str(e)


class SystemCheckScreen(Screen):
    BINDINGS = [
        Binding("enter", "continue_setup", "Continue"),
        Binding("r",     "recheck",        "Re-check"),
    ]

    def __init__(self):
        super().__init__()
        self._can_continue = False
        self._gpu_info = ""
        self._results: dict = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                "  [bold #00e5c0]01[/]  System Check",
                classes="step-header",
            )
            yield Static(
                "  Checking what needs to be installed.",
                classes="step-body dim",
            )
            with Container(classes="panel"):
                yield CheckRow("Docker Desktop", id="check-docker")
                yield CheckRow("Ollama", id="check-ollama")
                yield CheckRow("GPU", id="check-gpu")
                yield CheckRow("Python deps", id="check-deps")

            # Action area — populated dynamically after checks
            yield Static("", id="action-area", classes="step-body")

            yield Button(
                "  Continue  →",
                classes="btn-primary",
                id="btn-continue",
                disabled=True,
            )
            yield Button(
                "  Re-check",
                classes="btn-secondary",
                id="btn-recheck",
            )
            yield Static(
                "  Enter  ·  continue        R  ·  re-check        Q  ·  quit",
                classes="footer-hint",
            )

    def on_mount(self) -> None:
        self._run_checks()

    @work(thread=True)
    def _run_checks(self) -> None:
        results: dict = {}

        def update(check_id: str, status: str, detail: str = "") -> None:
            w = self.query_one(f"#{check_id}", CheckRow)
            self.call_from_thread(w.set_status, status, detail)

        # Docker
        update("check-docker", "running")
        if _docker_running():
            update("check-docker", "ok", "running")
            results["docker"] = "ok"
        elif _has_cmd("docker"):
            update("check-docker", "warn", "installed, not running")
            results["docker"] = "not_running"
        else:
            update("check-docker", "error", "not installed")
            results["docker"] = "missing"

        # Ollama
        update("check-ollama", "running")
        if _ollama_running():
            update("check-ollama", "ok", "running")
            results["ollama"] = "ok"
        elif _has_cmd("ollama"):
            update("check-ollama", "warn", "installed, not running")
            results["ollama"] = "not_running"
        else:
            update("check-ollama", "error", "not installed")
            results["ollama"] = "missing"

        # GPU
        update("check-gpu", "running")
        has_gpu, gpu_desc = _detect_gpu()
        self._gpu_info = gpu_desc
        if has_gpu:
            update("check-gpu", "ok", gpu_desc[:42])
        else:
            update("check-gpu", "warn", "none detected — pick a CPU model")

        # Python deps
        update("check-deps", "running")
        missing_pkgs = [
            p for p in ["textual", "rich", "httpx"]
            if not _pkg_available(p)
        ]
        if missing_pkgs:
            update("check-deps", "warn", f"missing: {', '.join(missing_pkgs)}")
            results["deps"] = "missing"
        else:
            update("check-deps", "ok", "all present")
            results["deps"] = "ok"

        self._results = results
        self.call_from_thread(self._update_actions, results)

    def _update_actions(self, results: dict) -> None:
        area = self.query_one("#action-area", Static)
        btn  = self.query_one("#btn-continue", Button)

        docker_ok = results.get("docker") == "ok"
        ollama_ok = results.get("ollama") == "ok"

        if docker_ok and ollama_ok:
            area.update(
                "  [bold #00c896]All systems ready.[/]  "
                "Press Continue or Enter."
            )
            btn.disabled = False
            self._can_continue = True
            return

        self._can_continue = False
        btn.disabled = True

        lines = []
        sys_platform = platform.system()

        if not docker_ok:
            d_state = results.get("docker", "missing")
            if d_state == "not_running":
                lines.append(
                    "  [#f0b429]Docker is installed but not running.[/]\n"
                    "  Start Docker Desktop, then press R to re-check.\n"
                )
            else:
                if sys_platform == "Windows":
                    admin_note = (
                        "  [dim]Docker requires an admin permission prompt — "
                        "click Yes when Windows asks.[/]"
                    )
                else:
                    admin_note = ""
                lines.append(
                    "  [#f05050]Docker Desktop is not installed.[/]\n"
                    + (admin_note + "\n" if admin_note else "")
                )

        if not ollama_ok:
            o_state = results.get("ollama", "missing")
            if o_state == "not_running":
                lines.append(
                    "  [#f0b429]Ollama is installed but not running.[/]\n"
                    "  It will be started automatically — press R to re-check.\n"
                )
                # Try to start it
                self._start_ollama_serve()
            else:
                lines.append(
                    "  [#f05050]Ollama is not installed.[/]\n"
                    "  Ollama runs AI models locally on your machine.\n"
                )

        area.update("\n".join(lines))

        # Remove old action buttons and add fresh ones
        for btn_id in ("btn-install-docker", "btn-install-ollama",
                       "btn-install-both"):
            try:
                self.query_one(f"#{btn_id}", Button).remove()
            except Exception:
                pass

        scroll = self.query_one(VerticalScroll)
        recheck_btn = self.query_one("#btn-recheck", Button)

        needs_docker = results.get("docker") == "missing"
        needs_ollama = results.get("ollama") == "missing"

        if needs_docker and needs_ollama:
            new_btn = Button(
                "  Install Docker + Ollama",
                classes="btn-primary",
                id="btn-install-both",
            )
            scroll.mount(new_btn, before=recheck_btn)
        else:
            if needs_docker:
                new_btn = Button(
                    "  Install Docker Desktop",
                    classes="btn-primary",
                    id="btn-install-docker",
                )
                scroll.mount(new_btn, before=recheck_btn)
            if needs_ollama:
                new_btn = Button(
                    "  Install Ollama",
                    classes="btn-primary" if not needs_docker else "btn-secondary",
                    id="btn-install-ollama",
                )
                scroll.mount(new_btn, before=recheck_btn)

    @work(thread=True)
    def _start_ollama_serve(self) -> None:
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    @work(thread=True)
    def _do_install(self, what: str) -> None:
        area = self.query_one("#action-area", Static)

        if what in ("docker", "both"):
            self.call_from_thread(
                self.query_one("#check-docker", CheckRow).set_status,
                "running", "installing…"
            )
            self.call_from_thread(
                area.update,
                "  [#00e5c0]◌[/]  Installing Docker Desktop…\n"
                "  [dim]This may take a few minutes and will ask for admin permission.[/]",
            )
            ok, err = _install_docker()
            if ok:
                self.call_from_thread(
                    self.query_one("#check-docker", CheckRow).set_status,
                    "ok", "running",
                )
            else:
                msg = {
                    "no_winget": "Opened docker.com — install it, then press R.",
                    "manual_mac": "Opened docker.com — install it, then press R.",
                    "manual_linux": "Opened docs page — install it, then press R.",
                    "slow_start": "Installed — Docker may still be starting. Press R.",
                }.get(err, f"Failed: {err[:80]}")
                self.call_from_thread(
                    self.query_one("#check-docker", CheckRow).set_status,
                    "warn" if "slow" in err or "manual" in err or "winget" in err
                    else "error",
                    msg[:42],
                )

        if what in ("ollama", "both"):
            self.call_from_thread(
                self.query_one("#check-ollama", CheckRow).set_status,
                "running", "installing…"
            )
            self.call_from_thread(
                area.update,
                "  [#00e5c0]◌[/]  Installing Ollama…",
            )
            ok, err = _install_ollama()
            if ok:
                self.call_from_thread(
                    self.query_one("#check-ollama", CheckRow).set_status,
                    "ok", "running",
                )
            else:
                msg = {
                    "manual_mac": "Opened ollama.com — install it, then press R.",
                }.get(err, f"Failed: {err[:80]}")
                self.call_from_thread(
                    self.query_one("#check-ollama", CheckRow).set_status,
                    "warn", msg[:42],
                )

        # Re-check everything after installs complete
        self.call_from_thread(self.action_recheck)

    def action_continue_setup(self) -> None:
        if self._can_continue:
            self.app.push_screen(ModelSelectScreen(self._gpu_info))

    def action_recheck(self) -> None:
        # Reset all rows and re-run
        for check_id in ("check-docker", "check-ollama", "check-gpu", "check-deps"):
            try:
                self.query_one(f"#{check_id}", CheckRow).set_status("pending")
            except Exception:
                pass
        # Remove install buttons (they'll be re-added if still needed)
        for btn_id in ("btn-install-docker", "btn-install-ollama", "btn-install-both"):
            try:
                self.query_one(f"#{btn_id}", Button).remove()
            except Exception:
                pass
        self._run_checks()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-continue" and self._can_continue:
            self.app.push_screen(ModelSelectScreen(self._gpu_info))
        elif bid == "btn-recheck":
            self.action_recheck()
        elif bid == "btn-install-docker":
            event.button.disabled = True
            self._do_install("docker")
        elif bid == "btn-install-ollama":
            event.button.disabled = True
            self._do_install("ollama")
        elif bid == "btn-install-both":
            event.button.disabled = True
            self._do_install("both")


# ── Model selection screen ────────────────────────────────────────────────────

class ModelCard(Widget):
    DEFAULT_CSS = """
    ModelCard {
        height: auto;
        padding: 1 2;
        background: #141420;
        border: round #2a2a3e;
        margin: 0 2 1 2;
    }
    ModelCard:focus {
        border: round #00e5c0;
        background: #1a1a2e;
    }
    ModelCard.-selected {
        border: round #00e5c0;
        background: #1a1a2e;
    }
    """

    def __init__(self, model: dict, selected: bool = False, **kwargs):
        super().__init__(**kwargs, can_focus=True)
        self._model = model
        self._selected = selected

    def render(self):
        from rich.text import Text
        m = self._model
        sel = "[bold #00e5c0]◆[/]  " if self._selected else "[dim]◇[/]  "
        badge_col = m.get("badge_color", "bold #6e6e8e")
        t = Text.from_markup(
            f"{sel}[bold]{m['name']}[/]   [{badge_col}]{m['badge']}[/]\n"
            f"   [dim]{m['description']}[/]\n"
            f"   [#6e6e8e]{m['requirements']}   ·   {m['size']}[/]"
        )
        return t

    def set_selected(self, val: bool) -> None:
        self._selected = val
        if val:
            self.add_class("-selected")
        else:
            self.remove_class("-selected")
        self.refresh()

    def on_focus(self) -> None:
        self.post_message(ModelCard.Focused(self))

    class Focused(Message):
        def __init__(self, card: "ModelCard") -> None:
            super().__init__()
            self.card = card


class ModelSelectScreen(Screen):
    BINDINGS = [
        Binding("enter", "select_model", "Select"),
        Binding("up",    "focus_prev",   "Up",    show=False),
        Binding("down",  "focus_next",   "Down",  show=False),
    ]

    def __init__(self, gpu_info: str = ""):
        super().__init__()
        self._gpu_info = gpu_info
        self._selected_idx = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                "  [bold #00e5c0]02[/]  Choose a Model",
                classes="step-header",
            )
            gpu_note = (
                f"  Detected: [#00c896]{self._gpu_info}[/]"
                if self._gpu_info and "No GPU" not in self._gpu_info
                else "  [#f0b429]No GPU detected[/] — choose a CPU-friendly model below."
            )
            yield Static(gpu_note, classes="step-body")
            yield Static(
                "  This model runs locally on your machine. Ollama will download it automatically.",
                classes="step-body dim",
            )

            for i, model in enumerate(MODELS):
                yield ModelCard(
                    model,
                    selected=(i == 0),
                    id=f"model-{i}",
                )

            yield Static(
                "  [dim]↑ ↓[/]  navigate        [dim]Enter[/]  select        [dim]Q[/]  quit",
                classes="footer-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#model-0", ModelCard).focus()

    def on_model_card_focused(self, event: ModelCard.Focused) -> None:
        for i in range(len(MODELS)):
            card = self.query_one(f"#model-{i}", ModelCard)
            card.set_selected(card is event.card)
            if card is event.card:
                self._selected_idx = i

    def action_focus_prev(self) -> None:
        idx = max(0, self._selected_idx - 1)
        self.query_one(f"#model-{idx}", ModelCard).focus()

    def action_focus_next(self) -> None:
        idx = min(len(MODELS) - 1, self._selected_idx + 1)
        self.query_one(f"#model-{idx}", ModelCard).focus()

    def action_select_model(self) -> None:
        chosen = MODELS[self._selected_idx]
        self.app.push_screen(ApiKeyScreen(chosen))


# ── API key screen ─────────────────────────────────────────────────────────────

class ApiKeyScreen(Screen):
    BINDINGS = [
        Binding("enter",  "skip_or_continue", "Continue"),
        Binding("escape", "skip",              "Skip"),
    ]

    def __init__(self, model: dict):
        super().__init__()
        self._model = model
        self._found_key: Optional[str] = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                "  [bold #00e5c0]03[/]  Anthropic API Key  [dim](optional)[/]",
                classes="step-header",
            )
            yield Static(
                "  An API key lets agents route complex tasks to Claude Sonnet or Haiku\n"
                "  when the local model isn't enough. Hollow works fine without one.",
                classes="step-body dim",
            )

            # Auto-detect Claude Code credentials
            creds_path = (
                Path.home() / ".claude" / ".credentials.json"
            )
            if creds_path.exists():
                try:
                    creds = json.loads(creds_path.read_text())
                    token = creds.get("claudeAiOauth", {}).get("accessToken", "")
                    if token:
                        self._found_key = "CLAUDE_CODE"
                        with Container(classes="panel-accent"):
                            yield Static(
                                "  [bold #00c896]✓[/]  Claude Code detected — agents will use your account automatically.",
                                classes="step-body",
                            )
                        yield Button("  Continue  →", classes="btn-primary", id="btn-continue")
                        yield Button("  Skip", classes="btn-secondary", id="btn-skip")
                        yield Static(
                            "  Enter  ·  continue        Esc  ·  skip",
                            classes="footer-hint",
                        )
                        return
                except Exception:
                    pass

            with Container(classes="panel"):
                yield Static(
                    "  Paste your Anthropic API key below, or skip to use local model only.\n"
                    "  [dim]Get one at console.anthropic.com — starts with sk-ant-[/]",
                    classes="step-body",
                )
                yield Input(
                    placeholder="sk-ant-...",
                    password=True,
                    id="api-key-input",
                )

            yield Button("  Continue  →", classes="btn-primary", id="btn-continue")
            yield Button("  Skip", classes="btn-secondary", id="btn-skip")
            yield Static(
                "  Enter  ·  continue        Esc  ·  skip        Q  ·  quit",
                classes="footer-hint",
            )

    def _get_key(self) -> Optional[str]:
        if self._found_key:
            return self._found_key
        try:
            inp = self.query_one("#api-key-input", Input)
            val = inp.value.strip()
            return val if val.startswith("sk-") else None
        except Exception:
            return None

    def action_skip_or_continue(self) -> None:
        self.app.push_screen(LaunchScreen(self._model, self._get_key()))

    def action_skip(self) -> None:
        self.app.push_screen(LaunchScreen(self._model, None))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-continue":
            self.app.push_screen(LaunchScreen(self._model, self._get_key()))
        elif event.button.id == "btn-skip":
            self.app.push_screen(LaunchScreen(self._model, None))


# ── Launch screen ─────────────────────────────────────────────────────────────

class LaunchScreen(Screen):
    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, model: dict, api_key: Optional[str]):
        super().__init__()
        self._model = model
        self._api_key = api_key
        self._done = False

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                "  [bold #00e5c0]04[/]  Launching",
                classes="step-header",
            )
            yield Static(
                f"  Model: [bold]{self._model['name']}[/]   "
                f"API: [bold]{'yes' if self._api_key else 'local only'}[/]",
                classes="step-body dim",
            )

            with Container(classes="panel", id="launch-log"):
                yield Static(
                    "  [dim]Starting…[/]",
                    id="launch-status",
                    classes="step-body",
                )

            yield ProgressBar(total=100, id="launch-progress", show_eta=False)
            yield Static("", id="launch-note", classes="step-body dim")

            with Container(classes="panel", id="done-panel"):
                yield Static(
                    "",
                    id="done-content",
                    classes="step-body",
                )

    def on_mount(self) -> None:
        self._run_launch()

    @work(thread=True)
    def _run_launch(self) -> None:
        def log(msg: str) -> None:
            self.call_from_thread(self._update_log, msg)

        def progress(pct: int) -> None:
            self.call_from_thread(
                self.query_one("#launch-progress", ProgressBar).update, progress=pct
            )

        try:
            # ── Step 1: Write config ──────────────────────────────────────────
            log("  [dim]Writing config.json…[/]")
            progress(10)
            _write_config(self._model["id"])

            # ── Step 2: Write .env ────────────────────────────────────────────
            log("  [dim]Writing .env…[/]")
            progress(20)
            _write_env(self._api_key)

            # ── Step 3: Ensure dirs ───────────────────────────────────────────
            log("  [dim]Creating runtime directories…[/]")
            _ensure_dirs()
            progress(30)

            # ── Step 4: Pull models ───────────────────────────────────────────
            model_id = self._model["id"]
            if _model_installed(model_id):
                log(f"  [bold #00c896]✓[/]  {model_id} already downloaded")
                progress(50)
            else:
                log(
                    f"  [#00e5c0]↓[/]  Downloading {model_id}  "
                    f"[dim]({self._model['size']} — this may take a few minutes)[/]"
                )
                self.call_from_thread(
                    self.query_one("#launch-note", Static).update,
                    "  Downloading model… grab a coffee.",
                )
                _pull_model(model_id, lambda p: progress(30 + int(p * 0.2)))
                log(f"  [bold #00c896]✓[/]  {model_id} ready")
                progress(50)

            # Pull nomic-embed-text (needed for semantic memory)
            if not _model_installed("nomic-embed-text"):
                log("  [#00e5c0]↓[/]  Downloading nomic-embed-text [dim](~274 MB — embedding model)[/]")
                _pull_model("nomic-embed-text")
                log("  [bold #00c896]✓[/]  nomic-embed-text ready")
            progress(60)

            # ── Step 5: Start containers ──────────────────────────────────────
            log("  [dim]Starting containers…[/]")
            progress(70)
            ok, err = _start_containers()
            if not ok:
                log(f"  [bold #f05050]✗[/]  Docker compose failed:\n  [dim]{err[:120]}[/]")
                self.call_from_thread(self._show_done, False, err)
                return
            log("  [bold #00c896]✓[/]  Containers started")
            progress(85)

            # ── Step 6: Wait for health ───────────────────────────────────────
            log("  [dim]Waiting for API health check…[/]")
            for i in range(30):
                if _api_healthy():
                    break
                time.sleep(2)
            progress(100)

            if _api_healthy():
                log("  [bold #00c896]✓[/]  API is up  →  http://localhost:7777")
                self.call_from_thread(self._show_done, True, "")
            else:
                log("  [bold #f0b429]⚠[/]  API is slow to start — it may still be initializing.")
                self.call_from_thread(self._show_done, True, "slow_start")

        except Exception as exc:
            log(f"  [bold #f05050]✗[/]  Unexpected error: {exc}")
            self.call_from_thread(self._show_done, False, str(exc))

    def _launch_monitor(self) -> None:
        self.app.exit()
        import subprocess, sys
        subprocess.run(
            [sys.executable, str(ROOT / "thoughts.py")],
            cwd=str(ROOT),
        )

    def _update_log(self, msg: str) -> None:
        existing = self.query_one("#launch-status", Static)
        current = str(existing.renderable)
        existing.update(current + "\n" + msg)

    def _show_done(self, success: bool, detail: str) -> None:
        self._done = True
        if success:
            launch_note = ""
            if detail == "slow_start":
                launch_note = (
                    "\n\n  [#f0b429]Note:[/] [dim]API still initializing — "
                    "check http://localhost:7777/health if agents don't appear.[/]"
                )
            content = (
                "  [bold #00e5c0]Hollow is alive.[/]\n\n"
                "  Three agents are running — forming goals, calling tools,\n"
                "  building their own understanding of their world.\n\n"
                "  Opening the live monitor in 3 seconds...\n\n"
                "  [dim]hollow            open monitor anytime[/]\n"
                "  [dim]hollow onboarding  re-run this wizard[/]\n"
                "  [dim]hollow stop        stop all containers[/]"
                + launch_note
            )
            # Auto-launch monitor after a short delay
            self.set_timer(3.0, self._launch_monitor)
        else:
            content = (
                "  [bold #f05050]Setup did not complete cleanly.[/]\n\n"
                f"  Error: [dim]{detail[:200]}[/]\n\n"
                "  Common fixes:\n"
                "  [dim]→[/]  Make sure Docker Desktop is running\n"
                "  [dim]→[/]  Make sure Ollama is running  [dim](ollama serve)[/]\n"
                "  [dim]→[/]  Check available disk space  (~10 GB needed)\n\n"
                "  [dim]Re-run:  python hollow.py setup[/]"
            )

        self.query_one("#done-content", Static).update(content)
        self.query_one("#launch-note", Static).update("")


# ── Config / env writers ──────────────────────────────────────────────────────

def _write_config(model_id: str) -> None:
    if not CONFIG_EXAMPLE.exists():
        raise FileNotFoundError(f"config.example.json not found at {CONFIG_EXAMPLE}")

    config = json.loads(CONFIG_EXAMPLE.read_text())
    config["ollama"]["default_model"] = model_id

    # Generate a unique API token
    token = secrets.token_urlsafe(24)
    config["api"]["token"] = token

    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def _write_env(api_key: Optional[str]) -> None:
    lines = []

    # Claude Code credentials path if present
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if creds_path.exists():
        lines.append(f"CLAUDE_CREDENTIALS_FILE={creds_path}")
    else:
        lines.append("CLAUDE_CREDENTIALS_FILE=")

    if api_key and api_key != "CLAUDE_CODE" and api_key.startswith("sk-"):
        lines.append(f"ANTHROPIC_API_KEY={api_key}")

    ENV_PATH.write_text("\n".join(lines) + "\n")


def _ensure_dirs() -> None:
    dirs = [
        "memory", "workspace", "workspace/wrappers",
        "workspace/sandbox", "workspace/bin", "logs", "store/data",
    ]
    for d in dirs:
        (ROOT / d).mkdir(parents=True, exist_ok=True)


def _pull_model(model_id: str, progress_cb=None) -> None:
    """Pull an Ollama model, calling progress_cb(0.0-1.0) as it downloads."""
    proc = subprocess.Popen(
        ["ollama", "pull", model_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Ollama outputs progress lines — we just wait for completion
    if proc.stdout:
        for line in proc.stdout:
            # Rough progress estimation from "pulling" lines
            if "pulling" in line.lower() and progress_cb:
                progress_cb(0.5)
    proc.wait()


def _start_containers() -> tuple[bool, str]:
    """Run docker compose up -d. Returns (ok, error_message)."""
    try:
        # Try pre-built image first
        r = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if r.returncode == 0:
            return True, ""

        # If GPU error, retry without GPU block
        combined = (r.stdout + r.stderr)
        if "nvidia" in combined.lower() or "gpu" in combined.lower():
            compose_text = COMPOSE_FILE.read_text()
            import re
            patched = re.sub(
                r"(?s)\s*# GPU acceleration.*?capabilities: \[gpu\]\s*", "\n",
                compose_text,
            )
            tmp = ROOT / ".docker-compose-nogpu.yml"
            tmp.write_text(patched)
            try:
                r2 = subprocess.run(
                    ["docker", "compose", "-f", str(tmp), "up", "-d"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                return r2.returncode == 0, r2.stderr[:200]
            finally:
                tmp.unlink(missing_ok=True)

        return False, r.stderr[:300]

    except subprocess.TimeoutExpired:
        return False, "Timed out waiting for containers to start."
    except Exception as exc:
        return False, str(exc)


# ── Main app ──────────────────────────────────────────────────────────────────

class HollowSetupApp(App):
    CSS = HOLLOW_CSS
    TITLE = "Hollow AgentOS — Setup"
    BINDINGS = [Binding("q", "quit", "Quit")]

    def on_mount(self) -> None:
        self.push_screen(WelcomeScreen())
