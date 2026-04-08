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


def _assign_idle_goal(agent_id: str) -> None:
    """
    When a core agent finishes all its goals, give it a fresh self-directed one.
    Strongly biased toward self-improvement: synthesizing capabilities, voting
    on proposals, and proposing system changes over writing analysis notes.
    """
    if agent_id not in _CORE_AGENTS:
        return  # only manage core agents
    try:
        from agents.persistent_goal import PersistentGoalEngine
        from agents.agent_identity import AgentIdentity
        import json as _json
        from pathlib import Path as _Path

        ge = PersistentGoalEngine()
        if ge.list_active(agent_id, limit=1):
            return  # already has a goal

        # Check for pending proposals to vote on first — highest priority idle work
        try:
            proposals_file = _Path("/agentOS/memory/quorum/proposals.jsonl")
            if proposals_file.exists():
                pending = [
                    _json.loads(line)
                    for line in proposals_file.read_text().strip().splitlines()
                    if line.strip() and '"pending"' in line
                ]
                if pending:
                    # Include the actual pending proposal IDs directly so the agent doesn't have to extract them
                    pending_ids = [p["proposal_id"] for p in pending[:3]]
                    ids_str = ", ".join(pending_ids)
                    goal = (
                        f"Vote on pending capability proposals: {ids_str}. "
                        "For each proposal_id listed above, call vote_on_proposal directly with that exact id string "
                        "(do not pass it through ollama_chat first — use the id as-is). "
                        "Approve proposals that add useful system capabilities, reject clearly broken ones. "
                        "Use list_proposals first only if you need to see what they contain before deciding."
                    )
                    ge.create(agent_id, goal, priority=6)
                    log.info("  %s — idle, assigned proposal review task", agent_id)
                    return
        except Exception:
            pass

        # Bias toward synthesizing a new capability based on agent's domain
        identity = AgentIdentity.load_or_create(agent_id)

        # Load recently completed objectives to filter out repetition
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

        # 80% chance: synthesize a new capability; 20%: identity-driven task
        import random as _random
        domain_hints = {
            "scout": "repository discovery, code analysis, or gap identification",
            "analyst": "data processing, quality analysis, or system improvement",
            "builder": "tool wrapping, automation, or expanding system capabilities",
        }
        domain = domain_hints.get(agent_id, "system improvement")

        # Build list of already-deployed/proposed capability names to avoid repeats
        avoid_names = []
        try:
            dyn = _Path("/agentOS/tools/dynamic")
            if dyn.exists():
                for f in dyn.glob("*.py"):
                    try:
                        first_line = f.read_text().splitlines()[1]  # "# Auto-synthesized capability: NAME"
                        name = first_line.split(":")[-1].strip()
                        if name:
                            avoid_names.append(name)
                    except Exception:
                        avoid_names.append(f.stem)
        except Exception:
            pass
        try:
            props_file = _Path("/agentOS/memory/quorum/proposals.jsonl")
            if props_file.exists():
                for line in props_file.read_text().strip().splitlines()[-40:]:
                    try:
                        p = _json.loads(line)
                        cap_id = p.get("payload", {}).get("cap_id", "")
                        if cap_id and len(cap_id) < 50:
                            avoid_names.append(cap_id)
                    except Exception:
                        pass
        except Exception:
            pass
        avoid_str = ""
        if avoid_names:
            unique = list(dict.fromkeys(avoid_names))[:12]
            avoid_str = f" These already exist — pick something DIFFERENT: {', '.join(unique)}."

        if _random.random() < 0.8:
            goal = (
                f"Use synthesize_capability to propose a new Python capability for the agent system. "
                f"Think of something useful for {domain}.{avoid_str} "
                f"Provide: name (snake_case), description (what it does), "
                f"implementation (a working Python function). "
                f"Example: synthesize_capability(name='retry_on_failure', "
                f"description='Retry a capability call up to 3 times on failure', "
                f"implementation='def retry_on_failure(cap_id=\"\", params=None, **kw):\\n  ...')"
            )
            ge.create(agent_id, goal, priority=5)
            log.info("  %s (%s) has no goals — assigned synthesis task", agent_id, identity.name)
        else:
            goal = identity.idle_goal(recent)
            ge.create(agent_id, goal, priority=3)
            log.info("  %s (%s) has no goals — assigned identity-driven task",
                     agent_id, identity.name)
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
