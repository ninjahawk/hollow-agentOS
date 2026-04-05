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
    try:
        synthesis_engine = CapabilitySynthesisEngine()
        self_mod = SelfModificationCycle(
            execution_engine=engine,
            synthesis_engine=synthesis_engine,
            quorum=cap_quorum,   # pass CapabilityQuorum so daemon's vote_on_pending picks it up
            semantic_memory=memory,
        )
        log.info("Self-modification cycle initialized")
    except Exception as e:
        self_mod = None
        log.warning("Self-modification unavailable: %s", e)

    loop = AutonomyLoop(
        goal_engine=goal_engine,
        reasoning_layer=reasoning,
        execution_engine=engine,
        semantic_memory=memory,
        self_modification=self_mod,
    )
    _stack = (graph, engine, reasoning, loop, goal_engine, cap_quorum, self_mod)
    log.info("Autonomy stack ready: %d capabilities registered", len(graph._embedder and [] or []))
    return _stack


# --------------------------------------------------------------------------- #
#  Agent discovery                                                             #
# --------------------------------------------------------------------------- #

def _agents_with_goals() -> list[str]:
    """Return agent IDs that have at least one active goal."""
    candidates = set()

    # 1. Registered active agents from the registry
    try:
        data = _get("/agents")
        for a in data.get("agents", []):
            if a.get("status") == "active":
                candidates.add(a["agent_id"])
    except Exception as e:
        log.debug("Could not fetch agent registry: %s", e)

    # 2. Any agent with a goal directory on disk (catches unregistered agents)
    try:
        from agents.persistent_goal import GOAL_PATH
        if GOAL_PATH.exists():
            for d in GOAL_PATH.iterdir():
                if d.is_dir():
                    candidates.add(d.name)
    except Exception as e:
        log.debug("Could not scan goal path: %s", e)

    with_goals = []
    for agent_id in sorted(candidates):
        try:
            result = _get(f"/goals/{agent_id}")
            if result.get("count", 0) > 0:
                with_goals.append(agent_id)
            else:
                # Agent exists but has no active goal — give it something to do
                _assign_idle_goal(agent_id)
        except Exception:
            pass

    # Core agents always get a slot; rest sorted alphabetically
    _CORE = {"scout", "analyst", "builder"}
    core = [a for a in with_goals if a in _CORE]
    rest = [a for a in with_goals if a not in _CORE]
    prioritized = core + rest
    return prioritized[:MAX_ACTIVE_AGENTS]


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


def _assign_idle_goal(agent_id: str) -> None:
    """
    When an agent finishes all its goals, give it a fresh self-directed one
    shaped by its persistent identity (domains + traits).
    """
    try:
        from agents.persistent_goal import PersistentGoalEngine
        from agents.agent_identity import AgentIdentity
        ge = PersistentGoalEngine()
        if ge.list_active(agent_id, limit=1):
            return  # already has a goal

        identity = AgentIdentity.load_or_create(agent_id)

        # Load recently completed objectives to filter out
        import json as _json
        from pathlib import Path as _Path
        recent = []
        reg_path = _Path(f"/agentOS/memory/goals/{agent_id}/registry.jsonl")
        if reg_path.exists():
            for line in reg_path.read_text().strip().splitlines()[-20:]:
                try:
                    g = _json.loads(line)
                    if g.get("status") == "completed":
                        recent.append(g.get("objective", "")[:80])
                except Exception:
                    pass

        goal = identity.idle_goal(recent)
        ge.create(agent_id, goal, priority=3)
        log.info("  %s (%s) has no goals — assigned identity-driven task",
                 agent_id, identity.name)
    except Exception as e:
        log.debug("_assign_idle_goal failed for %s: %s", agent_id, e)


# Layer 3 meta-goal text per core agent
_LAYER3_GOALS = {
    "scout": (
        "LAYER 3 — Map the repo ingestion pipeline: "
        "Step 1: use fs_read to read /agentOS/agents/live_capabilities.py and identify "
        "the git_clone capability and what it returns. "
        "Step 2: use git_clone with url='https://github.com/BurntSushi/ripgrep' to clone "
        "a real repo and see what comes back. "
        "Step 3: use fs_write to write your findings — what the capability returned, what "
        "the top-level file structure looks like, what the README says — to "
        "/agentOS/workspace/scout/layer3_recon.md. "
        "Step 4: use shared_log_write to broadcast a one-line summary to other agents."
    ),
    "analyst": (
        "LAYER 3 — Audit live_capabilities.py for gaps: "
        "Step 1: use fs_read to read /agentOS/agents/live_capabilities.py. "
        "Step 2: use ollama_chat to reason about what capabilities are missing for a full "
        "repo-ingestion pipeline (clone → read structure → generate interface). "
        "Step 3: use fs_write to write a gap report to "
        "/agentOS/workspace/analyst/layer3_gaps.md listing each missing capability and "
        "a one-paragraph spec for it. "
        "Step 4: for the most important gap, use propose_change with "
        "proposal_type='new_tool' to formally propose it."
    ),
    "builder": (
        "LAYER 3 — Wrap ripgrep using the wrap_repo capability: "
        "Step 1: use wrap_repo with url='https://github.com/BurntSushi/ripgrep' "
        "to generate a Hollow app wrapper for ripgrep. "
        "Step 2: use fs_read to verify the wrapper was created — read "
        "/agentOS/workspace/wrappers/ripgrep/wrapper.json and confirm it has content. "
        "Step 3: use shared_log_write to broadcast: "
        "'ripgrep wrapped — wrapper at /agentOS/workspace/wrappers/ripgrep/wrapper.json'."
    ),
}


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
                                ident = AgentIdentity.load_or_create(agent_id)
                                ident.update_narrative(
                                    outcome.get("goal_id", ""),
                                    f"progress={progress:.0%} steps={outcome.get('steps',0)}"
                                )
                            except Exception:
                                pass
                            _assign_idle_goal(agent_id)
                    else:
                        metrics.errors += 1
                        log.warning("  %s → error: %s", agent_id, outcome.get("error"))

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
            if self_mod:
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
