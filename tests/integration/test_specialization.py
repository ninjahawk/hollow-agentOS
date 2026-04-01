"""
Integration tests for AgentOS v3.9.0: Agent Specialization.

Phase 6, primitive 4. Tests verify that agents develop specialization
profiles from their execution history, and that the engine routes tasks
to the most capable agent.

Run:
    PYTHONPATH=. pytest tests/integration/test_specialization.py -v
"""

import pytest
import time
import uuid
import shutil
import tempfile
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.specialization import (
        SpecializationEngine, SpecializationProfile,
        SpecializationStrength, TaskPerformance,
    )
    SPEC_AVAILABLE = True
except ImportError:
    SPEC_AVAILABLE = False

try:
    from agents.execution_engine import ExecutionEngine
    ENGINE_AVAILABLE = True
except ImportError:
    ENGINE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not SPEC_AVAILABLE,
    reason="specialization module not available"
)


def _uid():
    return f"agent-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def tmp_spec(monkeypatch):
    """Isolated temp storage for specialization tests."""
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)
    if ENGINE_AVAILABLE:
        import agents.execution_engine as em
        monkeypatch.setattr(em, "EXECUTION_PATH", Path(tmpdir) / "executions")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 1 — profile() returns correct structure
# ---------------------------------------------------------------------------

class TestProfileStructure:
    def test_profile_fresh_agent_returns_empty(self, tmp_spec):
        """profile() on an agent with no history returns a valid empty profile."""
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        agent_id = _uid()

        profile = engine.profile(agent_id)

        assert isinstance(profile, SpecializationProfile)
        assert profile.agent_id == agent_id
        assert profile.strengths == []
        assert profile.weaknesses == []
        assert profile.best_task_type is None
        assert profile.total_tasks == 0
        assert 0.0 <= profile.specialization_score <= 1.0

    def test_profile_reflects_update_calls(self, tmp_spec):
        """
        After update() calls, profile() shows the correct strengths
        with accurate success_rate and sample_count.
        """
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        agent_id = _uid()

        # 3 successes on code tasks
        for _ in range(3):
            engine.update(agent_id, "code_review", success=True, duration_ms=500.0)
        # 2 failures on reasoning tasks
        for _ in range(2):
            engine.update(agent_id, "reasoning", success=False, duration_ms=2000.0)
        # 1 success on reasoning
        engine.update(agent_id, "reasoning", success=True, duration_ms=1500.0)

        profile = engine.profile(agent_id)

        assert profile.total_tasks == 6

        strengths = {s["task_type"]: s for s in profile.strengths}
        assert "code_review" in strengths
        assert "reasoning" in strengths

        assert strengths["code_review"]["success_rate"] == pytest.approx(1.0)
        assert strengths["code_review"]["sample_count"] == 3

        assert strengths["reasoning"]["success_rate"] == pytest.approx(1/3, abs=0.05)
        assert strengths["reasoning"]["sample_count"] == 3

    def test_best_task_type_is_highest_success(self, tmp_spec):
        """best_task_type points to the task type with the highest success_rate."""
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        agent_id = _uid()

        engine.update(agent_id, "file_ops", True, 100.0)
        engine.update(agent_id, "file_ops", True, 120.0)
        engine.update(agent_id, "search", False, 800.0)
        engine.update(agent_id, "search", False, 900.0)

        profile = engine.profile(agent_id)
        assert profile.best_task_type == "file_ops"

    def test_weaknesses_contain_low_success_types(self, tmp_spec):
        """Task types with success_rate < 0.4 appear in weaknesses."""
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        agent_id = _uid()

        # 1 out of 5 = 20% success on analysis
        engine.update(agent_id, "analysis", True, 500.0)
        for _ in range(4):
            engine.update(agent_id, "analysis", False, 1000.0)

        profile = engine.profile(agent_id)
        assert "analysis" in profile.weaknesses


# ---------------------------------------------------------------------------
# Test 2 — specialization score reflects variance
# ---------------------------------------------------------------------------

class TestSpecializationScore:
    def test_generalist_has_low_score(self, tmp_spec):
        """Agent equally good at everything has specialization_score near 0."""
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        agent_id = _uid()

        # 80% success on everything
        for task in ["file_ops", "search", "reasoning", "code_review"]:
            for _ in range(4):
                engine.update(agent_id, task, True, 500.0)
            engine.update(agent_id, task, False, 500.0)

        profile = engine.profile(agent_id)
        assert profile.specialization_score < 0.3

    def test_specialist_has_high_score(self, tmp_spec):
        """Agent that excels at one thing and fails at others has high score."""
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        agent_id = _uid()

        # 100% on code_review, 0% on everything else
        for _ in range(5):
            engine.update(agent_id, "code_review", True, 200.0)
        for task in ["search", "reasoning"]:
            for _ in range(5):
                engine.update(agent_id, task, False, 2000.0)

        profile = engine.profile(agent_id)
        assert profile.specialization_score > 0.5


# ---------------------------------------------------------------------------
# Test 3 — route() picks the best specialist
# ---------------------------------------------------------------------------

class TestRouting:
    def test_route_returns_best_agent(self, tmp_spec):
        """
        Agent A has high success on code tasks, agent B has low success.
        route("code", [A, B]) returns A.
        """
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        agent_a = _uid()
        agent_b = _uid()

        for _ in range(5):
            engine.update(agent_a, "code", True, 300.0)
        for _ in range(5):
            engine.update(agent_b, "code", False, 1500.0)

        result = engine.route("code", [agent_a, agent_b])
        assert result == agent_a

    def test_route_falls_back_when_no_data(self, tmp_spec):
        """route() returns the first candidate when no agents have data."""
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        candidates = [_uid(), _uid(), _uid()]

        result = engine.route("unknown_task_type", candidates)
        assert result == candidates[0]

    def test_route_single_candidate_always_returned(self, tmp_spec):
        """route() with one candidate always returns that candidate."""
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        only_agent = _uid()

        result = engine.route("anything", [only_agent])
        assert result == only_agent

    def test_route_prefers_speed_when_success_rates_equal(self, tmp_spec):
        """
        When two agents have the same success rate, route() prefers the faster one.
        """
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        fast_agent = _uid()
        slow_agent = _uid()

        # both 100% success
        for _ in range(5):
            engine.update(fast_agent, "task", True, 100.0)   # fast
        for _ in range(5):
            engine.update(slow_agent, "task", True, 4000.0)  # slow

        result = engine.route("task", [fast_agent, slow_agent])
        assert result == fast_agent


# ---------------------------------------------------------------------------
# Test 4 — top_specialist() finds the best globally
# ---------------------------------------------------------------------------

class TestTopSpecialist:
    def test_top_specialist_returns_best_agent(self, tmp_spec):
        """top_specialist() returns the agent with highest score for the task type."""
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        expert = _uid()
        novice = _uid()

        for _ in range(8):
            engine.update(expert, "reasoning", True, 400.0)
        for _ in range(5):
            engine.update(novice, "reasoning", False, 1200.0)

        best = engine.top_specialist("reasoning")
        assert best == expert

    def test_top_specialist_returns_none_for_unknown_task(self, tmp_spec):
        """top_specialist() returns None when no agent has data for the task type."""
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        engine.update(_uid(), "file_ops", True, 100.0)

        result = engine.top_specialist("unknown_task_xyz")
        assert result is None


# ---------------------------------------------------------------------------
# Test 5 — compare_specializations() maps task types to best agents
# ---------------------------------------------------------------------------

class TestCompareSpecializations:
    def test_compare_shows_distinct_specialists(self, tmp_spec):
        """
        Agent A excels at file ops, agent B excels at reasoning.
        compare_specializations() maps each task to the correct specialist.
        """
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        agent_a = _uid()
        agent_b = _uid()

        # A is the file ops expert
        for _ in range(5):
            engine.update(agent_a, "file_ops", True, 100.0)
        engine.update(agent_b, "file_ops", False, 2000.0)

        # B is the reasoning expert
        for _ in range(5):
            engine.update(agent_b, "reasoning", True, 300.0)
        engine.update(agent_a, "reasoning", False, 2000.0)

        comparison = engine.compare_specializations([agent_a, agent_b])

        assert "file_ops" in comparison
        assert comparison["file_ops"]["best_agent"] == agent_a

        assert "reasoning" in comparison
        assert comparison["reasoning"]["best_agent"] == agent_b

    def test_compare_includes_all_agents_in_scores(self, tmp_spec):
        """compare_specializations() includes all_scores for every agent."""
        engine = SpecializationEngine(storage_path=tmp_spec / "spec")
        agents = [_uid(), _uid(), _uid()]

        for aid in agents:
            engine.update(aid, "search", True, 500.0)

        comparison = engine.compare_specializations(agents)

        for task_data in comparison.values():
            for aid in agents:
                assert aid in task_data["all_scores"]


# ---------------------------------------------------------------------------
# Test 6 — persistence: profiles and history survive restart
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_history_persists_across_instances(self, tmp_spec):
        """Task history written by one engine instance is loaded by the next."""
        storage = tmp_spec / "spec"
        engine1 = SpecializationEngine(storage_path=storage)
        agent_id = _uid()

        for _ in range(5):
            engine1.update(agent_id, "code", True, 300.0)
        engine1.update(agent_id, "code", False, 800.0)

        # new instance reads from disk
        engine2 = SpecializationEngine(storage_path=storage)
        profile = engine2.profile(agent_id)

        assert profile.total_tasks == 6
        code_strength = next(s for s in profile.strengths if s["task_type"] == "code")
        assert code_strength["success_rate"] == pytest.approx(5/6, abs=0.05)

    def test_save_and_load_profile(self, tmp_spec):
        """save_profile() + load_profile() round-trips the profile correctly."""
        storage = tmp_spec / "spec"
        engine = SpecializationEngine(storage_path=storage)
        agent_id = _uid()

        for _ in range(3):
            engine.update(agent_id, "reasoning", True, 600.0)
        engine.update(agent_id, "reasoning", False, 1200.0)

        engine.save_profile(agent_id)
        loaded = engine.load_profile(agent_id)

        assert loaded is not None
        assert loaded.agent_id == agent_id
        assert loaded.total_tasks == 4
        assert len(loaded.strengths) == 1
        assert loaded.strengths[0]["task_type"] == "reasoning"
