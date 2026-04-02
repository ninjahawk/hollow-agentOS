#!/usr/bin/env python3
"""
HOLLOW live monitor
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

DAEMON_LOG   = Path("/agentOS/logs/daemon.log")
THOUGHTS_LOG = Path("/agentOS/logs/thoughts.log")
CONFIG_PATH  = Path("/agentOS/config.json")

THOUGHTS_OFFSET = 0
AGENT_STATE: dict = {}
CYCLE = 0
MODEL = "unknown"
AGENT_GOALS: dict = {}       # agent -> current goal text
AGENT_LAST_ACTION: dict = {} # agent -> (action_str, timestamp)

ANSI_RE = re.compile(r"\033\[[0-9;]*m")

def _strip(s): return ANSI_RE.sub("", s).strip()

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
        m = re.search(r"INFO\s+(\S+) → goal=\S+ progress=([\d.]+) steps=(\d+)", line)
        if m:
            AGENT_STATE[m.group(1)] = {
                "progress": float(m.group(2)),
                "steps": int(m.group(3)),
            }

def _short(agent_id: str) -> str:
    parts = agent_id.split("-")
    if parts and re.match(r"^[0-9a-f]{6,}$", parts[-1]):
        parts = parts[:-1]
    return "-".join(parts)[:14]

def _cap_verb(cap: str, param: str) -> tuple[str, str]:
    """Returns (human phrase, style) for a capability + params."""
    if cap == "semantic_search":
        q = re.search(r'"query"\s*:\s*"([^"]{3,})"', param)
        phrase = f'digging through memory for "{q.group(1)[:55]}"' if q else "searching memory…"
        return phrase, "cyan"
    if cap == "shell_exec":
        cmd = re.search(r'"command"\s*:\s*"([^"]{1,60})"', param)
        phrase = f'running → {cmd.group(1)}' if cmd else "running a shell command…"
        return phrase, "yellow"
    if cap == "ollama_chat":
        return "thinking it over…", "magenta"
    if cap == "memory_set":
        k = re.search(r'"key"\s*:\s*"([^"]{1,40})"', param)
        phrase = f'filing away "{k.group(1)}"' if k else "saving to memory…"
        return phrase, "dim"
    if cap == "memory_get":
        k = re.search(r'"key"\s*:\s*"([^"]{1,40})"', param)
        phrase = f'recalling "{k.group(1)}"' if k else "reading from memory…"
        return phrase, "dim"
    if cap == "fs_read":
        p = re.search(r'"path"\s*:\s*"([^"]{1,60})"', param)
        phrase = f'reading {p.group(1).split("/")[-1]}' if p else "reading a file…"
        return phrase, "yellow"
    if cap == "fs_write":
        p = re.search(r'"path"\s*:\s*"([^"]{1,60})"', param)
        phrase = f'writing {p.group(1).split("/")[-1]}' if p else "writing a file…"
        return phrase, "yellow"
    return f"{cap}…", "dim"

def _cap_result(cap: str, result: str) -> str | None:
    """Human-readable result string, or None to skip."""
    if cap == "memory_set":
        return None
    if cap == "semantic_search":
        c = result.strip()
        if not c or c.startswith("{") or len(c) < 8:
            return None
        return c[:80]
    if cap == "shell_exec":
        try:
            d = json.loads(result)
            out = d.get("stdout", "").strip()
            if out:
                return out.splitlines()[0][:80]
        except Exception:
            pass
        return None
    if cap == "ollama_chat":
        c = result.strip()
        try:
            d = json.loads(c)
            c = d.get("response", c)
        except Exception:
            pass
        c = c.strip().strip('"')
        if len(c) > 15 and not c.startswith("{"):
            return c[:85]
        return None
    if cap in ("fs_read", "memory_get"):
        c = result.strip()
        if c and not c.startswith("{") and len(c) > 5:
            return c[:80]
    return None

def _humanize(raw: str) -> tuple[str, str] | None:
    """Returns (markup_string, plain_for_alignment) or None to skip."""
    line = _strip(raw)
    if not line:
        return None

    # timestamp + agent + message
    m = re.match(r"^(\d{2}:\d{2}:\d{2})\s{2}(\S+)\s{2,}(.+)$", line)
    cont = re.match(r"^\s{6,}(\S+)\s{2,}(.+)$", line)

    if m:
        ts, agent, rest = m.group(1), m.group(2), m.group(3).strip()
    elif cont:
        ts, agent, rest = "", cont.group(1), cont.group(2).strip()
    else:
        return None

    name = _short(agent)
    ts_s = ts or "        "

    # ── GOAL ─────────────────────────────────────────────────────────────
    if rest.startswith("◎"):
        goal = rest[1:].strip()[:85]
        AGENT_GOALS[name] = goal
        markup = (
            f"[dim]{ts_s}[/dim]  [bold white]{name:<14}[/bold white]"
            f"  [white]{goal}[/white]"
        )
        return markup, "goal"

    # skip step definitions and plan lines
    if rest.startswith("↳") or re.match(r"step \d+:", rest):
        return None

    # ── RUN ──────────────────────────────────────────────────────────────
    if rest.startswith("▶"):
        parts = rest[1:].strip().split(None, 1)
        cap   = parts[0] if parts else ""
        param = parts[1] if len(parts) > 1 else ""
        phrase, style = _cap_verb(cap, param)
        AGENT_LAST_ACTION[name] = (phrase, time.time())
        markup = (
            f"[dim]        [/dim]  [dim]{name:<14}[/dim]"
            f"  [dim]└[/dim]  [{style}]{phrase}[/{style}]"
        )
        return markup, "run"

    # ── OK ───────────────────────────────────────────────────────────────
    if rest.startswith("✓"):
        parts  = rest[1:].strip().split(None, 1)
        cap    = parts[0] if parts else ""
        result = parts[1] if len(parts) > 1 else ""
        res    = _cap_result(cap, result)
        if res is None:
            # silent success — don't clutter
            return None
        markup = (
            f"[dim]        [/dim]  [dim]{name:<14}[/dim]"
            f"  [dim]└[/dim]  [green]got it:[/green]  [dim]{res}[/dim]"
        )
        return markup, "ok"

    # ── FAIL ─────────────────────────────────────────────────────────────
    if rest.startswith("✗"):
        parts = rest[1:].strip().split(None, 1)
        cap   = parts[0] if parts else ""
        err   = parts[1].strip() if len(parts) > 1 else ""
        if "timed out" in err:
            msg = "that took too long — moving on"
        elif "no prompt" in err:
            msg = "nothing to work with yet — will try again"
        elif "no path" in err or "no key" in err:
            msg = "couldn't find what it needed — retrying"
        else:
            msg = "hit a snag — will retry next cycle"
        markup = (
            f"[dim]        [/dim]  [dim]{name:<14}[/dim]"
            f"  [dim]└[/dim]  [dim red]{msg}[/dim red]"
        )
        return markup, "fail"

    return None

def _read_new_thoughts() -> list[str]:
    global THOUGHTS_OFFSET
    if not THOUGHTS_LOG.exists():
        return []
    try:
        with open(THOUGHTS_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if THOUGHTS_OFFSET == 0 and size > 10000:
                THOUGHTS_OFFSET = max(0, size - 10000)
            f.seek(THOUGHTS_OFFSET)
            raw = f.read()
            THOUGHTS_OFFSET = size
        return raw.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return []

def _narrator_text() -> str:
    done   = sum(1 for s in AGENT_STATE.values() if s["progress"] >= 1.0)
    active = sum(1 for s in AGENT_STATE.values() if 0 < s["progress"] < 1.0)
    total  = len(AGENT_STATE)
    if not total:
        return "waiting for agents…"
    lines = [f"{total} agents  ·  {done} complete  ·  {active} working\n"]
    shown = 0
    for name, goal in list(AGENT_GOALS.items())[-7:]:
        lines.append(f"[white]{name}[/white]")
        lines.append(f"[dim]  {goal[:60]}[/dim]")
        shown += 1
    if not shown:
        lines.append("[dim]waiting for first cycle…[/dim]")
    return "\n".join(lines)


# ── CSS ──────────────────────────────────────────────────────────────────────
CSS = """
Screen { background: #0a0a0a; color: #c0c0c0; }

#title {
    height: 1;
    background: #0a0a0a;
    padding: 0 2;
    border-bottom: solid #1a1a1a;
}

#main { height: 1fr; }

#left {
    width: 24;
    border-right: solid #1a1a1a;
    padding: 1 1;
    background: #0a0a0a;
}

.sec { color: #2e2e2e; text-style: bold; margin-bottom: 1; }

#agent-list   { height: 1fr; margin-bottom: 1; }
#divider      { color: #1a1a1a; margin-bottom: 1; }
#narrator-box { height: auto; max-height: 18; }

#right        { padding: 1 1; background: #0a0a0a; }
#act-title    { color: #2e2e2e; text-style: bold; margin-bottom: 1; }

#act-log {
    height: 1fr;
    background: #0a0a0a;
    scrollbar-size: 1 1;
    scrollbar-color: #1a1a1a #0a0a0a;
}

#footer {
    height: 1;
    background: #0a0a0a;
    color: #1e1e1e;
    padding: 0 2;
    border-top: solid #1a1a1a;
}
"""

# ── Widgets ───────────────────────────────────────────────────────────────────
class TitleBar(Static):
    def on_mount(self):
        self._tick()
        self.set_interval(3.0, self._tick)
    def _tick(self):
        total = len(AGENT_STATE)
        self.update(
            f"[bold #7c7cf5]HOLLOW[/bold #7c7cf5]"
            f"  [dim]·[/dim]  [dim]{MODEL}[/dim]"
            f"  [dim]·[/dim]  cycle [white]{CYCLE}[/white]"
            f"  [dim]·[/dim]  [dim]{total} agents[/dim]"
            f"  [dim]·  q quit[/dim]"
        )

class AgentList(Static):
    def on_mount(self):
        self._tick()
        self.set_interval(2.0, self._tick)
    def _tick(self):
        if not AGENT_STATE:
            self.update("[dim]waiting…[/dim]")
            return
        agents = sorted(AGENT_STATE.items(), key=lambda x: -x[1]["progress"])
        out = []
        for aid, s in agents:
            prog = s["progress"]
            name = _short(aid)
            pct  = "done" if prog >= 1.0 else f"{prog:.0%}"
            if prog >= 1.0:
                out.append(f"[green]{name:<15} {pct}[/green]")
            elif prog > 0.5:
                out.append(f"[white]{name:<15}[/white] [dim cyan]{pct}[/dim cyan]")
            elif prog > 0:
                out.append(f"[dim]{name:<15} {pct}[/dim]")
            else:
                out.append(f"[dim]{name:<15}  —[/dim]")
        self.update("\n".join(out))

class NarratorBox(Static):
    def on_mount(self):
        self._tick()
        self.set_interval(2.0, self._tick)
    def _tick(self):
        self.update(_narrator_text())

class ActivityLog(ScrollableContainer):
    def compose(self):
        yield RichLog(highlight=False, auto_scroll=True, max_lines=1000, id="log")
    def on_mount(self):
        self.set_interval(0.5, self._poll)
    def _poll(self):
        raw_lines = _read_new_thoughts()
        if not raw_lines:
            return
        log = self.query_one("#log", RichLog)
        for raw in raw_lines:
            result = _humanize(raw)
            if result:
                markup, _ = result
                try:
                    log.write(Text.from_markup(markup))
                except Exception:
                    pass

# ── App ───────────────────────────────────────────────────────────────────────
class HollowMonitor(App):
    CSS = CSS
    BINDINGS = [("q", "quit", "quit")]

    def compose(self):
        yield TitleBar(id="title")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("AGENTS", classes="sec")
                yield AgentList(id="agent-list")
                yield Static("─" * 20, id="divider")
                yield Static("WHAT'S HAPPENING", classes="sec")
                yield NarratorBox(id="narrator-box")
            with Vertical(id="right"):
                yield Static("ACTIVITY", id="act-title")
                yield ActivityLog(id="act-log")
        yield Static("[dim]  HOLLOW  ·  autonomous agent runtime[/dim]", id="footer")

    def on_mount(self):
        _load_config()
        _parse_daemon_log()
        self.set_interval(2.0, _parse_daemon_log)

if __name__ == "__main__":
    HollowMonitor().run()
