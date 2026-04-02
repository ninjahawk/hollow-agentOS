#!/usr/bin/env python3
"""
hollowOS live monitor
Usage: wsl -e python3 /agentOS/monitor.py
"""

import re
import json
import time
import threading
from pathlib import Path
from collections import deque

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Log, Label
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive

# ── Paths ───────────────────────────────────────────────────────────────────
DAEMON_LOG   = Path("/agentOS/logs/daemon.log")
THOUGHTS_LOG = Path("/agentOS/logs/thoughts.log")
CONFIG_PATH  = Path("/agentOS/config.json")

# ── Shared state ────────────────────────────────────────────────────────────
THOUGHTS_OFFSET = 0
AGENT_STATE: dict = {}
CYCLE = reactive(0)
MODEL = "unknown"


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
            if THOUGHTS_OFFSET == 0 and size > 6000:
                THOUGHTS_OFFSET = max(0, size - 6000)
            f.seek(THOUGHTS_OFFSET)
            raw = f.read()
            THOUGHTS_OFFSET = size
        return raw.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return []


def _build_summary() -> str:
    """Generate a plain-text summary of current system state."""
    if not AGENT_STATE:
        return "waiting for agents…"

    total   = len(AGENT_STATE)
    done    = sum(1 for s in AGENT_STATE.values() if s["progress"] >= 1.0)
    active  = sum(1 for s in AGENT_STATE.values() if 0 < s["progress"] < 1.0)
    stalled = total - done - active

    lines = []
    lines.append(f"{total} agents  ·  {done} done  ·  {active} running  ·  {stalled} idle")

    # top movers
    in_progress = [
        (aid, s) for aid, s in AGENT_STATE.items()
        if 0 < s["progress"] < 1.0
    ]
    in_progress.sort(key=lambda x: -x[1]["progress"])
    if in_progress:
        top = in_progress[:3]
        parts = [f"{aid.split('-')[0]} {s['progress']:.0%}" for aid, s in top]
        lines.append("leading:  " + "   ".join(parts))

    if done:
        completed = [aid for aid, s in AGENT_STATE.items() if s["progress"] >= 1.0]
        lines.append("done:  " + "  ".join(c.split("-")[0] for c in completed[:4]))

    return "\n".join(lines)


# ── CSS ─────────────────────────────────────────────────────────────────────
CSS = """
Screen {
    background: #111111;
    color: #cccccc;
}

#header-bar {
    height: 1;
    background: #111111;
    color: #555555;
    padding: 0 2;
    border-bottom: solid #222222;
}

#main {
    height: 1fr;
}

#left {
    width: 26;
    border-right: solid #222222;
    padding: 1 2;
    background: #111111;
}

#section-label {
    color: #444444;
    text-style: bold;
    margin-bottom: 1;
}

#agent-list {
    height: 1fr;
}

#summary-divider {
    color: #222222;
    margin-top: 1;
    margin-bottom: 1;
}

#summary-text {
    color: #555555;
    height: auto;
}

#right {
    padding: 1 2;
    background: #111111;
}

#thoughts-label {
    color: #444444;
    text-style: bold;
    margin-bottom: 1;
}

#thoughts {
    height: 1fr;
    background: #111111;
    scrollbar-size: 1 1;
    scrollbar-color: #222222 #111111;
}

#footer-bar {
    height: 1;
    background: #111111;
    color: #333333;
    padding: 0 2;
    border-top: solid #222222;
}
"""


# ── Widgets ──────────────────────────────────────────────────────────────────
class AgentList(Static):
    def on_mount(self) -> None:
        self.set_interval(1.5, self._refresh)

    def _refresh(self) -> None:
        self.update(self._render())

    def _render(self) -> str:
        if not AGENT_STATE:
            return "[dim]waiting…[/dim]"

        agents = sorted(AGENT_STATE.items(), key=lambda x: -x[1]["progress"])
        out = []
        for agent_id, state in agents:
            prog = state["progress"]

            # shorten name: strip long hex suffixes
            parts = agent_id.split("-")
            # drop last part if it looks like a hex suffix (6+ hex chars)
            if parts and re.match(r"^[0-9a-f]{6,}$", parts[-1]):
                parts = parts[:-1]
            name = "-".join(parts)
            if len(name) > 16:
                name = name[:16]

            pct = f"{prog:.0%}"

            if prog >= 1.0:
                out.append(f"[green]{name:<16}  {pct:>4}[/green]")
            elif prog > 0.5:
                out.append(f"[white]{name:<16}[/white]  [cyan]{pct:>4}[/cyan]")
            elif prog > 0:
                out.append(f"[dim white]{name:<16}  {pct:>4}[/dim white]")
            else:
                out.append(f"[dim]{name:<16}   —[/dim]")

        return "\n".join(out)

    def render(self) -> str:
        return self._render()


class SummaryText(Static):
    def on_mount(self) -> None:
        self.set_interval(3.0, self._refresh)

    def _refresh(self) -> None:
        self.update(f"[dim]{_build_summary()}[/dim]")

    def render(self) -> str:
        return f"[dim]{_build_summary()}[/dim]"


class HeaderBar(Static):
    def on_mount(self) -> None:
        self.set_interval(2.0, self._refresh)

    def _refresh(self) -> None:
        self.update(self._render())

    def _render(self) -> str:
        total = len(AGENT_STATE)
        return (
            f"[bold #7c7cf5]hollowOS[/bold #7c7cf5]"
            f"  [dim]·[/dim]  {MODEL}"
            f"  [dim]·[/dim]  cycle [white]{CYCLE}[/white]"
            f"  [dim]·[/dim]  [dim]{total} agents[/dim]"
            f"  [dim]·[/dim]  [dim]q  quit[/dim]"
        )

    def render(self) -> str:
        return self._render()


class ThoughtsPane(ScrollableContainer):
    def compose(self) -> ComposeResult:
        yield Log(highlight=False, auto_scroll=True, max_lines=600, id="log")

    def on_mount(self) -> None:
        self.set_interval(0.4, self._poll)

    def _poll(self) -> None:
        lines = _read_new_thoughts()
        log = self.query_one("#log", Log)
        for line in lines:
            if line.strip():
                log.write_line(line)


# ── App ───────────────────────────────────────────────────────────────────────
class HollowMonitor(App):
    CSS = CSS
    BINDINGS = [("q", "quit", "quit")]

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header-bar")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("AGENTS", id="section-label")
                yield AgentList(id="agent-list")
                yield Static("─" * 22, id="summary-divider")
                yield Static("SUMMARY", id="section-label")
                yield SummaryText(id="summary-text")
            with Vertical(id="right"):
                yield Static("THOUGHTS", id="thoughts-label")
                yield ThoughtsPane(id="thoughts")
        yield Static(
            "[dim]  hollowOS autonomous runtime  ·  agents think, you watch[/dim]",
            id="footer-bar",
        )

    def on_mount(self) -> None:
        _load_config()
        _parse_daemon_log()
        self.set_interval(2.0, self._poll_daemon)

    def _poll_daemon(self) -> None:
        _parse_daemon_log()


if __name__ == "__main__":
    HollowMonitor().run()
