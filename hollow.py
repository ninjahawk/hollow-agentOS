#!/usr/bin/env python3
"""
hollow — Hollow AgentOS setup and launcher.

Usage:
    python hollow.py          first-run setup or re-run if already configured
    python hollow.py setup    force re-run setup wizard
    python hollow.py logs     open the live agent monitor
    python hollow.py status   show current agent status
    python hollow.py stop     stop all containers
    python hollow.py help     show this message
"""

import sys
import os
import json
import subprocess
import shutil
import platform
import secrets
import time
import asyncio
from pathlib import Path

# ── Root directory (wherever hollow.py lives) ─────────────────────────────────
ROOT = Path(__file__).parent.resolve()
CONFIG_PATH = ROOT / "config.json"
CONFIG_EXAMPLE = ROOT / "config.example.json"
ENV_PATH = ROOT / ".env"
COMPOSE_FILE = ROOT / "docker-compose.yml"


# ── CLI dispatch ──────────────────────────────────────────────────────────────

def main():
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else ""

    if arg == "help":
        print(__doc__)
        return

    if arg in ("logs", "monitor"):
        os.chdir(ROOT)
        os.execv(sys.executable, [sys.executable, str(ROOT / "thoughts.py")])
        return

    if arg == "stop":
        _run_quiet(["docker", "compose", "stop"], cwd=ROOT)
        print("  Hollow stopped.")
        return

    if arg == "status":
        _show_status()
        return

    if arg in ("setup", "onboarding"):
        _run_setup()
        return

    # Default (no args): wizard if not configured, monitor if already running
    if not CONFIG_PATH.exists():
        _run_setup()
    else:
        # Config exists — go straight to the monitor
        os.chdir(ROOT)
        os.execv(sys.executable, [sys.executable, str(ROOT / "thoughts.py")])


def _run_quiet(cmd, cwd=None):
    try:
        subprocess.run(cmd, cwd=cwd, capture_output=True)
    except Exception:
        pass


def _show_status():
    """Quick status check without full TUI."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:7777/health", timeout=2) as r:
            data = json.loads(r.read())
            if data.get("ok"):
                print("  Hollow is running.  http://localhost:7777")
                print("  → hollow logs      watch agents live")
                print("  → hollow stop      stop containers")
                return
    except Exception:
        pass
    print("  Hollow is not running.")
    print("  → hollow setup     run setup wizard")


def _run_setup():
    try:
        from textual.app import App
    except ImportError:
        print("  Installing Textual...")
        subprocess.run([sys.executable, "-m", "pip", "install", "textual>=0.60.0", "-q"])

    from hollow_setup import HollowSetupApp
    app = HollowSetupApp()
    app.run()


if __name__ == "__main__":
    main()
