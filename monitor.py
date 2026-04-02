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

# narrator: dict of agent_id -> latest activity string, built from thoughts log
NARRATOR_LINES: dict = {}
NARRATOR_OFFSET = 0


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


ANSI_RE = re.compile(r"\033\[[0-9;]*m")

def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def _humanize(raw: str) -> str | None:
    """
    Convert a raw thoughts-log line to plain English, or return None to skip it.
    """
    line = _strip_ansi(raw).strip()
    if not line:
        return None

    # Extract timestamp and agent — format: "HH:MM:SS  agent_id  message"
    # or blank timestamp for continuation lines
    ts_m = re.match(r"^(\d{2}:\d{2}:\d{2})\s{2}(\S+)\s{2,}(.+)$", line)
    cont_m = re.match(r"^\s{6,}(\S+)\s{2,}(.+)$", line)  # continuation (no timestamp)

    if ts_m:
        ts, agent, rest = ts_m.group(1), ts_m.group(2), ts_m.group(3).strip()
    elif cont_m:
        ts, agent, rest = "", cont_m.group(1), cont_m.group(2).strip()
    else:
        return None

    # ── GOAL ──────────────────────────────────────────────────────────────
    if rest.startswith("◎"):
        goal = rest[1:].strip()[:90]
        short = _short_agent(agent)
        return f"[white]{ts or '      '}  {short}[/white]  [dim white]{goal}[/dim white]"

    # ── PLAN ──────────────────────────────────────────────────────────────
    if rest.startswith("↳"):
        # skip — too technical
        return None

    # ── step N: definitions ───────────────────────────────────────────────
    if re.match(r"step \d+:", rest):
        return None

    # ── RUN ───────────────────────────────────────────────────────────────
    if rest.startswith("▶"):
        parts = rest[1:].strip().split(None, 1)
        cap   = parts[0] if parts else ""
        param = parts[1].strip() if len(parts) > 1 else ""
        label = _cap_label(cap, param, "running")
        short = _short_agent(agent)
        return f"[dim]         {short}  {label}[/dim]"

    # ── OK ────────────────────────────────────────────────────────────────
    if rest.startswith("✓"):
        parts  = rest[1:].strip().split(None, 1)
        cap    = parts[0] if parts else ""
        result = parts[1].strip() if len(parts) > 1 else ""
        label  = _cap_result(cap, result)
        if label is None:
            return None
        short  = _short_agent(agent)
        return f"[dim]         {short}  [green]✓[/green] {label}[/dim]"

    # ── FAIL ──────────────────────────────────────────────────────────────
    if rest.startswith("✗"):
        parts = rest[1:].strip().split(None, 1)
        cap   = parts[0] if parts else "unknown"
        # pull first meaningful error word, skip tracebacks
        err_raw = parts[1].strip() if len(parts) > 1 else ""
        if "timed out" in err_raw:
            err = "timed out — will retry"
        elif "no prompt" in err_raw:
            err = "skipped (no input from previous step)"
        elif "no path" in err_raw or "no key" in err_raw:
            err = "skipped (missing params)"
        else:
            err = "failed — will retry"
        short = _short_agent(agent)
        return f"[dim]         {short}  [red]✗[/red] {_cap_name(cap)} {err}[/dim]"

    return None


def _short_agent(agent: str) -> str:
    parts = agent.split("-")
    if parts and re.match(r"^[0-9a-f]{6,}$", parts[-1]):
        parts = parts[:-1]
    name = "-".join(parts)[:14]
    return f"{name:<14}"


def _cap_name(cap: str) -> str:
    return {
        "semantic_search": "search",
        "ollama_chat":     "AI analysis",
        "memory_set":      "memory write",
        "memory_get":      "memory read",
        "fs_read":         "file read",
        "fs_write":        "file write",
        "shell_exec":      "shell command",
        "agent_message":   "agent message",
    }.get(cap, cap)


def _cap_label(cap: str, param: str, default: str) -> str:
    """Human label for a RUN line."""
    if cap == "semantic_search":
        q = re.search(r'"query"\s*:\s*"([^"]{3,60})"', param)
        return f'searching for "{q.group(1)}"' if q else "searching memory…"
    if cap == "shell_exec":
        cmd = re.search(r'"command"\s*:\s*"([^"]{1,50})"', param)
        return f'running: {cmd.group(1)}' if cmd else "running shell command…"
    if cap == "ollama_chat":
        return "thinking with AI…"
    if cap == "memory_set":
        k = re.search(r'"key"\s*:\s*"([^"]{1,40})"', param)
        return f'saving "{k.group(1)}"' if k else "saving to memory…"
    if cap == "memory_get":
        k = re.search(r'"key"\s*:\s*"([^"]{1,40})"', param)
        return f'reading "{k.group(1)}"' if k else "reading from memory…"
    if cap == "fs_read":
        p = re.search(r'"path"\s*:\s*"([^"]{1,50})"', param)
        return f'reading file {p.group(1).split("/")[-1]}' if p else "reading file…"
    if cap == "fs_write":
        p = re.search(r'"path"\s*:\s*"([^"]{1,50})"', param)
        return f'writing file {p.group(1).split("/")[-1]}' if p else "writing file…"
    return f"{_cap_name(cap)}…"


def _cap_result(cap: str, result: str) -> str | None:
    """Human label for an OK line, or None to skip."""
    if cap == "memory_set":
        return None  # not interesting to show
    if cap == "semantic_search":
        if not result or result.startswith("{") or len(result) < 8:
            return None
        return f'found: {result[:80]}'
    if cap == "shell_exec":
        # result is JSON — pull stdout
        try:
            d = json.loads(result)
            out = d.get("stdout", "").strip()
            if out:
                first = out.splitlines()[0][:80]
                return f'output: {first}'
        except Exception:
            pass
        return None
    if cap == "ollama_chat":
        # strip leading JSON if present
        clean = result.lstrip("{").strip()
        if clean.startswith('"response"'):
            m = re.search(r'"response"\s*:\s*"([^"]{10,})"', result)
            if m:
                return m.group(1)[:80]
        if len(result) > 15 and not result.startswith("{"):
            return result[:80]
        return None
    if cap in ("fs_read", "memory_get"):
        if result and not result.startswith("{") and len(result) > 5:
            return result[:80]
        return None
    if len(result) > 5 and not result.startswith("{"):
        return result[:80]
    return None


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


def _update_narrator(lines: list[str]) -> None:
    """Update per-agent status from new thought lines."""
    global NARRATOR_LINES
    for raw in lines:
        line = _strip_ansi(raw).strip()
        # GOAL line — update agent's current objective
        m = re.match(r"^(\d{2}:\d{2}:\d{2})\s{2}(\S+)\s{2,}◎\s+(.+)$", line)
        if m:
            agent = m.group(2)
            goal  = m.group(3).strip()[:70]
            parts = agent.split("-")
            if parts and re.match(r"^[0-9a-f]{6,}$", parts[-1]):
                parts = parts[:-1]
            name = "-".join(parts)
            NARRATOR_LINES[name] = goal


def _narrator_summary() -> str:
    if not NARRATOR_LINES:
        if AGENT_STATE:
            return f"{len(AGENT_STATE)} agents running. Waiting for first cycle…"
        return "waiting for agents…"
    lines = []
    done = sum(1 for s in AGENT_STATE.values() if s["progress"] >= 1.0)
    active = sum(1 for s in AGENT_STATE.values() if 0 < s["progress"] < 1.0)
    lines.append(f"{len(AGENT_STATE)} agents  ·  {done} complete  ·  {active} working")
    for name, goal in list(NARRATOR_LINES.items())[-6:]:
        lines.append(f"  {name}: {goal}")
    return "\n".join(lines)


def _stats_line() -> str:
    total  = len(AGENT_STATE)
    done   = sum(1 for s in AGENT_STATE.values() if s["progress"] >= 1.0)
    active = sum(1 for s in AGENT_STATE.values() if 0 < s["progress"] < 1.0)
    idle   = total - done - active
    return f"{total} agents  ·  {done} done  ·  {active} active  ·  {idle} idle"


# ── CSS ─────────────────────────────────────────────────────────────────────
CSS = """
Screen {
    background: #0e0e0e;
    color: #cccccc;
}

#hollow-title {
    height: 1;
    background: #0e0e0e;
    color: #7c7cf5;
    text-style: bold;
    padding: 0 2;
    border-bottom: solid #1c1c1c;
}

#main {
    height: 1fr;
}

#left {
    width: 26;
    border-right: solid #1c1c1c;
    padding: 1 1;
    background: #0e0e0e;
}

.sec {
    color: #333333;
    text-style: bold;
    margin-bottom: 1;
}

#agent-list {
    height: 1fr;
    margin-bottom: 1;
}

#divider {
    color: #1c1c1c;
    margin-bottom: 1;
}

#narrator-box {
    height: auto;
    max-height: 10;
    color: #555555;
}

#right {
    padding: 1 1;
    background: #0e0e0e;
}

#thoughts-title {
    color: #333333;
    text-style: bold;
    margin-bottom: 1;
}

#thoughts-log {
    height: 1fr;
    background: #0e0e0e;
    scrollbar-size: 1 1;
    scrollbar-color: #1c1c1c #0e0e0e;
}

#footer-bar {
    height: 1;
    background: #0e0e0e;
    color: #252525;
    padding: 0 2;
    border-top: solid #1c1c1c;
}
"""


# ── Widgets ──────────────────────────────────────────────────────────────────
class TitleBar(Static):
    def on_mount(self) -> None:
        self.update(self._build())
        self.set_interval(3.0, lambda: self.update(self._build()))

    def _build(self) -> str:
        total = len(AGENT_STATE)
        return (
            f"[bold #7c7cf5]HOLLOW[/bold #7c7cf5]"
            f"  [dim]·[/dim]  [dim]{MODEL}[/dim]"
            f"  [dim]·[/dim]  cycle [white]{CYCLE}[/white]"
            f"  [dim]·[/dim]  [dim]{total} agents[/dim]"
            f"  [dim]·[/dim]  [dim]q quit[/dim]"
        )


class AgentList(Static):
    def on_mount(self) -> None:
        self.update(self._build())
        self.set_interval(2.0, lambda: self.update(self._build()))

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
            name = "-".join(parts)[:15]
            pct = "done" if prog >= 1.0 else f"{prog:.0%}"
            if prog >= 1.0:
                out.append(f"[green]{name:<15}  {pct}[/green]")
            elif prog > 0.5:
                out.append(f"[white]{name:<15}[/white]  [dim cyan]{pct}[/dim cyan]")
            elif prog > 0:
                out.append(f"[dim]{name:<15}  {pct}[/dim]")
            else:
                out.append(f"[dim]{name:<15}   —[/dim]")
        return "\n".join(out)


class NarratorBox(Static):
    def on_mount(self) -> None:
        self.update(self._build())
        self.set_interval(2.0, lambda: self.update(self._build()))

    def _build(self) -> str:
        return f"[dim]{_narrator_summary()}[/dim]"


class ThoughtsLog(ScrollableContainer):
    def compose(self) -> ComposeResult:
        yield RichLog(highlight=False, auto_scroll=True, max_lines=800, id="log")

    def on_mount(self) -> None:
        self.set_interval(0.5, self._poll)

    def _poll(self) -> None:
        raw_lines = _read_new_thoughts()
        if not raw_lines:
            return
        _update_narrator(raw_lines)
        log = self.query_one("#log", RichLog)
        for raw in raw_lines:
            human = _humanize(raw)
            if human:
                log.write(Text.from_ansi(human) if "\033" in human else Text.from_markup(human))


# ── App ─────────────────────────────────────────────────────────────────────
class HollowMonitor(App):
    CSS = CSS
    BINDINGS = [("q", "quit", "quit")]

    def compose(self) -> ComposeResult:
        yield TitleBar(id="hollow-title")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("AGENTS", classes="sec")
                yield AgentList(id="agent-list")
                yield Static("─" * 22, id="divider")
                yield Static("WHAT'S HAPPENING", classes="sec")
                yield NarratorBox(id="narrator-box")
            with Vertical(id="right"):
                yield Static("ACTIVITY", id="thoughts-title")
                yield ThoughtsLog(id="thoughts-log")
        yield Static(
            "[dim]  HOLLOW  ·  autonomous agent runtime[/dim]",
            id="footer-bar",
        )

    def on_mount(self) -> None:
        _load_config()
        _parse_daemon_log()
        self.set_interval(2.0, _parse_daemon_log)


if __name__ == "__main__":
    HollowMonitor().run()
