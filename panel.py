#!/usr/bin/env python3
"""
Hollow AgentOS Control Panel — single-file GUI for the operator.

Run with: python panel.py
Or via:   panel.bat (double-click)

Buttons for: start/stop, status, nuclear reset, host messages,
agent suspend/resume, open monitor, view workspace.

This is the operator interface — it does not run inside the container.
It manipulates files in the project directory and calls the API on
localhost:7777 when available.
"""

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
HOST_MSG_FILE = ROOT / "logs" / "host_message.txt"
THOUGHTS_LOG = ROOT / "logs" / "thoughts.log"
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


def _api_running():
    """Quick check — is the daemon reachable?"""
    try:
        import httpx
        r = httpx.get(f"{API_BASE}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _api_post(path, body=None):
    try:
        import httpx
        headers = {"Authorization": f"Bearer {_token()}"}
        r = httpx.post(f"{API_BASE}{path}", json=body or {}, headers=headers, timeout=10)
        return r.status_code, (r.json() if r.content else {})
    except Exception as e:
        return 0, {"error": str(e)}


def _api_get(path):
    try:
        import httpx
        headers = {"Authorization": f"Bearer {_token()}"}
        r = httpx.get(f"{API_BASE}{path}", headers=headers, timeout=10)
        return r.status_code, (r.json() if r.content else {})
    except Exception as e:
        return 0, {"error": str(e)}


# ── Operations ───────────────────────────────────────────────────────────────

def op_start():
    """Run launch.bat (Windows) or detect platform."""
    if (ROOT / "launch.bat").exists():
        subprocess.Popen(["cmd.exe", "/c", "start", "", str(ROOT / "launch.bat")],
                         cwd=str(ROOT), shell=False)
        return "Started launch.bat in new window"
    return "launch.bat not found"


def op_stop():
    if (ROOT / "stop.bat").exists():
        subprocess.Popen(["cmd.exe", "/c", "start", "", str(ROOT / "stop.bat")],
                         cwd=str(ROOT), shell=False)
        return "Started stop.bat in new window"
    return "stop.bat not found"


def op_open_monitor():
    """Launch python thoughts.py in a new console."""
    thoughts = ROOT / "thoughts.py"
    if not thoughts.exists():
        return "thoughts.py not found"
    subprocess.Popen(
        ["cmd.exe", "/c", "start", "", "python", str(thoughts)],
        cwd=str(ROOT), shell=False,
    )
    return "Monitor opened in new window"


def op_open_workspace():
    """Open workspace folder in File Explorer."""
    if WORKSPACE.exists():
        os.startfile(str(WORKSPACE))
        return f"Opened {WORKSPACE}"
    return f"{WORKSPACE} does not exist"


def op_send_message(text):
    """Write to host_message.txt — daemon picks up next cycle."""
    text = text.strip()
    if not text:
        return "Empty message — nothing sent"
    HOST_MSG_FILE.parent.mkdir(parents=True, exist_ok=True)
    HOST_MSG_FILE.write_text(text, encoding="utf-8")
    return f"Message queued ({len(text)} chars). Daemon will deliver next cycle."


def op_suspend_agent(agent_id):
    code, body = _api_post(f"/agents/{agent_id}/suspend")
    if code == 200:
        return f"{agent_id} suspended (daemon will skip until resumed)"
    return f"suspend failed: {body.get('error', body)}"


def op_resume_agent(agent_id):
    code, body = _api_post(f"/agents/{agent_id}/resume")
    if code == 200:
        return f"{agent_id} resumed"
    return f"resume failed: {body.get('error', body)}"


def op_status():
    """Compose multi-line status report."""
    lines = []
    lines.append(f"API reachable: {'YES' if _api_running() else 'NO (daemon stopped)'}")
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
            if _api_running():
                _, body = _api_get(f"/agents/{aid}")
                api_status = body.get("status", "?")
            lines.append(f"{aid} ({name}):")
            lines.append(f"  status={api_status} | suffering={load:.2f} | ops={ops} | questions={qs}")
            if stressors:
                lines.append(f"  stressors: {', '.join(stressors)}")
        except Exception as e:
            lines.append(f"{aid}: error reading state — {e}")
    lines.append("")
    try:
        ws_count = sum(1 for _ in WORKSPACE.rglob("*") if _.is_file()) if WORKSPACE.exists() else 0
        dt_count = len(list(DYNAMIC_TOOLS.glob("*.py"))) if DYNAMIC_TOOLS.exists() else 0
        lines.append(f"Workspace files: {ws_count}")
        lines.append(f"Dynamic tools: {dt_count}")
    except Exception:
        pass
    return "\n".join(lines)


def op_nuclear_reset():
    """Full wipe — workspace, dynamic tools, profiles, suffering, goals.
    Names preserved. Does NOT touch the daemon — call op_stop first if running."""
    log = []

    # Workspace
    if WORKSPACE.exists():
        for sub in WORKSPACE.iterdir():
            if sub.is_dir() and sub.name in CORE_AGENTS:
                for f in sub.rglob("*"):
                    if f.is_file():
                        try:
                            f.unlink()
                        except Exception:
                            pass
            elif sub.is_dir() and sub.name not in {"agents", "repos", "wrappers"}:
                # Non-standard subdir created by agents — remove
                try:
                    shutil.rmtree(sub, ignore_errors=True)
                except Exception:
                    pass
            elif sub.is_file():
                try:
                    sub.unlink()
                except Exception:
                    pass
        log.append("Workspace wiped")

    # Dynamic tools
    if DYNAMIC_TOOLS.exists():
        for f in DYNAMIC_TOOLS.iterdir():
            if f.is_file() and (f.suffix in (".py", ".json", ".pyc")):
                try:
                    f.unlink()
                except Exception:
                    pass
        pycache = DYNAMIC_TOOLS / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache, ignore_errors=True)
        log.append("Dynamic tools cleared")

    # Broken tools list
    bt = ROOT / "memory" / "broken_tools.json"
    bt.write_text(json.dumps({"broken": []}), encoding="utf-8")

    # Goals
    for aid in CORE_AGENTS:
        for fname in ("registry.jsonl", "last_outcome.txt", "index.json", "embeddings.npy"):
            try:
                (GOALS_DIR / aid / fname).unlink(missing_ok=True)
            except Exception:
                pass
    log.append("Goals cleared")

    # Autonomy chains
    autonomy = ROOT / "memory" / "autonomy"
    if autonomy.exists():
        shutil.rmtree(autonomy, ignore_errors=True)

    # Proposals, messages, checkpoints
    for sub in (ROOT / "memory" / "proposals").glob("*.json") if (ROOT / "memory" / "proposals").exists() else []:
        try:
            sub.unlink()
        except Exception:
            pass
    msgs_dir = ROOT / "memory" / "messages"
    if msgs_dir.exists():
        for sub in msgs_dir.iterdir():
            if sub.is_dir():
                for f in sub.glob("inbox.jsonl"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
    cp_dir = ROOT / "memory" / "checkpoints" / "root"
    if cp_dir.exists():
        for f in cp_dir.glob("*.json"):
            try:
                f.unlink()
            except Exception:
                pass

    # Claude queues
    for f in ("claude_requests.jsonl", "claude_responses.jsonl"):
        (ROOT / "memory" / f).write_text("", encoding="utf-8")

    # Audit + semantic index
    (ROOT / "memory" / "audit-baselines.json").write_text("{}", encoding="utf-8")
    (ROOT / "memory" / "semantic-index.json").write_text("{}", encoding="utf-8")

    # Host message
    if HOST_MSG_FILE.exists():
        HOST_MSG_FILE.write_text("", encoding="utf-8")

    # Profiles + suffering — keep names, wipe rest
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


# ── GUI ──────────────────────────────────────────────────────────────────────

class Panel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Hollow AgentOS Control")
        self.geometry("700x720")
        self.configure(padx=12, pady=12)

        # Status bar at top — colored dot + text
        top = tk.Frame(self)
        top.pack(fill=tk.X)
        self.status_dot = tk.Label(top, text="●", font=("Segoe UI", 16), fg="gray")
        self.status_dot.pack(side=tk.LEFT)
        self.status_text = tk.Label(top, text="checking…", font=("Segoe UI", 10))
        self.status_text.pack(side=tk.LEFT, padx=6)
        self._refresh_health_async()

        ttk.Separator(self).pack(fill=tk.X, pady=8)

        # System control row
        row = tk.Frame(self)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text="System:", width=12, anchor="w").pack(side=tk.LEFT)
        ttk.Button(row, text="Start", command=self._do(op_start, refresh_after=True)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="Stop", command=self._do(op_stop, refresh_after=True)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="Status", command=self._do(op_status)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="Open Monitor", command=self._do(op_open_monitor)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="Workspace…", command=self._do(op_open_workspace)).pack(side=tk.LEFT, padx=2)

        # Send message
        ttk.Separator(self).pack(fill=tk.X, pady=8)
        msg_frame = tk.Frame(self)
        msg_frame.pack(fill=tk.X, pady=4)
        tk.Label(msg_frame, text="Send host message to all agents:", anchor="w").pack(fill=tk.X)
        self.msg_entry = scrolledtext.ScrolledText(msg_frame, height=4, font=("Segoe UI", 10))
        self.msg_entry.pack(fill=tk.X, pady=4)
        ttk.Button(msg_frame, text="Send Message", command=self._send_msg).pack(anchor="e")

        # Per-agent control
        ttk.Separator(self).pack(fill=tk.X, pady=8)
        agent_frame = tk.Frame(self)
        agent_frame.pack(fill=tk.X, pady=4)
        tk.Label(agent_frame, text="Per-agent control:", anchor="w").pack(fill=tk.X)
        for aid in CORE_AGENTS:
            row = tk.Frame(agent_frame)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=aid, width=12, anchor="w").pack(side=tk.LEFT)
            ttk.Button(row, text="Suspend",
                       command=self._do_with_arg(op_suspend_agent, aid)).pack(side=tk.LEFT, padx=2)
            ttk.Button(row, text="Resume",
                       command=self._do_with_arg(op_resume_agent, aid)).pack(side=tk.LEFT, padx=2)

        # Destructive operation
        ttk.Separator(self).pack(fill=tk.X, pady=8)
        nuke_frame = tk.Frame(self)
        nuke_frame.pack(fill=tk.X, pady=4)
        tk.Label(nuke_frame, text="Destructive:", width=12, anchor="w").pack(side=tk.LEFT)
        nuke_btn = tk.Button(
            nuke_frame, text="NUCLEAR RESET",
            bg="#cc3333", fg="white", font=("Segoe UI", 9, "bold"),
            command=self._nuke,
        )
        nuke_btn.pack(side=tk.LEFT, padx=2)
        tk.Label(
            nuke_frame, text="(wipes workspace, tools, profiles, suffering, goals — keeps names)",
            font=("Segoe UI", 8), fg="gray",
        ).pack(side=tk.LEFT, padx=8)

        # Output log
        ttk.Separator(self).pack(fill=tk.X, pady=8)
        tk.Label(self, text="Output:", anchor="w").pack(fill=tk.X)
        self.output = scrolledtext.ScrolledText(self, height=18, font=("Consolas", 9), wrap=tk.WORD)
        self.output.pack(fill=tk.BOTH, expand=True)
        self._log("Panel ready.")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.output.insert(tk.END, f"[{ts}] {msg}\n\n")
        self.output.see(tk.END)

    def _do(self, fn, refresh_after=False):
        """Wrap an operation to run in background thread and log result."""
        def wrapped():
            def run():
                try:
                    result = fn()
                    self.after(0, lambda: self._log(result))
                except Exception as e:
                    self.after(0, lambda: self._log(f"ERROR: {e}"))
                if refresh_after:
                    self.after(2000, self._refresh_health_async)
            threading.Thread(target=run, daemon=True).start()
        return wrapped

    def _do_with_arg(self, fn, arg):
        def wrapped():
            def run():
                try:
                    result = fn(arg)
                    self.after(0, lambda: self._log(result))
                except Exception as e:
                    self.after(0, lambda: self._log(f"ERROR: {e}"))
            threading.Thread(target=run, daemon=True).start()
        return wrapped

    def _send_msg(self):
        text = self.msg_entry.get("1.0", tk.END).strip()
        if not text:
            self._log("(empty message)")
            return
        result = op_send_message(text)
        self._log(result)
        self.msg_entry.delete("1.0", tk.END)

    def _nuke(self):
        if not messagebox.askyesno(
            "Nuclear Reset",
            "Wipe workspace, dynamic tools, profiles, suffering, goals?\n"
            "Names will be preserved.\n\n"
            "STOP the daemon first if running. Continuing while running may produce inconsistent state.\n\n"
            "Continue?",
        ):
            return
        def run():
            try:
                result = op_nuclear_reset()
                self.after(0, lambda: self._log(result))
            except Exception as e:
                self.after(0, lambda: self._log(f"ERROR: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def _refresh_health_async(self):
        def run():
            up = _api_running()
            self.after(0, lambda: self._set_health(up))
        threading.Thread(target=run, daemon=True).start()
        self.after(5000, self._refresh_health_async)  # poll every 5s

    def _set_health(self, up):
        if up:
            self.status_dot.config(fg="#2da44e")
            self.status_text.config(text="API reachable — daemon running")
        else:
            self.status_dot.config(fg="#cc3333")
            self.status_text.config(text="API unreachable — daemon stopped")


if __name__ == "__main__":
    app = Panel()
    app.mainloop()
