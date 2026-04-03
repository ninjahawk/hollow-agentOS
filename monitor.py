#!/usr/bin/env python3
"""
HOLLOW live monitor
Usage: wsl hollow
"""

import re
import json
import time
import threading
import httpx
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Static, RichLog, Input, Label
from textual.containers import Horizontal, Vertical, ScrollableContainer

DAEMON_LOG   = Path("/agentOS/logs/daemon.log")
THOUGHTS_LOG = Path("/agentOS/logs/thoughts.log")
CONFIG_PATH  = Path("/agentOS/config.json")
API_BASE     = "http://localhost:7777"

THOUGHTS_OFFSET = 0
AGENT_STATE: dict = {}
CYCLE = 0
MODEL = "unknown"
ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# ── Fun agent names ───────────────────────────────────────────────────────────
_NAMES = [
    "Bob", "Alice", "Holly", "Bucky", "Turbo", "Chaos", "Sparky",
    "Mango", "Rex", "Pickle", "Toast", "Waffles", "Noodle", "Blaze",
    "Cosmo", "Rusty", "Duke", "Skip", "Penny", "Ziggy", "Fudge",
    "Beans", "Gizmo", "Clunk", "Wobble",
]

_name_cache: dict[str, str] = {}

def _fun_name(agent_id: str) -> str:
    if agent_id not in _name_cache:
        h = sum(ord(c) * (i + 1) for i, c in enumerate(agent_id)) % len(_NAMES)
        _name_cache[agent_id] = _NAMES[h]
    return _name_cache[agent_id]


# ── Rotating phrase banks ─────────────────────────────────────────────────────
_SEARCH = [
    'digging through memory for "{q}"',
    'rummaging around for "{q}"',
    'hunting down "{q}"',
    'on the trail of "{q}"',
    'sifting the archives for "{q}"',
    'desperately seeking "{q}"',
    'got a hunch about "{q}"',
    'sniffing around for "{q}"',
    'asking the void about "{q}"',
    'sending a search party for "{q}"',
]
_THINK = [
    "consulting the oracle…",
    "staring into the void…",
    "having a big think…",
    "asking the big brain nicely…",
    "grinding through it…",
    "chewing on this one…",
    "running it through the noodle…",
    "definitely not napping…",
    "processing. very hard.",
    "doing smart things, probably…",
    "summoning forbidden knowledge…",
    "arguing with itself internally…",
    "let me think… no wait… ok yes…",
    "pretending to be smarter than it is…",
    "this might take a moment. or several.",
    "the gears are turning. slowly.",
    "cooking up something. hopefully edible.",
]
_THINK_OK = [
    "had some thoughts about this",
    "the oracle has spoken",
    "big brain moment achieved",
    "finished pondering",
    "thoughts: acquired",
    "nailed it, allegedly",
    "came to a conclusion. a real one.",
    "brain did a thing",
    "output obtained. quality unknown.",
    "the noodle delivered",
]
_SHELL_OK = [
    "reviewed more lines of code",
    "poked the filesystem",
    "the shell complied, for once",
    "command survived",
    "asked the computer, got an answer",
    "computer said yes",
    "more code inspected. still no treasure.",
    "filesystem poked. it didn't poke back.",
    "ran that. it worked. shocking.",
    "shell: surprisingly cooperative today",
]
_SAVE = [
    "filed that away",
    "committing this to memory",
    "scribbling notes…",
    "saving, just in case",
    "tucking that into the brain",
    "noted. very officially.",
    "wrote that down so i won't forget",
    "lodged in the memory banks",
]
_FAIL_TIMEOUT = [
    "took way too long — moving on",
    "the oracle fell asleep",
    "timed out. classic.",
    "still waiting… just kidding, giving up",
    "that was optimistic of me",
    "patience: expired",
    "waited forever. got nothing.",
    "the brain ghosted me",
    "it's not you, it's the timeout",
    "officially giving up on that one",
]
_FAIL_GENERIC = [
    "hit a snag — retrying next round",
    "well that didn't work",
    "error: skill issue (temporary)",
    "crashed gracefully",
    "didn't pan out — onto the next thing",
    "the void stared back",
    "spectacular failure, 0/10",
    "that went sideways",
    "nope. absolutely not. moving on.",
    "filed under: problems for future me",
    "blew up. noted for later.",
    "tried that. computers said no.",
]
_GOAL_VERBS = [
    "setting out to", "on a mission to", "determined to",
    "boldly attempting to", "going to try to", "enthusiastically starting to",
    "absolutely certain it can", "taking a crack at",
    "charging headfirst into", "not afraid to",
]

_counters: dict[str, int] = {}

def _pick(lst: list, key: str) -> str:
    n = _counters.get(key, 0)
    _counters[key] = n + 1
    return lst[n % len(lst)]


# ── Core logic ────────────────────────────────────────────────────────────────
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


# ── Goal API helpers ──────────────────────────────────────────────────────────
def _post_goal(agent_id: str, objective: str, priority: int = 8) -> bool:
    try:
        r = httpx.post(
            f"{API_BASE}/goals/{agent_id}",
            json={"objective": objective, "priority": priority},
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False

def _set_individual_goal(agent_id: str, objective: str) -> str:
    ok = _post_goal(agent_id, objective)
    name = _fun_name(agent_id)
    return f"✓ {name} has a new goal" if ok else f"✗ failed to reach API"

def _set_group_goal(objective: str) -> str:
    agents = sorted(AGENT_STATE.keys())
    if not agents:
        return "✗ no agents found"
    # First agent coordinates; rest get the same goal so they all work on it
    coordinator = agents[0]
    coord_obj = (
        f"SHARED GOAL — coordinate with all agents to accomplish: {objective}. "
        f"Decompose into subtasks, delegate, and track progress."
    )
    _post_goal(coordinator, coord_obj, priority=10)
    for aid in agents[1:]:
        _post_goal(aid, objective, priority=9)
    names = [_fun_name(a) for a in agents[:4]]
    extra = f" + {len(agents)-4} more" if len(agents) > 4 else ""
    return f"✓ assigned to {', '.join(names)}{extra}"


def _humanize(raw: str) -> str | None:
    line = _strip(raw)
    if not line:
        return None

    m = re.match(r"^(\d{2}:\d{2}:\d{2})\s{2}(\S+)\s{2,}(.+)$", line)
    c = re.match(r"^\s{6,}(\S+)\s{2,}(.+)$", line)

    if m:
        ts, agent_id, rest = m.group(1), m.group(2), m.group(3).strip()
    elif c:
        ts, agent_id, rest = "", c.group(1), c.group(2).strip()
    else:
        return None

    name = _fun_name(agent_id)
    ts_s = ts if ts else "        "

    if rest.startswith("↳") or re.match(r"step \d+:", rest):
        return None

    if rest.startswith("◎"):
        goal = rest[1:].strip()
        verb = _pick(_GOAL_VERBS, f"verb-{name}")
        return (
            f"[dim]{ts_s}[/dim]  [bold white]{name:<10}[/bold white]"
            f"  [white]{verb} {goal[:70]}[/white]"
        )

    if rest.startswith("▶"):
        parts = rest[1:].strip().split(None, 1)
        cap   = parts[0] if parts else ""
        param = parts[1] if len(parts) > 1 else ""
        phrase, style = _run_phrase(cap, param, name)
        return f"[dim]                    [/dim]  [dim]└[/dim]  [{style}]{phrase}[/{style}]"

    if rest.startswith("✓"):
        parts  = rest[1:].strip().split(None, 1)
        cap    = parts[0] if parts else ""
        result = parts[1] if len(parts) > 1 else ""
        line_  = _ok_phrase(cap, result, name)
        if line_ is None:
            return None
        phrase, style = line_
        return f"[dim]                    [/dim]  [dim]└[/dim]  [green]✓[/green]  [{style}]{phrase}[/{style}]"

    if rest.startswith("✗"):
        parts = rest[1:].strip().split(None, 1)
        err   = parts[1].strip() if len(parts) > 1 else ""
        msg = _pick(_FAIL_TIMEOUT, f"timeout-{name}") if "timed out" in err else _pick(_FAIL_GENERIC, f"fail-{name}")
        return f"[dim]                    [/dim]  [dim]└  [red]{msg}[/red][/dim]"

    return None


def _run_phrase(cap: str, param: str, name: str) -> tuple[str, str]:
    if cap == "semantic_search":
        q = re.search(r'"query"\s*:\s*"([^"]{3,})"', param)
        qstr = q.group(1)[:50] if q else "something"
        return _pick(_SEARCH, f"search-{name}").replace("{q}", qstr), "cyan"
    if cap == "shell_exec":
        cmd = re.search(r'"command"\s*:\s*"([^"]{1,55})"', param)
        return (f"running → {cmd.group(1)}" if cmd else "running a shell command…"), "yellow"
    if cap == "ollama_chat":
        return _pick(_THINK, f"think-{name}"), "magenta"
    if cap == "memory_set":
        k = re.search(r'"key"\s*:\s*"([^"]{1,35})"', param)
        return (_pick(_SAVE, f"save-{name}") + (f' — "{k.group(1)}"' if k else "")), "dim"
    if cap == "memory_get":
        k = re.search(r'"key"\s*:\s*"([^"]{1,35})"', param)
        return (f'recalling "{k.group(1)}"' if k else "digging through memory…"), "dim"
    if cap == "fs_read":
        p = re.search(r'"path"\s*:\s*"([^"]{1,60})"', param)
        fname = p.group(1).split("/")[-1] if p else "a file"
        return f"reading {fname}", "yellow"
    if cap == "fs_write":
        p = re.search(r'"path"\s*:\s*"([^"]{1,60})"', param)
        fname = p.group(1).split("/")[-1] if p else "a file"
        return f"writing {fname}", "yellow"
    return f"{cap}…", "dim"


def _ok_phrase(cap: str, result: str, name: str) -> tuple[str, str] | None:
    if cap == "memory_set":
        return None
    if cap == "semantic_search":
        c = result.strip()
        if not c or c.startswith("{") or len(c) < 8:
            return _pick(_SHELL_OK, f"searchok-{name}"), "dim"
        lines = [l.strip() for l in c.splitlines() if l.strip()]
        return f'found: {lines[0][:75] if lines else c[:75]}', "dim white"
    if cap == "shell_exec":
        try:
            d = json.loads(result)
            out = d.get("stdout", "").strip()
            if out:
                comment = _pick(_SHELL_OK, f"shellok-{name}")
                return f"{comment} → {out.splitlines()[0][:75]}", "dim white"
        except Exception:
            pass
        return None
    if cap == "ollama_chat":
        c = result.strip()
        try:
            c = json.loads(c).get("response", c)
        except Exception:
            pass
        c = c.strip().strip('"')
        if len(c) > 20 and not c.startswith("{"):
            return f'{_pick(_THINK_OK, f"thinkdone-{name}")} — "{c[:70]}"', "dim white"
        return _pick(_THINK_OK, f"thinkdone-{name}"), "dim"
    if cap in ("fs_read", "memory_get"):
        c = result.strip()
        if c and not c.startswith("{") and len(c) > 5:
            return f"got: {c[:75]}", "dim white"
    return None


# ── CSS ───────────────────────────────────────────────────────────────────────
CSS = """
Screen { background: #0a0a0a; color: #aaaaaa; }

#title {
    height: 3;
    background: #0a0a0a;
    padding: 1 2;
    border-bottom: solid #1c1c1c;
    content-align: left middle;
}

#main { height: 1fr; }

#left {
    width: 22;
    border-right: solid #1c1c1c;
    padding: 1 1;
    background: #0a0a0a;
}

.sec { color: #333333; text-style: bold; margin-bottom: 1; }
#agent-list { height: 1fr; }

#agent-stats {
    height: auto;
    padding: 1 0 0 0;
    border-top: solid #1c1c1c;
    color: #444444;
}

#right { padding: 1 1; background: #0a0a0a; }
#act-title { color: #333333; text-style: bold; margin-bottom: 1; }

#act-log {
    height: 1fr;
    background: #0a0a0a;
    scrollbar-size: 1 1;
    scrollbar-color: #1c1c1c #0a0a0a;
}

#goal-bar {
    height: 3;
    background: #0a0a0a;
    border-top: solid #333333;
    padding: 0 1;
    display: none;
}

#goal-bar.visible { display: block; }

#goal-label {
    color: #555555;
    width: 28;
    content-align: left middle;
    height: 3;
    padding: 1 1;
}

#goal-input {
    background: #0a0a0a;
    border: none;
    height: 1;
    margin: 1 0;
    color: white;
}

#goal-input:focus { border: none; }

#footer {
    height: 1;
    background: #0a0a0a;
    color: #222222;
    padding: 0 2;
    border-top: solid #1c1c1c;
}
"""


# ── Widgets ───────────────────────────────────────────────────────────────────
class TitleBar(Static):
    def on_mount(self):
        self._tick()
        self.set_interval(3.0, self._tick)
    def _tick(self):
        total = len(AGENT_STATE)
        done  = sum(1 for s in AGENT_STATE.values() if s["progress"] >= 1.0)
        self.update(
            f"[reverse bold] HOLLOW [/reverse bold]"
            f"  [dim]{MODEL}  ·  cycle {CYCLE}  ·  {total} agents  ·  {done} done"
            f"  ·  [white]g[/white] goal  [white]G[/white] group goal  [white]q[/white] quit[/dim]"
        )

class AgentList(Static):
    selected: int = 0

    def on_mount(self):
        self._tick()
        self.set_interval(2.0, self._tick)

    def _tick(self):
        if not AGENT_STATE:
            self.update("[dim]waiting…[/dim]")
            return
        agents = sorted(AGENT_STATE.items(), key=lambda x: -x[1]["progress"])
        out = []
        for i, (aid, s) in enumerate(agents):
            prog = s["progress"]
            name = _fun_name(aid)
            pct  = "done" if prog >= 1.0 else f"{prog:.0%}"
            cursor = "[reverse]" if i == self.selected else ""
            end    = "[/reverse]" if i == self.selected else ""
            if prog >= 1.0:
                out.append(f"{cursor}[green]{name:<10} {pct}[/green]{end}")
            elif prog > 0.5:
                out.append(f"{cursor}[white]{name:<10}[/white] [dim cyan]{pct}[/dim cyan]{end}")
            elif prog > 0:
                out.append(f"{cursor}[dim]{name:<10} {pct}[/dim]{end}")
            else:
                out.append(f"{cursor}[dim]{name:<10}  —[/dim]{end}")
        self.update("\n".join(out))

    def selected_agent_id(self) -> str | None:
        agents = sorted(AGENT_STATE.keys(), key=lambda a: -AGENT_STATE[a]["progress"])
        if not agents:
            return None
        return agents[min(self.selected, len(agents) - 1)]

    def move(self, delta: int):
        count = len(AGENT_STATE)
        if count:
            self.selected = (self.selected + delta) % count
            self._tick()


class AgentStats(Static):
    def on_mount(self):
        self._tick()
        self.set_interval(2.0, self._tick)
    def _tick(self):
        total  = len(AGENT_STATE)
        done   = sum(1 for s in AGENT_STATE.values() if s["progress"] >= 1.0)
        active = sum(1 for s in AGENT_STATE.values() if 0 < s["progress"] < 1.0)
        idle   = total - done - active
        self.update(
            f"[dim]active  [/dim][white]{active}[/white]\n"
            f"[dim]idle    [/dim][dim white]{idle}[/dim white]\n"
            f"[dim]done    [/dim][green]{done}[/green]\n"
            f"[dim]total   [/dim][dim]{total}[/dim]"
        )


class ActivityLog(ScrollableContainer):
    def compose(self):
        yield RichLog(highlight=False, auto_scroll=True, max_lines=1200, wrap=True, id="log")
    def on_mount(self):
        self.set_interval(0.5, self._poll)
    def _poll(self):
        raw_lines = _read_new_thoughts()
        if not raw_lines:
            return
        log = self.query_one("#log", RichLog)
        for raw in raw_lines:
            human = _humanize(raw)
            if human:
                try:
                    log.write(Text.from_markup(human))
                except Exception:
                    pass


class HollowMonitor(App):
    CSS = CSS
    BINDINGS = [
        ("q",      "quit",       "quit"),
        ("g",      "goal_single","set agent goal"),
        ("G",      "goal_group", "set group goal"),
        ("p",      "push_files", "sync to desktop"),
        ("escape", "cancel_goal","cancel"),
        ("up",     "agent_up",   "prev agent"),
        ("down",   "agent_down", "next agent"),
    ]

    _goal_mode: str = ""   # "single" | "group" | ""

    def compose(self):
        yield TitleBar(id="title")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("AGENTS", classes="sec")
                yield AgentList(id="agent-list")
                yield AgentStats(id="agent-stats")
            with Vertical(id="right"):
                yield Static("ACTIVITY", id="act-title")
                yield ActivityLog(id="act-log")
        with Horizontal(id="goal-bar"):
            yield Label("", id="goal-label")
            yield Input(placeholder="type goal, press enter…", id="goal-input")
        yield Static(
            "[dim]  [white]↑↓[/white] select  "
            "[white]g[/white] goal  "
            "[white]G[/white] group goal  "
            "[white]p[/white] sync to desktop  "
            "[white]q[/white] quit[/dim]",
            id="footer",
        )

    def on_mount(self):
        _load_config()
        _parse_daemon_log()
        self.set_interval(2.0, _parse_daemon_log)

    # ── goal bar open/close ───────────────────────────────────────────────
    def _open_goal_bar(self, mode: str, label: str):
        self._goal_mode = mode
        bar   = self.query_one("#goal-bar")
        lbl   = self.query_one("#goal-label", Label)
        inp   = self.query_one("#goal-input", Input)
        lbl.update(label)
        inp.value = ""
        bar.add_class("visible")
        inp.focus()

    def _close_goal_bar(self, status: str = ""):
        self._goal_mode = ""
        bar = self.query_one("#goal-bar")
        bar.remove_class("visible")
        footer = self.query_one("#footer", Static)
        if status:
            footer.update(f"[dim]  {status}[/dim]")
            self.set_timer(3.0, lambda: footer.update("[dim]  HOLLOW  ·  autonomous agent runtime[/dim]"))
        self.query_one("#act-log").focus()

    # ── keybindings ───────────────────────────────────────────────────────
    def action_goal_single(self):
        agent_id = self.query_one("#agent-list", AgentList).selected_agent_id()
        if not agent_id:
            return
        name = _fun_name(agent_id)
        self._open_goal_bar("single", f"  goal for {name}  ")

    def action_goal_group(self):
        self._open_goal_bar("group", f"  group goal  ")

    def action_cancel_goal(self):
        if self._goal_mode:
            self._close_goal_bar()

    def action_agent_up(self):
        self.query_one("#agent-list", AgentList).move(-1)

    def action_agent_down(self):
        self.query_one("#agent-list", AgentList).move(1)

    def action_push_files(self):
        footer = self.query_one("#footer", Static)
        footer.update("[dim]  syncing to desktop…[/dim]")
        def _sync():
            import subprocess
            result = subprocess.run(
                ["rsync", "-a", "--delete",
                 "--exclude=__pycache__", "--exclude=*.pyc",
                 "/agentOS/workspace/",
                 "/mnt/c/Users/jedin/Desktop/hollow-workspace/"],
                capture_output=True,
            )
            status = "✓ synced to Desktop/hollow-workspace" if result.returncode == 0 else "✗ sync failed"
            self.call_from_thread(lambda: footer.update(f"[dim]  {status}[/dim]"))
            time.sleep(3)
            self.call_from_thread(lambda: footer.update(
                "[dim]  [white]↑↓[/white] select  "
                "[white]g[/white] goal  "
                "[white]G[/white] group goal  "
                "[white]p[/white] sync to desktop  "
                "[white]q[/white] quit[/dim]"
            ))
        threading.Thread(target=_sync, daemon=True).start()

    # ── input submitted ───────────────────────────────────────────────────
    def on_input_submitted(self, event: Input.Submitted):
        objective = event.value.strip()
        if not objective or not self._goal_mode:
            self._close_goal_bar()
            return

        mode = self._goal_mode
        self._close_goal_bar("sending…")

        def _send():
            if mode == "single":
                agent_id = self.query_one("#agent-list", AgentList).selected_agent_id()
                if agent_id:
                    status = _set_individual_goal(agent_id, objective)
                else:
                    status = "✗ no agent selected"
            else:
                status = _set_group_goal(objective)
            self.call_from_thread(
                lambda: self.query_one("#footer", Static).update(f"[dim]  {status}[/dim]")
            )
            time.sleep(4)
            self.call_from_thread(
                lambda: self.query_one("#footer", Static).update(
                    "[dim]  HOLLOW  ·  autonomous agent runtime[/dim]"
                )
            )

        threading.Thread(target=_send, daemon=True).start()


if __name__ == "__main__":
    HollowMonitor().run()
