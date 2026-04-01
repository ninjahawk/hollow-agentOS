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
    """Agent goal pursuit: pure embedding-space autonomy loop."""

    def __init__(self, goal_engine=None, execution_engine=None,
                 semantic_memory=None, native_interface=None):
        """
        goal_engine: PersistentGoalEngine
        execution_engine: ExecutionEngine
        semantic_memory: SemanticMemory (for learning)
        native_interface: AgentNativeInterface (embedding-space capability search)
        """
        self._lock = threading.RLock()
        self._goal_engine = goal_engine
        self._execution_engine = execution_engine
        self._semantic_memory = semantic_memory
        self._native_interface = native_interface
        AUTONOMY_PATH.mkdir(parents=True, exist_ok=True)

    # ── API ────────────────────────────────────────────────────────────────

    def execute_step(self, agent_id: str) -> Tuple[Optional[str], bool]:
        """
        Execute one step of autonomy loop for an agent.
        Pure embedding-space flow: get goal → embed intent → semantic search → execute → learn
        Returns (goal_id, step_completed_successfully)
        """
        if not self._goal_engine or not self._native_interface or not self._execution_engine:
            return (None, False)

        step_id = f"step-{uuid.uuid4().hex[:12]}"

        with self._lock:
            # Step 1: Get active goal
            active_goals = self._goal_engine.list_active(agent_id, limit=1)
            if not active_goals:
                return (None, False)

            active_goal = active_goals[0]
            goal_id = active_goal.goal_id

            # Step 2: Pure embedding-space request
            # Agent submits intent, OS finds capability by semantic meaning
            intent = f"progress towards: {active_goal.objective}"
            response_data, confidence = self._native_interface.request(agent_id, intent)

            # Extract capability from response
            cap_id = response_data.get("resolved_capability_id") if response_data else None
            if not cap_id:
                # No capability found through embedding search
                return (goal_id, False)

            # Step 3: Execute the capability
            # ExecutionEngine will handle execution with minimal parameters
            result, status = self._execution_engine.execute(agent_id, cap_id, {})

            if status != "success":
                # Execution failed, record and continue
                self._record_step(
                    agent_id, AutonomyStep(
                        step_id=step_id,
                        agent_id=agent_id,
                        goal_id=goal_id,
                        reasoning_text=f"Found capability by semantic search: {cap_id}",
                        execution_result=result,
                        execution_status=status,
                        step_status="failed",
                    )
                )
                return (goal_id, False)

            # Step 4: Learn from execution - store outcome in semantic memory
            if self._semantic_memory and result:
                outcome_text = f"Goal '{active_goal.objective}' step succeeded: {json.dumps(result) if isinstance(result, dict) else str(result)}"
                self._semantic_memory.store(agent_id, outcome_text)

            # Step 5: Update goal progress
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
                    execution_id=execution_id,
                    reasoning_text=f"Semantic search found capability {cap_id} (confidence: {confidence:.2f})",
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
