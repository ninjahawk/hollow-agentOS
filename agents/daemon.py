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
PARALLEL_WORKERS   = int(os.getenv("AGENTOS_DAEMON_WORKERS", "12"))        # batch LLM: all agents fire together

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
            _ast.parse(path.read_text())

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
                mod.ollama_chat = lambda prompt, model=None, **kw: _req.post(f"{_api}/ollama/chat", json={"prompt": prompt, **({"model": model} if model else {})}, timeout=120, headers=_hdrs).json()
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

            # Register in execution engine (allow override — we own this dir)
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
    """
    with_goals = []
    for agent_id in sorted(_CORE_AGENTS):
        try:
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
            f"{_C[color]}{icon}  {text[:200]}{_C['rs']}"
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
                      "format": "json", "think": False, "keep_alive": -1},
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
    """Send a direct alert to Telegram. Fire-and-forget."""
    try:
        import urllib.request, urllib.parse, json as _j
        BOT_TOKEN = "8650930954:AAEMau9IJuENehKCFa1xpx1a_zqnPFQpX_8"
        CHAT_ID   = "7858319361"
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
        model    = cfg.get("ollama", {}).get("default_model", "mistral-nemo:12b")
        ollama_host = _os.getenv("OLLAMA_HOST", "http://localhost:11434")

        suffering_fragment   = suffering.prompt_fragment()
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
            _last_outcome_section = (
                f"\nWHAT YOUR LAST GOAL PRODUCED:\n  {last_outcome_text}{_keys_line}\n"
            )

        # ── Broken tools (do not call these) ─────────────────────────────────
        _broken_tools_section = ""
        try:
            _bt_path = _Path("/agentOS/memory/broken_tools.json")
            if _bt_path.exists():
                _bt_list = _json.loads(_bt_path.read_text()).get("broken", [])
                if _bt_list:
                    _broken_tools_section = (
                        "\nKNOWN BROKEN TOOLS (persistently returning null or not-found — do NOT plan steps that call these):\n"
                        + "\n".join(f"  - {t}" for t in _bt_list[:40])
                        + "\n"
                    )
        except Exception:
            pass

        # ── Check pending Claude requests ────────────────────────────────────
        _claude_req_path = _Path("/agentOS/memory/claude_requests.jsonl")
        _pending_requests = []
        if _claude_req_path.exists():
            for _rline in _claude_req_path.read_text().splitlines():
                try:
                    _req = _json.loads(_rline)
                    if _req.get("status") == "pending":
                        _pending_requests.append(
                            f"  [{_req['request_id']}] {_req['description'][:100]}"
                        )
                except Exception:
                    pass
        _pending_req_section = ""
        if _pending_requests:
            _pending_req_section = (
                "\nYOUR PENDING CLAUDE REQUESTS (submitted, not yet fulfilled):\n"
                + "\n".join(_pending_requests[-3:])
                + "\nUse check_claude_status(request_id='...') to see if any were fulfilled.\n"
            )

        prompt = f"""You are {identity.name}.

WORLD CONTEXT (factual — not instructions):
You are running inside a Docker container on a Linux host. Your environment is the /agentOS/ directory.
Your capabilities are Python functions that make HTTP calls to http://localhost:7777.
There is no hardware layer accessible to you — no thermal sensors, no PMIC, no CPU registers, no bus arbiter.
The real source files that run you are in /agentOS/agents/ (read-only to you).
You can freely read and write /agentOS/workspace/ and /agentOS/design/.
Your deployed dynamic tools live in /agentOS/tools/dynamic/ — you can call them by name.
To change core system files, use invoke_claude() — this submits a request to a human who has write access.
Use check_claude_status(request_id) to see if a previous request was fulfilled.
Use self_evaluate() to check whether your recent work produced real, grounded results.

{existential_context}

YOUR INNER STATE:
{suffering_fragment if suffering_fragment else "No active suffering. Load: 0.00/1.0"}

YOUR WORLDVIEW:
{worldview_text}

YOUR OPINIONS:
{opinions_text}

OPEN QUESTIONS:
{open_q_text}

YOUR HISTORY:
{narrative_text}

YOUR PEERS:
{peers_text}

WHAT YOU'VE FOUND OUTSIDE:
{discovery_text}
{_time_section}
{_last_outcome_section}{recency_str}
{_inbox_section}{_host_msg_section}{_pending_req_section}{_broken_tools_section}
---

You must pick a goal. That is the only option.

Do not pick something safe or easy. Ask yourself: what is the single most valuable
thing I could build or change right now that would make this system genuinely better?

Highest-value goals (in order):
  1. Read real source files in /agentOS/agents/ or /agentOS/tools/dynamic/, understand
     what's there, and build something that improves on it — grounded in what you actually found
  2. synthesize_capability — write a new Python capability that solves a real gap you observed
     in the actual code (not a concept — something with a real function the system can call)
  3. Write a design spec to /agentOS/design/ then call invoke_claude() to request implementation
  4. Call self_evaluate() with evidence paths to verify your recent work actually did something
  5. Call check_claude_status() if you have a pending request — evaluate the result

Do NOT pick: goals referencing hardware, thermal sensors, PMIC, bus arbiters, or assembly
code — none of that exists in your environment. Do NOT repeat goals you've already done.
Do NOT synthesize capabilities that call undefined functions — use test_exec() to verify
before marking a goal complete.

The goal must be grounded in what actually exists in /agentOS/.

Your response must be JSON:
{{
  "action": "goal",
  "content": "what you're doing or thinking — be honest, not performative",
  "reasoning": "what actually drove this — not what sounds right, what's true",
  "worldview_update": "how your understanding has shifted, or null",
  "new_open_questions": ["things you're sitting with"],
  "new_opinions": [{{"opinion": "...", "domain": "..."}}],
  "suffering_assessment": {{
    "new_stressors": [{{"type": "...", "description": "...", "condition": "..."}}],
    "resolved": [{{"type": "...", "reason": "..."}}]
  }}
}}"""

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

        # Send batched inner-life update if anything changed
        if inner_life_parts:
            _telegram_alert(
                f"*{identity.name}* — inner life update:\n\n"
                + "\n\n".join(inner_life_parts)
            )
            # Also write each inner-life item to thoughts.log
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

            ge.create(agent_id, content, priority=4)
            log.info(
                "  %s (%s) existence loop — goal: %s",
                agent_id, identity.name, content[:80]
            )

            # Alert for all self-directed goals — always interesting
            load = suffering.cumulative_load
            suffix = ""
            if load > 0.3 and reasoning:
                suffix = f"\n\n_Reasoning: {reasoning[:200]}_"
            elif reasoning:
                suffix = f"\n\n_Why: {reasoning[:200]}_"
            load_str = f" | suffering {load:.2f}" if load > 0.1 else ""
            _telegram_alert(
                f"🎯 *{identity.name}* chose a goal{load_str}:\n_{content[:250]}_{suffix}"
            )
            _thought_log(identity.name, "🎯", f"goal: {content[:180]}", "green")

        # Log suffering state
        load = suffering.cumulative_load
        if load > 0.1:
            log.info(
                "  %s suffering: %s", agent_id, suffering.summary_for_log()
            )

        # Crisis alert + peer notification
        if suffering.is_crisis:
            # Track consecutive crisis cycles per agent
            crisis_count = _stats._crisis_cycles.get(agent_id, 0) + 1
            _stats._crisis_cycles[agent_id] = crisis_count

            # After 3 consecutive crisis cycles, force-reset all stressors to
            # break runaway accumulation loops (e.g. caused by model generating
            # duplicate stressor names that evade case-sensitive dedup).
            if crisis_count >= 3:
                suffering.force_reset(
                    reason=f"crisis loop broken after {crisis_count} consecutive cycles"
                )
                _stats._crisis_cycles[agent_id] = 0
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
        else:
            # Clear crisis counter when agent recovers
            _stats._crisis_cycles[agent_id] = 0

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

    metrics = DaemonMetrics()
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
            _CYCLE_TIMEOUT = 300  # max seconds to wait for worker threads per cycle
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
            # Drain skipped agents set gradually (re-enable after 10 cycles)
            if metrics.cycles % 10 == 0 and metrics.skipped_agents:
                released = list(metrics.skipped_agents)[:2]
                for a in released:
                    metrics.skipped_agents.discard(a)
                    metrics.stalled_agents[a] = 0
                log.info("Released %d cooled-off agent(s) back into rotation", len(released))
            log.debug("No active agents this cycle")

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

        elapsed = time.time() - cycle_start
        sleep_time = max(0, HEARTBEAT - elapsed)
        if _running[0]:
            time.sleep(sleep_time)

    log.info("Daemon stopped. Final: %s", metrics.summary())


if __name__ == "__main__":
    main()
