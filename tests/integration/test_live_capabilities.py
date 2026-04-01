"""
Integration tests for AgentOS v3.11.1: Live Capabilities.

Verifies that the live OS operations are correctly registered as
CapabilityGraph entries with ExecutionEngine implementations, and that
agents can discover them semantically.

Run:
    PYTHONPATH=. pytest tests/integration/test_live_capabilities.py -v
"""

import pytest
import os

pytestmark = pytest.mark.integration

try:
    from agents.live_capabilities import (
        build_capability_graph,
        build_execution_engine,
        build_live_stack,
        LIVE_CAPABILITIES,
    )
    LIVE_CAPS_AVAILABLE = True
except ImportError:
    LIVE_CAPS_AVAILABLE = False

try:
    from agents.capability_graph import CapabilityGraph, CapabilityRecord
    from agents.execution_engine import ExecutionEngine
    STACK_AVAILABLE = True
except ImportError:
    STACK_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not LIVE_CAPS_AVAILABLE,
    reason="live_capabilities module not available"
)

API_REACHABLE = False
try:
    import httpx
    r = httpx.get("http://localhost:7777/state",
                  headers={"Authorization": "Bearer " +
                           __import__("json").loads(
                               open("/agentOS/config.json").read()
                           )["api"]["token"]},
                  timeout=3)
    API_REACHABLE = r.status_code == 200
except Exception:
    pass


# --------------------------------------------------------------------------- #
# Test 1 — manifest is complete and well-formed
# --------------------------------------------------------------------------- #

class TestManifest:
    def test_all_capabilities_have_required_fields(self):
        """Every entry in LIVE_CAPABILITIES has all required fields."""
        required = {"capability_id", "name", "description",
                    "input_schema", "output_schema", "composition_tags", "fn"}
        for cap in LIVE_CAPABILITIES:
            missing = required - set(cap.keys())
            assert not missing, f"{cap.get('capability_id')} missing: {missing}"

    def test_expected_capability_ids_present(self):
        """All eight expected capabilities are in the manifest."""
        ids = {c["capability_id"] for c in LIVE_CAPABILITIES}
        expected = {
            "shell_exec", "ollama_chat", "fs_read", "fs_write",
            "semantic_search", "memory_set", "memory_get", "agent_message",
        }
        assert expected == ids

    def test_all_fns_are_callable(self):
        """Every capability has a callable fn."""
        for cap in LIVE_CAPABILITIES:
            assert callable(cap["fn"]), f"{cap['capability_id']} fn not callable"

    def test_descriptions_are_non_trivial(self):
        """Descriptions are long enough to embed meaningfully (>20 chars)."""
        for cap in LIVE_CAPABILITIES:
            assert len(cap["description"]) > 20, (
                f"{cap['capability_id']} description too short"
            )


# --------------------------------------------------------------------------- #
# Test 2 — CapabilityGraph registration
# --------------------------------------------------------------------------- #

class TestCapabilityGraphRegistration:
    def test_build_returns_capability_graph(self):
        """build_capability_graph() returns a CapabilityGraph instance."""
        if not STACK_AVAILABLE:
            pytest.skip("CapabilityGraph not available")
        graph = build_capability_graph()
        assert isinstance(graph, CapabilityGraph)

    def test_all_capabilities_registered(self):
        """All 8 capabilities appear in the graph."""
        if not STACK_AVAILABLE:
            pytest.skip("CapabilityGraph not available")
        graph = build_capability_graph()
        for cap in LIVE_CAPABILITIES:
            stored = graph.get(cap["capability_id"])
            assert stored is not None, f"{cap['capability_id']} not registered"

    def test_capability_fields_preserved(self):
        """Registered capabilities preserve name, description, schemas."""
        if not STACK_AVAILABLE:
            pytest.skip("CapabilityGraph not available")
        graph = build_capability_graph()
        rec = graph.get("shell_exec")
        assert rec is not None
        assert "shell" in rec.description.lower() or "command" in rec.description.lower()
        assert rec.input_schema != ""
        assert rec.output_schema != ""

    def test_semantic_search_finds_shell_capability(self):
        """Searching 'run a shell command' returns shell_exec in top results."""
        if not STACK_AVAILABLE:
            pytest.skip("CapabilityGraph not available")
        graph = build_capability_graph()
        results = graph.find("run a shell command", top_k=3, similarity_threshold=0.3)
        cap_ids = [r[0].capability_id for r in results]
        assert "shell_exec" in cap_ids, f"shell_exec not found, got: {cap_ids}"

    def test_semantic_search_finds_llm_capability(self):
        """Searching 'ask a language model' returns ollama_chat."""
        if not STACK_AVAILABLE:
            pytest.skip("CapabilityGraph not available")
        graph = build_capability_graph()
        results = graph.find("ask a language model for analysis", top_k=3, similarity_threshold=0.3)
        cap_ids = [r[0].capability_id for r in results]
        assert "ollama_chat" in cap_ids, f"ollama_chat not found, got: {cap_ids}"

    def test_semantic_search_finds_search_capability(self):
        """Searching 'find code by meaning' returns semantic_search."""
        if not STACK_AVAILABLE:
            pytest.skip("CapabilityGraph not available")
        graph = build_capability_graph()
        results = graph.find("find code by meaning", top_k=3, similarity_threshold=0.3)
        cap_ids = [r[0].capability_id for r in results]
        assert "semantic_search" in cap_ids, (
            f"semantic_search not found, got: {cap_ids}"
        )


# --------------------------------------------------------------------------- #
# Test 3 — ExecutionEngine registration
# --------------------------------------------------------------------------- #

class TestExecutionEngineRegistration:
    def test_build_returns_execution_engine(self):
        """build_execution_engine() returns an ExecutionEngine."""
        if not STACK_AVAILABLE:
            pytest.skip("ExecutionEngine not available")
        engine = build_execution_engine()
        assert isinstance(engine, ExecutionEngine)

    def test_all_capabilities_have_implementations(self):
        """Every capability ID has an implementation registered."""
        if not STACK_AVAILABLE:
            pytest.skip("ExecutionEngine not available")
        engine = build_execution_engine()
        registered = engine.list_registered()
        for cap in LIVE_CAPABILITIES:
            assert cap["capability_id"] in registered, (
                f"{cap['capability_id']} has no implementation"
            )

    def test_build_live_stack_returns_both(self):
        """build_live_stack() returns (CapabilityGraph, ExecutionEngine)."""
        if not STACK_AVAILABLE:
            pytest.skip("stack modules not available")
        graph, engine = build_live_stack()
        assert isinstance(graph, CapabilityGraph)
        assert isinstance(engine, ExecutionEngine)


# --------------------------------------------------------------------------- #
# Test 4 — live API calls (skipped if API not reachable)
# --------------------------------------------------------------------------- #

class TestLiveExecution:
    def test_shell_exec_runs_echo(self):
        """shell_exec can run 'echo hello' and returns stdout."""
        if not API_REACHABLE:
            pytest.skip("AgentOS API not reachable")
        if not STACK_AVAILABLE:
            pytest.skip("ExecutionEngine not available")
        engine = build_execution_engine()
        result, status = engine.execute(
            "test-agent", "shell_exec", {"command": "echo hello"}
        )
        assert status == "success"
        assert result is not None
        assert "hello" in result.get("stdout", "")

    def test_semantic_search_returns_results(self):
        """semantic_search returns results from the live index."""
        if not API_REACHABLE:
            pytest.skip("AgentOS API not reachable")
        if not STACK_AVAILABLE:
            pytest.skip("ExecutionEngine not available")
        engine = build_execution_engine()
        result, status = engine.execute(
            "test-agent", "semantic_search",
            {"query": "agent goal pursuit", "top_k": 3}
        )
        assert status == "success"
        assert result is not None
        assert result.get("count", 0) > 0

    def test_memory_set_then_get_roundtrip(self):
        """memory_set + memory_get round-trips a value."""
        if not API_REACHABLE:
            pytest.skip("AgentOS API not reachable")
        if not STACK_AVAILABLE:
            pytest.skip("ExecutionEngine not available")
        import uuid
        engine = build_execution_engine()
        key = f"test-{uuid.uuid4().hex[:8]}"
        val = "live-cap-test-value"

        set_result, set_status = engine.execute(
            "test-agent", "memory_set", {"key": key, "value": val}
        )
        assert set_status == "success"

        get_result, get_status = engine.execute(
            "test-agent", "memory_get", {"key": key}
        )
        assert get_status == "success"
        assert get_result["value"] == val

    def test_fs_write_then_read_roundtrip(self):
        """fs_write + fs_read round-trips file content."""
        if not API_REACHABLE:
            pytest.skip("AgentOS API not reachable")
        if not STACK_AVAILABLE:
            pytest.skip("ExecutionEngine not available")
        import uuid
        engine = build_execution_engine()
        path = f"/agentOS/workspace/test-livecap-{uuid.uuid4().hex[:8]}.txt"
        content = "live capabilities test content"

        write_result, write_status = engine.execute(
            "test-agent", "fs_write", {"path": path, "content": content}
        )
        assert write_status == "success"

        read_result, read_status = engine.execute(
            "test-agent", "fs_read", {"path": path}
        )
        assert read_status == "success"
        assert read_result["content"] == content
