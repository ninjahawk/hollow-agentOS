#!/usr/bin/env python3
"""
hollowOS live monitor — watch agents think in real time.

Usage:
  python3 monitor.py              # inside container
  wsl -e python3 /agentOS/monitor.py
"""

import re
import json
import time
from pathlib import Path
from collections import deque, defaultdict

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Log, DataTable
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual import work
from rich.text import Text
from rich.table import Table
from rich import box

# ── Paths ──────────────────────────────────────────────────────────────────
DAEMON_LOG   = Path("/agentOS/logs/daemon.log")
THOUGHTS_LOG = Path("/agentOS/logs/thoughts.log")
CONFIG_PATH  = Path("/agentOS/config.json")

# ── State ──────────────────────────────────────────────────────────────────
THOUGHTS_OFFSET = 0   # byte offset so we only read new lines
AGENT_STATE: dict = {}
CYCLE = 0
MODEL = "unknown"


def _load_config():
    global MODEL
    try:
        MODEL = json.loads(CONFIG_PATH.read_text())["ollama"]["default_model"]
    except Exception:
        pass


def _parse_daemon_log():
    global CYCLE, AGENT_STATE
    if not DAEMON_LOG.exists():
        return
    try:
        lines = DAEMON_LOG.read_text(errors="replace").splitlines()[-300:]
    except Exception:
        return
    seen_this_cycle: dict = {}
    for line in lines:
        m = re.search(r"Cycle (\d+):", line)
        if m:
            CYCLE = int(m.group(1))
        m = re.search(
            r"INFO\s+(\S+) → goal=(\S+) progress=([\d.]+) steps=(\d+)", line
        )
        if m:
            agent_id, goal_id, progress, steps = m.groups()
            seen_this_cycle[agent_id] = {
                "progress": float(progress),
                "goal": goal_id,
                "steps": int(steps),
                "last_seen": time.time(),
            }
    if seen_this_cycle:
        AGENT_STATE.update(seen_this_cycle)


def _read_new_thoughts() -> list[str]:
    """Return new lines from thoughts.log since last read."""
    global THOUGHTS_OFFSET
    if not THOUGHTS_LOG.exists():
        return []
    try:
        with open(THOUGHTS_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if THOUGHTS_OFFSET == 0 and size > 4000:
                # On first open, start from last 4KB
                THOUGHTS_OFFSET = max(0, size - 4000)
            f.seek(THOUGHTS_OFFSET)
            raw = f.read()
            THOUGHTS_OFFSET = size
        return raw.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return []


# ── CSS ────────────────────────────────────────────────────────────────────
CSS = """
Screen {
    background: #0d0d0d;
}

#title-bar {
    height: 1;
    background: #1a1a2e;
    color: #7c85f5;
    text-align: center;
    text-style: bold;
    padding: 0 2;
}

#main {
    height: 1fr;
}

#left-panel {
    width: 34;
    border: solid #2a2a3e;
    background: #0f0f1a;
    padding: 0 1;
}

#left-title {
    height: 1;
    color: #5a5a8a;
    text-style: bold;
    padding: 0 0 1 0;
    text-align: center;
}

#agent-table {
    height: 1fr;
    overflow-y: auto;
}

#right-panel {
    border: solid #2a2a3e;
    background: #080810;
    padding: 0 1;
}

#right-title {
    height: 1;
    color: #5a5a8a;
    text-style: bold;
    padding: 0 0 1 0;
}

#thoughts-log {
    height: 1fr;
    scrollbar-size: 1 1;
    scrollbar-color: #2a2a3e;
    background: #080810;
}

#status-bar {
    height: 1;
    background: #1a1a2e;
    color: #5a5a8a;
    padding: 0 2;
}
"""


# ── Widgets ────────────────────────────────────────────────────────────────
class AgentTable(Static):
    """Renders the agent list with progress bars."""

    def render_agents(self) -> Text:
        lines = Text()
        agents = sorted(
            AGENT_STATE.items(), key=lambda x: -x[1]["progress"]
        )
        if not agents:
            lines.append("  waiting for agents…\n", style="dim")
            return lines

        for agent_id, state in agents:
            prog = state["progress"]
            filled = int(prog * 12)
            empty  = 12 - filled

            # Name — strip common prefix, keep last 16 chars
            name = agent_id
            if len(name) > 18:
                name = name[-18:]

            # Color by progress
            if prog >= 1.0:
                name_style = "bold green"
                bar_style  = "green"
                pct_style  = "green"
            elif prog > 0.5:
                name_style = "bold cyan"
                bar_style  = "cyan"
                pct_style  = "cyan"
            elif prog > 0:
                name_style = "white"
                bar_style  = "yellow"
                pct_style  = "yellow"
            else:
                name_style = "dim"
                bar_style  = "dim"
                pct_style  = "dim"

            bar = Text()
            bar.append("█" * filled, style=bar_style)
            bar.append("░" * empty,  style="dim")

            line = Text()
            line.append(f" {'●' if prog > 0 else '○'} ", style=bar_style)
            line.append(f"{name:<18}", style=name_style)
            line.append("  ")
            line.append(bar)
            line.append(f"  {prog:>4.0%}\n", style=pct_style)
            lines.append_text(line)

        return lines

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh_table)

    def refresh_table(self) -> None:
        self.update(self.render_agents())

    def render(self):
        return self.render_agents()


class StatusBar(Static):
    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh_status)

    def refresh_status(self) -> None:
        self.update(self.render_status())

    def render_status(self) -> Text:
        active = sum(1 for s in AGENT_STATE.values() if s["progress"] > 0)
        done   = sum(1 for s in AGENT_STATE.values() if s["progress"] >= 1.0)
        total  = len(AGENT_STATE)
        t = Text()
        t.append(f" ◉ hollowOS", style="bold #7c85f5")
        t.append(f"  │  ", style="dim")
        t.append(f"cycle {CYCLE}", style="white")
        t.append(f"  │  ", style="dim")
        t.append(f"{total} agents", style="cyan")
        t.append(f"  │  ", style="dim")
        t.append(f"{done} complete  {active} active", style="green" if done else "white")
        t.append(f"  │  ", style="dim")
        t.append(f"model: {MODEL}", style="#7c85f5")
        t.append(f"  │  ", style="dim")
        t.append(f"heartbeat: 6s", style="dim")
        return t

    def render(self):
        return self.render_status()


class ThoughtsLog(ScrollableContainer):
    """Scrollable thoughts pane — auto-scrolls to bottom."""

    def on_mount(self) -> None:
        self._log = Log(highlight=False, markup=False, auto_scroll=True)
        self.mount(self._log)
        self.set_interval(0.5, self.poll_thoughts)

    def poll_thoughts(self) -> None:
        lines = _read_new_thoughts()
        for line in lines:
            if line.strip():
                self._log.write_line(line)


# ── App ────────────────────────────────────────────────────────────────────
class HollowMonitor(App):
    CSS = CSS
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("c", "clear_thoughts", "Clear"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(
            " ◉  h o l l o w O S  —  autonomous agent runtime ",
            id="title-bar",
        )
        with Horizontal(id="main"):
            with Vertical(id="left-panel"):
                yield Static(" AGENTS", id="left-title")
                yield AgentTable(id="agent-table")
            with Vertical(id="right-panel"):
                yield Static(" LIVE THOUGHTS", id="right-title")
                yield ThoughtsLog(id="thoughts-log")
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        _load_config()
        self.set_interval(2.0, self.poll_daemon)

    def poll_daemon(self) -> None:
        _parse_daemon_log()

    def action_clear_thoughts(self) -> None:
        log_widget = self.query_one("#thoughts-log ThoughtsLog Log")
        try:
            log_widget.clear()
        except Exception:
            pass


if __name__ == "__main__":
    _load_config()
    _parse_daemon_log()
    app = HollowMonitor()
    app.run()
