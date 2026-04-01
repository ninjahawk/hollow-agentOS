"""
Integration tests for AgentOS v2.2.0: Persistent Goal Engine.

Tests verify long-term objective tracking, decomposition, progress monitoring,
and semantic goal discovery.

Run:
    PYTHONPATH=. pytest tests/integration/test_persistent_goal.py -v -m integration
"""

import pytest
import time
import shutil
import tempfile
import os
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.persistent_goal import PersistentGoalEngine, GoalRecord
    PERSISTENT_GOAL_AVAILABLE = True
except ImportError:
    PERSISTENT_GOAL_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PERSISTENT_GOAL_AVAILABLE,
    reason="sentence-transformers not available"
)


@pytest.fixture(autouse=True)
def fresh_goal_storage(monkeypatch):
    """
    Provide a fresh temporary directory for goal storage in each test.
    Isolates tests so they don't interfere with each other's goals.
    """
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)

    # Also patch the module-level GOAL_PATH in persistent_goal
    import agents.persistent_goal as goal_module
    goal_module.GOAL_PATH = Path(tmpdir) / "goals"

    yield

    # Cleanup
    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Test 1 — Create and retrieve goal
# ---------------------------------------------------------------------------

class TestPersistentGoalCreate:
    def test_create_and_get_goal(self):
        """
        Create a goal. Retrieve it by ID.
        Assert: goal returned exactly, status is 'active', timestamps set.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-goals"
        objective = "optimize data pipeline performance to reduce latency by 50%"

        goal_id = engine.create(agent_id, objective, priority=8)
        assert goal_id.startswith("goal-")

        goal = engine.get(agent_id, goal_id)
        assert goal is not None
        assert goal.objective == objective
        assert goal.status == "active"
        assert goal.priority == 8
        assert goal.created_at > 0
        assert goal.completed_at is None


# ---------------------------------------------------------------------------
# Test 2 — List active goals
# ---------------------------------------------------------------------------

class TestPersistentGoalList:
    def test_list_active_goals_sorted_by_priority(self):
        """
        Create 3 goals with different priorities.
        Call list_active().
        Assert: returned in priority order (highest first).
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-list"

        goals_data = [
            ("reduce latency", 5),
            ("fix critical bugs", 9),
            ("improve documentation", 2),
        ]

        goal_ids = []
        for objective, priority in goals_data:
            goal_id = engine.create(agent_id, objective, priority=priority)
            goal_ids.append(goal_id)
            time.sleep(0.01)  # Ensure different timestamps

        active_goals = engine.list_active(agent_id, limit=10)

        assert len(active_goals) == 3
        # First should be highest priority
        assert active_goals[0].priority == 9
        assert active_goals[1].priority == 5
        assert active_goals[2].priority == 2

    def test_list_active_respects_limit(self):
        """
        Create 5 goals. Call list_active(limit=2).
        Assert: returns only 2 goals.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-limit"

        for i in range(5):
            engine.create(agent_id, f"goal {i}", priority=5)

        active_goals = engine.list_active(agent_id, limit=2)
        assert len(active_goals) == 2


# ---------------------------------------------------------------------------
# Test 3 — Search goals by semantic similarity
# ---------------------------------------------------------------------------

class TestPersistentGoalSearch:
    def test_search_finds_similar_goals(self):
        """
        Create 3 goals: one about latency, two about other topics.
        Search for 'reduce response time'. Assert: latency goal found.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-search"

        goals = [
            "optimize data pipeline to reduce query latency",
            "improve error handling and recovery",
            "add comprehensive unit test coverage",
        ]

        for objective in goals:
            engine.create(agent_id, objective, priority=5)

        results = engine.search_goals(agent_id, "optimize query latency", top_k=3, similarity_threshold=0.4)

        assert len(results) > 0
        # Should find latency-related goal
        assert any("latency" in r.objective.lower() for r in results)

    def test_search_respects_similarity_threshold(self):
        """
        Create unrelated goals. Search with high threshold.
        Assert: low-similarity goals filtered out.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-threshold"

        goals = [
            "increase user engagement metrics",
            "improve system documentation",
            "optimize memory allocation",
        ]

        for objective in goals:
            engine.create(agent_id, objective, priority=5)

        results = engine.search_goals(agent_id, "cook a pizza", top_k=5, similarity_threshold=0.85)

        # Should find few or none (pizza unrelated to all goals)
        assert len(results) <= 1


# ---------------------------------------------------------------------------
# Test 4 — Goal status transitions
# ---------------------------------------------------------------------------

class TestPersistentGoalStatus:
    def test_complete_goal(self):
        """
        Create a goal, mark it complete.
        Assert: status changes, completed_at is set.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-complete"

        goal_id = engine.create(agent_id, "finish project", priority=7)
        goal = engine.get(agent_id, goal_id)
        assert goal.status == "active"
        assert goal.completed_at is None

        engine.complete(agent_id, goal_id)
        goal = engine.get(agent_id, goal_id)
        assert goal.status == "completed"
        assert goal.completed_at is not None

    def test_pause_and_resume_goal(self):
        """
        Create a goal, pause it, resume it.
        Assert: status transitions correctly.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-pause"

        goal_id = engine.create(agent_id, "long-running task", priority=5)

        engine.pause(agent_id, goal_id)
        goal = engine.get(agent_id, goal_id)
        assert goal.status == "paused"

        engine.resume(agent_id, goal_id)
        goal = engine.get(agent_id, goal_id)
        assert goal.status == "active"

    def test_abandon_goal(self):
        """
        Create a goal, abandon it.
        Assert: status is 'abandoned'.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-abandon"

        goal_id = engine.create(agent_id, "experimental task", priority=2)
        engine.abandon(agent_id, goal_id)
        goal = engine.get(agent_id, goal_id)
        assert goal.status == "abandoned"


# ---------------------------------------------------------------------------
# Test 5 — Goal decomposition
# ---------------------------------------------------------------------------

class TestPersistentGoalDecompose:
    def test_decompose_goal_into_subgoals(self):
        """
        Create a high-level goal, decompose it into subgoals.
        Assert: parent goal tracks subgoal IDs, subgoals are created.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-decompose"

        parent_goal_id = engine.create(
            agent_id,
            "launch new product feature",
            priority=8
        )

        subgoals = [
            "design database schema for new feature",
            "implement API endpoints",
            "write comprehensive tests",
            "deploy to staging environment",
        ]

        engine.decompose(agent_id, parent_goal_id, subgoals)

        # Retrieve parent goal
        parent = engine.get(agent_id, parent_goal_id)
        assert parent is not None
        assert len(parent.subgoals) == len(subgoals)

        # Verify subgoals were created
        for subgoal_id in parent.subgoals:
            subgoal = engine.get(agent_id, subgoal_id)
            assert subgoal is not None
            assert subgoal.priority == parent.priority  # Inherited priority


# ---------------------------------------------------------------------------
# Test 6 — Update goal progress
# ---------------------------------------------------------------------------

class TestPersistentGoalProgress:
    def test_update_progress_metrics(self):
        """
        Create a goal, update its progress metrics multiple times.
        Assert: metrics accumulate and reflect progress.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-progress"

        goal_id = engine.create(agent_id, "process 1 million records", priority=7)

        # Update progress
        engine.update_progress(agent_id, goal_id, {"records_processed": 100000, "completion_percentage": 10})
        goal = engine.get(agent_id, goal_id)
        assert goal.metrics["records_processed"] == 100000
        assert goal.metrics["completion_percentage"] == 10

        # Update again
        engine.update_progress(agent_id, goal_id, {"records_processed": 500000, "completion_percentage": 50})
        goal = engine.get(agent_id, goal_id)
        assert goal.metrics["records_processed"] == 500000
        assert goal.metrics["completion_percentage"] == 50
        assert goal.last_worked_on is not None


# ---------------------------------------------------------------------------
# Test 7 — Get next focus
# ---------------------------------------------------------------------------

class TestPersistentGoalFocus:
    def test_get_next_focus_returns_highest_priority(self):
        """
        Create multiple goals with varying priorities and status.
        Call get_next_focus().
        Assert: returns highest priority active goals.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-focus"

        # Create goals in mixed order
        g1 = engine.create(agent_id, "low priority task", priority=2)
        g2 = engine.create(agent_id, "high priority task", priority=9)
        g3 = engine.create(agent_id, "medium priority task", priority=5)

        focus = engine.get_next_focus(agent_id, top_k=1)

        assert len(focus) == 1
        assert focus[0].priority == 9
        assert focus[0].objective == "high priority task"

    def test_get_next_focus_ignores_inactive_goals(self):
        """
        Create active and paused goals.
        Call get_next_focus().
        Assert: only active goals are returned.
        """
        engine = PersistentGoalEngine()
        agent_id = "test-agent-focus-inactive"

        g1 = engine.create(agent_id, "active task", priority=8)
        g2 = engine.create(agent_id, "paused task", priority=9)  # Higher priority but paused

        engine.pause(agent_id, g2)

        focus = engine.get_next_focus(agent_id, top_k=3)

        # Should only return active goals
        assert all(g.status == "active" for g in focus)
        assert focus[0].goal_id == g1


# ---------------------------------------------------------------------------
# Test 8 — Multi-agent isolation
# ---------------------------------------------------------------------------

class TestPersistentGoalMultiAgent:
    def test_agents_have_separate_goals(self):
        """
        Two agents create goals with similar objectives.
        Assert: each agent's goal list only shows their own goals.
        """
        engine = PersistentGoalEngine()

        agent1_id = "agent-alice"
        agent2_id = "agent-bob"

        g1 = engine.create(agent1_id, "Alice's goal: improve performance", priority=5)
        g2 = engine.create(agent2_id, "Bob's goal: improve performance", priority=5)

        # Alice's list should only have her goal
        alice_goals = engine.list_active(agent1_id)
        assert len(alice_goals) == 1
        assert alice_goals[0].agent_id == agent1_id

        # Bob's list should only have his goal
        bob_goals = engine.list_active(agent2_id)
        assert len(bob_goals) == 1
        assert bob_goals[0].agent_id == agent2_id
