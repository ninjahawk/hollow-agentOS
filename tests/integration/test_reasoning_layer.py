"""
Integration tests for AgentOS v2.6.0: Reasoning Layer.

Tests verify intent reasoning, capability selection, parameter generation,
and learning from execution outcomes.

Run:
    PYTHONPATH=. pytest tests/integration/test_reasoning_layer.py -v -m integration
"""

import pytest
import time
import shutil
import tempfile
import os
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.reasoning_layer import ReasoningLayer, ReasoningContext
    from agents.capability_graph import CapabilityGraph, CapabilityRecord
    from agents.execution_engine import ExecutionEngine
    REASONING_LAYER_AVAILABLE = True
except ImportError:
    REASONING_LAYER_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not REASONING_LAYER_AVAILABLE,
    reason="reasoning_layer not available"
)


@pytest.fixture(autouse=True)
def fresh_reasoning_storage(monkeypatch):
    """Fresh temporary directory for reasoning storage."""
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)

    import agents.reasoning_layer as reasoning_module
    import agents.capability_graph as cap_module
    reasoning_module.REASONING_PATH = Path(tmpdir) / "reasoning"
    cap_module.CAPABILITY_PATH = Path(tmpdir) / "capabilities"

    yield

    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Test 1 — Basic reasoning
# ---------------------------------------------------------------------------

class TestReasoningBasics:
    def test_reason_about_intent(self):
        """
        Agent submits intent, reasoning layer picks a capability.
        Assert: reasoning returns capability, params, confidence.
        """
        graph = CapabilityGraph()

        # Register some capabilities
        cap = CapabilityRecord(
            capability_id="",
            name="read_file",
            description="reads file from disk",
            input_schema="file path",
            output_schema="file content",
        )
        graph.register(cap)

        reasoning = ReasoningLayer(capability_graph=graph)
        agent_id = "agent-alice"

        cap_id, params, confidence, reasoning_text = reasoning.reason(
            agent_id,
            "read the log file"
        )

        assert cap_id is not None
        assert isinstance(params, dict)
        assert confidence > 0.0
        assert "read" in reasoning_text.lower()

    def test_reasoning_with_no_matching_capabilities(self):
        """
        No capabilities match the intent.
        Assert: returns None for capability_id.
        """
        reasoning = ReasoningLayer(capability_graph=None)
        agent_id = "agent-bob"

        cap_id, params, confidence, reasoning_text = reasoning.reason(
            agent_id,
            "do something completely unknown"
        )

        assert cap_id is None
        assert confidence == 0.0


# ---------------------------------------------------------------------------
# Test 2 — Reasoning history
# ---------------------------------------------------------------------------

class TestReasoningHistory:
    def test_reasoning_history_recorded(self):
        """
        Perform multiple reasonings.
        Assert: all recorded in history.
        """
        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="does something",
            input_schema="x",
            output_schema="y",
        )
        graph.register(cap)

        reasoning = ReasoningLayer(capability_graph=graph)
        agent_id = "agent-carol"

        for i in range(3):
            reasoning.reason(agent_id, f"intent {i}")

        history = reasoning.get_reasoning_history(agent_id)

        assert len(history) == 3
        assert all(h.reasoning_id.startswith("rsn-") for h in history)


# ---------------------------------------------------------------------------
# Test 3 — Learning from outcomes
# ---------------------------------------------------------------------------

class TestLearningFromExecution:
    def test_learn_from_successful_execution(self):
        """
        Reasoning produces result, execution succeeds.
        Assert: learning recorded in history.
        """
        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="operation",
            input_schema="x",
            output_schema="y",
        )
        graph.register(cap)

        reasoning = ReasoningLayer(capability_graph=graph)
        agent_id = "agent-dave"

        cap_id, params, conf, text = reasoning.reason(agent_id, "perform an operation")

        # Get reasoning_id before learning
        history_before = reasoning.get_reasoning_history(agent_id)
        assert len(history_before) > 0
        reasoning_id = history_before[0].reasoning_id

        # Simulate learning from execution
        reasoning.learn_from_execution(
            agent_id,
            reasoning_id,
            {"result": "success"},
            "success"
        )

        history = reasoning.get_reasoning_history(agent_id)
        assert history[0].execution_status == "success"
        assert history[0].execution_result == {"result": "success"}

    def test_learn_from_failed_execution(self):
        """
        Reasoning produces result, execution fails.
        Assert: failure recorded for learning.
        """
        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="operation",
            input_schema="x",
            output_schema="y",
        )
        graph.register(cap)

        reasoning = ReasoningLayer(capability_graph=graph)
        agent_id = "agent-eve"

        reasoning.reason(agent_id, "perform an operation")

        reasoning.learn_from_execution(
            agent_id,
            reasoning.get_reasoning_history(agent_id)[0].reasoning_id,
            {"error": "failed"},
            "failed"
        )

        history = reasoning.get_reasoning_history(agent_id)
        assert history[0].execution_status == "failed"


# ---------------------------------------------------------------------------
# Test 4 — Success rate tracking
# ---------------------------------------------------------------------------

class TestSuccessRateTracking:
    def test_success_rate_calculation(self):
        """
        Perform reasonings with mix of success/failure.
        Assert: success rate computed correctly.
        """
        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="operation",
            input_schema="x",
            output_schema="y",
        )
        graph.register(cap)

        reasoning = ReasoningLayer(capability_graph=graph)
        agent_id = "agent-frank"

        # Perform 5 reasonings
        for i in range(5):
            reasoning.reason(agent_id, f"perform operation {i}")

        history = reasoning.get_reasoning_history(agent_id)

        # Mark 3 as success, 2 as failed
        for i, h in enumerate(history):
            status = "success" if i < 3 else "failed"
            reasoning.learn_from_execution(agent_id, h.reasoning_id, {}, status)

        success_rate = reasoning.get_success_rate(agent_id)

        assert success_rate == pytest.approx(0.6, rel=0.01)


# ---------------------------------------------------------------------------
# Test 5 — Integration: Reasoning + Execution
# ---------------------------------------------------------------------------

class TestReasoningExecutionIntegration:
    def test_full_reasoning_execution_cycle(self):
        """
        Full cycle: intent → reason → execute → learn.
        Assert: all components work together.
        """
        # Set up graph
        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="process",
            description="processes data",
            input_schema="data",
            output_schema="result",
        )
        cap_id = graph.register(cap)

        # Set up execution
        execution = ExecutionEngine()
        execution.register(cap_id, lambda data: {"processed": data})

        # Set up reasoning
        reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution)
        agent_id = "agent-grace"

        # Step 1: Reason
        selected_cap, params, conf, text = reasoning.reason(agent_id, "process my data")
        assert selected_cap is not None

        # Step 2: Execute
        result, status = execution.execute(agent_id, selected_cap, params)
        assert status == "success"

        # Step 3: Learn
        reasoning_id = reasoning.get_reasoning_history(agent_id)[0].reasoning_id
        reasoning.learn_from_execution(agent_id, reasoning_id, result, status)

        # Verify learning recorded
        history = reasoning.get_reasoning_history(agent_id)
        assert history[0].execution_status == "success"
        assert history[0].execution_result == result


# ---------------------------------------------------------------------------
# Test 6 — Multi-agent isolation
# ---------------------------------------------------------------------------

class TestReasoningMultiAgent:
    def test_agents_have_separate_reasoning_histories(self):
        """
        Multiple agents perform reasoning independently.
        Assert: each agent has separate history.
        """
        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="operation",
            input_schema="x",
            output_schema="y",
        )
        graph.register(cap)

        reasoning = ReasoningLayer(capability_graph=graph)

        agent1_id = "agent-henry"
        agent2_id = "agent-iris"

        reasoning.reason(agent1_id, "intent 1")
        reasoning.reason(agent1_id, "intent 2")
        reasoning.reason(agent2_id, "intent 1")

        history1 = reasoning.get_reasoning_history(agent1_id)
        history2 = reasoning.get_reasoning_history(agent2_id)

        assert len(history1) == 2
        assert len(history2) == 1
