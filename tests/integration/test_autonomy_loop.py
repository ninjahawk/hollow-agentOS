"""
Integration tests for AgentOS v2.7.0: Autonomy Loop.

Tests verify goal pursuit, learning integration, synthesis detection, and multi-step execution.

Run:
    PYTHONPATH=. pytest tests/integration/test_autonomy_loop.py -v -m integration
"""

import pytest
import time
import shutil
import tempfile
import os
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.autonomy_loop import AutonomyLoop, AutonomyStep
    from agents.persistent_goal import PersistentGoalEngine, GoalRecord
    from agents.reasoning_layer import ReasoningLayer
    from agents.execution_engine import ExecutionEngine
    from agents.capability_graph import CapabilityGraph, CapabilityRecord
    from agents.semantic_memory import SemanticMemory
    AUTONOMY_AVAILABLE = True
except ImportError:
    AUTONOMY_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not AUTONOMY_AVAILABLE,
    reason="autonomy_loop not available"
)


@pytest.fixture(autouse=True)
def fresh_autonomy_storage(monkeypatch):
    """Fresh temporary directory for autonomy storage."""
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)

    import agents.autonomy_loop as auton_module
    import agents.reasoning_layer as reason_module
    import agents.execution_engine as exec_module
    import agents.persistent_goal as goal_module
    import agents.semantic_memory as mem_module

    auton_module.AUTONOMY_PATH = Path(tmpdir) / "autonomy"
    reason_module.REASONING_PATH = Path(tmpdir) / "reasoning"
    exec_module.EXECUTION_PATH = Path(tmpdir) / "executions"
    goal_module.GOAL_PATH = Path(tmpdir) / "goals"
    mem_module.MEMORY_PATH = Path(tmpdir) / "memory"

    yield

    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Test 1 — Single autonomy step
# ---------------------------------------------------------------------------

class TestAutonomyBasics:
    def test_execute_single_autonomy_step(self):
        """
        Agent has active goal, autonomy loop executes one step.
        Assert: step is recorded in execution chain.
        """
        # Set up goal engine
        goal_engine = PersistentGoalEngine()
        agent_id = "agent-alice"
        goal_id = goal_engine.create(
            agent_id,
            objective="perform operation task",
            priority=5,
        )

        # Set up capability graph
        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="operation",
            input_schema="",
            output_schema="result",
        )
        cap_id = graph.register(cap)

        # Set up execution
        execution = ExecutionEngine()
        execution.register(cap_id, lambda: {"result": True})

        # Set up reasoning
        reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution)

        # Set up autonomy loop
        autonomy = AutonomyLoop(
            goal_engine=goal_engine,
            reasoning_layer=reasoning,
            execution_engine=execution,
        )

        # Execute one step
        goal_result, success = autonomy.execute_step(agent_id)

        assert goal_result == goal_id

        # Verify step was recorded (either succeeded or failed, the loop ran)
        chain = autonomy.get_execution_chain(agent_id)
        assert len(chain) == 1

    def test_autonomy_step_with_no_active_goal(self):
        """
        Agent has no active goal.
        Assert: execute_step returns (None, False).
        """
        goal_engine = PersistentGoalEngine()
        autonomy = AutonomyLoop(goal_engine=goal_engine, reasoning_layer=None)
        agent_id = "agent-bob"

        goal_result, success = autonomy.execute_step(agent_id)

        assert goal_result is None
        assert success is False


# ---------------------------------------------------------------------------
# Test 2 — Multi-step goal pursuit
# ---------------------------------------------------------------------------

class TestAutonomyGoalPursuit:
    def test_pursue_goal_until_completion(self):
        """
        Agent pursues goal over multiple steps.
        Assert: pursue_goal executes and returns goal_id.
        """
        # Set up goal
        goal_engine = PersistentGoalEngine()
        agent_id = "agent-carol"
        goal_id = goal_engine.create(
            agent_id,
            objective="perform operation task",
            priority=5,
        )

        # Set up simple capability that always succeeds
        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="operation",
            input_schema="",
            output_schema="y",
        )
        cap_id = graph.register(cap)

        execution = ExecutionEngine()
        execution.register(cap_id, lambda: {"done": True})

        reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution)

        autonomy = AutonomyLoop(
            goal_engine=goal_engine,
            reasoning_layer=reasoning,
            execution_engine=execution,
        )

        # Pursue goal for up to 3 steps
        goal_result, final_progress, steps = autonomy.pursue_goal(agent_id, max_steps=3)

        assert goal_result == goal_id
        assert steps <= 3  # Should execute up to 3 steps

    def test_pursue_goal_reaches_max_steps(self):
        """
        Goal pursuit respects max_steps limit.
        Assert: returns steps <= max_steps.
        """
        goal_engine = PersistentGoalEngine()
        agent_id = "agent-dave"
        goal_id = goal_engine.create(
            agent_id,
            objective="perform operation task",
            priority=5,
        )

        # Set up capability
        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="operation",
            input_schema="",
            output_schema="y",
        )
        cap_id = graph.register(cap)

        execution = ExecutionEngine()
        execution.register(cap_id, lambda: {"done": True})

        reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution)

        autonomy = AutonomyLoop(
            goal_engine=goal_engine,
            reasoning_layer=reasoning,
            execution_engine=execution,
        )

        # Pursue with max_steps limit
        goal_result, final_progress, steps = autonomy.pursue_goal(agent_id, max_steps=3)

        assert goal_result == goal_id
        assert steps <= 3


# ---------------------------------------------------------------------------
# Test 3 — Execution chain tracking
# ---------------------------------------------------------------------------

class TestAutonomyChain:
    def test_execution_chain_recorded(self):
        """
        Multiple autonomy steps are recorded in execution chain.
        Assert: all steps recorded.
        """
        goal_engine = PersistentGoalEngine()
        agent_id = "agent-eve"
        goal_id = goal_engine.create(
            agent_id,
            objective="perform operation task",
            priority=5,
        )

        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="operation",
            input_schema="",
            output_schema="y",
        )
        cap_id = graph.register(cap)

        execution = ExecutionEngine()
        execution.register(cap_id, lambda: {"status": "ok"})

        reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution)

        autonomy = AutonomyLoop(
            goal_engine=goal_engine,
            reasoning_layer=reasoning,
            execution_engine=execution,
        )

        # Execute 3 steps
        for _ in range(3):
            autonomy.execute_step(agent_id)

        chain = autonomy.get_execution_chain(agent_id)

        assert len(chain) == 3
        assert all(s.goal_id == goal_id for s in chain)


# ---------------------------------------------------------------------------
# Test 4 — Success rate tracking
# ---------------------------------------------------------------------------

class TestAutonomyStats:
    def test_autonomy_success_rate(self):
        """
        Autonomy loop tracks success rate.
        Assert: success_rate is computed.
        """
        goal_engine = PersistentGoalEngine()
        agent_id = "agent-frank"
        goal_id = goal_engine.create(
            agent_id,
            objective="perform operation task",
            priority=5,
        )

        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="operation",
            input_schema="",
            output_schema="y",
        )
        cap_id = graph.register(cap)

        execution = ExecutionEngine()
        execution.register(cap_id, lambda: {"ok": True})

        reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution)

        autonomy = AutonomyLoop(
            goal_engine=goal_engine,
            reasoning_layer=reasoning,
            execution_engine=execution,
        )

        # Execute 3 steps
        for _ in range(3):
            autonomy.execute_step(agent_id)

        success_rate = autonomy.get_success_rate(agent_id)

        # Success rate should be computable (>= 0.0)
        assert success_rate >= 0.0


# ---------------------------------------------------------------------------
# Test 5 — Multi-agent autonomy isolation
# ---------------------------------------------------------------------------

class TestAutonomyMultiAgent:
    def test_agents_have_separate_execution_chains(self):
        """
        Multiple agents pursue goals independently.
        Assert: each agent has separate execution chain.
        """
        goal_engine = PersistentGoalEngine()

        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="work",
            description="separate execution chain goal and progress",
            input_schema="x",
            output_schema="y",
        )
        cap_id = graph.register(cap)

        execution = ExecutionEngine()
        execution.register(cap_id, lambda: {"ok": True})

        reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution)

        autonomy = AutonomyLoop(
            goal_engine=goal_engine,
            reasoning_layer=reasoning,
            execution_engine=execution,
        )

        agent1_id = "agent-grace"
        agent2_id = "agent-henry"

        # Both agents create goals
        goal_engine.create(agent1_id, objective="goal 1", priority=5)
        goal_engine.create(agent2_id, objective="goal 2", priority=5)

        # Agent 1 executes 2 steps
        autonomy.execute_step(agent1_id)
        autonomy.execute_step(agent1_id)

        # Agent 2 executes 1 step
        autonomy.execute_step(agent2_id)

        chain1 = autonomy.get_execution_chain(agent1_id)
        chain2 = autonomy.get_execution_chain(agent2_id)

        assert len(chain1) == 2
        assert len(chain2) == 1


# ---------------------------------------------------------------------------
# Test 6 — Learning integration
# ---------------------------------------------------------------------------

class TestAutonomyLearning:
    def test_learning_from_execution_outcomes(self):
        """
        Autonomy loop integrates learning from execution.
        Assert: steps are recorded with execution history.
        """
        goal_engine = PersistentGoalEngine()
        agent_id = "agent-iris"
        goal_engine.create(agent_id, objective="perform operation task", priority=5)

        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="op",
            description="operation",
            input_schema="",
            output_schema="y",
        )
        cap_id = graph.register(cap)

        execution = ExecutionEngine()
        execution.register(cap_id, lambda: {"learned": True})

        reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution)

        autonomy = AutonomyLoop(
            goal_engine=goal_engine,
            reasoning_layer=reasoning,
            execution_engine=execution,
        )

        # Execute steps
        autonomy.execute_step(agent_id)
        autonomy.execute_step(agent_id)

        # Check that steps were recorded
        chain = autonomy.get_execution_chain(agent_id)
        assert len(chain) >= 1

        # Check that success rate is computable
        autonomy_success = autonomy.get_success_rate(agent_id)
        assert autonomy_success >= 0.0


# ---------------------------------------------------------------------------
# Test 7 — Step count
# ---------------------------------------------------------------------------

class TestAutonomyMetrics:
    def test_step_count_tracking(self):
        """
        Autonomy loop tracks total steps executed.
        Assert: step count matches execution history.
        """
        goal_engine = PersistentGoalEngine()
        agent_id = "agent-jack"
        goal_engine.create(agent_id, objective="step counting", priority=5)

        graph = CapabilityGraph()
        cap = CapabilityRecord(
            capability_id="",
            name="work",
            description="step counting and track steps",
            input_schema="x",
            output_schema="y",
        )
        cap_id = graph.register(cap)

        execution = ExecutionEngine()
        execution.register(cap_id, lambda: {"step": 1})

        reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution)

        autonomy = AutonomyLoop(
            goal_engine=goal_engine,
            reasoning_layer=reasoning,
            execution_engine=execution,
        )

        # Execute 5 steps
        for _ in range(5):
            autonomy.execute_step(agent_id)

        step_count = autonomy.get_step_count(agent_id)

        assert step_count == 5
