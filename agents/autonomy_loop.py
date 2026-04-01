"""
Autonomy Loop — AgentOS v2.7.0.

Agents pursue goals indefinitely. Retrieve goal → reason about next step → execute → learn.
Full feedback cycle with goal progress tracking and synthesis integration.

Design:
  AutonomyLoop:
    pursue_goal(agent_id) → iterates: reason → execute → learn → update progress
    execute_step(agent_id) → single: get goal → reason → execute → learn
    get_active_goal(agent_id) → GoalRecord
    update_goal_progress(agent_id, goal_id, progress_update) → None
    get_execution_chain(agent_id) → list of (goal_id, reasoning_id, execution_id)

Storage:
  /agentOS/memory/autonomy/
    {agent_id}/
      execution_chain.jsonl  # goal → reasoning → execution chain
      goal_progress.json     # current goal state + progress
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
    reasoning_text: str = ""
    execution_result: Optional[dict] = None
    execution_status: Optional[str] = None
    goal_progress_delta: float = 0.0  # How much goal progress this step contributed
    step_status: str = "pending"  # pending, running, completed, failed
    timestamp: float = field(default_factory=time.time)


class AutonomyLoop:
    """Agent goal pursuit: repeatedly execute reasoning → execution → learning loop."""

    def __init__(self, goal_engine=None, reasoning_layer=None, execution_engine=None,
                 semantic_memory=None, synthesis_engine=None):
        """
        goal_engine: PersistentGoalEngine
        reasoning_layer: ReasoningLayer
        execution_engine: ExecutionEngine
        semantic_memory: SemanticMemory (for learning)
        synthesis_engine: CapabilitySynthesis (for gap detection)
        """
        self._lock = threading.RLock()
        self._goal_engine = goal_engine
        self._reasoning_layer = reasoning_layer
        self._execution_engine = execution_engine
        self._semantic_memory = semantic_memory
        self._synthesis_engine = synthesis_engine
        AUTONOMY_PATH.mkdir(parents=True, exist_ok=True)

    # ── API ────────────────────────────────────────────────────────────────

    def execute_step(self, agent_id: str, max_reasoning_attempts: int = 3) -> Tuple[Optional[str], bool]:
        """
        Execute one step of autonomy loop for an agent.
        Returns (goal_id, step_completed_successfully)

        Flow:
        1. Get active goal for agent
        2. Reason about next step to pursue that goal
        3. Execute the selected capability
        4. Learn from execution outcome
        5. Update goal progress based on execution result
        6. Record step in execution chain
        """
        if not self._goal_engine or not self._reasoning_layer or not self._execution_engine:
            return (None, False)

        step_id = f"step-{uuid.uuid4().hex[:12]}"

        with self._lock:
            # Step 1: Get active goal
            active_goals = self._goal_engine.list_active(agent_id, limit=1)
            if not active_goals:
                return (None, False)

            active_goal = active_goals[0]
            goal_id = active_goal.goal_id

            # Step 2: Reason about next step
            # Intent: "progress towards: {goal_description}"
            intent = f"progress towards: {active_goal.objective}"
            cap_id, params, confidence, reasoning_text = self._reasoning_layer.reason(
                agent_id, intent
            )

            if not cap_id:
                # No capability matched, try synthesis if available
                if self._synthesis_engine:
                    # Observe gap: "need capability to progress goal"
                    self._synthesis_engine.observe_gap(agent_id, f"capability for: {intent}")
                return (goal_id, False)

            # Get reasoning record ID for linking
            history = self._reasoning_layer.get_reasoning_history(agent_id, limit=1)
            reasoning_id = history[0].reasoning_id if history else None

            # Step 3: Execute the capability
            result, status = self._execution_engine.execute(agent_id, cap_id, params)

            if status != "success":
                # Execution failed, record and return
                self._record_step(
                    agent_id, AutonomyStep(
                        step_id=step_id,
                        agent_id=agent_id,
                        goal_id=goal_id,
                        reasoning_id=reasoning_id,
                        reasoning_text=reasoning_text,
                        execution_result=result,
                        execution_status=status,
                        step_status="failed",
                    )
                )
                return (goal_id, False)

            # Step 4: Learn from execution
            if reasoning_id:
                self._reasoning_layer.learn_from_execution(
                    agent_id, reasoning_id, result, status
                )

            # Step 5: Store execution result in semantic memory for future reference
            if self._semantic_memory and result:
                # Store as embedding for future context
                memory_key = f"execution_{goal_id}_{step_id}"
                result_text = json.dumps(result) if isinstance(result, dict) else str(result)
                self._semantic_memory.store(agent_id, memory_key, result_text)

            # Step 6: Update goal progress
            # Success = 0.1 progress (each step contributes incrementally)
            progress_delta = 0.1 if status == "success" else 0.0
            current_metrics = active_goal.metrics.copy() if active_goal.metrics else {}
            current_progress = current_metrics.get("progress", 0.0)
            new_progress = current_progress + progress_delta
            current_metrics["progress"] = new_progress
            self._goal_engine.update_progress(agent_id, goal_id, current_metrics)

            # Record step
            exec_history = self._execution_engine.get_execution_history(agent_id, limit=1)
            execution_id = exec_history[0].execution_id if exec_history else None

            self._record_step(
                agent_id, AutonomyStep(
                    step_id=step_id,
                    agent_id=agent_id,
                    goal_id=goal_id,
                    reasoning_id=reasoning_id,
                    execution_id=execution_id,
                    reasoning_text=reasoning_text,
                    execution_result=result,
                    execution_status=status,
                    goal_progress_delta=progress_delta,
                    step_status="completed",
                )
            )

            return (goal_id, status == "success")

    def pursue_goal(self, agent_id: str, max_steps: int = 10) -> Tuple[Optional[str], float, int]:
        """
        Pursue agent's active goal until complete or max_steps reached.
        Returns (goal_id, final_progress, steps_executed)

        This is the main autonomy loop: keeps executing steps until
        goal completes (progress >= 1.0) or we hit max_steps.
        """
        if not self._goal_engine:
            return (None, 0.0, 0)

        active_goals = self._goal_engine.list_active(agent_id, limit=1)
        if not active_goals:
            return (None, 0.0, 0)

        active_goal = active_goals[0]
        goal_id = active_goal.goal_id
        steps_executed = 0

        for step_num in range(max_steps):
            _, success = self.execute_step(agent_id)

            steps_executed += 1

            # Check if goal is complete
            current_goal = self._goal_engine.get(agent_id, goal_id)
            if current_goal:
                current_progress = current_goal.metrics.get("progress", 0.0)
                if current_progress >= 1.0:
                    # Goal completed
                    self._goal_engine.complete(agent_id, goal_id)
                    return (goal_id, 1.0, steps_executed)

            # If step failed, consider backing off (don't burn through max_steps)
            if not success:
                time.sleep(0.1)  # Brief pause before retry

        # Max steps reached, return current progress
        final_goal = self._goal_engine.get(agent_id, goal_id)
        final_progress = final_goal.metrics.get("progress", 0.0) if final_goal else 0.0

        return (goal_id, final_progress, steps_executed)

    def get_execution_chain(self, agent_id: str, limit: int = 50) -> List[AutonomyStep]:
        """Get execution chain (all steps) for an agent."""
        with self._lock:
            agent_dir = AUTONOMY_PATH / agent_id
            if not agent_dir.exists():
                return []

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

    def _record_step(self, agent_id: str, step: AutonomyStep) -> None:
        """Record autonomy step."""
        with self._lock:
            agent_dir = AUTONOMY_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            chain_file = agent_dir / "execution_chain.jsonl"
            chain_file.write_text(
                chain_file.read_text() + json.dumps(asdict(step)) + "\n"
                if chain_file.exists()
                else json.dumps(asdict(step)) + "\n"
            )

    def get_step_count(self, agent_id: str) -> int:
        """Get total steps executed by agent."""
        return len(self.get_execution_chain(agent_id, limit=10000))

    def get_success_rate(self, agent_id: str) -> float:
        """Get success rate of autonomy steps (% that completed successfully)."""
        chain = self.get_execution_chain(agent_id, limit=1000)

        if not chain:
            return 0.0

        successful = sum(1 for step in chain if step.step_status == "completed")
        return successful / len(chain) if len(chain) > 0 else 0.0
