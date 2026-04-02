"""
Autonomy Loop — AgentOS v3.14.0.

Agents pursue goals indefinitely. Retrieve goal → reason about next step → execute → learn.
Full feedback cycle with goal progress tracking, step context passing, and goal completion.

Changes from v2.7.0:
  - Accepts reasoning_layer (ReasoningLayer) instead of native_interface
  - execute_step() passes previous step result as context to next reasoning call
  - pursue_goal() threads context through all steps
  - Goal marked completed when progress >= 1.0

Design:
  AutonomyLoop:
    pursue_goal(agent_id, max_steps) → (goal_id, final_progress, steps_executed)
    execute_step(agent_id, context) → (goal_id, success, result)

Storage:
  /agentOS/memory/autonomy/
    {agent_id}/
      execution_chain.jsonl  # full step history
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Tuple, List

AUTONOMY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "autonomy"


@dataclass
class AutonomyStep:
    """Single step in autonomy loop: goal → reasoning → execution."""
    step_id: str
    agent_id: str
    goal_id: str
    reasoning_id: Optional[str] = None
    execution_id: Optional[str] = None
    capability_id: Optional[str] = None
    reasoning_text: str = ""
    execution_result: Optional[dict] = None
    execution_status: Optional[str] = None
    goal_progress_delta: float = 0.0
    step_status: str = "pending"  # pending, completed, failed
    timestamp: float = field(default_factory=time.time)


class AutonomyLoop:
    """Agent goal pursuit: reason → execute → learn → repeat."""

    def __init__(self, goal_engine=None, execution_engine=None,
                 reasoning_layer=None, semantic_memory=None,
                 native_interface=None):
        """
        goal_engine:      PersistentGoalEngine
        execution_engine: ExecutionEngine
        reasoning_layer:  ReasoningLayer  (Ollama-backed capability selection)
        semantic_memory:  SemanticMemory  (for storing learned outcomes)
        native_interface: ignored, kept for backwards compat
        """
        self._lock = threading.RLock()
        self._goal_engine = goal_engine
        self._execution_engine = execution_engine
        self._reasoning_layer = reasoning_layer
        self._semantic_memory = semantic_memory
        AUTONOMY_PATH.mkdir(parents=True, exist_ok=True)

    # ── API ────────────────────────────────────────────────────────────────

    def execute_step(
        self, agent_id: str, context: Optional[dict] = None
    ) -> Tuple[Optional[str], bool, Optional[dict]]:
        """
        Execute one autonomy step for an agent.
        context: result dict from the previous step (None on first step).
        Returns (goal_id, success, result_dict).
        """
        if not self._goal_engine or not self._reasoning_layer or not self._execution_engine:
            return (None, False, None)

        step_id = f"step-{uuid.uuid4().hex[:12]}"

        # Step 1: Get highest-priority active goal
        active_goals = self._goal_engine.list_active(agent_id, limit=1)
        if not active_goals:
            return (None, False, None)

        active_goal = active_goals[0]
        goal_id = active_goal.goal_id

        # Step 2: Build intent — include previous result as context
        intent = f"progress towards: {active_goal.objective}"
        if context:
            # Summarise previous result so Ollama can pick a better next step
            ctx_summary = json.dumps(context)[:300]
            intent = (
                f"progress towards: {active_goal.objective}\n"
                f"Previous step result: {ctx_summary}"
            )

        # Step 3: Reason — Ollama picks capability + generates params
        cap_id, params, confidence, reasoning_text = self._reasoning_layer.reason(
            agent_id, intent
        )

        if not cap_id:
            return (goal_id, False, None)

        # Step 4: Execute
        result, status = self._execution_engine.execute(agent_id, cap_id, params)

        # Step 5: Learn — store outcome in semantic memory
        if self._semantic_memory and result and status == "success":
            outcome = (
                f"Goal '{active_goal.objective}' step used {cap_id}: "
                f"{json.dumps(result)[:200]}"
            )
            self._semantic_memory.store(agent_id, outcome)

        # Step 6: Update goal progress
        progress_delta = 0.1 if status == "success" else 0.0
        current_metrics = dict(active_goal.metrics) if active_goal.metrics else {}
        current_progress = current_metrics.get("progress", 0.0)
        current_metrics["progress"] = current_progress + progress_delta
        current_metrics["steps_completed"] = current_metrics.get("steps_completed", 0) + 1
        self._goal_engine.update_progress(agent_id, goal_id, current_metrics)

        # Step 7: Record
        exec_history = self._execution_engine.get_execution_history(agent_id, limit=1)
        execution_id = exec_history[0].execution_id if exec_history else None

        self._record_step(agent_id, AutonomyStep(
            step_id=step_id,
            agent_id=agent_id,
            goal_id=goal_id,
            execution_id=execution_id,
            capability_id=cap_id,
            reasoning_text=reasoning_text,
            execution_result=result,
            execution_status=status,
            goal_progress_delta=progress_delta,
            step_status="completed" if status == "success" else "failed",
        ))

        return (goal_id, status == "success", result)

    def pursue_goal(
        self, agent_id: str, max_steps: int = 10
    ) -> Tuple[Optional[str], float, int]:
        """
        Pursue the agent's highest-priority active goal.
        Threads the result of each step as context into the next.
        Marks goal completed when progress >= 1.0.
        Returns (goal_id, final_progress, steps_executed).
        """
        if not self._goal_engine:
            return (None, 0.0, 0)

        active_goals = self._goal_engine.list_active(agent_id, limit=1)
        if not active_goals:
            return (None, 0.0, 0)

        goal_id = active_goals[0].goal_id
        steps_executed = 0
        context = None  # carries result from one step to the next

        for _ in range(max_steps):
            goal_id_returned, success, result = self.execute_step(agent_id, context=context)

            if goal_id_returned is None:
                break  # no active goal

            steps_executed += 1

            # Pass this step's result as context to the next step
            if success and result:
                context = result

            # Check for completion
            current_goal = self._goal_engine.get(agent_id, goal_id)
            if current_goal:
                current_progress = current_goal.metrics.get("progress", 0.0)
                if current_progress >= 1.0:
                    self._goal_engine.complete(agent_id, goal_id)
                    return (goal_id, 1.0, steps_executed)

            if not success:
                time.sleep(0.1)

        final_goal = self._goal_engine.get(agent_id, goal_id)
        final_progress = final_goal.metrics.get("progress", 0.0) if final_goal else 0.0
        return (goal_id, final_progress, steps_executed)

    # ── Introspection ──────────────────────────────────────────────────────

    def get_execution_chain(self, agent_id: str, limit: int = 50) -> List[AutonomyStep]:
        """Get execution history for an agent."""
        with self._lock:
            agent_dir = AUTONOMY_PATH / agent_id
            chain_file = agent_dir / "execution_chain.jsonl"
            if not chain_file.exists():
                return []
            try:
                steps = [
                    AutonomyStep(**json.loads(line))
                    for line in chain_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                steps.sort(key=lambda s: s.timestamp, reverse=True)
                return steps[:limit]
            except Exception:
                return []

    def get_step_count(self, agent_id: str) -> int:
        return len(self.get_execution_chain(agent_id, limit=10000))

    def get_success_rate(self, agent_id: str) -> float:
        chain = self.get_execution_chain(agent_id, limit=1000)
        if not chain:
            return 0.0
        successful = sum(1 for s in chain if s.step_status == "completed")
        return successful / len(chain)

    # ── Internal ───────────────────────────────────────────────────────────

    def _record_step(self, agent_id: str, step: AutonomyStep) -> None:
        with self._lock:
            agent_dir = AUTONOMY_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)
            chain_file = agent_dir / "execution_chain.jsonl"
            chain_file.write_text(
                chain_file.read_text() + json.dumps(asdict(step)) + "\n"
                if chain_file.exists()
                else json.dumps(asdict(step)) + "\n"
            )
