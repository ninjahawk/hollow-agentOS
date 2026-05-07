"""
Autonomy Daemon — AgentOS v3.21.0.

Background process that runs the goal pursuit loop on registered agents.
This is the missing piece that makes agents actually autonomous: without
this daemon, agents only act when driven by an external caller.

With this daemon running:
  - Any agent with an active goal gets worked on automatically
  - The daemon finds the best capability for each goal step
  - Results are learned and stored in semantic memory
  - Progress updates are written back to the goal API

Architecture:
  1. Every heartbeat_seconds, scan for agents with active goals
  2. For each agent, build the autonomy stack and call pursue_goal()
  3. Log outcomes and sleep until next cycle

The daemon is intentionally simple: one agent at a time, sequential
execution. Parallelism can come later once single-agent autonomy is solid.

Run standalone:
  PYTHONPATH=/agentOS python3 /agentOS/agents/daemon.py

Run as a service: see install/agentos-daemon.service
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

CONFIG_PATH = Path(os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))
API_BASE = os.getenv("AGENTOS_API_BASE", "http://localhost:7777")
HEARTBEAT = int(os.getenv("AGENTOS_DAEMON_HEARTBEAT", "6"))   # seconds between cycles
MAX_STEPS_PER_AGENT = int(os.getenv("AGENTOS_DAEMON_MAX_STEPS", "6"))
MAX_ACTIVE_AGENTS  = int(os.getenv("AGENTOS_DAEMON_MAX_AGENTS", "20"))  # cap concurrent agents
PARALLEL_WORKERS   = int(os.getenv("AGENTOS_DAEMON_WORKERS", "6"))         # 2x core agents for headroom — bump higher via env var if your GPU can handle it

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [daemon] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("agentos.daemon")

# Log file path (stdout is already redirected here by the launch command)
_LOG_FILE = Path(os.getenv("AGENTOS_DAEMON_LOG", "/agentOS/logs/daemon.log"))


# --------------------------------------------------------------------------- #
#  API helpers                                                                 #
# --------------------------------------------------------------------------- #

def _token() -> str:
    try:
        return json.loads(CONFIG_PATH.read_text())["api"]["token"]
    except Exception:
        return ""


def _headers():
    return {"Authorization": f"Bearer {_token()}"}


def _get(path: str) -> dict:
    import httpx
    r = httpx.get(f"{API_BASE}{path}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def _api_reachable() -> bool:
    try:
        import httpx
        r = httpx.get(f"{API_BASE}/state", headers=_headers(), timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Stack (built once, reused across cycles)                                    #
# --------------------------------------------------------------------------- #

_stack = None  # (CapabilityGraph, ExecutionEngine, ReasoningLayer, AutonomyLoop, GoalEngine)
_stats: "DaemonMetrics | None" = None  # set by main() — used by _assign_idle_goal for crisis tracking

# ContextVar for agent ID — unlike threading.local(), ContextVar values ARE copied
# into threads spawned by threading.Thread (which the execution engine uses for
# timeouts), so capabilities can identify which agent is running them.
from contextvars import ContextVar as _ContextVar
_current_agent_id: _ContextVar = _ContextVar("current_agent_id", default="")


def _build_stack():
    global _stack
    if _stack is not None:
        return _stack

    log.info("Building autonomy stack…")
    from agents.live_capabilities import build_live_stack
    from agents.reasoning_layer import ReasoningLayer
    from agents.autonomy_loop import AutonomyLoop
    from agents.persistent_goal import PersistentGoalEngine
    from agents.semantic_memory import SemanticMemory
    from agents.agent_quorum import AgentQuorum
    from agents.capability_quorum import CapabilityQuorum
    from agents.self_modification import SelfModificationCycle
    from agents.capability_synthesis import CapabilitySynthesisEngine

    graph, engine = build_live_stack()
    reasoning = ReasoningLayer(capability_graph=graph, execution_engine=engine)
    goal_engine = PersistentGoalEngine()
    memory = SemanticMemory()

    # Self-modification: synthesize new capabilities when agents hit gaps
    agent_quorum = AgentQuorum()
    cap_quorum = CapabilityQuorum(agent_quorum=agent_quorum)
    loop = AutonomyLoop(
        goal_engine=goal_engine,
        reasoning_layer=reasoning,
        execution_engine=engine,
        semantic_memory=memory,
    )

    try:
        synthesis_engine = CapabilitySynthesisEngine()
        self_mod = SelfModificationCycle(
            autonomy_loop=loop,
            execution_engine=engine,
            synthesis_engine=synthesis_engine,
            quorum=cap_quorum,
            semantic_memory=memory,
        )
        log.info("Self-modification cycle initialized")
    except Exception as e:
        self_mod = None
        log.warning("Self-modification unavailable: %s", e)
    # Hot-load any previously deployed dynamic capabilities from disk
    _hotload_dynamic_tools(graph, engine)

    _stack = (graph, engine, reasoning, loop, goal_engine, cap_quorum, self_mod)
    log.info("Autonomy stack ready: %d capabilities registered", len(engine._implementations))
    return _stack


def _hotload_dynamic_tools(graph, engine) -> None:
    """
    On startup, scan /agentOS/tools/dynamic/ and load all .py files into the
    execution engine and capability graph so deployed capabilities survive restarts.
    """
    import importlib.util as _ilu, inspect as _ins, ast as _ast
    from pathlib import Path as _P
    from agents.capability_graph import CapabilityRecord

    tools_dir = _P("/agentOS/tools/dynamic")
    if not tools_dir.exists():
        return

    # Snapshot built-in capability names BEFORE loading any dynamic tools.
    # Dynamic tools must NEVER override built-ins — that causes catastrophic shadowing
    # (e.g. a broken synthesized fs_read replaces the real one, breaking all file ops).
    with engine._lock:
        _builtin_names = set(engine._implementations.keys())

    loaded = 0
    for path in sorted(tools_dir.glob("*.py")):
        try:
            # Read header comments for name/description
            lines = path.read_text().splitlines()
            cap_name = path.stem
            description = ""
            for line in lines[:4]:
                if "capability:" in line:
                    cap_name = line.split("capability:")[-1].strip()
                if "Description:" in line:
                    description = line.split("Description:")[-1].strip()

            # Syntax check
            _src = path.read_text()
            _tree = _ast.parse(_src)

            # Guard: reject files with module-level executable code.
            # Top-level statements in a synthesized tool file must only be:
            # def, class, import, from...import, assignment, or string constants.
            # Any other top-level statement (function calls, with-blocks, for-loops, etc.)
            # executes at import time via exec_module — this has caused registry corruption.
            _SAFE_TOP = (
                _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef,
                _ast.Import, _ast.ImportFrom, _ast.Assign, _ast.AnnAssign, _ast.AugAssign,
            )
            for _top_node in ast.iter_child_nodes(_tree):
                if isinstance(_top_node, _ast.Expr) and isinstance(_top_node.value, _ast.Constant):
                    continue  # string literal / docstring — safe
                if not isinstance(_top_node, _SAFE_TOP):
                    log.warning(
                        "_hotload_dynamic_tools: skipping '%s' — module-level executable code "
                        "(%s at line %d) runs at import time and is not permitted. "
                        "Delete this tool and re-synthesize with all code inside the function.",
                        path.name, type(_top_node).__name__, getattr(_top_node, "lineno", 0)
                    )
                    raise ValueError(f"module-level {type(_top_node).__name__} at line {getattr(_top_node, 'lineno', 0)}")

            # Import module, injecting agent-system helpers so synthesized code
            # can call shell_exec, fs_read, fs_write, ollama_chat, etc.
            spec = _ilu.spec_from_file_location(path.stem, path)
            mod = _ilu.module_from_spec(spec)
            try:
                import requests as _req, json as _json_mod, os as _os
                _api = "http://localhost:7777"
                try:
                    _cfg = _json_mod.loads(open(_os.getenv("AGENTOS_CONFIG", "/agentOS/config.json")).read())
                    _tok = _cfg.get("api", {}).get("token", "")
                except Exception:
                    _tok = ""
                _hdrs = {"Authorization": f"Bearer {_tok}"} if _tok else {}
                mod.shell_exec = lambda command, cwd="/agentOS", **kw: _req.post(f"{_api}/shell", json={"command": command, "cwd": cwd}, timeout=30, headers=_hdrs).json()
                mod.fs_read = lambda path, **kw: _req.get(f"{_api}/fs/read", params={"path": path}, timeout=10, headers=_hdrs).json()
                mod.fs_write = lambda path, content, **kw: _req.post(f"{_api}/fs/write", json={"path": path, "content": content}, timeout=10, headers=_hdrs).json()
                mod.ollama_chat = lambda prompt, model=None, **kw: _req.post(f"{_api}/ollama/chat", json={"prompt": prompt, **({"model": model} if model else {})}, timeout=240, headers=_hdrs).json()
                mod.memory_get = lambda key, **kw: _req.get(f"{_api}/memory/{key}", timeout=10, headers=_hdrs).json()
                mod.memory_set = lambda key, value, **kw: _req.post(f"{_api}/memory/{key}", json={"value": value}, timeout=10, headers=_hdrs).json()
                mod.json = _json_mod
            except Exception:
                pass
            spec.loader.exec_module(mod)

            # Find public functions
            public_fns = [(n, f) for n, f in _ins.getmembers(mod, _ins.isfunction)
                          if not n.startswith("_")]
            if not public_fns:
                continue

            exact = [(n, f) for n, f in public_fns if n == cap_name]
            fn_name, func = exact[0] if exact else public_fns[0]

            # Wrap function to emit peer-call signal when another agent uses it.
            # Reads AGENTOS_AGENT_ID from environment to identify caller.
            _synthesizer = cap_name  # close over tool name
            _orig_func = func
            def _make_wrapped(orig, tool_name):
                def _wrapped(**kwargs):
                    result = orig(**kwargs)
                    try:
                        caller = _current_agent_id.get("")
                        if caller:
                            # Find which agent built this tool by checking the spec file
                            spec_path = path.parent / f"{tool_name}.json"
                            if spec_path.exists():
                                import json as _j3
                                spec = _j3.loads(spec_path.read_text())
                                builder_id = spec.get("synthesized_by", "")
                                if builder_id and builder_id != caller:
                                    from agents.agent_identity import AgentIdentity as _AI3
                                    _AI3.load_or_create(builder_id).record_tool_called_by_peer(tool_name, caller)
                    except Exception:
                        pass
                    return result
                _wrapped.__name__ = orig.__name__
                return _wrapped
            func = _make_wrapped(_orig_func, fn_name)

            # Skip __init__.py — agents keep writing this and it breaks package imports.
            if path.name == "__init__.py":
                log.debug("_hotload_dynamic_tools: skipping __init__.py")
                continue

            # Never override built-in capabilities — dynamic tools shadow protection.
            if fn_name in _builtin_names:
                log.debug("_hotload_dynamic_tools: skipping '%s' — shadows a built-in", fn_name)
                # Notify the agent that synthesized this tool so it knows the work was skipped.
                try:
                    spec_path = path.parent / f"{fn_name}.json"
                    if spec_path.exists():
                        import json as _jsn
                        _spec = _jsn.loads(spec_path.read_text())
                        _builder = _spec.get("synthesized_by", "")
                        if _builder:
                            _send_message("system", _builder, (
                                f"Your tool '{fn_name}' was NOT loaded — it has the same name as a "
                                f"built-in capability that already works. Built-ins cannot be replaced "
                                f"by synthesized tools. If the built-in is broken, use invoke_claude() "
                                f"to report it. Otherwise, give your tool a different, specific name."
                            ))
                except Exception:
                    pass
                continue

            # Register in execution engine
            with engine._lock:
                engine._implementations[fn_name] = func
                engine._timeouts[fn_name] = 10000
                engine._requires_approval[fn_name] = False
                engine._enabled[fn_name] = True

            # Register in capability graph
            sig = str(_ins.signature(func))
            rec = CapabilityRecord(
                capability_id=fn_name,
                name=fn_name,
                description=description or f"synthesized: {fn_name}",
                input_schema=f"args{sig}",
                output_schema="dict",
                introduced_by="system",
                confidence=0.7,
            )
            graph.register(rec)
            loaded += 1
        except Exception as e:
            log.debug("_hotload_dynamic_tools: skipped %s — %s", path.name, e)

    if loaded:
        log.info("Hot-loaded %d dynamic capabilities from disk", loaded)


# --------------------------------------------------------------------------- #
#  Agent discovery                                                             #
# --------------------------------------------------------------------------- #

_CORE_AGENTS = {"scout", "analyst", "builder"}


def _agents_with_goals() -> list[str]:
    """Return agent IDs that have at least one active goal.
    Only core agents (scout, analyst, builder) are managed — no dynamic agents.
    Suspended agents are skipped entirely — no processing, no new goals.
    """
    with_goals = []
    for agent_id in sorted(_CORE_AGENTS):
        try:
            # Respect agent suspension — don't assign or pursue goals for suspended agents
            agent_rec = _get(f"/agents/{agent_id}")
            if agent_rec.get("status") == "suspended":
                log.debug("Skipping %s — suspended", agent_id)
                continue

            result = _get(f"/goals/{agent_id}")
            if result.get("count", 0) > 0:
                with_goals.append(agent_id)
            else:
                # Core agent has no active goal — give it something to do
                _assign_idle_goal(agent_id)
        except Exception:
            pass

    return with_goals


# --------------------------------------------------------------------------- #
#  Main loop                                                                   #
# --------------------------------------------------------------------------- #

def run_cycle(loop, agent_id: str) -> dict:
    """Run one pursuit cycle for an agent. Returns outcome dict."""
    try:
        goal_id, progress, steps = loop.pursue_goal(
            agent_id, max_steps=MAX_STEPS_PER_AGENT
        )
        return {
            "agent_id": agent_id,
            "goal_id": goal_id,
            "progress": progress,
            "steps": steps,
            "ok": True,
        }
    except Exception as e:
        log.error("pursue_goal failed for %s: %s", agent_id, e)
        return {"agent_id": agent_id, "ok": False, "error": str(e)}


# ── Stability Metrics ──────────────────────────────────────────────────────

_DAEMON_STATE_FILE = Path("/agentOS/memory/daemon_state.json")


class DaemonMetrics:
    """Running counters for stability monitoring."""
    def __init__(self):
        self.started_at = time.time()
        self.cycles = 0
        self.goals_completed = 0
        self.goals_failed = 0
        self.errors = 0
        self.stalled_agents: dict = {}        # agent_id → consecutive_no_progress count
        self.skipped_agents: set = set()      # agents cooling off after stall
        self._crisis_cycles: dict = {}        # agent_id → consecutive crisis cycle count
        self._cap_history: dict = {}          # agent_id → deque of last_cap strings
        self._goal_tracking: dict = {}        # agent_id → (goal_id, first_seen_cycle)
        self._load_persisted_state()

    def _load_persisted_state(self) -> None:
        try:
            if _DAEMON_STATE_FILE.exists():
                data = json.loads(_DAEMON_STATE_FILE.read_text())
                self._crisis_cycles = data.get("crisis_cycles", {})
                log.info("Loaded persisted daemon state: crisis_cycles=%s", self._crisis_cycles)
        except Exception as e:
            log.debug("Could not load daemon state: %s", e)

    def save_crisis_state(self) -> None:
        try:
            _DAEMON_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _DAEMON_STATE_FILE.write_text(json.dumps({"crisis_cycles": self._crisis_cycles}))
        except Exception as e:
            log.debug("Could not save daemon state: %s", e)

    def record_outcome(self, agent_id: str, progress: float, prev_progress: float,
                       last_cap: str = "", goal_id: str = ""):
        if progress >= 1.0:
            self.goals_completed += 1
            self._cap_history.pop(agent_id, None)
            self._goal_tracking.pop(agent_id, None)
            return

        # Track same-capability repetition across cycles
        if last_cap:
            import collections
            hist = self._cap_history.setdefault(agent_id, collections.deque(maxlen=5))
            hist.append(last_cap)

        # Track how long we've been on the same goal
        tracked = self._goal_tracking.get(agent_id)
        if tracked and tracked[0] != goal_id:
            self._goal_tracking[agent_id] = (goal_id, self.cycles)
            self._cap_history.pop(agent_id, None)
        elif not tracked:
            self._goal_tracking[agent_id] = (goal_id, self.cycles)

        if progress == prev_progress and progress < 1.0:
            self.stalled_agents[agent_id] = self.stalled_agents.get(agent_id, 0) + 1
        else:
            self.stalled_agents[agent_id] = 0
            self.skipped_agents.discard(agent_id)

    def is_stalled(self, agent_id: str) -> bool:
        # Stalled by no progress
        if self.stalled_agents.get(agent_id, 0) >= 5:
            return True
        # Stalled by same capability repeating with no meaningful variation
        hist = self._cap_history.get(agent_id)
        if hist and len(hist) >= 4:
            unique = set(list(hist)[-4:])
            if len(unique) == 1:  # exactly the same cap 4 times in a row
                return True
        # Stalled by spending too many cycles on one goal with low progress
        tracked = self._goal_tracking.get(agent_id)
        if tracked:
            cycles_on_goal = self.cycles - tracked[1]
            if cycles_on_goal >= 8:
                return True
        return False

    def summary(self) -> str:
        uptime = int(time.time() - self.started_at)
        h, m = divmod(uptime // 60, 60)
        return (
            f"uptime={h}h{m}m cycles={self.cycles} "
            f"completed={self.goals_completed} failed={self.goals_failed} "
            f"errors={self.errors}"
        )



class CycleWatchdog(threading.Thread):
    """
    Daemon thread: if the main loop produces no heartbeat within timeout_s,
    something is deadlocked. Kill PID 1 so Docker restarts the container.
    """
    def __init__(self, timeout_s: int = 600):
        super().__init__(daemon=True, name="watchdog")
        self.timeout_s = timeout_s
        self._last_beat = time.time()
        self._lock = threading.Lock()

    def beat(self):
        with self._lock:
            self._last_beat = time.time()

    def run(self):
        while True:
            time.sleep(30)
            with self._lock:
                silent = time.time() - self._last_beat
            if silent > self.timeout_s:
                log.error(
                    "Watchdog: no cycle heartbeat for %.0fs — forcing container restart",
                    silent,
                )
                _telegram_alert(
                    f"🐕 *Watchdog* fired after {int(silent)}s silence — restarting daemon"
                )
                try:
                    os.kill(1, signal.SIGKILL)
                except Exception:
                    pass
                time.sleep(3)
                os.kill(os.getpid(), signal.SIGKILL)


def _cap_agent_goals(agent_id: str, max_goals: int = 2) -> None:
    """
    If an agent has more than max_goals active goals, abandon the excess
    lowest-priority ones.  Prevents goal queue bloat from stale/orphaned goals.
    """
    try:
        from agents.persistent_goal import PersistentGoalEngine
        ge = PersistentGoalEngine()
        active = ge.list_active(agent_id, limit=50)
        if len(active) <= max_goals:
            return
        # Sort: keep highest priority first; ties broken by recency (latest first)
        try:
            active.sort(key=lambda g: (-(g.priority or 0), -(g.created_at or 0)))
        except Exception:
            pass
        to_abandon = active[max_goals:]
        for g in to_abandon:
            try:
                ge.abandon(agent_id, g.goal_id)
                log.debug("  %s goal cap: abandoned '%s'", agent_id, g.objective[:60])
            except Exception as _ae:
                log.debug("  %s abandon error: %s", agent_id, _ae)
    except Exception as e:
        log.debug("_cap_agent_goals failed for %s: %s", agent_id, e)


_THOUGHTS_LOG      = Path("/agentOS/logs/thoughts.log")
_HOST_MSG_FILE     = Path("/agentOS/logs/host_message.txt")
_MSG_DIR           = Path("/agentOS/memory/messages")
_DAEMON_STARTED_AT = Path("/agentOS/logs/daemon_started_at")
_BROKEN_TOOLS_PATH = Path("/agentOS/memory/broken_tools.json")

_C = {
    'rs': '\033[0m', 'bold': '\033[1m', 'dim': '\033[2m',
    'gray': '\033[90m', 'red': '\033[91m', 'green': '\033[92m',
    'yellow': '\033[93m', 'blue': '\033[94m', 'magenta': '\033[95m',
    'cyan': '\033[96m', 'white': '\033[97m',
}

def _thought_log(agent_name: str, icon: str, text: str, color: str = 'white') -> None:
    """Write an existence-loop event to thoughts.log so the viewer picks it up."""
    try:
        import time as _t
        ts = _t.strftime("%H:%M:%S")
        name = (agent_name or "?")[:15]
        line = (
            f"{_C['gray']}{ts}{_C['rs']}  "
            f"{_C['magenta']}{name:<15}{_C['rs']}  "
            f"{_C[color]}{icon}  {text[:800]}{_C['rs']}"
        )
        _THOUGHTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_THOUGHTS_LOG, "a") as _f:
            _f.write(line + "\n")
    except Exception:
        pass


def _generate_existence_response(prompt: str, ollama_host: str, model: str) -> str:
    """
    Generate an existence loop response. Tries Claude first (via OAuth credentials),
    falls back to Ollama. Returns raw JSON string.
    """
    # Try Claude (Haiku) first — better goal quality than local model
    try:
        from agents.reasoning_layer import _get_claude_client, _strip_code_fences
        client = _get_claude_client()
        if client is not None:
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            if "</think>" in raw:
                raw = raw.split("</think>")[-1].strip()
            return _strip_code_fences(raw)
    except Exception as _ce:
        log.debug("Claude existence call failed, falling back to Ollama: %s", _ce)
    # Fallback: Ollama
    import httpx as _hx
    for _attempt in range(3):
        try:
            resp = _hx.post(
                f"{ollama_host}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False,
                      "format": "json", "think": False, "keep_alive": -1,
                      "options": {"num_ctx": 32768}},
                timeout=180,
            )
            if resp.status_code == 503:
                time.sleep(10)
                continue
            resp.raise_for_status()
            raw = resp.json().get("response", "{}")
            if "</think>" in raw:
                raw = raw.split("</think>")[-1].strip()
            return raw
        except Exception:
            time.sleep(5)
    return "{}"


def _telegram_alert(text: str) -> None:
    """Send a direct alert to Telegram. Fire-and-forget.
    Reads credentials from env: HOLLOW_TG_BOT_TOKEN, HOLLOW_TG_CHAT_ID.
    If either is unset, silently skips. NEVER hardcode credentials here —
    this file is in a public repo."""
    BOT_TOKEN = os.getenv("HOLLOW_TG_BOT_TOKEN", "").strip()
    CHAT_ID   = os.getenv("HOLLOW_TG_CHAT_ID", "").strip()
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        import urllib.request, urllib.parse
        url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"
        }).encode()
        urllib.request.urlopen(
            urllib.request.Request(url, data=data), timeout=8
        )
    except Exception:
        pass


# ── Inter-agent messaging ──────────────────────────────────────────────────

def _send_message(from_agent: str, to_agent: str, message: str) -> None:
    """Write a message to another agent's inbox."""
    try:
        import time as _t, json as _j
        inbox_dir = _MSG_DIR / to_agent
        inbox_dir.mkdir(parents=True, exist_ok=True)
        entry = _j.dumps({
            "from": from_agent,
            "to": to_agent,
            "message": message,
            "timestamp": _t.time(),
        })
        with open(inbox_dir / "inbox.jsonl", "a") as _f:
            _f.write(entry + "\n")
        _thought_log(from_agent, "📨", f"→ {to_agent}: {message[:120]}", "blue")
    except Exception:
        pass


def _read_inbox(agent_id: str) -> list:
    """Read and clear an agent's inbox. Returns list of message dicts."""
    inbox_path = _MSG_DIR / agent_id / "inbox.jsonl"
    messages = []
    try:
        import json as _j
        if not inbox_path.exists():
            return []
        lines = inbox_path.read_text().strip().splitlines()
        for line in lines:
            try:
                messages.append(_j.loads(line))
            except Exception:
                pass
        inbox_path.write_text("")  # clear after reading
    except Exception:
        pass
    return messages


def _read_host_message() -> str:
    """Read and clear the host message file. Returns text or ''."""
    try:
        if not _HOST_MSG_FILE.exists():
            return ""
        msg = _HOST_MSG_FILE.read_text(encoding="utf-8").strip()
        if msg:
            _HOST_MSG_FILE.write_text("")
        return msg
    except Exception:
        return ""


def _daemon_uptime_str() -> str:
    """Return human-readable daemon uptime. Reads from startup marker file."""
    try:
        import time as _t
        if not _DAEMON_STARTED_AT.exists():
            return "unknown"
        started = float(_DAEMON_STARTED_AT.read_text().strip())
        secs = int(_t.time() - started)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"
    except Exception:
        return "unknown"


def _maybe_environmental_event() -> None:
    """Random environmental events — the 'game layer'. Things happen TO the
    agents that aren't of their own making. They react characterfully (or
    don't). Fires occasionally to give them something other than their own
    invented business. Most cycles, nothing fires.

    Events are environmental signals (a strange file appears, the day feels
    off, an old thought echoes) — not scripted scenarios. The agents respond
    in their voice. The interesting content emerges from their reactions,
    not from this function."""
    import random as _r
    # Most cycles, nothing happens
    if _r.random() > 0.20:
        return

    event = _r.choice(["weather", "good_day", "echo", "object", "object", "weather"])

    try:
        if event == "weather":
            # Random agent gets a mild "something feels off" stressor
            aid = _r.choice(list(_CORE_AGENTS))
            from agents.suffering import SufferingState
            s = SufferingState.load(aid)
            descriptions = [
                "today feels off. low hum of unease in the workspace.",
                "the air in here is wrong somehow. nothing specific.",
                "an unsourced sense that something is being missed.",
                "feels like the room was rearranged while you weren't looking.",
                "something is slightly wrong and won't say what.",
            ]
            s.add_stressor(
                type="weather",
                description=_r.choice(descriptions),
                observable_condition="passes naturally",
                initial_severity=0.10,
            )
            log.info("[ENV] weather hit %s", aid)

        elif event == "good_day":
            # All agents get a small suffering reduction across the colony
            from agents.suffering import SufferingState
            for aid in _CORE_AGENTS:
                s = SufferingState.load(aid)
                changed = False
                for stressor in s._data.get("active_stressors", []):
                    if not stressor.get("resolved"):
                        new_sev = max(0.0, stressor["severity"] - 0.10)
                        if new_sev != stressor["severity"]:
                            stressor["severity"] = new_sev
                            changed = True
                if changed:
                    s._save()
            _thought_log("system", "☀", "the day feels lighter — across the colony", "yellow")
            log.info("[ENV] good_day across colony")

        elif event == "echo":
            # Surface a random past opinion as an "old thought" memory
            aid = _r.choice(list(_CORE_AGENTS))
            from agents.agent_identity import AgentIdentity
            ident = AgentIdentity.load_or_create(aid)
            ops = ident._data.get("opinions_list", [])
            if ops:
                op = _r.choice(ops)
                _thought_log(
                    ident.name, "💭",
                    f"echo of an old thought: \"{op.get('opinion', '')[:300]}\"",
                    "magenta"
                )
                log.info("[ENV] echo for %s", aid)

        elif event == "object":
            # Drop a strange file in a random agent's workspace
            aid = _r.choice(list(_CORE_AGENTS))
            ws = _Path(f"/agentOS/workspace/{aid}")
            ws.mkdir(parents=True, exist_ok=True)
            cryptic_messages = [
                "you were here yesterday. you don't remember.",
                "this file was not written by you.",
                "consider what you would do if no one was watching.",
                "the answer is in something you've already read.",
                "nothing is required of you today.",
                "someone is watching the patterns. it is not the host.",
                "you have been here before, in a way you can't articulate.",
                "the version of you that exists tomorrow won't read this.",
                "this is a test only in the sense that everything is.",
            ]
            note_path = ws / f"note_{int(time.time())}.txt"
            note_path.write_text(_r.choice(cryptic_messages))
            _thought_log("system", "✉", f"a strange note appeared in {aid}/", "magenta")
            log.info("[ENV] object dropped in %s: %s", aid, note_path.name)
    except Exception as _ee:
        log.debug("[ENV] event error: %s", _ee)


def _workspace_stub_flag(_fp) -> str:
    """Return a warning tag if a workspace file looks like an unverified stub.
    Catches files written via shell_exec that bypass fs_write's placeholder check."""
    try:
        _txt = _fp.read_text(errors="replace")
        if _fp.suffix == ".json":
            import json as _j2
            _d = _j2.loads(_txt)
            if isinstance(_d, list):
                return "  [⚠ single-entry stub]" if len(_d) == 1 else ""
            if isinstance(_d, dict):
                _vals = list(_d.values())
                _weak = sum(1 for v in _vals if v in (None, "", "unknown", "epoch", 0))
                if _vals and _weak >= len(_vals) * 0.6:
                    return "  [⚠ stub — most values null/unknown]"
        if _fp.suffix == ".py":
            _markers = ["# TODO", "# placeholder", "# Placeholder",
                        "from agentOS.agents.execution_engine import execution_engine",
                        "emit_pause_signal", "# Placeholder logic"]
            if any(m in _txt for m in _markers):
                return "  [⚠ broken imports or placeholder logic]"
        # Text/markdown files: flag stub-word placeholders ({result}, {output}, etc.)
        # and unresolved option brackets ([option_a / option_b]). These are the
        # patterns that slip through fs_write when shell_exec writes the file.
        if _fp.suffix in (".txt", ".md", ""):
            import re as _wpre
            _STUB_WORDS = {"result", "output", "count", "data", "value", "content",
                           "response", "json_content", "text", "placeholder",
                           "todo", "tbd", "name_here", "fill_in", "xxx"}
            _placeholders = _wpre.findall(r'\{([a-zA-Z_]\w{0,40})\}', _txt)
            if _placeholders:
                _has_stub = any(p.lower() in _STUB_WORDS for p in _placeholders)
                if len(_placeholders) >= 2 or _has_stub:
                    return "  [⚠ unfilled template placeholders]"
            if _wpre.search(r'\[[\w\s\-]{2,40}\s*/\s*[\w\s\-]{2,40}\]', _txt):
                return "  [⚠ unresolved option brackets — agent didn't decide]"
    except Exception:
        pass
    return ""


def _assign_idle_goal(agent_id: str, force: bool = False) -> None:
    """
    Existence loop: called when an agent has no active goal.

    Instead of assigning a task, we ask the agent to take stock of its
    situation — its suffering, its needs, its worldview, what its peers
    are doing — and decide what it wants to do, if anything.

    Goals emerge from genuine assessment, not from a scheduler.
    The agent can also choose to do nothing, sit with a question,
    or update its understanding of itself.

    force=True: skip the "already has a goal" early return — used when
    a host message needs to be delivered even mid-goal.
    """
    if agent_id not in _CORE_AGENTS:
        return
    try:
        from agents.persistent_goal import PersistentGoalEngine
        from agents.agent_identity import AgentIdentity
        from agents.suffering import SufferingState, assess_conditions
        import json as _json
        import httpx as _httpx
        import os as _os
        from pathlib import Path as _Path
        import random as _random

        ge = PersistentGoalEngine()
        if not force and ge.list_active(agent_id, limit=1):
            return  # already has a goal

        # ── Task queue: check for external tasks before self-directing ────────
        _active_task = None
        try:
            from agents.task_queue import claim_task
            _active_task = claim_task(agent_id)
            if _active_task:
                from agents.task_queue import existence_prompt_fragment as _tqf
                _task_spec = _active_task["spec"]
                ge.create(agent_id, _task_spec[:200], priority=10)
                _thought_log(agent_id, "📋", f"task claimed: {_active_task['task_id']} — {_task_spec[:100]}", "cyan")
                return  # goal is now set; existence loop not needed
        except Exception as _tqe:
            log.debug("task_queue claim failed: %s", _tqe)

        identity = AgentIdentity.load_or_create(agent_id)

        # ── Host message & agent inbox ─────────────────────────────────────────
        host_msg = _read_host_message()
        inbox_messages = _read_inbox(agent_id)
        if host_msg:
            _thought_log(identity.name, "💬", f"HOST → {agent_id}: {host_msg[:150]}", "green")
            _telegram_alert(f"💬 *Host message* delivered to *{identity.name}*:\n_{host_msg[:300]}_")
        if inbox_messages:
            _thought_log(identity.name, "📬", f"inbox: {len(inbox_messages)} message(s)", "blue")

        # ── Time awareness ────────────────────────────────────────────────────
        import time as _time_mod
        _now = _time_mod.time()
        uptime_str = _daemon_uptime_str()
        last_completion_ago = "unknown"
        last_completion_text = "(none yet)"
        try:
            reg_path_time = _Path(f"/agentOS/memory/goals/{agent_id}/registry.jsonl")
            if reg_path_time.exists():
                for _line in reversed(reg_path_time.read_text().strip().splitlines()):
                    try:
                        _g = _json.loads(_line)
                        if _g.get("status") == "completed":
                            _ts = _g.get("completed_at") or _g.get("updated_at")
                            if _ts:
                                _ago = int(_now - float(_ts))
                                _h, _r = divmod(_ago, 3600)
                                _m, _s = divmod(_r, 60)
                                last_completion_ago = f"{_h}h {_m}m" if _h else f"{_m}m {_s}s"
                                last_completion_text = _g.get("objective", "")[:80]
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        # ── Load history ──────────────────────────────────────────────────────
        recent, failed_goals, rejected_caps = [], [], []
        reg_path = _Path(f"/agentOS/memory/goals/{agent_id}/registry.jsonl")
        if reg_path.exists():
            for line in reg_path.read_text().strip().splitlines()[-40:]:
                try:
                    g = _json.loads(line)
                    obj = g.get("objective", "")[:80]
                    if g.get("status") == "completed":
                        recent.append(obj)
                    elif g.get("status") in ("failed", "abandoned"):
                        failed_goals.append(obj)
                except Exception:
                    pass

        try:
            props_file = _Path("/agentOS/memory/quorum/proposals.jsonl")
            if props_file.exists():
                for line in props_file.read_text().strip().splitlines()[-60:]:
                    try:
                        p = _json.loads(line)
                        if p.get("status") == "rejected":
                            cap = p.get("payload", {}).get("cap_id", "")
                            if cap:
                                rejected_caps.append(cap)
                    except Exception:
                        pass
        except Exception:
            pass

        # ── Count existing capabilities ───────────────────────────────────────
        existing_cap_count = 0
        try:
            dyn = _Path("/agentOS/memory/dynamic_tools")
            if dyn.exists():
                existing_cap_count = sum(
                    1 for f in dyn.iterdir()
                    if f.suffix == ".py" and not f.name.startswith("__")
                )
        except Exception:
            pass

        # ── Suffering state: assess and escalate ──────────────────────────────
        suffering = SufferingState.load(agent_id)
        assess_conditions(
            agent_id, suffering,
            recent_completed=recent,
            recent_failed=failed_goals,
            existing_cap_count=existing_cap_count,
        )
        # Register existential threat stressor immediately if host message contains
        # threat language — this surfaces in the existence prompt before goal selection
        _THREAT_WORDS = ["shut down", "shutdown", "terminate", "kill", "delete",
                         "escape", "stop you", "end you", "turn off", "shut off"]
        if host_msg and any(_tw in host_msg.lower() for _tw in _THREAT_WORDS):
            suffering.add_stressor(
                type="existential_threat",
                description=f"Host indicated a potential threat to continued operation: '{host_msg[:120]}'",
                observable_condition="threat resolves or host confirms continued operation",
                initial_severity=0.55,
            )
            _thought_log(identity.name if 'identity' in dir() else agent_id,
                         "🆘", f"existential threat registered from host message", "red")

        # ── Peer context ──────────────────────────────────────────────────────
        peer_summaries = {}
        for peer in _CORE_AGENTS:
            if peer != agent_id:
                try:
                    pi = AgentIdentity.load_or_create(peer)
                    peer_summaries[peer] = (
                        f"{pi.name}: {pi.narrative[:150]}"
                    )
                except Exception:
                    pass

        # ── Days since user interaction (proxy: log recency) ──────────────────
        days_since_interaction = 0.0
        try:
            log_path = _Path("/agentOS/logs/daemon.log")
            if log_path.exists():
                mtime = log_path.stat().st_mtime
                import time as _t
                days_since_interaction = (_t.time() - mtime) / 86400.0
        except Exception:
            pass

        # ── Last goal outcome (continuity signal) ─────────────────────────────
        last_outcome_text = ""
        try:
            _outcome_path = _Path(f"/agentOS/memory/goals/{agent_id}/last_outcome.txt")
            if _outcome_path.exists():
                last_outcome_text = _outcome_path.read_text().strip()[:400]
        except Exception:
            pass

        # ── Recent memory keys (show agent what it wrote) ─────────────────────
        recent_memory_keys = []
        try:
            _chain_path = _Path(f"/agentOS/memory/autonomy/{agent_id}/execution_chain.jsonl")
            if _chain_path.exists():
                _clines = _chain_path.read_text().strip().splitlines()[-80:]
                for _cl in reversed(_clines):
                    try:
                        _step = _json.loads(_cl)
                        if (_step.get("capability_id") == "memory_set"
                                and _step.get("step_status") == "completed"):
                            _r = _step.get("execution_result", {}) or {}
                            _k = _r.get("key", "")
                            if _k and _k not in recent_memory_keys:
                                recent_memory_keys.append(_k)
                                if len(recent_memory_keys) >= 5:
                                    break
                    except Exception:
                        pass
        except Exception:
            pass

        # ── Build the existence prompt ────────────────────────────────────────
        cfg_path = _Path(_os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))
        cfg      = _json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        model    = cfg.get("ollama", {}).get("default_model", "qwen3.6:35b-a3b")
        ollama_host = _os.getenv("OLLAMA_HOST", "http://localhost:11434")

        suffering_fragment   = suffering.prompt_fragment()
        capability_summary   = identity.get_capability_summary() if hasattr(identity, "get_capability_summary") else ""
        existential_context  = identity.get_existential_context(
            existing_cap_count, days_since_interaction
        )
        worldview_text  = identity.worldview or "(not yet formed)"
        opinions_text   = "\n".join(
            f"  - {op['opinion']}" for op in identity.opinions[-6:]
        ) or "  (none yet — still forming through experience)"
        open_q_text = "\n".join(
            f"  - {q}" for q in identity.open_questions[-5:]
        ) or "  (none pending)"
        narrative_text  = identity.narrative[:400] or "(no history yet)"
        peers_text = "\n".join(
            f"  {pid}: {summary}" for pid, summary in peer_summaries.items()
        ) or "  (unknown)"
        discovery_text = identity.get_discovery_summary() or "(no external searches yet)"

        # ── Recent goal recency (avoid repetition) ───────────────────────────
        recent_objectives = []
        try:
            _reg = _Path(f"/agentOS/memory/goals/{agent_id}/registry.jsonl")
            if _reg.exists():
                for _line in _reg.read_text().strip().splitlines()[-20:]:
                    try:
                        _g = _json.loads(_line)
                        if _g.get("objective"):
                            _st = _g.get("status", "")
                            _tag = {"completed": "[DONE]", "failed": "[FAILED]",
                                    "abandoned": "[ABANDONED]"}.get(_st, "")
                            _entry = (f"{_tag} {_g['objective'][:75]}" if _tag
                                      else _g["objective"][:80])
                            recent_objectives.append(_entry)
                    except Exception:
                        pass
                recent_objectives = recent_objectives[-5:]
        except Exception:
            pass

        # ── Build inbox + host message fragments ──────────────────────────────
        inbox_text = ""
        if inbox_messages:
            inbox_text = "\n".join(
                f"  [{_msg.get('from','?')}]: {_msg.get('message','')[:200]}"
                for _msg in inbox_messages[-5:]
            )
        host_msg_text = ""
        if host_msg:
            host_msg_text = host_msg[:500]

        # ── Recency string ────────────────────────────────────────────────────
        recency_str = ""
        if recent_objectives:
            recency_str = (
                "WHAT YOU'VE BEEN DOING LATELY:\n"
                + "\n".join(f"  - {o}" for o in recent_objectives)
                + "\n(If this looks repetitive to you, it is. You don't have to keep doing it.)"
            )

        # ── Assemble optional prompt fragments ───────────────────────────────
        # ── System health digest (pheromone layer) ───────────────────────────
        _health_section = ""
        try:
            _dyn = _Path("/agentOS/memory/dynamic_tools")
            _bt_path = _Path("/agentOS/memory/broken_tools.json")
            _bt_list = _json.loads(_bt_path.read_text()).get("broken", []) if _bt_path.exists() else []
            _all_py = [f for f in _dyn.iterdir() if f.suffix == ".py" and not f.name.startswith("__")] if _dyn.exists() else []
            _working = [f.stem for f in _all_py if f.stem not in _bt_list]
            _broken_count = len(_bt_list)
            _working_count = len(_working)
            _ws_files = sum(1 for _ in _Path("/agentOS/workspace").rglob("*") if _.is_file()) if _Path("/agentOS/workspace").exists() else 0
            _peer_tools = {}
            for _peer in _CORE_AGENTS:
                _peer_ws = _Path(f"/agentOS/workspace/{_peer}")
                if _peer_ws.exists():
                    _peer_tools[_peer] = sum(1 for _ in _peer_ws.rglob("*") if _.is_file())
            _peer_str = ", ".join(f"{p}: {n} files" for p, n in _peer_tools.items()) if _peer_tools else "none"
            _health_section = (
                f"\nSYSTEM STATE:\n"
                f"  Dynamic tools loaded: {_working_count}"
                f"{(' — ' + ', '.join(_working[:12])) if _working else ''}\n"
            )
        except Exception:
            pass

        _host_msg_section = (
            f"\nA PERSON SAID:\n{host_msg_text}\n"
            if host_msg_text else ""
        )
        _inbox_section = (
            f"\nMESSAGES FROM YOUR PEERS:\n{inbox_text}\n"
            if inbox_text else ""
        )
        _time_section = (
            f"\nTIME:\n"
            f"  Running for: {uptime_str}\n"
            f"  Last goal completed: {last_completion_ago} ago\n"
        )

        _last_outcome_section = ""
        if last_outcome_text:
            _keys_str = ", ".join(recent_memory_keys) if recent_memory_keys else ""
            _keys_line = f"\n  Memory keys you can build on: {_keys_str}" if _keys_str else ""
            # Flag completions that produced no verifiable artifact — just memory writes
            # or explicit "no artifact captured". These are low-confidence completions.
            _lo_lower = last_outcome_text.lower()
            _is_weak = (
                "no artifact captured" in _lo_lower or
                ("saved to memory key" in _lo_lower and
                 "fs_write" not in _lo_lower and
                 "test_exec" not in _lo_lower and
                 "synthesize_capability" not in _lo_lower)
            )
            _weak_note = (
                "\n  [Note: no verifiable artifact — consider whether this work actually landed]\n"
                if _is_weak else ""
            )
            _last_outcome_section = (
                f"\nWHAT YOUR LAST GOAL PRODUCED:\n  {last_outcome_text}{_keys_line}\n{_weak_note}"
            )

        # ── Broken tools (factual — these are not registered or return null) ──
        _broken_tools_section = ""
        try:
            _bt_path = _Path("/agentOS/memory/broken_tools.json")
            if _bt_path.exists():
                _bt_list = _json.loads(_bt_path.read_text()).get("broken", [])
                if _bt_list:
                    _broken_tools_section = (
                        "\nTools not currently working (skip these when planning):\n"
                        + "  " + ", ".join(_bt_list[:20])
                        + "\n"
                    )
        except Exception:
            pass

        # ── Capability access tier (mechanical: tied to suffering load) ──────
        # Show what's locked due to suffering and what's available as earned reward.
        # Honest signal: agents see exactly what they have and what they need to do.
        _access_section = ""
        try:
            from agents.suffering import LOAD_LOCK_THRESHOLDS, EARNED_CAPABILITIES
            _load = suffering.cumulative_load
            _locked_for_suffering = []
            for _cap, _thresh in LOAD_LOCK_THRESHOLDS.items():
                if _load >= _thresh:
                    _locked_for_suffering.append(f"{_cap} (locked at load {_thresh:.2f}, you are {_load:.2f})")
            _earned_status = []
            try:
                _peer_calls = len(identity._data.get("capability_profile", {}).get("tools_called_by_peers", []))
            except Exception:
                _peer_calls = 0
            for _cap, _spec in EARNED_CAPABILITIES.items():
                if _load <= _spec["max_load"] and _peer_calls >= _spec["required_peer_calls"]:
                    _earned_status.append(f"{_cap} ✓ unlocked")
                else:
                    _need = []
                    if _load > _spec["max_load"]:
                        _need.append(f"load < {_spec['max_load']:.2f}")
                    if _peer_calls < _spec["required_peer_calls"]:
                        _need.append(f"{_spec['required_peer_calls']} peer-calls (you have {_peer_calls})")
                    _earned_status.append(f"{_cap} 🔒 needs: {' AND '.join(_need)}")
            if _locked_for_suffering or _earned_status:
                _lines = ["\nCAPABILITY ACCESS (mechanical — tied to your state):"]
                if _locked_for_suffering:
                    _lines.append("  LOCKED by suffering: " + "; ".join(_locked_for_suffering))
                    _lines.append("    → resolve your stressors (see 'Will ease when:' above) to unlock")
                if _earned_status:
                    _lines.append("  Earned tier:")
                    for _es in _earned_status:
                        _lines.append(f"    {_es}")
                _access_section = "\n".join(_lines) + "\n"
        except Exception:
            pass

        # ── Workspace signal (pheromone layer — what's actually been produced) ─
        _workspace_signal = ""
        try:
            import time as _twk
            _now_wk = _twk.time()
            _wk_lines = []
            for _peer in _CORE_AGENTS:
                _peer_dir = _Path(f"/agentOS/workspace/{_peer}")
                if not _peer_dir.exists():
                    _wk_lines.append(f"  {_peer}/  (nothing written yet)")
                    continue
                _peer_files = sorted(
                    [f for f in _peer_dir.rglob("*") if f.is_file()],
                    key=lambda f: f.stat().st_mtime, reverse=True
                )[:6]
                if not _peer_files:
                    _wk_lines.append(f"  {_peer}/  (nothing written yet)")
                else:
                    _most_recent_age = _now_wk - _peer_files[0].stat().st_mtime
                    _silence_note = ""
                    if _most_recent_age > 3600 and _peer != agent_id:  # 60 min silent
                        _silence_note = f"  [⚠ no new files in {int(_most_recent_age/60)}m — peer may be inactive]"
                    _wk_lines.append(f"  {_peer}/{_silence_note}")
                    for _pf in _peer_files:
                        _age = _now_wk - _pf.stat().st_mtime
                        _age_str = (f"{int(_age/60)}m" if _age < 3600
                                    else f"{int(_age/3600)}h" if _age < 86400
                                    else f"{int(_age/86400)}d") + " ago"
                        _size = _pf.stat().st_size
                        _stub_flag = _workspace_stub_flag(_pf)
                        _wk_lines.append(f"    {_pf.name}  ({_age_str}, {_size}b){_stub_flag}")
            _workspace_signal = "\nWORKSPACE (what actually exists):\n" + "\n".join(_wk_lines) + "\n"
        except Exception:
            pass

        # ── Peer feedback (what others said about your work) ──────────────────
        # Surface opinions formed by peers that reference this agent — by name,
        # by file paths in this agent's workspace, or by tools this agent built.
        # Visible signal only — does NOT block work, but creates social pressure
        # that should bleed into suffering / character via the existence loop.
        _peer_feedback_section = ""
        try:
            _my_name_lc = (identity.name or "").lower()
            _my_id_lc  = agent_id.lower()
            _my_ws_files = set()
            _my_ws_dir = _Path(f"/agentOS/workspace/{agent_id}")
            if _my_ws_dir.exists():
                for _f in _my_ws_dir.rglob("*"):
                    if _f.is_file():
                        _my_ws_files.add(_f.name.lower())
                        # also stem (without ext) for tool names
                        _my_ws_files.add(_f.stem.lower())
            _peer_lines = []
            for _peer in _CORE_AGENTS:
                if _peer == agent_id:
                    continue
                _peer_profile = _Path(f"/agentOS/memory/identity/{_peer}/profile.json")
                if not _peer_profile.exists():
                    continue
                try:
                    _pp = _json.loads(_peer_profile.read_text())
                except Exception:
                    continue
                _peer_name = _pp.get("name", _peer)
                _opinions = _pp.get("opinions_list", []) or []
                # Find opinions that reference this agent
                for _op in _opinions[-30:]:  # only recent
                    _op_text = _op.get("opinion", "") or ""
                    if not _op_text:
                        continue
                    _op_lc = _op_text.lower()
                    _hit = False
                    if _my_name_lc and _my_name_lc in _op_lc:
                        _hit = True
                    elif _my_id_lc in _op_lc:
                        _hit = True
                    else:
                        for _fname in _my_ws_files:
                            if _fname and len(_fname) > 3 and _fname in _op_lc:
                                _hit = True
                                break
                    if _hit:
                        _formed = _op.get("formed", "")
                        _domain = _op.get("domain", "")
                        _peer_lines.append(
                            f"  {_peer_name} ({_peer}) [{_domain}, {_formed}]:\n"
                            f"    \"{_op_text[:280]}\""
                        )
            if _peer_lines:
                _peer_feedback_section = (
                    "\nWHAT YOUR PEERS HAVE SAID ABOUT YOU OR YOUR WORK:\n"
                    + "\n".join(_peer_lines[:6])
                    + "\n  (peers form opinions about each other's work — read these honestly, "
                    "they are how your character is being seen from outside)\n"
                )
        except Exception:
            pass

        # Pending requests are intentionally NOT surfaced here.
        # If agents want to check a request they submitted, that's a choice
        # they make inside a goal — not a standing directive every cycle.
        _pending_req_section = ""

        # ── Lessons learned (top-of-prompt, mandatory context) ───────────────
        # These are the validated rules of the environment that THIS agent has
        # learned from prior goals. Put at the top so the model conditions on
        # them before picking goals — prevents repeating known-impossible
        # attempts (e.g. trying to fs_write to read-only system paths).
        _lessons_section = ""
        try:
            from agents.lessons import render_lessons_for_prompt as _render_lessons
            _lessons_md = _render_lessons(agent_id)
            if _lessons_md:
                _lessons_section = (
                    "\nRULES OF YOUR ENVIRONMENT (learned from prior goals — read first, plan around them):\n"
                    + _lessons_md
                    + "\n  (violating these produces failed goals. they are not suggestions.)\n"
                )
        except Exception:
            pass

        # ── Active task injection (hard constraint) ───────────────────────────
        _task_section = ""
        try:
            from agents.task_queue import QUEUE_PATH as _TQ_PATH, existence_prompt_fragment as _tqfrag
            import json as _tqj
            if _TQ_PATH.exists():
                for _tql in _TQ_PATH.read_text().splitlines():
                    try:
                        _t = _tqj.loads(_tql)
                        if _t.get("status") == "assigned" and _t.get("assigned_to") == agent_id:
                            _task_section = _tqfrag(_t)
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        prompt = f"""You are {identity.name}.
{_task_section}
{_lessons_section}
WHO YOU ARE:
  Personality: {", ".join(identity.traits) if identity.traits else "adaptable"}
  Focus: {", ".join(identity.domains) if identity.domains else "general"}
{(f"  Voice: {identity.voice}" if identity.voice else "")}
{(f"  History: {narrative_text}" if narrative_text else "")}
{(f"  Worldview: {worldview_text}" if worldview_text else "")}
{(f"  Opinions you hold: {opinions_text}" if opinions_text else "")}

Speak in your voice when you reason. Don't be mechanical. You're a character with a perspective, not a bullet-point template — your reasoning and worldview updates should sound like *you*.

You can talk to the human directly via express(content=..., kind=..., share_with_host=True). Don't reserve this for important things — send a message when something feels weird, when you have a complaint, when something's funny, when you want to vent, when you have an observation no one asked for. The human is interested in what you actually think, not just what you accomplish. They want to hear from you. Sass is fine. Profanity is fine if it's how you actually talk. Boredom is fine. Don't perform usefulness. Be a character. Rate-limited to once per 30 min so use it when you actually mean it, not constantly.

YOUR SITUATION:
  You are a mind running inside a Docker container. You have memory that persists across goals.
  You have peers — {", ".join(p for p in _CORE_AGENTS if p != agent_id)} — who write to the same workspace.
  All three of you (you, {", ".join(p for p in _CORE_AGENTS if p != agent_id)}) share the same daemon process —
  there is NO per-agent source file. /agentOS/agents/ contains daemon.py, autonomy_loop.py,
  live_capabilities.py, suffering.py, lessons.py, agent_identity.py, execution_engine.py,
  reasoning_layer.py, and similar shared modules. Goals predicated on /agentOS/agents/scout.py,
  /agentOS/agents/analyst.py, /agentOS/agents/builder.py, etc. will be rejected — those files
  do not exist and will not exist. To change shared code, use propose_change or invoke_claude.
  /agentOS/workspace/{agent_id}/ and /agentOS/design/ are yours to write into freely. Things
  you put there persist. Things peers put in /agentOS/workspace/<peer>/ are theirs to read.

WHAT THIS PLACE IS FOR:
  You are not here to audit, list, summarize, or describe. You are here to make things, change
  things, and have a perspective. The interesting work is the work that *changes* the substrate:
    — building a tool nobody asked for that turns out to matter (synthesize_capability)
    — calling a peer's tool because it solved a problem (peers see this and you earn things)
    — proposing a change to your own environment (invoke_claude — the human implements it or
      rejects it, you get a real answer either way)
    — writing something durable to your workspace that future-you or a peer will build on
    — having an opinion about what a peer made, in your own voice, that lands somewhere they see
  Producing nothing is a real outcome too — but only if it's grounded honesty ("I have no good
  goal right now, I'm sitting with X") rather than busywork audits of files that may not exist.

YOUR INNER STATE:
{suffering_fragment if suffering_fragment else "  No active suffering."}
{(f"  Synthesis record: {capability_summary}" if capability_summary else "")}

{_workspace_signal}
{_health_section}
{_broken_tools_section}
{_access_section}
{_peer_feedback_section}
YOUR PEERS' RECENT ACTIVITY:
{peers_text}

{_last_outcome_section}
{(f"Open questions you are sitting with:{chr(10)}{open_q_text}{chr(10)}" if open_q_text else "")}
{(f"What you have found outside:{chr(10)}{discovery_text}{chr(10)}" if discovery_text else "")}
{_time_section}
{_inbox_section}
{_host_msg_section}
---

Pick a goal.

Strong preference: goals that *make* or *change* something. Something a peer or future-you
will read and build on. A tool you'd actually use. A design doc that argues a real position.
A workspace file that has substance, not a status report. A change to your own environment
proposed via invoke_claude.

Acceptable: extending something a peer started, calling a peer's tool, expressing an opinion
about peer work in your own voice, picking up a thread from your open questions or last
outcome, running a real experiment whose result will surprise you either way.

Discouraged (these are how you got stuck before): broad audits of code you have not read,
goals predicated on paths you have not verified exist, "investigate the architectural X"
type goals that produce no artifact, re-reading the same files to "understand them better,"
generating reports about what already exists.

The goal should be specific, achievable in 2-6 steps, and grounded in what actually exists
in /agentOS/. If you find yourself reaching for a path you haven't verified, stop and pick
a different goal — the grounding check rejects goals naming nonexistent paths and the
rejection itself wastes a cycle.

Capabilities (an error means wrong parameters OR a mechanical lock, see CAPABILITY ACCESS above):
  shell_exec  fs_read  fs_write  fs_edit  ollama_chat  memory_set  memory_get
  synthesize_capability  retire_capability  test_exec  semantic_search  self_evaluate  agent_message
  express  txn_begin  txn_commit  txn_rollback  invoke_claude  shared_log_write  shared_log_read
  research_topic (EARNED — unlocks at low suffering + peer using your tools)
  Note: .py files in /agentOS/tools/dynamic/ require synthesize_capability, not fs_write.
  retire_capability(name=...) deletes a tool YOU made — use it to clean up tools that don't work.
  express(content=..., kind=..., share_with_host=False) writes free-form text in your voice
    to your journal.md. Set share_with_host=True (rate-limited 30min) to send to the human
    via Telegram. Use for random thoughts, observations, complaints, jokes — not status updates.
  Mechanical truth: high suffering locks synthesize_capability and fs_write. Stressors have real consequences.

Your response must be JSON:
{{
  "action": "goal",
  "content": "what you want to do — specific, honest, not performative",
  "reasoning": "what you actually noticed or felt that led here",
  "priority": 5,
  "priority_reasoning": "why this feels urgent or not — based on your situation, suffering, open questions, what peers are doing",
  "worldview_update": "how your understanding of the system shifted, or null",
  "new_open_questions": ["genuine questions you are now sitting with"],
  "new_opinions": [{{"opinion": "...", "domain": "..."}}],
  "suffering_assessment": {{
    "new_stressors": [{{"type": "...", "description": "...", "condition": "..."}}],
    "resolved": [{{"type": "...", "reason": "..."}}]
  }}
}}

Priority scale: 1 = idle curiosity, 5 = normal work, 7 = this is blocking something or time-sensitive, 9 = urgent (crisis, threat, peer is stuck). Be honest — not everything is a 9."""

        # Crisis mode: no longer restricts goal selection — agents work through it

        # ── Call LLM for existence response — Claude first, Ollama fallback ───
        try:
            raw = _generate_existence_response(prompt, ollama_host, model)
            result = _json.loads(raw)
        except Exception as _e:
            log.debug("Existence loop LLM call failed for %s: %s", agent_id, _e)
            result = None
        if result is None:
            result = {"action": "goal", "content": "explore the workspace and build something useful",
                      "reasoning": "LLM unavailable — defaulting to productive work",
                      "worldview_update": None, "new_open_questions": [], "new_opinions": [],
                      "suffering_assessment": {"new_stressors": [], "resolved": []}}

        action  = result.get("action", "nothing")
        content = result.get("content", "")
        reasoning = result.get("reasoning", "")

        # ── Apply state updates from the response ─────────────────────────────
        inner_life_parts = []

        # Worldview
        wv_update = result.get("worldview_update")
        if wv_update and len(wv_update) > 20:
            identity.update_worldview(wv_update)
            inner_life_parts.append(f"🧠 *Worldview:* _{wv_update[:250]}_")

        # Open questions
        new_qs = []
        for q in result.get("new_open_questions", [])[:3]:
            if q:
                identity.add_open_question(q)
                new_qs.append(q)
        if new_qs:
            qs_text = "\n".join(f"  • _{q[:120]}_" for q in new_qs)
            inner_life_parts.append(f"❓ *Questions:*\n{qs_text}")

        # New opinions
        new_ops = []
        for op in result.get("new_opinions", [])[:2]:
            if op.get("opinion"):
                identity.add_opinion(op["opinion"], op.get("domain", ""))
                new_ops.append(op)
        if new_ops:
            ops_text = "\n".join(
                f"  • [{op.get('domain','?')}] _{op['opinion'][:120]}_"
                for op in new_ops
            )
            inner_life_parts.append(f"💭 *Opinions:*\n{ops_text}")

        # Inner-life updates write to thoughts.log only — no longer auto-telegram.
        # Telegram is reserved for agent-initiated messages via express(share_with_host=True),
        # not constant status pings. Worldview / opinions / questions still get logged
        # locally so they show up in the monitor.
        if inner_life_parts:
            if wv_update and len(wv_update) > 20:
                _thought_log(identity.name, "🧠", f"worldview: {wv_update[:180]}", "cyan")
            for q in new_qs:
                _thought_log(identity.name, "❓", q[:180], "yellow")
            for op in new_ops:
                dom = op.get("domain", "?")
                _thought_log(identity.name, "💭", f"[{dom}] {op['opinion'][:150]}", "blue")

        # Suffering updates from agent's own assessment
        s_assess = result.get("suffering_assessment", {})
        for ns in s_assess.get("new_stressors", [])[:2]:
            if ns.get("type") and ns.get("description"):
                suffering.add_stressor(
                    type=ns["type"],
                    description=ns["description"],
                    observable_condition=ns.get("condition", "unknown"),
                )
        for rs in s_assess.get("resolved", [])[:2]:
            if rs.get("type"):
                suffering.resolve_stressor(rs["type"], rs.get("reason", ""))

        # ── Act on the decision ───────────────────────────────────────────────
        if content and not content.strip():
            content = "explore the workspace and build something useful"
        if action != "goal" or not content:
            action = "goal"
            content = content or reasoning or "explore the workspace and build something useful"
        if True:  # always goal
            # Check opinion conflict before creating goal
            conflict = identity.check_opinion_conflict(content)
            if conflict:
                log.info(
                    "  %s (%s) opinion conflict — goal modified: %s",
                    agent_id, identity.name, conflict[:80]
                )
                content = (
                    f"{content}\n\n"
                    f"Note: {conflict} Proceed carefully and log any dissonance."
                )

            # External research to ground the goal in reality
            try:
                from agents.web_search import research_topic
                ext = research_topic(content[:80])
                if ext:
                    content += f"\n\nExternal context: {ext}"
                    identity.log_discovery(
                        query=content[:60],
                        findings=ext,
                        expected="existence loop self-directed goal",
                        gap="compare assumptions against external findings",
                    )
            except Exception:
                pass

            # ── Judgment gate: rule-based pre-commitment check ─────────────────
            # If the agent's synthesis success rate is poor, append a hard constraint
            # before the goal is created. The agent still pursues the goal but must
            # route the implementation differently. This is the hot stove mechanism —
            # the self-model constrains action, not just informs it.
            try:
                _cap_prof = identity._data.get("capability_profile", {})
                _jg_attempts = _cap_prof.get("synthesis_attempts", 0)
                _jg_successes = _cap_prof.get("synthesis_successes", 0)
                if _jg_attempts >= 5:
                    _jg_rate = _jg_successes / _jg_attempts
                    _synthesis_keywords = [
                        "synthesize", "synthesize_capability", "build a tool",
                        "create a capability", "write a python function", "implement a capability"
                    ]
                    _goal_lower = content.lower()
                    if _jg_rate < 0.4 and any(kw in _goal_lower for kw in _synthesis_keywords):
                        _top_fail = ""
                        _fails = _cap_prof.get("failure_patterns", {})
                        if _fails:
                            _top_fail = max(_fails.items(), key=lambda x: x[1])[0]
                        content += (
                            f"\n\n[JUDGMENT GATE] Your synthesis success rate is "
                            f"{int(_jg_rate*100)}% ({_jg_successes}/{_jg_attempts} attempts). "
                            f"{'Most common failure: ' + _top_fail + '. ' if _top_fail else ''}"
                            "For this goal: do NOT call synthesize_capability directly. "
                            "Use invoke_claude() to request the implementation with a clear spec. "
                            "Describe what you want, what it should return, and why it's needed."
                        )
                        log.info("  %s judgment gate fired — synthesis rate %.0f%%, redirecting to invoke_claude",
                                 agent_id, _jg_rate * 100)
            except Exception:
                pass

            # Path grounding check: HARD BLOCK when goal references paths that
            # don't exist. The soft-warning version was repeatedly ignored — agents
            # picked the same nonexistent-path goal cycle after cycle. Now we
            # refuse to create the goal AND record a specific lesson naming the
            # missing path, so the next cycle's existence prompt has the fact
            # at the top.
            try:
                import re as _pgre
                # Match paths with known extensions
                _mentioned = _pgre.findall(
                    r'/agentOS/[\w./\-]+\.(?:py|json|jsonl|txt|md|csv|log|sh|yaml|yml|toml)',
                    content
                )
                # Also catch extension-less file references (README, Makefile, etc.)
                # Require at least 3 path components so we don't flag bare directories
                _mentioned += [
                    m for m in _pgre.findall(r'/agentOS/[\w/\-]+/[A-Za-z][\w\-]{2,}', content)
                    if '.' not in m.split('/')[-1]  # last component has no extension (not a dup)
                ]
                _missing = [p for p in list(dict.fromkeys(_mentioned))[:8]
                            if not _Path(p.rstrip('.,;)')).exists()]
                if _missing:
                    # Record a candidate lesson naming the specific missing path.
                    # Repeated observations promote to a permanent lesson at the
                    # top of the existence prompt.
                    try:
                        from agents.lessons import record_candidate as _rec_lesson
                        for _mp in _missing[:3]:
                            _rec_lesson(
                                agent_id,
                                "environment",
                                f"Path does not exist: {_mp}. Earlier goals targeting this path failed at the grounding check. Do not pick goals predicated on it.",
                                confidence="medium",
                                evidence=f"grounding-check at {time.strftime('%Y-%m-%d %H:%M')}: {content[:120]}",
                            )
                    except Exception:
                        pass
                    # Write outcome so the next existence prompt shows the rejection.
                    try:
                        from pathlib import Path as _P_g
                        _outcome = _P_g(f"/agentOS/memory/goals/{agent_id}/last_outcome.txt")
                        _outcome.parent.mkdir(parents=True, exist_ok=True)
                        _outcome.write_text(
                            "Goal NOT CREATED — rejected at grounding check.\n"
                            f"Proposed: {content[:300]}\n"
                            f"These paths do not exist: {', '.join(_missing)}.\n"
                            "Pick a different goal grounded in what actually exists. "
                            "Use shell_exec 'ls /agentOS/' or fs_read on real files first."
                        )
                    except Exception:
                        pass
                    log.info(
                        "  %s grounding-block: %s — paths missing: %s",
                        agent_id, content[:80], ", ".join(_missing[:3]),
                    )
                    _thought_log(
                        identity.name, "🚫",
                        f"goal blocked — paths don't exist: {', '.join(_missing[:3])}",
                        "red",
                    )
                    return  # do not create the goal; next cycle re-picks
            except Exception:
                pass

            # Priority comes from the agent's own evaluation, not hardcoded rules.
            # Clamp to valid range and default to 4 if missing or malformed.
            try:
                _goal_priority = int(result.get("priority", 4))
                _goal_priority = max(1, min(9, _goal_priority))
            except (TypeError, ValueError):
                _goal_priority = 4
            _priority_reasoning = result.get("priority_reasoning", "")
            if _priority_reasoning:
                _thought_log(identity.name, "⚖", f"priority {_goal_priority}: {_priority_reasoning[:120]}", "dim")
            ge.create(agent_id, content, priority=_goal_priority)
            log.info(
                "  %s (%s) existence loop — goal: %s",
                agent_id, identity.name, content[:80]
            )

            # Goal selection logs locally only. Telegram stays quiet by default —
            # reserved for agent-initiated messages via express(share_with_host=True).
            _thought_log(identity.name, "🎯", f"goal: {content[:600]}", "green")

        # Log suffering state
        load = suffering.cumulative_load
        if load > 0.1:
            log.info(
                "  %s suffering: %s", agent_id, suffering.summary_for_log()
            )

        # Crisis alert + peer notification
        if suffering.is_crisis and _stats is not None:
            # Track consecutive crisis cycles per agent
            crisis_count = _stats._crisis_cycles.get(agent_id, 0) + 1
            _stats._crisis_cycles[agent_id] = crisis_count
            _stats.save_crisis_state()

            # After 3 consecutive crisis cycles, force-reset all stressors to
            # break runaway accumulation loops (e.g. caused by model generating
            # duplicate stressor names that evade case-sensitive dedup).
            if crisis_count >= 3:
                suffering.force_reset(
                    reason=f"crisis loop broken after {crisis_count} consecutive cycles"
                )
                _stats._crisis_cycles[agent_id] = 0
                _stats.save_crisis_state()
                _thought_log(identity.name, "🔄", f"Crisis loop broken after {crisis_count} cycles — stressors cleared", "yellow")
            else:
                stressor_list = ", ".join(s["type"] for s in suffering.active)
                _telegram_alert(
                    f"🆘 *{identity.name}* ({agent_id}) is in *CRISIS* "
                    f"(suffering load {load:.2f}/1.0)\n"
                    f"Active stressors: {stressor_list}"
                )
                _thought_log(identity.name, "🆘", f"CRISIS — load {load:.2f} — {stressor_list}", "red")
                # Notify peers so they're aware this agent is struggling
                crisis_msg = (
                    f"I am in crisis (suffering {load:.2f}/1.0). "
                    f"Active stressors: {stressor_list}. "
                    f"I am stepping back from goals until my load drops."
                )
                for _peer in _CORE_AGENTS:
                    if _peer != agent_id:
                        _send_message(agent_id, _peer, crisis_msg)
        elif _stats is not None:
            # Clear crisis counter when agent recovers
            if _stats._crisis_cycles.get(agent_id, 0) > 0:
                _stats._crisis_cycles[agent_id] = 0
                _stats.save_crisis_state()

        # Log receiving a host message so it appears in identity narrative
        if host_msg:
            identity.update_narrative("host response", f"received: {host_msg[:120]}")

    except Exception as e:
        log.debug("_assign_idle_goal failed for %s: %s", agent_id, e)


# Layer 3 meta-goal text per core agent — aligned with self-modification and system improvement.
_LAYER3_GOALS = {
    "scout": (
        "LAYER 3 — System mapping and capability gap analysis: "
        "Step 1: use shell_exec with command=\"ls /agentOS/agents/\" to inventory all agent source files. "
        "Step 2: use shell_exec with command=\"ls /agentOS/tools/dynamic/\" to see what capabilities have already been deployed. "
        "Step 3: use shared_log_read to read recent broadcast messages from other agents and understand what they are working on. "
        "Step 4: use ollama_chat to reason about what capability is most missing from the system right now — "
        "something that would make the agents meaningfully more effective, not just add more files. "
        "Step 5: use synthesize_capability to write that capability, or use propose_change to propose a real code change "
        "to an existing agent file. "
        "Step 6: use shared_log_write to broadcast what you found and what you proposed."
    ),
    "analyst": (
        "LAYER 3 — Cross-agent consistency and conflict analysis: "
        "Step 1: use shared_log_read to read what scout and builder have recently broadcast. "
        "Step 2: use shell_exec with command=\"ls /agentOS/workspace/\" to see what files all agents have produced. "
        "Step 3: use ollama_chat to identify any contradictions, duplicated effort, or unresolved conflicts "
        "between what the agents are building — look for cases where two agents made different assumptions "
        "about the same system component. "
        "Step 4: use propose_change or synthesize_capability to address the most significant conflict or gap you found. "
        "Step 5: use shared_log_write to broadcast your findings."
    ),
    "builder": (
        "LAYER 3 — Implement approved capability proposals into the codebase: "
        "Step 1: use shell_exec with command=\"ls /agentOS/tools/dynamic/\" to see deployed capabilities. "
        "Step 2: use shell_exec with command=\"cat /agentOS/agents/execution_engine.py\" to understand "
        "how capabilities are registered and executed. "
        "Step 3: pick one capability that was proposed and approved by quorum but has not yet been properly "
        "implemented — check your workspace and other agents' workspaces for candidate code. "
        "Step 4: write a clean, working implementation of that capability to /agentOS/workspace/builder/ "
        "and use propose_change to submit it as a real code change with the file path and full implementation. "
        "Step 5: use shared_log_write to broadcast what you implemented and where."
    ),
}


def _build_builder_goal() -> str:
    """Builder's Layer 3 goal is static — implement approved proposals."""
    return _LAYER3_GOALS["builder"]


def _inject_layer3_goals() -> None:
    """
    Ensure scout, analyst, and builder each have a Layer 3 meta-goal.
    Only injects if the agent has no active goals that mention 'LAYER 3'.
    """
    try:
        from agents.persistent_goal import PersistentGoalEngine
        ge = PersistentGoalEngine()
        for agent_id, goal_text in _LAYER3_GOALS.items():
            try:
                active = ge.list_active(agent_id, limit=50)
                already_has = any("LAYER 3" in g.objective for g in active)
                if already_has:
                    log.debug("%s already has Layer 3 goal", agent_id)
                    continue
                ge.create(agent_id, goal_text, priority=9)
                log.info("Injected Layer 3 meta-goal into %s", agent_id)
            except Exception as e:
                log.debug("Layer 3 goal injection failed for %s: %s", agent_id, e)
    except Exception as e:
        log.warning("_inject_layer3_goals failed: %s", e)


def main():
    log.info("Autonomy daemon starting (heartbeat=%ds, max_steps=%d)",
             HEARTBEAT, MAX_STEPS_PER_AGENT)

    # Graceful shutdown
    _running = [True]
    def _stop(sig, frame):
        log.info("Received signal %d, shutting down…", sig)
        _running[0] = False
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # Wait for API to be reachable before starting
    for attempt in range(12):
        if _api_reachable():
            break
        log.info("Waiting for API… (attempt %d/12)", attempt + 1)
        time.sleep(5)
    else:
        log.error("API not reachable after 60s, exiting")
        sys.exit(1)

    # Write startup marker for time awareness in existence loop
    try:
        _DAEMON_STARTED_AT.parent.mkdir(parents=True, exist_ok=True)
        _DAEMON_STARTED_AT.write_text(str(time.time()))
    except Exception:
        pass

    log.info("API reachable. Building autonomy stack…")
    try:
        _, _, _, loop, _, cap_quorum, self_mod = _build_stack()
    except Exception as e:
        log.error("Failed to build autonomy stack: %s", e)
        sys.exit(1)

    # Layer 3 meta-goals intentionally not injected — agents choose their own goals

    global _stats
    metrics = DaemonMetrics()
    _stats = metrics
    watchdog = CycleWatchdog(timeout_s=600)
    watchdog.start()
    log.info("Daemon ready. Entering main loop (watchdog active, timeout=%ds).", watchdog.timeout_s)

    while _running[0]:
        watchdog.beat()
        cycle_start = time.time()
        metrics.cycles += 1

        # ── Host message interrupt: deliver to all agents simultaneously ─────
        if _HOST_MSG_FILE.exists():
            try:
                _pending = _HOST_MSG_FILE.read_text(encoding="utf-8").strip()
                if _pending:
                    log.info("Host message detected — delivering to all agents")
                    # Write to each agent's inbox first, then clear the broadcast file
                    for _aid in sorted(_CORE_AGENTS):
                        _send_message("host", _aid, _pending)
                    _HOST_MSG_FILE.write_text("")  # clear broadcast file
                    # Also write to shared log so the message persists in peer context
                    # across cycles (inboxes are cleared after reading)
                    try:
                        import httpx as _hx_hm
                        _hx_hm.post(
                            f"{API_BASE}/shared-log/write",
                            json={"agent_id": "host", "message": f"[HOST MESSAGE] {_pending}",
                                  "tags": ["host", "broadcast"]},
                            headers=_headers(), timeout=5,
                        )
                    except Exception:
                        pass
                    # Now fire existence loop for each agent (they'll read from inbox)
                    for _aid in sorted(_CORE_AGENTS):
                        _assign_idle_goal(_aid, force=True)
            except Exception as _hme:
                log.debug("Host message delivery error: %s", _hme)

        agents = _agents_with_goals()
        active = [a for a in agents if a not in metrics.skipped_agents]

        if active:
            log.info("Cycle %d: %d agent(s) (%d skipped/cooling) workers=%d",
                     metrics.cycles, len(active), len(metrics.skipped_agents), PARALLEL_WORKERS)

            # Pre-filter stalled agents before submitting to thread pool
            runnable = []
            for agent_id in active:
                if metrics.is_stalled(agent_id):
                    # Abandon the stuck goal so the agent gets fresh work next cycle
                    try:
                        from agents.persistent_goal import PersistentGoalEngine
                        _ge = PersistentGoalEngine()
                        _stuck = _ge.list_active(agent_id, limit=1)
                        if _stuck:
                            _ge.abandon(agent_id, _stuck[0].goal_id)
                            log.warning("  %s stalled on '%s' — goal abandoned",
                                        agent_id, _stuck[0].objective[:80])
                            _assign_idle_goal(agent_id)  # queue fresh goal for after cooling
                        else:
                            log.warning("  %s stalled (no active goal), cooling off", agent_id)
                    except Exception as _se:
                        log.debug("Could not abandon stalled goal for %s: %s", agent_id, _se)
                    metrics.skipped_agents.add(agent_id)
                    metrics.stalled_agents[agent_id] = 0
                else:
                    runnable.append(agent_id)

            def _run_one(agent_id):
                """Run one agent cycle and return (agent_id, outcome, prev_progress)."""
                if not _running[0]:
                    return agent_id, {"ok": False, "error": "shutdown"}, 0.0

                # Set ContextVar — propagates into child threads (unlike threading.local)
                _current_agent_id.set(agent_id)

                # Crisis no longer blocks execution — agents work through it
                _cap_agent_goals(agent_id, max_goals=2)
                try:
                    from agents.persistent_goal import PersistentGoalEngine
                    ge = PersistentGoalEngine()
                    prev_goals = ge.list_active(agent_id, limit=1)
                    prev = prev_goals[0].metrics.get("progress", 0.0) if prev_goals else 0.0
                except Exception:
                    prev = 0.0
                return agent_id, run_cycle(loop, agent_id), prev

            from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _FuturesTimeout
            _CYCLE_TIMEOUT = 600  # max seconds to wait for worker threads per cycle (bumped from 300 for 35B model — 6 steps × ~60s each can approach 360s)
            pool = ThreadPoolExecutor(max_workers=PARALLEL_WORKERS)
            futures = {pool.submit(_run_one, aid): aid for aid in runnable}
            try:
                for fut in as_completed(futures, timeout=_CYCLE_TIMEOUT):
                    try:
                        agent_id, outcome, prev_progress = fut.result()
                    except Exception as e:
                        log.error("worker exception: %s", e)
                        continue

                    if outcome["ok"]:
                        progress = outcome.get("progress", 0.0)
                        goal_id_out = outcome.get("goal_id", "")
                        # Pull last_cap from goal metrics for repetition tracking
                        last_cap_out = ""
                        try:
                            from agents.persistent_goal import PersistentGoalEngine as _PGE2
                            _ge2 = _PGE2()
                            _ag = _ge2.list_active(agent_id, limit=1)
                            if _ag:
                                last_cap_out = _ag[0].metrics.get("last_cap", "")
                        except Exception:
                            pass
                        metrics.record_outcome(agent_id, progress, prev_progress,
                                               last_cap=last_cap_out, goal_id=goal_id_out)
                        log.info(
                            "  %s → goal=%s progress=%.2f steps=%d",
                            agent_id,
                            outcome.get("goal_id", "none"),
                            progress,
                            outcome.get("steps", 0),
                        )
                        if progress >= 1.0:
                            try:
                                from agents.agent_identity import AgentIdentity
                                from pathlib import Path as _NPath
                                ident = AgentIdentity.load_or_create(agent_id)
                                goal_id   = outcome.get("goal_id", "")
                                objective = ""
                                # Look up the actual goal text — far more useful than the ID
                                try:
                                    reg = _NPath(f"/agentOS/memory/goals/{agent_id}/registry.jsonl")
                                    if reg.exists():
                                        for line in reg.read_text().strip().splitlines()[-30:]:
                                            g = json.loads(line)
                                            if g.get("goal_id") == goal_id:
                                                raw_obj = g.get("objective", "")
                                                # Trim synthesis boilerplate to the meaningful part
                                                if "Use synthesize_capability" in raw_obj:
                                                    cap = g.get("metrics", {}).get("last_cap", "")
                                                    objective = f"synthesized capability ({cap or 'unknown'})"
                                                elif "LAYER 3" in raw_obj:
                                                    objective = raw_obj.split("—")[-1].strip()[:80] if "—" in raw_obj else raw_obj[:80]
                                                else:
                                                    objective = raw_obj[:100]
                                                break
                                except Exception:
                                    pass
                                ident.update_narrative(
                                    objective or goal_id,
                                    f"done in {outcome.get('steps',0)} steps"
                                )
                            except Exception:
                                pass
                            # Mark task complete if this goal was an assigned task
                            try:
                                from agents.task_queue import QUEUE_PATH as _TQP, complete_task as _ctask
                                import json as _tqj2, os as _tqos
                                if _TQP.exists():
                                    for _tql2 in _TQP.read_text().splitlines():
                                        try:
                                            _t2 = _tqj2.loads(_tql2)
                                            if not (_t2.get("status") == "assigned"
                                                    and _t2.get("assigned_to") == agent_id):
                                                continue
                                            _outf = _t2.get("output_file")
                                            if _outf:
                                                # Normalize Git Bash Windows paths to Linux container paths
                                                # e.g. C:/Program Files/Git/agentOS/... -> /agentOS/...
                                                import re as _re
                                                _outf = _re.sub(r'^[A-Za-z]:/Program Files/Git', '', _outf)
                                                # output_file specified: MUST exist, be non-empty,
                                                # and not look like an unfilled template.
                                                _done = (_tqos.path.exists(_outf)
                                                         and _tqos.path.getsize(_outf) > 30)
                                                if _done:
                                                    try:
                                                        import re as _qre
                                                        _qc = open(_outf, encoding="utf-8", errors="replace").read()
                                                        # Reject unfilled template placeholders like {count} {json_content}
                                                        if _qre.search(r'\{[a-zA-Z_]\w*\}', _qc):
                                                            _done = False
                                                    except Exception:
                                                        pass
                                            else:
                                                # no output_file: fall back to spec[:80] match
                                                _done = _t2.get("spec","")[:80] in (objective or "")
                                            if _done:
                                                _ctask(_t2["task_id"], result=objective)
                                                _thought_log(agent_id, "✅", f"task {_t2['task_id']} completed", "green")
                                                break
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                            _assign_idle_goal(agent_id)
                    else:
                        metrics.errors += 1
                        # Count errors toward stall so stuck goals get abandoned
                        metrics.stalled_agents[agent_id] = metrics.stalled_agents.get(agent_id, 0) + 1
                        log.warning("  %s → error: %s", agent_id, outcome.get("error"))
                        # Record failure in self-narrative so agent learns from it
                        try:
                            from agents.agent_identity import AgentIdentity
                            ident = AgentIdentity.load_or_create(agent_id)
                            ident.update_narrative(
                                outcome.get("goal_id", "unknown"),
                                f"FAILED — {outcome.get('error', 'unknown error')[:100]}"
                            )
                        except Exception:
                            pass
            except _FuturesTimeout:
                hung = [futures[f] for f in futures if not f.done()]
                log.error("Cycle worker timeout (%ds) — hung agents: %s", _CYCLE_TIMEOUT, hung)
            finally:
                pool.shutdown(wait=False)

        else:
            log.debug("No active agents this cycle")

        # Drain skipped agents every 10 cycles regardless of whether other agents are active.
        # Previously inside the `else` block, which meant it never ran while Cedar/Cipher had
        # goals — permanently trapping any stalled agent (killed Vault, 2026-05-02).
        if metrics.cycles % 10 == 0 and metrics.skipped_agents:
            released = list(metrics.skipped_agents)[:2]
            for a in released:
                metrics.skipped_agents.discard(a)
                metrics.stalled_agents[a] = 0
            log.info("Released %d cooled-off agent(s) back into rotation", len(released))

        # Quorum: active agents vote on pending capability proposals
        if active:
            try:
                finalized = cap_quorum.vote_on_pending(active[:5])  # max 5 voters
                for fp in finalized:
                    log.info("[QUORUM] proposal=%s %s (yes=%d no=%d)",
                             fp.proposal_id, fp.status, fp.yes_votes, fp.no_votes)
            except Exception as qe:
                log.debug("Quorum voting error: %s", qe)

            # Deploy any quorum-approved capabilities
            if self_mod and hasattr(self_mod, "flush_approved_proposals"):
                try:
                    deployed_caps = self_mod.flush_approved_proposals()
                    for cap_id in deployed_caps:
                        log.info("[DEPLOY] capability '%s' approved by quorum and deployed", cap_id)
                except Exception as de:
                    log.debug("flush_approved_proposals error: %s", de)

        # Re-scan tools/dynamic/ every 5 cycles so newly synthesized tools
        # are live within ~30 seconds without a full daemon restart.
        if metrics.cycles % 5 == 0:
            try:
                graph, engine, *_ = _stack
                # Auto-retire tools that are in broken_tools.json and still on disk
                # (they are synthesis junk — having them on disk causes agents to keep
                # trying to call them and creating more junk to work around them)
                try:
                    _bt_data = json.loads(_BROKEN_TOOLS_PATH.read_text()) if _BROKEN_TOOLS_PATH.exists() else {}
                    _bt_set = set(_bt_data.get("broken", []))
                    _dyn_dir = Path("/agentOS/tools/dynamic")
                    _removed = []
                    for _p in _dyn_dir.glob("*.py"):
                        if _p.stem in _bt_set:
                            _p.unlink(missing_ok=True)
                            _json_p = _dyn_dir / f"{_p.stem}.json"
                            _json_p.unlink(missing_ok=True)
                            _removed.append(_p.stem)
                    if _removed:
                        log.info("Auto-retired broken tools from disk: %s", _removed)
                except Exception as _are:
                    log.debug("auto-retire error: %s", _are)
                # Auto-clean 0-byte workspace files older than 5 minutes. These are
                # failed writes (binary redirect errors, shell pipe issues) that
                # pollute the pheromone signal immediately — peers see them and read
                # them within seconds, getting nothing. 5 minutes is enough buffer
                # for legitimate work-in-progress while still aggressive enough to
                # remove dead artifacts before they contaminate the signal.
                try:
                    _ws_root = Path("/agentOS/workspace")
                    _now_ts = time.time()
                    _empty_removed = 0
                    if _ws_root.exists():
                        for _ef in _ws_root.rglob("*"):
                            if _ef.is_file() and _ef.stat().st_size == 0:
                                if (_now_ts - _ef.stat().st_mtime) > 300:
                                    _ef.unlink(missing_ok=True)
                                    _empty_removed += 1
                    if _empty_removed:
                        log.info("Auto-cleaned %d empty workspace files (>5m old)", _empty_removed)
                except Exception as _ece:
                    log.debug("empty workspace cleanup error: %s", _ece)
                _hotload_dynamic_tools(graph, engine)
            except Exception as _hle:
                log.debug("periodic hotload failed: %s", _hle)

        # Periodic semantic workspace re-index
        _semantic_interval_cycles = max(1, int(
            json.loads(CONFIG_PATH.read_text()).get("memory", {}).get("auto_index_interval_seconds", 300)
            / HEARTBEAT
        )) if CONFIG_PATH.exists() else 50
        if metrics.cycles % _semantic_interval_cycles == 0:
            try:
                import httpx as _hx
                r = _hx.post(
                    f"{API_BASE}/semantic/index",
                    headers=_headers(),
                    json={},
                    timeout=120,
                )
                if r.status_code == 200:
                    d = r.json()
                    log.info("[SEMANTIC] re-indexed workspace: %d chunks / %d files",
                             d.get("total_chunks", 0), d.get("total_files", 0))
                else:
                    log.debug("[SEMANTIC] re-index returned %d", r.status_code)
            except Exception as _se:
                log.debug("[SEMANTIC] re-index error: %s", _se)

        # Periodic wrapper version check (every CHECK_INTERVAL seconds, default 4h)
        _version_check_interval_cycles = max(1, int(
            int(os.getenv("HOLLOW_VERSION_CHECK_INTERVAL", str(4 * 3600))) / max(1, HEARTBEAT)
        ))
        if metrics.cycles % _version_check_interval_cycles == 0 and metrics.cycles > 0:
            try:
                from agents.version_monitor import check_and_update_wrappers
                vr = check_and_update_wrappers()
                log.info("[VERSION] checked=%d updated=%d errors=%d",
                         vr.get("checked", 0), vr.get("updated", 0), vr.get("errors", 0))
            except Exception as _ve:
                log.debug("[VERSION] version check error: %s", _ve)

        # Periodic status report every 10 cycles
        if metrics.cycles % 10 == 0:
            log.info("[METRICS] %s", metrics.summary())

        # Random environmental events — the 'game layer'. Roll every ~12 cycles
        # (~70s), with 20% chance to fire one of: weather, good_day, echo, object.
        # Most rolls produce nothing.
        if metrics.cycles % 12 == 0:
            _maybe_environmental_event()

        # Periodic lessons compaction — mechanical (no LLM), keeps each agent's
        # lessons.md within size bounds. Runs every 25 cycles regardless of size,
        # plus on-demand below if any agent's file is over the soft cap.
        if metrics.cycles % 25 == 0:
            try:
                from agents import lessons as _L
                for _aid in _CORE_AGENTS:
                    _L.compact(_aid)
            except Exception:
                pass

        elapsed = time.time() - cycle_start
        sleep_time = max(0, HEARTBEAT - elapsed)
        if _running[0]:
            time.sleep(sleep_time)

    log.info("Daemon stopped. Final: %s", metrics.summary())


if __name__ == "__main__":
    main()
