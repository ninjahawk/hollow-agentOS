"""
Integration tests for AgentOS v2.5.0: Agent-Native Interface.

Tests verify pure embedding-space agent-OS communication with semantic intents,
capability discovery, and full introspection capabilities.

Run:
    PYTHONPATH=. pytest tests/integration/test_agent_native_interface.py -v -m integration
"""

import pytest
import time
import shutil
import tempfile
import os
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.agent_native_interface import AgentNativeInterface, OperationRecord
    from agents.capability_graph import CapabilityGraph, CapabilityRecord
    NATIVE_INTERFACE_AVAILABLE = True
except ImportError:
    NATIVE_INTERFACE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not NATIVE_INTERFACE_AVAILABLE,
    reason="sentence-transformers not available"
)


@pytest.fixture(autouse=True)
def fresh_interface_storage(monkeypatch):
    """
    Provide a fresh temporary directory for interface storage in each test.
    """
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)

    import agents.agent_native_interface as iface_module
    import agents.capability_graph as cap_module
    iface_module.INTERFACE_PATH = Path(tmpdir) / "native_interface"
    cap_module.CAPABILITY_PATH = Path(tmpdir) / "capabilities"

    yield

    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Test 1 — Pure semantic requests
# ---------------------------------------------------------------------------

class TestNativeRequest:
    def test_agent_request_with_intent(self):
        """
        Agent submits plain-text intent.
        Assert: request processed, operation recorded.
        """
        interface = AgentNativeInterface()
        agent_id = "agent-alice"

        response, confidence = interface.request(agent_id, "read a file from disk")

        assert isinstance(response, dict)
        assert isinstance(confidence, float)
        assert confidence >= 0.0 and confidence <= 1.0

    def test_request_with_context(self):
        """
        Agent submits intent with context data.
        Assert: context stored in operation record.
        """
        interface = AgentNativeInterface()
        agent_id = "agent-bob"
        context = {"timeout": 5000, "retries": 3, "priority": "high"}

        response, confidence = interface.request(
            agent_id,
            "process data with these settings",
            context=context
        )

        # Verify context was stored
        history = interface.get_operation_history(agent_id)
        assert len(history) > 0
        assert history[0]["context"] == context


# ---------------------------------------------------------------------------
# Test 2 — Capability search
# ---------------------------------------------------------------------------

class TestNativeSearch:
    def test_search_capabilities_by_intent(self):
        """
        Interface has capability graph with registered capabilities.
        Agent searches for capabilities matching an intent.
        Assert: matching capabilities returned.
        """
        # Create capability graph with some capabilities
        graph = CapabilityGraph()
        cap1 = CapabilityRecord(
            capability_id="",
            name="read_file",
            description="reads file from disk and returns content",
            input_schema="file path",
            output_schema="file content",
            confidence=0.95,
        )
        cap2 = CapabilityRecord(
            capability_id="",
            name="write_file",
            description="writes content to a file on disk",
            input_schema="file path and content",
            output_schema="success status",
            confidence=0.94,
        )
        graph.register(cap1)
        graph.register(cap2)

        # Create interface with capability graph
        interface = AgentNativeInterface(capability_graph=graph)
        agent_id = "agent-carol"

        # Search for file reading
        results = interface.search_capabilities(agent_id, "read a file", top_k=2)

        assert len(results) > 0
        assert all(isinstance(cap_id, str) and isinstance(conf, float) for cap_id, conf in results)


# ---------------------------------------------------------------------------
# Test 3 — Capability explanation
# ---------------------------------------------------------------------------

class TestNativeExplain:
    def test_explain_capability(self):
        """
        Agent queries what a capability does.
        Assert: semantic explanation returned.
        """
        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="json_parser",
            description="parses JSON strings into objects",
            input_schema="JSON string",
            output_schema="parsed object structure",
            composition_tags=["parsing", "serialization"],
            confidence=0.92,
        )
        cap_id = graph.register(cap)

        interface = AgentNativeInterface(capability_graph=graph)

        explanation = interface.explain_capability(cap_id)

        assert explanation is not None
        assert explanation["name"] == "json_parser"
        assert "parses" in explanation["description"].lower()
        assert "JSON" in explanation["input_schema"]


# ---------------------------------------------------------------------------
# Test 4 — Introspection capabilities
# ---------------------------------------------------------------------------

class TestNativeIntrospection:
    def test_introspect_memory(self):
        """
        Agent queries its own memory.
        Assert: recent memories returned.
        """
        from agents.semantic_memory import SemanticMemory

        memory_engine = SemanticMemory()
        agent_id = "agent-dave"

        # Store some memories
        memory_engine.store(agent_id, "first thought about optimization")
        memory_engine.store(agent_id, "second thought about performance")

        interface = AgentNativeInterface()
        memories = interface.introspect_memory(agent_id, semantic_memory=memory_engine)

        assert len(memories) > 0
        assert all("thought" in m for m in memories)
        assert all("memory_id" in m for m in memories)

    def test_introspect_goals(self):
        """
        Agent queries its active goals.
        Assert: active goals returned with details.
        """
        from agents.persistent_goal import PersistentGoalEngine

        goal_engine = PersistentGoalEngine()
        agent_id = "agent-eve"

        # Create some goals
        goal_engine.create(agent_id, "optimize system performance", priority=8)
        goal_engine.create(agent_id, "improve test coverage", priority=6)

        interface = AgentNativeInterface()
        goals = interface.introspect_goals(agent_id, goal_engine=goal_engine)

        assert len(goals) > 0
        assert all("objective" in g for g in goals)
        assert all("priority" in g for g in goals)


# ---------------------------------------------------------------------------
# Test 5 — Operation history and stats
# ---------------------------------------------------------------------------

class TestNativeHistory:
    def test_operation_history(self):
        """
        Agent performs multiple operations, query history.
        Assert: history returned in order.
        """
        interface = AgentNativeInterface()
        agent_id = "agent-frank"

        # Perform multiple operations
        for i in range(3):
            interface.request(agent_id, f"operation {i}")
            time.sleep(0.01)

        history = interface.get_operation_history(agent_id)

        assert len(history) == 3
        # Most recent first
        assert history[0]["request_intent"] == "operation 2"
        assert history[2]["request_intent"] == "operation 0"

    def test_interface_stats(self):
        """
        Agent performs operations, query interface stats.
        Assert: statistics computed correctly.
        """
        interface = AgentNativeInterface()
        agent_id = "agent-grace"

        for i in range(5):
            interface.request(agent_id, f"test operation {i}")

        stats = interface.get_interface_stats(agent_id)

        assert stats["total_operations"] == 5
        assert "average_confidence" in stats
        assert "average_latency_ms" in stats
        assert stats["average_latency_ms"] >= 0


# ---------------------------------------------------------------------------
# Test 6 — Integration: full workflow
# ---------------------------------------------------------------------------

class TestNativeIntegration:
    def test_full_workflow_with_all_components(self):
        """
        Full integration: agent uses native interface with goals, memory, and capabilities.
        Assert: all components work together seamlessly.
        """
        from agents.semantic_memory import SemanticMemory
        from agents.persistent_goal import PersistentGoalEngine

        # Set up components
        memory_engine = SemanticMemory()
        goal_engine = PersistentGoalEngine()
        graph = CapabilityGraph()

        # Register a capability
        cap = CapabilityRecord(
            capability_id="",
            name="process_data",
            description="processes data stream efficiently",
            input_schema="data items",
            output_schema="processed results",
            confidence=0.90,
        )
        graph.register(cap)

        # Create interface with all components
        interface = AgentNativeInterface(capability_graph=graph)
        agent_id = "agent-henry"

        # Agent stores memory
        memory_engine.store(agent_id, "planning to process large dataset")

        # Agent creates goal
        goal_engine.create(agent_id, "process all pending data", priority=8)

        # Agent makes request
        response, confidence = interface.request(agent_id, "process my data efficiently")

        # Agent introspects
        memories = interface.introspect_memory(agent_id, semantic_memory=memory_engine)
        goals = interface.introspect_goals(agent_id, goal_engine=goal_engine)
        capabilities = interface.search_capabilities(agent_id, "process data")

        # All should work
        assert len(memories) > 0
        assert len(goals) > 0
        assert len(capabilities) > 0
        assert confidence >= 0.0


# ---------------------------------------------------------------------------
# Test 7 — Multi-agent isolation
# ---------------------------------------------------------------------------

class TestNativeMultiAgent:
    def test_agents_have_separate_operations(self):
        """
        Multiple agents use the interface independently.
        Assert: each agent's operations are isolated.
        """
        interface = AgentNativeInterface()

        agent1_id = "agent-iris"
        agent2_id = "agent-jack"

        interface.request(agent1_id, "agent 1 operation")
        interface.request(agent2_id, "agent 2 operation")

        history1 = interface.get_operation_history(agent1_id)
        history2 = interface.get_operation_history(agent2_id)

        assert len(history1) == 1
        assert len(history2) == 1
        assert history1[0]["request_intent"] == "agent 1 operation"
        assert history2[0]["request_intent"] == "agent 2 operation"
