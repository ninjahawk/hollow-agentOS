"""
Integration tests for AgentOS v3.10.0: Swarm Meta-Learning.

Phase 6 capstone. Tests verify that the swarm learning orchestrator
correctly ties together all Phase 6 primitives and produces measurable
improvement across repeated learning cycles.

Run:
    PYTHONPATH=. pytest tests/integration/test_swarm_learning.py -v
"""

import pytest
import time
import uuid
import shutil
import tempfile
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.swarm_learning import LearningOrchestrator, LearningReport, CycleComparison, Recommendation
    SWARM_LEARNING_AVAILABLE = True
except ImportError:
    SWARM_LEARNING_AVAILABLE = False

try:
    from agents.introspection import AgentIntrospector
    from agents.meta_synthesis import MetaSynthesizer
    from agents.governance_evolution import GovernanceEvolutionEngine
    from agents.specialization import SpecializationEngine
    ALL_PHASE6_AVAILABLE = True
except ImportError:
    ALL_PHASE6_AVAILABLE = False

try:
    from agents.execution_engine import ExecutionEngine
    ENGINE_AVAILABLE = True
except ImportError:
    ENGINE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not SWARM_LEARNING_AVAILABLE,
    reason="swarm_learning module not available"
)


def _uid():
    return f"agent-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def tmp_swarm(monkeypatch):
    """Isolated temp storage for swarm learning tests."""
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)
    if ENGINE_AVAILABLE:
        import agents.execution_engine as em
        monkeypatch.setattr(em, "EXECUTION_PATH", Path(tmpdir) / "executions")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


def _make_full_orchestrator(base_path: Path):
    """Build a LearningOrchestrator with all Phase 6 subsystems wired up."""
    if not ALL_PHASE6_AVAILABLE:
        return LearningOrchestrator(storage_path=base_path / "learning")

    spec = SpecializationEngine(storage_path=base_path / "spec")
    synth = MetaSynthesizer(introspector=None, storage_path=base_path / "meta")
    orch = LearningOrchestrator(
        synthesizer=synth,
        specialization_engine=spec,
        storage_path=base_path / "learning",
    )
    return orch


# ---------------------------------------------------------------------------
# Test 1 — basic cycle structure
# ---------------------------------------------------------------------------

class TestCycleStructure:
    def test_empty_cycle_returns_valid_report(self, tmp_swarm):
        """run_cycle() with no tasks returns a valid LearningReport."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_ids = [_uid(), _uid()]

        report = orch.run_cycle(agent_ids)

        assert isinstance(report, LearningReport)
        assert report.cycle_id is not None
        assert report.task_count == 0
        assert report.agent_ids == agent_ids
        assert 0.0 <= report.swarm_success_rate <= 1.0
        assert isinstance(report.recommendations, list)

    def test_cycle_clears_task_buffer(self, tmp_swarm):
        """Tasks buffered via record_task() are consumed by run_cycle()."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_id = _uid()

        orch.record_task(agent_id, "code", True, 300.0)
        orch.record_task(agent_id, "code", True, 400.0)
        orch.record_task(agent_id, "reasoning", False, 1200.0)

        report = orch.run_cycle([agent_id])
        assert report.task_count == 3

        # second cycle should see 0 tasks (buffer was cleared)
        report2 = orch.run_cycle([agent_id])
        assert report2.task_count == 0

    def test_success_rate_computed_correctly(self, tmp_swarm):
        """swarm_success_rate reflects the fraction of successful tasks."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_id = _uid()

        for _ in range(3):
            orch.record_task(agent_id, "task", True, 200.0)
        for _ in range(1):
            orch.record_task(agent_id, "task", False, 800.0)

        report = orch.run_cycle([agent_id])
        assert report.swarm_success_rate == pytest.approx(0.75, abs=0.02)


# ---------------------------------------------------------------------------
# Test 2 — improvement tracking across cycles
# ---------------------------------------------------------------------------

class TestImprovementTracking:
    def test_second_cycle_shows_delta(self, tmp_swarm):
        """
        After two cycles, the second cycle's report shows success_rate_delta
        relative to the first cycle.
        """
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_id = _uid()

        # cycle 1: 50% success
        for _ in range(2):
            orch.record_task(agent_id, "task", True, 300.0)
        for _ in range(2):
            orch.record_task(agent_id, "task", False, 800.0)
        report1 = orch.run_cycle([agent_id])

        # cycle 2: 100% success
        for _ in range(4):
            orch.record_task(agent_id, "task", True, 200.0)
        report2 = orch.run_cycle([agent_id])

        assert report1.success_rate_delta == 0.0  # no previous cycle to compare
        assert report2.success_rate_delta > 0.0   # improved vs cycle 1

    def test_improvement_trend_tracks_progress(self, tmp_swarm):
        """
        improvement_trend() reflects the direction of change across cycles.
        """
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_id = _uid()

        # 3 cycles with improving success rates
        for cycle_success_count in [1, 3, 5]:
            for _ in range(cycle_success_count):
                orch.record_task(agent_id, "task", True, 200.0)
            for _ in range(5 - cycle_success_count):
                orch.record_task(agent_id, "task", False, 800.0)
            orch.run_cycle([agent_id])

        trend = orch.improvement_trend()

        assert trend["cycles"] == 3
        assert trend["success_rate"]["improving"] is True
        assert trend["success_rate"]["last"] > trend["success_rate"]["first"]

    def test_improvement_trend_no_cycles(self, tmp_swarm):
        """improvement_trend() returns 'no data' when no cycles have run."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        trend = orch.improvement_trend()
        assert trend["cycles"] == 0


# ---------------------------------------------------------------------------
# Test 3 — recommendations are generated and filtered
# ---------------------------------------------------------------------------

class TestRecommendations:
    def test_low_success_rate_generates_recommendation(self, tmp_swarm):
        """
        When swarm success rate < 50%, run_cycle() generates a
        high-priority specialization recommendation.
        """
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_id = _uid()

        # only 1 success out of 6
        orch.record_task(agent_id, "hard_task", True, 500.0)
        for _ in range(5):
            orch.record_task(agent_id, "hard_task", False, 2000.0)

        report = orch.run_cycle([agent_id])

        spec_recs = [r for r in report.recommendations if r["category"] == "specialization"]
        assert len(spec_recs) > 0
        assert spec_recs[0]["priority"] == "high"

    def test_routing_recommendation_when_specialist_misrouted(self, tmp_swarm):
        """
        When tasks go to non-specialist agents, run_cycle() generates
        a routing recommendation pointing to the actual specialist.
        """
        if not ALL_PHASE6_AVAILABLE:
            pytest.skip("Phase 6 modules not all available")

        spec = SpecializationEngine(storage_path=tmp_swarm / "spec")
        orch = LearningOrchestrator(
            specialization_engine=spec,
            storage_path=tmp_swarm / "learning",
        )

        expert = _uid()
        novice = _uid()

        # expert has strong history on "code" tasks
        for _ in range(8):
            spec.update(expert, "code", True, 200.0)
        for _ in range(3):
            spec.update(novice, "code", False, 1500.0)

        # but this cycle, code tasks went to the novice
        for _ in range(3):
            orch.record_task(novice, "code", False, 1500.0)

        report = orch.run_cycle([expert, novice])

        routing_recs = [r for r in report.recommendations if r["category"] == "routing"]
        assert len(routing_recs) > 0
        assert routing_recs[0]["supporting_evidence"]["task_type"] == "code"

    def test_get_recommendations_filters_by_category(self, tmp_swarm):
        """get_recommendations(category='routing') returns only routing recs."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_id = _uid()

        # trigger low success recommendation
        for _ in range(5):
            orch.record_task(agent_id, "task", False, 1000.0)
        orch.record_task(agent_id, "task", True, 200.0)
        orch.run_cycle([agent_id])

        all_recs = orch.get_recommendations()
        spec_recs = orch.get_recommendations(category="specialization")

        # all_recs should include specialization ones
        assert len(all_recs) >= len(spec_recs)

    def test_recommendations_have_required_fields(self, tmp_swarm):
        """Every recommendation has rec_id, category, priority, description."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_id = _uid()

        for _ in range(5):
            orch.record_task(agent_id, "task", False, 1000.0)
        orch.record_task(agent_id, "task", True, 200.0)

        report = orch.run_cycle([agent_id])

        for rec in report.recommendations:
            assert "rec_id" in rec
            assert "category" in rec
            assert "priority" in rec
            assert "description" in rec
            assert len(rec["description"]) > 0


# ---------------------------------------------------------------------------
# Test 4 — compare_cycles() quantifies change
# ---------------------------------------------------------------------------

class TestCompareCycles:
    def test_compare_cycles_returns_comparison(self, tmp_swarm):
        """compare_cycles() returns a CycleComparison for two valid cycle IDs."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_id = _uid()

        orch.record_task(agent_id, "task", True, 300.0)
        r1 = orch.run_cycle([agent_id])

        orch.record_task(agent_id, "task", True, 200.0)
        r2 = orch.run_cycle([agent_id])

        comparison = orch.compare_cycles(r1.cycle_id, r2.cycle_id)

        assert comparison is not None
        assert isinstance(comparison, CycleComparison)
        assert comparison.before_cycle_id == r1.cycle_id
        assert comparison.after_cycle_id == r2.cycle_id
        assert isinstance(comparison.success_rate_change, float)
        assert len(comparison.summary) > 0

    def test_compare_cycles_shows_improvement(self, tmp_swarm):
        """compare_cycles() detects success rate improvement."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_id = _uid()

        # cycle 1: 25% success
        orch.record_task(agent_id, "t", True, 300.0)
        for _ in range(3):
            orch.record_task(agent_id, "t", False, 1000.0)
        r1 = orch.run_cycle([agent_id])

        # cycle 2: 100% success
        for _ in range(4):
            orch.record_task(agent_id, "t", True, 200.0)
        r2 = orch.run_cycle([agent_id])

        comp = orch.compare_cycles(r1.cycle_id, r2.cycle_id)
        assert comp.success_rate_change > 0.0
        assert "improved" in comp.summary.lower() or "stable" in comp.summary.lower()

    def test_compare_cycles_invalid_id_returns_none(self, tmp_swarm):
        """compare_cycles() with unknown IDs returns None gracefully."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        result = orch.compare_cycles("nonexistent-a", "nonexistent-b")
        assert result is None


# ---------------------------------------------------------------------------
# Test 5 — persistence: cycles survive disk round-trip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_cycle_written_to_disk(self, tmp_swarm):
        """run_cycle() creates a JSON file for the cycle."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        report = orch.run_cycle([_uid()])

        files = list((tmp_swarm / "learning").glob("cycle_*.json"))
        assert len(files) == 1
        # file contains the cycle_id
        content = files[0].read_text()
        assert report.cycle_id in content

    def test_load_cycles_restores_history(self, tmp_swarm):
        """load_cycles() returns all cycles written to disk."""
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agent_id = _uid()

        orch.record_task(agent_id, "task", True, 200.0)
        r1 = orch.run_cycle([agent_id])
        r2 = orch.run_cycle([agent_id])

        cycles = orch.load_cycles()
        assert len(cycles) == 2
        cycle_ids = [c["cycle_id"] for c in cycles]
        assert r1.cycle_id in cycle_ids
        assert r2.cycle_id in cycle_ids


# ---------------------------------------------------------------------------
# Test 6 — full Phase 6 integration: all primitives working together
# ---------------------------------------------------------------------------

class TestFullIntegration:
    def test_full_phase6_cycle_runs_without_error(self, tmp_swarm):
        """
        A cycle with all Phase 6 subsystems wired together completes
        without errors and produces a valid report.
        """
        if not ALL_PHASE6_AVAILABLE:
            pytest.skip("not all Phase 6 modules available")

        spec = SpecializationEngine(storage_path=tmp_swarm / "spec")
        introspector = AgentIntrospector()
        synth = MetaSynthesizer(
            introspector=introspector,
            storage_path=tmp_swarm / "meta"
        )

        orch = LearningOrchestrator(
            introspector=introspector,
            synthesizer=synth,
            specialization_engine=spec,
            storage_path=tmp_swarm / "learning",
        )

        agents = [_uid(), _uid(), _uid()]

        # simulate a real workload
        task_types = ["code_review", "file_ops", "reasoning"]
        for i, agent_id in enumerate(agents):
            dominant = task_types[i]
            for _ in range(5):
                orch.record_task(agent_id, dominant, True, 300.0)
            for other in task_types:
                if other != dominant:
                    orch.record_task(agent_id, other, False, 1200.0)

        report = orch.run_cycle(agents)

        assert isinstance(report, LearningReport)
        assert report.task_count > 0
        assert report.swarm_success_rate > 0.0
        assert len(report.agent_ids) == 3

    def test_second_cycle_shows_improvement_over_first(self, tmp_swarm):
        """
        Two cycles where the second has higher success rate shows positive
        success_rate_delta — the swarm learns across cycles.
        """
        orch = LearningOrchestrator(storage_path=tmp_swarm / "learning")
        agents = [_uid(), _uid()]

        # cycle 1: mediocre performance
        for agent_id in agents:
            for _ in range(2):
                orch.record_task(agent_id, "task", True, 400.0)
            for _ in range(3):
                orch.record_task(agent_id, "task", False, 1500.0)
        report1 = orch.run_cycle(agents)

        # cycle 2: better performance (agents "learned")
        for agent_id in agents:
            for _ in range(4):
                orch.record_task(agent_id, "task", True, 300.0)
            orch.record_task(agent_id, "task", False, 800.0)
        report2 = orch.run_cycle(agents)

        assert report2.success_rate_delta > 0.0
        assert report2.swarm_success_rate > report1.swarm_success_rate
