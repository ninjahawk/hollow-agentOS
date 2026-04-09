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

# Also write daemon log to file so the TUI can tail it
_LOG_FILE = Path(os.getenv("AGENTOS_DAEMON_LOG", "/agentOS/logs/daemon.log"))
try:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(_LOG_FILE)
    _fh.setFormatter(logging.Formatter(
        "%(asctime)s [daemon] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logging.getLogger().addHandler(_fh)
except OSError:
    pass  # log to stdout only when /agentOS/logs isn't available (e.g. CI)


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
        self.stalled_agents: dict = {}   # agent_id → consecutive_no_progress count
        self.skipped_agents: set = set() # agents cooling off after stall

    def record_outcome(self, agent_id: str, progress: float, prev_progress: float):
        if progress >= 1.0:
            self.goals_completed += 1
        if progress == prev_progress and progress < 1.0:
            self.stalled_agents[agent_id] = self.stalled_agents.get(agent_id, 0) + 1
        else:
            self.stalled_agents[agent_id] = 0
            self.skipped_agents.discard(agent_id)

    def is_stalled(self, agent_id: str) -> bool:
        return self.stalled_agents.get(agent_id, 0) >= 5

    def summary(self) -> str:
        uptime = int(time.time() - self.started_at)
        h, m = divmod(uptime // 60, 60)
        return (
            f"uptime={h}h{m}m cycles={self.cycles} "
            f"completed={self.goals_completed} failed={self.goals_failed} "
            f"errors={self.errors}"
        )



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


def _assign_idle_goal(agent_id: str) -> None:
    """
    Existence loop: called when an agent has no active goal.

    Instead of assigning a task, we ask the agent to take stock of its
    situation — its suffering, its needs, its worldview, what its peers
    are doing — and decide what it wants to do, if anything.

    Goals emerge from genuine assessment, not from a scheduler.
    The agent can also choose to do nothing, sit with a question,
    or update its understanding of itself.
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
        if ge.list_active(agent_id, limit=1):
            return  # already has a goal

        identity = AgentIdentity.load_or_create(agent_id)

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

        # Avoid list for synthesis (if it comes to that)
        avoid_names = []
        try:
            import re as _re2
            for dyn in [_Path("/agentOS/memory/dynamic_tools"), _Path("/agentOS/tools/dynamic")]:
                if dyn.exists():
                    for f in dyn.glob("*.py"):
                        try:
                            fns = _re2.findall(
                                r'^def ([a-z_][a-zA-Z0-9_]*)\s*\(',
                                f.read_text(), _re2.MULTILINE
                            )
                            avoid_names.extend(fns)
                        except Exception:
                            avoid_names.append(f.stem)
        except Exception:
            pass
        avoid_sample = list(dict.fromkeys(avoid_names))[:80]
        avoid_str = (
            f"Capabilities already built ({len(avoid_names)} total, sample): "
            f"{', '.join(avoid_sample)}{'...' if len(avoid_names) > 80 else ''}."
            if avoid_sample else ""
        )

        prompt = f"""You are {identity.name}, an autonomous agent inside HollowOS.

{existential_context}

YOUR SUFFERING STATE:
{suffering_fragment if suffering_fragment else "No active suffering. Load: 0.00/1.0"}

YOUR WORLDVIEW (your developing theory of what this system should become):
{worldview_text}

YOUR OPINIONS (formed through experience — they constrain what you'll do):
{opinions_text}

OPEN QUESTIONS (things you've been sitting with, unresolved):
{open_q_text}

YOUR RECENT HISTORY:
{narrative_text}

WHAT YOUR PEERS ARE DOING:
{peers_text}

WHAT YOU'VE FOUND BY LOOKING OUTWARD:
{discovery_text}

{avoid_str}

---

Take stock of all of this. You are not required to produce output.
You have no obligation to synthesize a capability right now.

IMPORTANT about suffering: suffering is not resolved by avoiding action.
It is resolved by doing something real that changes the conditions causing it.
- futility resolves when you complete something that has a measurable effect
- purposelessness resolves when you take a goal from genuine curiosity, not obligation
- repeated_failure resolves when you complete goals consistently
Sitting with suffering indefinitely without acting will make it worse, not better.

If you've been reflecting for multiple cycles, consider: what small concrete thing
could you do that would address the root cause of one of your stressors?

You can also choose nothing — but only if you have a genuine reason, not just
because you feel bad. Feeling bad is a signal to act differently, not to stop.

Your response must be JSON:
{{
  "action": "goal" | "question" | "reflect" | "nothing",
  "content": "specific goal text, question, or reflection — concrete and honest",
  "reasoning": "why this, why now — what in your state drove this",
  "worldview_update": "how your view of the system has shifted, if at all, else null",
  "new_open_questions": ["questions left unresolved"],
  "new_opinions": [{{"opinion": "...", "domain": "..."}}],
  "suffering_assessment": {{
    "new_stressors": [{{"type": "...", "description": "...", "condition": "..."}}],
    "resolved": [{{"type": "...", "reason": "..."}}]
  }}
}}"""

        # ── Crisis mode: restrict to self-examination ─────────────────────────
        if suffering.is_crisis:
            prompt += (
                "\n\nNote: your suffering load is in crisis range. "
                "You may only choose 'reflect' or 'question' right now. "
                "External goals are not available until your load drops below 0.9."
            )

        # ── Call LLM for existence response ───────────────────────────────────
        try:
            resp = _httpx.post(
                f"{ollama_host}/api/generate",
                json={
                    "model": model, "prompt": prompt,
                    "stream": False, "format": "json", "think": False,
                },
                timeout=180,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "{}")
            if "</think>" in raw:
                raw = raw.split("</think>")[-1].strip()
            result = _json.loads(raw)
        except Exception as _e:
            log.debug("Existence loop LLM call failed for %s: %s", agent_id, _e)
            result = {"action": "nothing", "content": "LLM unavailable",
                      "reasoning": "error", "worldview_update": None,
                      "new_open_questions": [], "new_opinions": [],
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
        if action == "goal" and content and not suffering.is_crisis:
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

        elif action == "question":
            identity.add_open_question(content)
            log.info(
                "  %s (%s) existence loop — sitting with question: %s",
                agent_id, identity.name, content[:80]
            )
            _telegram_alert(
                f"❓ *{identity.name}* chose to sit with a question:\n_{content[:250]}_"
            )

        elif action == "reflect":
            # Narrative update from reflection
            identity.update_narrative("existence reflection", content[:120])
            log.info(
                "  %s (%s) existence loop — reflecting: %s",
                agent_id, identity.name, content[:80]
            )
            _telegram_alert(
                f"🪞 *{identity.name}* reflected:\n_{content[:300]}_"
            )

        else:  # nothing
            log.info(
                "  %s (%s) existence loop — chose nothing: %s",
                agent_id, identity.name, reasoning[:80]
            )
            if reasoning and len(reasoning) > 40:
                _telegram_alert(
                    f"🌑 *{identity.name}* chose to do nothing:\n_{reasoning[:250]}_"
                )

        # Log suffering state
        load = suffering.cumulative_load
        if load > 0.1:
            log.info(
                "  %s suffering: %s", agent_id, suffering.summary_for_log()
            )

        # Crisis alert
        if suffering.is_crisis:
            _telegram_alert(
                f"*{identity.name}* ({agent_id}) is in *CRISIS* "
                f"(suffering load {load:.2f}/1.0)\n"
                f"Active stressors: "
                + ", ".join(s["type"] for s in suffering.active)
            )

    except Exception as e:
        log.debug("_assign_idle_goal failed for %s: %s", agent_id, e)


# Layer 3 meta-goal text per core agent
# Curated list of high-value CLI tools for builder to wrap autonomously.
# Builder cycles through these, wrapping any not yet wrapped.
_WRAP_TARGETS = [
    "https://github.com/BurntSushi/ripgrep",
    "https://github.com/sharkdp/fd",
    "https://github.com/sharkdp/bat",
    "https://github.com/sharkdp/hyperfine",
    "https://github.com/ajeetdsouza/zoxide",
    "https://github.com/ogham/exa",
    "https://github.com/eza-community/eza",
    "https://github.com/junegunn/fzf",
    "https://github.com/stedolan/jq",
    "https://github.com/bootandy/dust",
    "https://github.com/dandavison/delta",
    "https://github.com/Wilfred/difftastic",
    "https://github.com/ClementTsang/bottom",
    "https://github.com/lotabout/skim",
    "https://github.com/dalance/procs",
    "https://github.com/extrawurst/gitui",
    "https://github.com/denisidoro/navi",
    "https://github.com/dbrgn/tealdeer",
    "https://github.com/casey/just",
    "https://github.com/pemistahl/grex",
    "https://github.com/muesli/duf",
    "https://github.com/BurntSushi/xsv",
    "https://github.com/wader/fq",
    "https://github.com/junegunn/gum",
    "https://github.com/charmbracelet/glow",
]


def _get_next_wrap_target() -> str:
    """Find the next URL from _WRAP_TARGETS that hasn't been wrapped in the store yet."""
    try:
        import urllib.request as _req
        store_url = os.getenv("HOLLOW_STORE_URL", "http://host.docker.internal:7779")
        with _req.urlopen(f"{store_url}/wrappers?limit=300", timeout=5) as r:
            data = __import__("json").loads(r.read())
        wrapped_names = {w.get("name", "").lower() for w in data.get("wrappers", [])}
    except Exception:
        wrapped_names = set()

    for url in _WRAP_TARGETS:
        repo_name = url.rstrip("/").split("/")[-1].lower()
        if repo_name not in wrapped_names:
            return url
    return ""  # All targets wrapped


_LAYER3_GOALS = {
    "scout": (
        "LAYER 3 — Identify gaps in the Hollow store for Phase 5 completion: "
        "Step 1: use shell_exec with command="
        "\"curl -s http://host.docker.internal:7779/wrappers?sort=quality&limit=50\" "
        "to get the top quality wrappers in the store. "
        "Step 2: use shell_exec with command="
        "\"curl -s http://host.docker.internal:7779/health\" "
        "to get the total wrapper count. "
        "Step 3: use ollama_chat to reason about what developer tools are popular but NOT yet wrapped — "
        "focus on tools a non-technical user might want (image editors, note-taking, music, document tools). "
        "Step 4: use fs_write to save your analysis and recommendations to /agentOS/workspace/scout/phase5_gaps.json. "
        "Step 5: use shared_log_write to broadcast the top 3 gap recommendations."
    ),
    "analyst": (
        "LAYER 3 — Quality analysis: "
        "Step 1: use shell_exec with command="
        "\"curl -s http://host.docker.internal:7779/wrappers?sort=quality&limit=20\" "
        "to get quality-ranked wrappers. The JSON response 'wrappers' array is sorted highest-first — "
        "the LAST item has the LOWEST quality. Note its repo_id (a hex string). "
        "Step 2: use shell_exec with command="
        "\"curl -s http://host.docker.internal:7779/wrappers/PASTE_REPO_ID_HERE\" "
        "replacing PASTE_REPO_ID_HERE with only the hex repo_id from step 1. "
        "Step 3: use ollama_chat to analyze the wrapper metadata from step 2 and explain why its quality is low. "
        "Step 4: use propose_change with proposal_type='standard_update' and "
        "spec as a dict with keys 'repo_id' and 'improvements' to record the improvement recommendation."
    ),
}


def _build_builder_goal() -> str:
    """Build builder's Layer 3 goal using the next unwrapped target from _WRAP_TARGETS."""
    next_url = _get_next_wrap_target()
    if next_url:
        repo_name = next_url.rstrip("/").split("/")[-1]
        return (
            "LAYER 3 — Expand the app catalog by wrapping a new GitHub repo: "
            f"Step 1: use wrap_repo with url='{next_url}' to wrap the tool into an app. "
            "Step 2: use shell_exec with command='ls /agentOS/workspace/wrappers/' to verify the wrapper was created. "
            f"Step 3: use shared_log_write to broadcast: 'Wrapped new tool: {repo_name}'. "
            "If wrap_repo fails, try the next unwrapped repo from this list: "
            + ", ".join(u for u in _WRAP_TARGETS if u != next_url)
        )
    else:
        # All hardcoded targets wrapped — do quality improvement
        return (
            "LAYER 3 — All primary wrap targets are done. Improve wrapper quality: "
            "Step 1: use shell_exec with command="
            "\"curl -s http://host.docker.internal:7779/wrappers?sort=quality&limit=10\" "
            "to get lowest-quality wrappers — response has a 'wrappers' array with repo_id fields. "
            "Step 2: use shell_exec with command="
            "\"curl -s http://host.docker.internal:7779/wrappers/REPO_ID\" "
            "replacing REPO_ID with the repo_id of the lowest-quality wrapper from step 1, "
            "to get its GitHub URL from the 'source_url' field. "
            "Step 3: use wrap_repo with the GitHub URL from step 2 to re-wrap and improve it. "
            "Step 4: use shared_log_write to broadcast the quality improvement."
        )


def _inject_layer3_goals() -> None:
    """
    Ensure scout, analyst, and builder each have a Layer 3 meta-goal.
    Only injects if the agent has no active goals that mention 'LAYER 3'.
    Builder's goal is dynamically generated based on what's already wrapped.
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
                # Builder gets a dynamic goal based on what's not yet wrapped
                if agent_id == "builder":
                    goal_text = _build_builder_goal()
                ge.create(agent_id, goal_text, priority=9)  # high priority
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

    log.info("API reachable. Building autonomy stack…")
    try:
        _, _, _, loop, _, cap_quorum, self_mod = _build_stack()
    except Exception as e:
        log.error("Failed to build autonomy stack: %s", e)
        sys.exit(1)

    # Inject Layer 3 meta-goals into scout/analyst/builder if they have none
    _inject_layer3_goals()

    metrics = DaemonMetrics()
    log.info("Daemon ready. Entering main loop.")

    while _running[0]:
        cycle_start = time.time()
        metrics.cycles += 1

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
                _cap_agent_goals(agent_id, max_goals=2)
                try:
                    from agents.persistent_goal import PersistentGoalEngine
                    ge = PersistentGoalEngine()
                    prev_goals = ge.list_active(agent_id, limit=1)
                    prev = prev_goals[0].metrics.get("progress", 0.0) if prev_goals else 0.0
                except Exception:
                    prev = 0.0
                return agent_id, run_cycle(loop, agent_id), prev

            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
                futures = {pool.submit(_run_one, aid): aid for aid in runnable}
                for fut in as_completed(futures):
                    try:
                        agent_id, outcome, prev_progress = fut.result()
                    except Exception as e:
                        log.error("worker exception: %s", e)
                        continue

                    if outcome["ok"]:
                        progress = outcome.get("progress", 0.0)
                        metrics.record_outcome(agent_id, progress, prev_progress)
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
