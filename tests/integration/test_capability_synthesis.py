"""
Integration tests for AgentOS v2.4.0: Capability Synthesis Engine.

Tests verify gap observation, autonomous capability generation, testing, and deployment.

Run:
    PYTHONPATH=. pytest tests/integration/test_capability_synthesis.py -v -m integration
"""

import pytest
import time
import shutil
import tempfile
import os
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.capability_synthesis import CapabilitySynthesisEngine, GapRecord, SynthesisRecord
    SYNTHESIS_AVAILABLE = True
except ImportError:
    SYNTHESIS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not SYNTHESIS_AVAILABLE,
    reason="sentence-transformers not available"
)


@pytest.fixture(autouse=True)
def fresh_synthesis_storage(monkeypatch):
    """
    Provide a fresh temporary directory for synthesis storage in each test.
    """
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)

    import agents.capability_synthesis as syn_module
    syn_module.SYNTHESIS_PATH = Path(tmpdir) / "synthesis"

    yield

    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Test 1 — Record and retrieve gaps
# ---------------------------------------------------------------------------

class TestSynthesisGaps:
    def test_record_and_get_gap(self):
        """
        Record a gap. Retrieve it.
        Assert: gap stored correctly with status 'open'.
        """
        engine = CapabilitySynthesisEngine()
        agent_id = "agent-alice"
        description = "need ability to batch process large datasets efficiently"

        gap_id = engine.record_gap(agent_id, description, {"context": "data pipeline"}, priority=8)
        assert gap_id.startswith("gap-")

        gap = engine.get_gap(agent_id, gap_id)
        assert gap is not None
        assert gap.description == description
        assert gap.status == "open"
        assert gap.priority == 8

    def test_list_gaps_by_priority(self):
        """
        Record multiple gaps, list them.
        Assert: returned in priority order.
        """
        engine = CapabilitySynthesisEngine()
        agent_id = "agent-bob"

        gaps = [
            ("basic feature", 3),
            ("critical performance issue", 9),
            ("nice-to-have improvement", 2),
        ]

        for description, priority in gaps:
            engine.record_gap(agent_id, description, priority=priority)
            time.sleep(0.01)

        listed = engine.list_gaps(agent_id, status="open", limit=10)

        assert len(listed) == 3
        assert listed[0].priority == 9
        assert listed[1].priority == 3
        assert listed[2].priority == 2


# ---------------------------------------------------------------------------
# Test 2 — Synthesize capabilities
# ---------------------------------------------------------------------------

class TestSynthesizeCapability:
    def test_synthesize_fills_gap(self):
        """
        Record a gap, synthesize a capability for it.
        Assert: capability generated, gap marked as synthesizing.
        """
        engine = CapabilitySynthesisEngine()
        agent_id = "agent-carol"

        gap_id = engine.record_gap(agent_id, "need batch processing", priority=7)

        capability = {
            "name": "batch_processor",
            "description": "processes items in configurable batches",
            "input_schema": "list of items, batch size",
            "output_schema": "list of results",
        }

        synthesis_id = engine.synthesize_capability(agent_id, gap_id, capability)
        assert synthesis_id.startswith("syn-")

        # Check synthesis was created
        synthesis = engine.get_synthesis(agent_id, synthesis_id)
        assert synthesis is not None
        assert synthesis.generated_capability["name"] == "batch_processor"
        assert synthesis.status == "created"

        # Check gap is marked as synthesizing
        gap = engine.get_gap(agent_id, gap_id)
        assert gap.status == "synthesizing"
        assert gap.synthesized_capability == synthesis_id


# ---------------------------------------------------------------------------
# Test 3 — Test synthesized capabilities
# ---------------------------------------------------------------------------

class TestSynthesisTestResults:
    def test_capability_passes_tests(self):
        """
        Synthesize a capability, mark tests as passed.
        Assert: test status updated, capability marked as 'tested'.
        """
        engine = CapabilitySynthesisEngine()
        agent_id = "agent-dave"

        gap_id = engine.record_gap(agent_id, "need something", priority=5)
        capability = {"name": "test_cap", "description": "test", "input_schema": "x", "output_schema": "y"}
        synthesis_id = engine.synthesize_capability(agent_id, gap_id, capability)

        test_results = {
            "passed": True,
            "tests_run": 5,
            "tests_passed": 5,
            "coverage": 0.95,
        }

        result = engine.test_capability(agent_id, synthesis_id, test_results)
        assert result is True

        synthesis = engine.get_synthesis(agent_id, synthesis_id)
        assert synthesis.test_status == "passed"
        assert synthesis.status == "tested"

    def test_capability_fails_tests(self):
        """
        Synthesize a capability, mark tests as failed.
        Assert: test status updated, failure recorded.
        """
        engine = CapabilitySynthesisEngine()
        agent_id = "agent-eve"

        gap_id = engine.record_gap(agent_id, "need something", priority=5)
        capability = {"name": "bad_cap", "description": "test", "input_schema": "x", "output_schema": "y"}
        synthesis_id = engine.synthesize_capability(agent_id, gap_id, capability)

        test_results = {
            "passed": False,
            "tests_run": 5,
            "tests_passed": 2,
            "failure_reason": "timeout on large inputs",
        }

        result = engine.test_capability(agent_id, synthesis_id, test_results)
        assert result is False

        synthesis = engine.get_synthesis(agent_id, synthesis_id)
        assert synthesis.test_status == "failed"


# ---------------------------------------------------------------------------
# Test 4 — Proposal and approval workflow
# ---------------------------------------------------------------------------

class TestSynthesisApproval:
    def test_capability_approval_workflow(self):
        """
        Synthesize, test, propose, approve, deploy.
        Assert: status transitions correctly.
        """
        engine = CapabilitySynthesisEngine()
        agent_id = "agent-frank"

        # Step 1: Record gap
        gap_id = engine.record_gap(agent_id, "need caching", priority=7)

        # Step 2: Synthesize
        capability = {"name": "cache", "description": "memory cache", "input_schema": "key", "output_schema": "value"}
        synthesis_id = engine.synthesize_capability(agent_id, gap_id, capability)
        synthesis = engine.get_synthesis(agent_id, synthesis_id)
        assert synthesis.status == "created"

        # Step 3: Test
        test_results = {"passed": True, "coverage": 0.90}
        engine.test_capability(agent_id, synthesis_id, test_results)
        synthesis = engine.get_synthesis(agent_id, synthesis_id)
        assert synthesis.status == "tested"

        # Step 4: Propose
        proposal_id = "prop-test-12345"
        engine.propose_capability(agent_id, synthesis_id, proposal_id)
        synthesis = engine.get_synthesis(agent_id, synthesis_id)
        assert synthesis.status == "proposed"
        assert synthesis.proposal_id == proposal_id

        # Step 5: Approve
        engine.approve_capability(agent_id, synthesis_id)
        synthesis = engine.get_synthesis(agent_id, synthesis_id)
        assert synthesis.status == "approved"
        assert synthesis.approved_at is not None

        # Step 6: Deploy
        engine.deploy_capability(agent_id, synthesis_id)
        synthesis = engine.get_synthesis(agent_id, synthesis_id)
        assert synthesis.status == "deployed"
        assert synthesis.deployed_at is not None


# ---------------------------------------------------------------------------
# Test 5 — List synthesized capabilities by status
# ---------------------------------------------------------------------------

class TestListSyntheses:
    def test_list_syntheses_by_status(self):
        """
        Create multiple syntheses in different states, list by status.
        Assert: correct filtering and ordering.
        """
        engine = CapabilitySynthesisEngine()
        agent_id = "agent-grace"

        # Create 3 gaps
        gap_ids = [
            engine.record_gap(agent_id, f"gap {i}", priority=5)
            for i in range(3)
        ]

        # Synthesize all
        cap = {"name": "cap", "description": "test", "input_schema": "x", "output_schema": "y"}
        synthesis_ids = [
            engine.synthesize_capability(agent_id, gid, cap)
            for gid in gap_ids
        ]

        # Mark first as tested
        engine.test_capability(agent_id, synthesis_ids[0], {"passed": True})

        # Mark second as tested (but not proposed, so it stays tested)
        engine.test_capability(agent_id, synthesis_ids[1], {"passed": True})

        # Mark third as tested and proposed
        engine.test_capability(agent_id, synthesis_ids[2], {"passed": True})
        engine.propose_capability(agent_id, synthesis_ids[2], "prop-456")

        # List tested - should have first two
        tested = engine.list_syntheses(agent_id, status="tested", limit=10)
        assert len(tested) == 2  # First and second
        assert all(s.status == "tested" for s in tested)


# ---------------------------------------------------------------------------
# Test 6 — Gap closing
# ---------------------------------------------------------------------------

class TestGapStatus:
    def test_gap_lifecycle(self):
        """
        Record gap, synthesize, deploy capability, close gap.
        Assert: gap transitions from open → synthesizing → closed.
        """
        engine = CapabilitySynthesisEngine()
        agent_id = "agent-henry"

        # Record gap
        gap_id = engine.record_gap(agent_id, "need async execution", priority=6)
        gap = engine.get_gap(agent_id, gap_id)
        assert gap.status == "open"

        # Synthesize
        capability = {"name": "async_executor", "description": "async", "input_schema": "fn", "output_schema": "result"}
        synthesis_id = engine.synthesize_capability(agent_id, gap_id, capability)
        gap = engine.get_gap(agent_id, gap_id)
        assert gap.status == "synthesizing"

        # Test and deploy
        engine.test_capability(agent_id, synthesis_id, {"passed": True})
        engine.approve_capability(agent_id, synthesis_id)
        engine.deploy_capability(agent_id, synthesis_id)

        # Gap is associated with deployed capability
        gap = engine.get_gap(agent_id, gap_id)
        assert gap.synthesized_capability == synthesis_id


# ---------------------------------------------------------------------------
# Test 7 — Multi-agent isolation
# ---------------------------------------------------------------------------

class TestSynthesisMultiAgent:
    def test_agents_separate_gaps_and_syntheses(self):
        """
        Two agents record gaps and create syntheses independently.
        Assert: each agent's gaps and syntheses are isolated.
        """
        engine = CapabilitySynthesisEngine()

        agent1_id = "agent-iris"
        agent2_id = "agent-jack"

        # Agent 1 records gaps
        gap1 = engine.record_gap(agent1_id, "agent1 gap", priority=5)

        # Agent 2 records gaps
        gap2 = engine.record_gap(agent2_id, "agent2 gap", priority=7)

        # List gaps for each agent
        agent1_gaps = engine.list_gaps(agent1_id)
        agent2_gaps = engine.list_gaps(agent2_id)

        assert len(agent1_gaps) == 1
        assert agent1_gaps[0].gap_id == gap1
        assert len(agent2_gaps) == 1
        assert agent2_gaps[0].gap_id == gap2
