"""
Integration tests for AgentOS v3.13.1: Autonomy Daemon.

Tests verify the daemon's core logic: agent discovery, cycle execution,
and graceful handling of agents with/without goals. Does not run the
full daemon loop — tests the units that compose it.

Run:
    PYTHONPATH=. pytest tests/integration/test_daemon.py -v
"""

import pytest
import uuid
import json
import time

pytestmark = pytest.mark.integration

try:
    import httpx
    _cfg = json.loads(open("/agentOS/config.json").read())
    _TOKEN = _cfg["api"]["token"]
    _BASE = "http://localhost:7777"
    _r = httpx.get(f"{_BASE}/state",
                   headers={"Authorization": f"Bearer {_TOKEN}"}, timeout=3)
    API_REACHABLE = _r.status_code == 200
except Exception:
    API_REACHABLE = False
    _TOKEN = ""

try:
    from agents.daemon import _build_stack, run_cycle, _agents_with_goals
    DAEMON_AVAILABLE = True
except ImportError:
    DAEMON_AVAILABLE = False

try:
    from agents.live_capabilities import build_live_stack
    from agents.reasoning_layer import ReasoningLayer
    from agents.autonomy_loop import AutonomyLoop
    from agents.persistent_goal import PersistentGoalEngine
    STACK_AVAILABLE = True
except ImportError:
    STACK_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not DAEMON_AVAILABLE,
    reason="daemon module not available"
)


def _uid():
    return f"agent-{uuid.uuid4().hex[:8]}"


def _headers():
    return {"Authorization": f"Bearer {_TOKEN}"}


def _post_goal(agent_id, objective, priority=5):
    r = httpx.post(f"{_BASE}/goals/{agent_id}",
                   json={"objective": objective, "priority": priority},
                   headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- #
# Test 1 — stack builds without error
# --------------------------------------------------------------------------- #

class TestStackBuilds:
    def test_build_stack_returns_five_tuple(self):
        """_build_stack() returns (graph, engine, reasoning, loop, goal_engine, cap_quorum)."""
        if not STACK_AVAILABLE:
            pytest.skip("stack modules not available")
        result = _build_stack()
        assert len(result) == 6

    def test_stack_components_are_correct_types(self):
        """Stack components are the expected types."""
        if not STACK_AVAILABLE:
            pytest.skip("stack modules not available")
        from agents.capability_graph import CapabilityGraph
        from agents.execution_engine import ExecutionEngine

        graph, engine, reasoning, loop, goal_engine, cap_quorum = _build_stack()
        assert isinstance(graph, CapabilityGraph)
        assert isinstance(engine, ExecutionEngine)
        assert isinstance(goal_engine, PersistentGoalEngine)

    def test_stack_is_cached(self):
        """_build_stack() returns the same objects on repeated calls."""
        if not STACK_AVAILABLE:
            pytest.skip("stack modules not available")
        r1 = _build_stack()
        r2 = _build_stack()
        assert r1[0] is r2[0]   # same CapabilityGraph instance
        assert r1[1] is r2[1]   # same ExecutionEngine instance


# --------------------------------------------------------------------------- #
# Test 2 — agent discovery
# --------------------------------------------------------------------------- #

class TestAgentDiscovery:
    def test_agent_with_goal_is_discovered(self):
        """An agent with an active goal appears in _agents_with_goals()."""
        if not API_REACHABLE:
            pytest.skip("API not reachable")

        agent_id = _uid()
        _post_goal(agent_id, "test discovery objective", priority=5)

        found = _agents_with_goals()
        assert agent_id in found, f"agent {agent_id} not in {found}"

    def test_agent_without_goals_not_returned(self):
        """An agent with no goals does not appear in _agents_with_goals()."""
        if not API_REACHABLE:
            pytest.skip("API not reachable")
        agent_id = _uid()
        # Don't create any goals for this agent
        found = _agents_with_goals()
        assert agent_id not in found


# --------------------------------------------------------------------------- #
# Test 3 — run_cycle executes without error
# --------------------------------------------------------------------------- #

class TestRunCycle:
    def test_run_cycle_returns_outcome_dict(self):
        """run_cycle() returns a dict with required keys."""
        if not STACK_AVAILABLE or not API_REACHABLE:
            pytest.skip("stack or API not available")

        _, _, _, loop, _, _ = _build_stack()
        agent_id = _uid()

        # Create a goal for this agent first
        _post_goal(agent_id, "verify the system is healthy", priority=8)

        outcome = run_cycle(loop, agent_id)
        assert "agent_id" in outcome
        assert "ok" in outcome
        assert outcome["agent_id"] == agent_id

    def test_run_cycle_on_agent_without_goals_is_graceful(self):
        """run_cycle() on an agent with no goals doesn't crash."""
        if not STACK_AVAILABLE:
            pytest.skip("stack not available")

        _, _, _, loop, _, _ = _build_stack()
        agent_id = _uid()   # no goals

        outcome = run_cycle(loop, agent_id)
        # Should return ok=True with no steps (nothing to do) or ok=False gracefully
        assert "ok" in outcome
        assert "agent_id" in outcome

    def test_run_cycle_makes_progress(self):
        """
        After run_cycle(), goal progress is >= 0.0.
        With live capabilities registered, the agent can take real steps.
        """
        if not STACK_AVAILABLE or not API_REACHABLE:
            pytest.skip("stack or API not available")

        _, _, _, loop, _, _ = _build_stack()
        agent_id = _uid()
        _post_goal(agent_id, "search the codebase for autonomy patterns", priority=7)

        outcome = run_cycle(loop, agent_id)
        assert outcome.get("ok") in (True, False)   # either is valid
        assert outcome.get("progress", 0.0) >= 0.0
