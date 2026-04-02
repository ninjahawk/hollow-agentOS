#!/usr/bin/env python3
"""
hollowOS live monitor
Usage: wsl -e python3 /agentOS/monitor.py
"""

import re
import json
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Static, RichLog
from textual.containers import Horizontal, Vertical, ScrollableContainer

# ── Paths ───────────────────────────────────────────────────────────────────
DAEMON_LOG   = Path("/agentOS/logs/daemon.log")
THOUGHTS_LOG = Path("/agentOS/logs/thoughts.log")
CONFIG_PATH  = Path("/agentOS/config.json")

# ── Shared state ────────────────────────────────────────────────────────────
THOUGHTS_OFFSET = 0
AGENT_STATE: dict = {}
CYCLE = 0
MODEL = "unknown"
NARRATOR_TEXT = "narrator starting…"


def _load_config():
    global MODEL
    try:
        MODEL = json.loads(CONFIG_PATH.read_text())["ollama"]["default_model"]
    except Exception:
        pass


def _parse_daemon_log():
    global CYCLE
    if not DAEMON_LOG.exists():
        return
    try:
        lines = DAEMON_LOG.read_text(errors="replace").splitlines()[-400:]
    except Exception:
        return
    for line in lines:
        m = re.search(r"Cycle (\d+):", line)
        if m:
            CYCLE = int(m.group(1))
        m = re.search(
            r"INFO\s+(\S+) → goal=(\S+) progress=([\d.]+) steps=(\d+)", line
        )
        if m:
            agent_id, goal_id, progress, steps = m.groups()
            AGENT_STATE[agent_id] = {
                "progress": float(progress),
                "goal": goal_id,
                "steps": int(steps),
            }


def _read_new_thoughts() -> list[str]:
    global THOUGHTS_OFFSET
    if not THOUGHTS_LOG.exists():
        return []
    try:
        with open(THOUGHTS_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if THOUGHTS_OFFSET == 0 and size > 8000:
                THOUGHTS_OFFSET = max(0, size - 8000)
            f.seek(THOUGHTS_OFFSET)
            raw = f.read()
            THOUGHTS_OFFSET = size
        return raw.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return []


def _poll_narrator():
    """
    Generate a narrator summary directly via Ollama — no agent queue needed.
    Reads recent project memory keys and asks the model to write 2-3 plain
    English sentences describing what the system is doing.
    Runs in a background thread so it never blocks the TUI.
    """
    global NARRATOR_TEXT
    import threading

    def _run():
        global NARRATOR_TEXT
        try:
            # 1. Read project memory for context
            cfg = json.loads(CONFIG_PATH.read_text())
            token = cfg["api"]["token"]
            model = cfg.get("ollama", {}).get("default_model", "mistral-nemo:12b")
            import urllib.request, urllib.error
            req = urllib.request.Request(
                "http://localhost:7777/memory/project",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                mem = json.loads(resp.read())

            # Pick a handful of interesting keys (skip test keys and nulls)
            skip = {"updated_at", "test_key"}
            snippets = []
            for k, v in mem.items():
                if k.startswith("test-") or k in skip:
                    continue
                val = str(v).strip()
                if len(val) < 5 or val in ("null", "None", "{}", "[]"):
                    continue
                snippets.append(f"{k}: {val[:120]}")
                if len(snippets) >= 8:
                    break

            if not snippets:
                return

            context = "\n".join(snippets)
            total  = len(AGENT_STATE)
            done   = sum(1 for s in AGENT_STATE.values() if s["progress"] >= 1.0)
            active = sum(1 for s in AGENT_STATE.values() if 0 < s["progress"] < 1.0)

            prompt = (
                f"You are narrating a live AI agent system to a non-technical observer.\n"
                f"System stats: {total} agents running, {done} completed goals, {active} actively working.\n"
                f"Recent results from agent memory:\n{context}\n\n"
                f"Write exactly 2-3 plain English sentences describing what these agents have "
                f"accomplished and what they are currently working on. Be specific. No jargon. "
                f"Do not mention JSON, keys, or code. Respond with only the sentences, nothing else."
            )

            # 2. Call Ollama directly
            payload = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,
            }).encode()
            req2 = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req2, timeout=45) as resp2:
                result = json.loads(resp2.read())
            text = result.get("response", "").strip()
            if text and len(text) > 20:
                NARRATOR_TEXT = text

        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _stats_line() -> str:
    total   = len(AGENT_STATE)
    done    = sum(1 for s in AGENT_STATE.values() if s["progress"] >= 1.0)
    active  = sum(1 for s in AGENT_STATE.values() if 0 < s["progress"] < 1.0)
    idle    = total - done - active
    return f"{total} agents  ·  {done} done  ·  {active} active  ·  {idle} idle"


# ── CSS ─────────────────────────────────────────────────────────────────────
CSS = """
Screen {
    background: #111111;
    color: #cccccc;
}

#header-bar {
    height: 1;
    background: #111111;
    color: #444444;
    padding: 0 2;
    border-bottom: solid #1e1e1e;
}

#main {
    height: 1fr;
}

#left {
    width: 28;
    border-right: solid #1e1e1e;
    padding: 1 2;
    background: #111111;
}

.section-title {
    color: #3a3a3a;
    text-style: bold;
    margin-bottom: 1;
}

#agent-list {
    height: 1fr;
    margin-bottom: 1;
}

#divider {
    color: #1e1e1e;
    margin-bottom: 1;
}

#stats-line {
    color: #2e2e2e;
    margin-bottom: 1;
}

#narrator-box {
    height: auto;
    max-height: 8;
    color: #666666;
}

#right {
    padding: 1 2;
    background: #111111;
}

#thoughts-title {
    color: #3a3a3a;
    text-style: bold;
    margin-bottom: 1;
}

#thoughts-log {
    height: 1fr;
    background: #111111;
    scrollbar-size: 1 1;
    scrollbar-color: #1e1e1e #111111;
}

#footer-bar {
    height: 1;
    background: #111111;
    color: #2a2a2a;
    padding: 0 2;
    border-top: solid #1e1e1e;
}
"""


# ── Widgets ───────────────────────────────────────────────────────────────────
class HeaderBar(Static):
    def on_mount(self) -> None:
        self.update(self._build())
        self.set_interval(2.0, lambda: self.update(self._build()))

    def _build(self) -> str:
        return (
            f"[bold #7c7cf5]hollowOS[/bold #7c7cf5]"
            f"  [dim]·[/dim]  [dim]{MODEL}[/dim]"
            f"  [dim]·[/dim]  cycle [white]{CYCLE}[/white]"
            f"  [dim]·[/dim]  [dim]{len(AGENT_STATE)} agents[/dim]"
            f"  [dim]·[/dim]  [dim]q quit[/dim]"
        )


class AgentList(Static):
    def on_mount(self) -> None:
        self.update(self._build())
        self.set_interval(1.5, lambda: self.update(self._build()))

    def _build(self) -> str:
        if not AGENT_STATE:
            return "[dim]waiting…[/dim]"
        agents = sorted(AGENT_STATE.items(), key=lambda x: -x[1]["progress"])
        out = []
        for agent_id, state in agents:
            prog = state["progress"]
            parts = agent_id.split("-")
            if parts and re.match(r"^[0-9a-f]{6,}$", parts[-1]):
                parts = parts[:-1]
            name = "-".join(parts)[:16]
            pct = "done" if prog >= 1.0 else f"{prog:.0%}"
            if prog >= 1.0:
                out.append(f"[green]{name:<16}  {pct}[/green]")
            elif prog > 0.5:
                out.append(f"[white]{name:<16}[/white]  [dim cyan]{pct}[/dim cyan]")
            elif prog > 0:
                out.append(f"[dim]{name:<16}  {pct}[/dim]")
            else:
                out.append(f"[dim]{name:<16}    —[/dim]")
        return "\n".join(out)


class StatsLine(Static):
    def on_mount(self) -> None:
        self.update(self._build())
        self.set_interval(2.0, lambda: self.update(self._build()))

    def _build(self) -> str:
        return f"[dim]{_stats_line()}[/dim]"


class NarratorBox(Static):
    def on_mount(self) -> None:
        self.update(self._build())
        self.set_interval(5.0, lambda: self.update(self._build()))

    def _build(self) -> str:
        return f"[dim]{NARRATOR_TEXT}[/dim]"


class ThoughtsLog(ScrollableContainer):
    def compose(self) -> ComposeResult:
        yield RichLog(highlight=False, auto_scroll=True, max_lines=800, id="log")

    def on_mount(self) -> None:
        self.set_interval(0.4, self._poll)

    def _poll(self) -> None:
        lines = _read_new_thoughts()
        if not lines:
            return
        log = self.query_one("#log", RichLog)
        for line in lines:
            if line.strip():
                log.write(Text.from_ansi(line))


# ── App ────────────────────────────────────────────────────────────────────
class HollowMonitor(App):
    CSS = CSS
    BINDINGS = [("q", "quit", "quit")]

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header-bar")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("AGENTS", classes="section-title")
                yield AgentList(id="agent-list")
                yield Static("─" * 24, id="divider")
                yield StatsLine(id="stats-line")
                yield Static("NARRATOR", classes="section-title")
                yield NarratorBox(id="narrator-box")
            with Vertical(id="right"):
                yield Static("THOUGHTS", id="thoughts-title")
                yield ThoughtsLog(id="thoughts-log")
        yield Static(
            "[dim]  autonomous agent runtime  ·  hollowOS[/dim]",
            id="footer-bar",
        )

    def on_mount(self) -> None:
        _load_config()
        _parse_daemon_log()
        _poll_narrator()                        # fire immediately in background
        self.set_interval(2.0, _parse_daemon_log)
        self.set_interval(30.0, _poll_narrator) # refresh narrator every 30s


if __name__ == "__main__":
    HollowMonitor().run()
