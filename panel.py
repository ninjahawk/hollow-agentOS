#!/usr/bin/env python3
"""
Hollow AgentOS Control Panel — pywebview-based GUI.

Native window with HTML/CSS UI. Run with `python panel.py` or double-click panel.bat.

Requires: pip install pywebview httpx
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
HOST_MSG_FILE = ROOT / "logs" / "host_message.txt"
WORKSPACE = ROOT / "workspace"
DYNAMIC_TOOLS = ROOT / "memory" / "dynamic_tools"
IDENTITY_DIR = ROOT / "memory" / "identity"
GOALS_DIR = ROOT / "memory" / "goals"

API_BASE = "http://localhost:7777"
CORE_AGENTS = ["scout", "analyst", "builder"]


def _token():
    try:
        return json.loads(CONFIG_PATH.read_text()).get("api", {}).get("token", "")
    except Exception:
        return ""


# ── Operations exposed to JS ─────────────────────────────────────────────────

class API:
    """All methods on this class are callable from JavaScript via
    window.pywebview.api.<method_name>(args). Each returns a string for the
    output log, or a boolean for state queries."""

    def __init__(self, window=None):
        self._window = window

    def _set_window(self, window):
        self._window = window

    # ── Health ───────────────────────────────────────────────────────────────
    def check_health(self):
        try:
            import httpx
            r = httpx.get(f"{API_BASE}/health", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    # ── System ───────────────────────────────────────────────────────────────
    def start(self):
        if (ROOT / "launch.bat").exists():
            subprocess.Popen(["cmd.exe", "/c", "start", "", str(ROOT / "launch.bat")],
                             cwd=str(ROOT), shell=False)
            return "Started launch.bat in new window."
        return "launch.bat not found."

    def stop(self):
        if (ROOT / "stop.bat").exists():
            subprocess.Popen(["cmd.exe", "/c", "start", "", str(ROOT / "stop.bat")],
                             cwd=str(ROOT), shell=False)
            return "Started stop.bat in new window."
        return "stop.bat not found."

    def open_monitor(self):
        thoughts = ROOT / "thoughts.py"
        if not thoughts.exists():
            return "thoughts.py not found."
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", "python", str(thoughts)],
            cwd=str(ROOT), shell=False,
        )
        return "Monitor opened in new window."

    def open_workspace(self):
        if WORKSPACE.exists():
            os.startfile(str(WORKSPACE))
            return f"Opened {WORKSPACE}"
        return f"{WORKSPACE} does not exist."

    # ── Status ───────────────────────────────────────────────────────────────
    def status(self):
        lines = []
        api_up = self.check_health()
        lines.append(f"API: {'reachable' if api_up else 'unreachable'}")
        lines.append("")
        for aid in CORE_AGENTS:
            try:
                p_path = IDENTITY_DIR / aid / "profile.json"
                s_path = IDENTITY_DIR / aid / "suffering.json"
                name = aid
                ops = qs = 0
                load = 0.0
                stressors = []
                if p_path.exists():
                    p = json.loads(p_path.read_text(encoding="utf-8"))
                    name = p.get("name", aid)
                    ops = len(p.get("opinions_list", []))
                    qs = len(p.get("open_questions", []))
                if s_path.exists():
                    s = json.loads(s_path.read_text(encoding="utf-8"))
                    active = [x for x in s.get("active_stressors", []) if not x.get("resolved")]
                    load = sum(x.get("severity", 0) for x in active)
                    stressors = [x.get("type", "") for x in active]
                api_status = "?"
                if api_up:
                    try:
                        import httpx
                        h = {"Authorization": f"Bearer {_token()}"}
                        r = httpx.get(f"{API_BASE}/agents/{aid}", headers=h, timeout=5)
                        if r.status_code == 200:
                            api_status = r.json().get("status", "?")
                    except Exception:
                        pass
                lines.append(f"{aid} ({name}):")
                lines.append(f"  status={api_status} | suffering={load:.2f} | ops={ops} | questions={qs}")
                if stressors:
                    lines.append(f"  stressors: {', '.join(stressors)}")
            except Exception as e:
                lines.append(f"{aid}: error — {e}")
        lines.append("")
        try:
            ws_count = sum(1 for _ in WORKSPACE.rglob("*") if _.is_file()) if WORKSPACE.exists() else 0
            dt_count = len(list(DYNAMIC_TOOLS.glob("*.py"))) if DYNAMIC_TOOLS.exists() else 0
            lines.append(f"Workspace files: {ws_count}  |  Dynamic tools: {dt_count}")
        except Exception:
            pass
        return "\n".join(lines)

    # ── Per-agent ────────────────────────────────────────────────────────────
    def suspend(self, agent_id):
        try:
            import httpx
            h = {"Authorization": f"Bearer {_token()}"}
            r = httpx.post(f"{API_BASE}/agents/{agent_id}/suspend", headers=h, timeout=10)
            if r.status_code == 200:
                return f"{agent_id} suspended (daemon will skip until resumed)."
            return f"suspend failed: HTTP {r.status_code}"
        except Exception as e:
            return f"suspend failed: {e}"

    def resume(self, agent_id):
        try:
            import httpx
            h = {"Authorization": f"Bearer {_token()}"}
            r = httpx.post(f"{API_BASE}/agents/{agent_id}/resume", headers=h, timeout=10)
            if r.status_code == 200:
                return f"{agent_id} resumed."
            return f"resume failed: HTTP {r.status_code}"
        except Exception as e:
            return f"resume failed: {e}"

    # ── Messaging ────────────────────────────────────────────────────────────
    def send_message(self, text):
        text = (text or "").strip()
        if not text:
            return "empty message — nothing sent."
        HOST_MSG_FILE.parent.mkdir(parents=True, exist_ok=True)
        HOST_MSG_FILE.write_text(text, encoding="utf-8")
        return f"message queued ({len(text)} chars). daemon delivers next cycle."

    # ── Confirm dialog ───────────────────────────────────────────────────────
    def confirm_dialog(self, title, message):
        """Show a native confirm dialog. Returns True if user clicked OK."""
        try:
            import webview
            if self._window:
                result = self._window.create_confirmation_dialog(title, message)
                return bool(result)
        except Exception:
            pass
        # Fallback: always proceed (UI should also have its own visual confirm)
        return True

    # ── Nuclear reset ────────────────────────────────────────────────────────
    def nuke(self):
        log = []

        # Workspace: keep agent dirs and standard subdirs, remove their contents
        if WORKSPACE.exists():
            for sub in WORKSPACE.iterdir():
                if sub.is_dir() and sub.name in CORE_AGENTS:
                    for f in sub.rglob("*"):
                        if f.is_file():
                            try: f.unlink()
                            except Exception: pass
                elif sub.is_dir() and sub.name not in {"agents", "repos", "wrappers"}:
                    try: shutil.rmtree(sub, ignore_errors=True)
                    except Exception: pass
                elif sub.is_file():
                    try: sub.unlink()
                    except Exception: pass
            log.append("Workspace wiped")

        # Dynamic tools
        if DYNAMIC_TOOLS.exists():
            for f in DYNAMIC_TOOLS.iterdir():
                if f.is_file() and f.suffix in (".py", ".json", ".pyc"):
                    try: f.unlink()
                    except Exception: pass
            pycache = DYNAMIC_TOOLS / "__pycache__"
            if pycache.exists():
                shutil.rmtree(pycache, ignore_errors=True)
            log.append("Dynamic tools cleared")

        # Broken tools list
        (ROOT / "memory" / "broken_tools.json").write_text(json.dumps({"broken": []}), encoding="utf-8")

        # Goals
        for aid in CORE_AGENTS:
            for fname in ("registry.jsonl", "last_outcome.txt", "index.json", "embeddings.npy"):
                try: (GOALS_DIR / aid / fname).unlink(missing_ok=True)
                except Exception: pass
        log.append("Goals cleared")

        # Autonomy chains, proposals, messages, checkpoints
        autonomy = ROOT / "memory" / "autonomy"
        if autonomy.exists():
            shutil.rmtree(autonomy, ignore_errors=True)
        prop_dir = ROOT / "memory" / "proposals"
        if prop_dir.exists():
            for f in prop_dir.glob("*.json"):
                try: f.unlink()
                except Exception: pass
        msgs_dir = ROOT / "memory" / "messages"
        if msgs_dir.exists():
            for sub in msgs_dir.iterdir():
                if sub.is_dir():
                    for f in sub.glob("inbox.jsonl"):
                        try: f.unlink()
                        except Exception: pass
        cp_dir = ROOT / "memory" / "checkpoints" / "root"
        if cp_dir.exists():
            for f in cp_dir.glob("*.json"):
                try: f.unlink()
                except Exception: pass

        # Claude queues
        for f in ("claude_requests.jsonl", "claude_responses.jsonl"):
            (ROOT / "memory" / f).write_text("", encoding="utf-8")

        # Audit baselines + semantic index
        (ROOT / "memory" / "audit-baselines.json").write_text("{}", encoding="utf-8")
        (ROOT / "memory" / "semantic-index.json").write_text("{}", encoding="utf-8")

        # Host message
        if HOST_MSG_FILE.exists():
            HOST_MSG_FILE.write_text("", encoding="utf-8")

        # Profiles + suffering — keep names, wipe everything else
        for aid in CORE_AGENTS:
            p_path = IDENTITY_DIR / aid / "profile.json"
            if p_path.exists():
                try:
                    p = json.loads(p_path.read_text(encoding="utf-8"))
                    p["narrative"] = ""
                    p["narrative_updated_at"] = time.time()
                    p["opinions"] = {}
                    p["opinions_list"] = []
                    p["open_questions"] = []
                    p["worldview"] = ""
                    p["capability_profile"] = {}
                    p_path.write_text(json.dumps(p, indent=2), encoding="utf-8")
                except Exception:
                    pass
            s_path = IDENTITY_DIR / aid / "suffering.json"
            s_path.write_text(json.dumps({
                "active_stressors": [],
                "resolved_history": [],
                "last_escalated": time.strftime("%Y-%m-%d %H:%M"),
            }, indent=2), encoding="utf-8")
        log.append("Profiles + suffering reset")

        return "\n".join(log) + "\n\nNuclear reset complete. Names preserved."


def main():
    try:
        import webview
    except ImportError:
        print("ERROR: pywebview not installed.")
        print("Install with: pip install pywebview")
        sys.exit(1)

    api = API()
    html_path = ROOT / "panel.html"
    if not html_path.exists():
        print(f"ERROR: {html_path} not found.")
        sys.exit(1)

    window = webview.create_window(
        "Hollow AgentOS",
        url=str(html_path),
        js_api=api,
        width=720,
        height=820,
        min_size=(640, 600),
        background_color="#1c1c1e",
        text_select=True,
    )
    api._set_window(window)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
