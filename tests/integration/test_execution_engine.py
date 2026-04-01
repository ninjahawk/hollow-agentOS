"""
Integration tests for AgentOS v2.6.0: Execution Engine.

Tests verify capability execution, error handling, history tracking, and integration
with the native interface.

Run:
    PYTHONPATH=. pytest tests/integration/test_execution_engine.py -v -m integration
"""

import pytest
import time
import shutil
import tempfile
import os
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.execution_engine import ExecutionEngine, ExecutionContext
    EXECUTION_ENGINE_AVAILABLE = True
except ImportError:
    EXECUTION_ENGINE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not EXECUTION_ENGINE_AVAILABLE,
    reason="execution_engine not available"
)


@pytest.fixture(autouse=True)
def fresh_execution_storage(monkeypatch):
    """Fresh temporary directory for execution storage."""
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)

    import agents.execution_engine as exec_module
    exec_module.EXECUTION_PATH = Path(tmpdir) / "executions"

    yield

    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Test 1 — Register and execute capability
# ---------------------------------------------------------------------------

class TestExecutionBasics:
    def test_register_and_execute_simple_function(self):
        """
        Register a simple function as a capability.
        Execute it, verify result.
        """
        engine = ExecutionEngine()

        # Register a simple capability
        def read_file(path):
            return f"contents of {path}"

        registered = engine.register("read_file", read_file)
        assert registered is True

        # Execute it
        agent_id = "agent-alice"
        result, status = engine.execute(agent_id, "read_file", {"path": "/data.txt"})

        assert status == "success"
        assert result is not None
        assert "contents" in result["output"]

    def test_execute_nonexistent_capability(self):
        """
        Try to execute a capability that doesn't exist.
        Assert: returns not_found status.
        """
        engine = ExecutionEngine()
        agent_id = "agent-bob"

        result, status = engine.execute(agent_id, "nonexistent", {})

        assert status == "not_found"
        assert result is None


# ---------------------------------------------------------------------------
# Test 2 — Execution with parameters
# ---------------------------------------------------------------------------

class TestExecutionParams:
    def test_execute_with_multiple_parameters(self):
        """
        Execute capability with multiple parameters.
        Assert: all parameters passed correctly.
        """
        engine = ExecutionEngine()

        def process_data(data, multiplier=1, operation="add"):
            return {
                "processed": data * multiplier,
                "operation": operation,
                "input": data,
            }

        engine.register("process_data", process_data)

        agent_id = "agent-carol"
        result, status = engine.execute(
            agent_id,
            "process_data",
            {"data": 10, "multiplier": 3, "operation": "multiply"}
        )

        assert status == "success"
        assert result["processed"] == 30
        assert result["operation"] == "multiply"


# ---------------------------------------------------------------------------
# Test 3 — Error handling
# ---------------------------------------------------------------------------

class TestExecutionErrors:
    def test_execution_exception_captured(self):
        """
        Capability raises exception.
        Assert: error captured, status is failed.
        """
        engine = ExecutionEngine()

        def failing_function():
            raise ValueError("Something went wrong")

        engine.register("failing", failing_function)

        agent_id = "agent-dave"
        result, status = engine.execute(agent_id, "failing", {})

        assert status == "failed"
        assert result is not None
        assert "error" in result
        assert "Something went wrong" in result["error"]

    def test_execution_with_wrong_parameters(self):
        """
        Call capability with wrong parameter signature.
        Assert: TypeError caught and reported.
        """
        engine = ExecutionEngine()

        def needs_params(x, y):
            return x + y

        engine.register("add", needs_params)

        agent_id = "agent-eve"
        # Try calling with wrong params
        result, status = engine.execute(agent_id, "add", {"x": 5})

        assert status == "failed"
        assert "error" in result


# ---------------------------------------------------------------------------
# Test 4 — Execution history
# ---------------------------------------------------------------------------

class TestExecutionHistory:
    def test_execution_history_recorded(self):
        """
        Execute capability multiple times.
        Assert: all executions logged in history.
        """
        engine = ExecutionEngine()

        def counter(n):
            return {"count": n}

        engine.register("count", counter)
        agent_id = "agent-frank"

        # Execute multiple times
        for i in range(3):
            engine.execute(agent_id, "count", {"n": i})

        history = engine.get_execution_history(agent_id)

        assert len(history) == 3
        assert history[0].status == "success"

    def test_execution_history_shows_duration(self):
        """
        Execute capability.
        Assert: duration_ms is recorded.
        """
        engine = ExecutionEngine()

        def slow_operation():
            time.sleep(0.01)
            return {"done": True}

        engine.register("slow", slow_operation)
        agent_id = "agent-grace"

        engine.execute(agent_id, "slow", {})
        history = engine.get_execution_history(agent_id)

        assert history[0].duration_ms >= 10.0


# ---------------------------------------------------------------------------
# Test 5 — Capability enable/disable
# ---------------------------------------------------------------------------

class TestCapabilityControl:
    def test_disable_capability(self):
        """
        Register and execute capability, then disable it.
        Assert: disabled capability returns 'disabled' status.
        """
        engine = ExecutionEngine()

        def some_func():
            return {"status": "ok"}

        engine.register("func", some_func)
        agent_id = "agent-henry"

        # First execution should work
        result, status = engine.execute(agent_id, "func", {})
        assert status == "success"

        # Disable it
        disabled = engine.disable_capability("func")
        assert disabled is True

        # Now execution should fail
        result, status = engine.execute(agent_id, "func", {})
        assert status == "disabled"

    def test_enable_after_disable(self):
        """
        Disable and then re-enable a capability.
        Assert: works again after re-enable.
        """
        engine = ExecutionEngine()

        def func():
            return {"ok": True}

        engine.register("func", func)
        agent_id = "agent-iris"

        engine.disable_capability("func")
        result, status = engine.execute(agent_id, "func", {})
        assert status == "disabled"

        engine.enable_capability("func")
        result, status = engine.execute(agent_id, "func", {})
        assert status == "success"


# ---------------------------------------------------------------------------
# Test 6 — Statistics
# ---------------------------------------------------------------------------

class TestExecutionStats:
    def test_execution_statistics(self):
        """
        Execute capabilities with mix of success/failure.
        Assert: statistics computed correctly.
        """
        engine = ExecutionEngine()

        def success_func():
            return {"ok": True}

        def fail_func():
            raise Exception("Intentional failure")

        engine.register("success", success_func)
        engine.register("fail", fail_func)
        agent_id = "agent-jack"

        # Execute mix of success and failures
        engine.execute(agent_id, "success", {})
        engine.execute(agent_id, "success", {})
        engine.execute(agent_id, "fail", {})

        stats = engine.get_stats(agent_id)

        assert stats["total_executions"] == 3
        assert stats["success_count"] == 2
        assert stats["failed_count"] == 1
        assert stats["success_rate"] == pytest.approx(2/3, rel=0.01)


# ---------------------------------------------------------------------------
# Test 7 — Multi-agent isolation
# ---------------------------------------------------------------------------

class TestMultiAgentExecution:
    def test_agents_have_separate_execution_histories(self):
        """
        Multiple agents execute same capability.
        Assert: each agent has separate execution history.
        """
        engine = ExecutionEngine()

        def func():
            return {"result": "ok"}

        engine.register("shared_func", func)

        agent1_id = "agent-alice"
        agent2_id = "agent-bob"

        engine.execute(agent1_id, "shared_func", {})
        engine.execute(agent1_id, "shared_func", {})
        engine.execute(agent2_id, "shared_func", {})

        alice_history = engine.get_execution_history(agent1_id)
        bob_history = engine.get_execution_history(agent2_id)

        assert len(alice_history) == 2
        assert len(bob_history) == 1
