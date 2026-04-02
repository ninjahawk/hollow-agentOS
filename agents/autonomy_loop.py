"""
Autonomy Loop — AgentOS v3.17.0.

Multi-step planning: before executing, Ollama generates a complete plan.
Each step's result is substituted into the next step's params ({result} placeholder).
Failed steps trigger replanning from that point.

Design:
  pursue_goal(agent_id, max_steps) → (goal_id, final_progress, steps_executed)
  execute_step(agent_id, context, planned_cap, planned_params) → (goal_id, success, result)
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
    step_status: str = "pending"
    timestamp: float = field(default_factory=time.time)


def _substitute_result(params: dict, previous_result: Optional[dict]) -> dict:
    """
    Replace {result} placeholders in params with the previous result.
    Also fixes invalid param values (False, None, empty) by substituting context.
    """
    if not previous_result:
        return params
    result_str = json.dumps(previous_result)[:500]
    out = {}
    for k, v in params.items():
        if isinstance(v, str) and "{result}" in v:
            out[k] = v.replace("{result}", result_str)
        elif not v and v != 0:
            # param is empty/False/None — substitute previous result
            out[k] = result_str
        else:
            out[k] = v
    return out


class AutonomyLoop:
    def __init__(self, goal_engine=None, execution_engine=None,
                 reasoning_layer=None, semantic_memory=None,
                 native_interface=None):
        self._lock = threading.RLock()
        self._goal_engine = goal_engine
        self._execution_engine = execution_engine
        self._reasoning_layer = reasoning_layer
        self._semantic_memory = semantic_memory
        AUTONOMY_PATH.mkdir(parents=True, exist_ok=True)

    # ── API ────────────────────────────────────────────────────────────────

    def execute_step(
        self,
        agent_id: str,
        context: Optional[dict] = None,
        planned_cap: Optional[str] = None,
        planned_params: Optional[dict] = None,
    ) -> Tuple[Optional[str], bool, Optional[dict]]:
        """Execute one step. Uses planned cap/params if provided, else falls back to reason()."""
        if not self._goal_engine or not self._reasoning_layer or not self._execution_engine:
            return (None, False, None)

        step_id = f"step-{uuid.uuid4().hex[:12]}"

        active_goals = self._goal_engine.list_active(agent_id, limit=1)
        if not active_goals:
            return (None, False, None)

        active_goal = active_goals[0]
        goal_id = active_goal.goal_id

        # Determine capability and params
        if planned_cap:
            cap_id = planned_cap
            params = _substitute_result(planned_params or {}, context)
            reasoning_text = f"planned: {cap_id}"
        else:
            # Fallback: single-step reasoning with context
            intent = f"progress towards: {active_goal.objective}"
            if context:
                intent += f"\nPrevious result: {json.dumps(context)[:300]}"
            cap_id, params, _, reasoning_text = self._reasoning_layer.reason(agent_id, intent)
            if not cap_id:
                return (goal_id, False, None)

        # Execute
        result, status = self._execution_engine.execute(agent_id, cap_id, params)

        # Learn
        if self._semantic_memory and result and status == "success":
            self._semantic_memory.store(
                agent_id,
                f"Goal '{active_goal.objective}' step {cap_id}: {json.dumps(result)[:200]}"
            )

        # Progress
        progress_delta = 0.1 if status == "success" else 0.0
        metrics = dict(active_goal.metrics) if active_goal.metrics else {}
        metrics["progress"] = min(1.0, metrics.get("progress", 0.0) + progress_delta)
        metrics["steps_completed"] = metrics.get("steps_completed", 0) + 1
        self._goal_engine.update_progress(agent_id, goal_id, metrics)

        exec_history = self._execution_engine.get_execution_history(agent_id, limit=1)
        self._record_step(agent_id, AutonomyStep(
            step_id=step_id,
            agent_id=agent_id,
            goal_id=goal_id,
            execution_id=exec_history[0].execution_id if exec_history else None,
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
        Plan then execute. Ollama generates the full step sequence upfront.
        Each step's result feeds the next via {result} substitution.
        Replans if a step fails.
        """
        if not self._goal_engine:
            return (None, 0.0, 0)

        active_goals = self._goal_engine.list_active(agent_id, limit=1)
        if not active_goals:
            return (None, 0.0, 0)

        goal = active_goals[0]
        goal_id = goal.goal_id
        steps_executed = 0
        context = None

        # Generate plan
        plan = self._reasoning_layer.plan(agent_id, goal.objective) if self._reasoning_layer else []
        if not plan:
            # Fallback: unplanned execution
            plan = [{"capability_id": None, "params": {}, "rationale": "fallback"}]

        # Cap at max_steps
        plan = plan[:max_steps]
        plan_index = 0

        while steps_executed < max_steps:
            if plan_index < len(plan):
                step_def = plan[plan_index]
                cap_id = step_def.get("capability_id")
                params = step_def.get("params", {})
            else:
                cap_id, params = None, {}

            goal_id_out, success, result = self.execute_step(
                agent_id,
                context=context,
                planned_cap=cap_id,
                planned_params=params,
            )

            if goal_id_out is None:
                break

            steps_executed += 1
            plan_index += 1

            if success and result:
                context = result
            elif not success and plan_index < len(plan):
                # Step failed — replan remaining steps with what we know
                remaining_objective = (
                    f"{goal.objective} "
                    f"(already tried {cap_id}, continue from step {plan_index})"
                )
                new_plan = self._reasoning_layer.plan(agent_id, remaining_objective)
                if new_plan:
                    plan = plan[:plan_index] + new_plan
                plan_index = plan_index  # stay at same index, new plan takes over

            # Check completion
            current = self._goal_engine.get(agent_id, goal_id)
            if current and current.metrics.get("progress", 0.0) >= 1.0:
                self._goal_engine.complete(agent_id, goal_id)
                self._synthesize_completion(agent_id, goal.objective, steps_executed)
                return (goal_id, 1.0, steps_executed)

            if not success:
                time.sleep(0.1)

        final = self._goal_engine.get(agent_id, goal_id)
        progress = final.metrics.get("progress", 0.0) if final else 0.0
        if progress >= 0.5:
            self._synthesize_completion(agent_id, goal.objective, steps_executed)
        return (goal_id, progress, steps_executed)

    # ── Introspection ──────────────────────────────────────────────────────

    def get_execution_chain(self, agent_id: str, limit: int = 50) -> List[AutonomyStep]:
        with self._lock:
            chain_file = AUTONOMY_PATH / agent_id / "execution_chain.jsonl"
            if not chain_file.exists():
                return []
            try:
                steps = [
                    AutonomyStep(**json.loads(l))
                    for l in chain_file.read_text().strip().split("\n") if l.strip()
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
        return sum(1 for s in chain if s.step_status == "completed") / len(chain)

    # ── Internal ───────────────────────────────────────────────────────────


    def _synthesize_completion(self, agent_id: str, objective: str, steps: int) -> None:
        """
        After a goal completes, extract what was learned and store in semantic memory.
        Makes past successful plans discoverable for future similar goals.
        """
        if not self._semantic_memory or not self._execution_engine:
            return
        try:
            # Gather what capabilities were used and what they produced
            chain = self.get_execution_chain(agent_id, limit=steps + 2)
            completed = [s for s in chain if s.step_status == "completed"]
            if not completed:
                return

            cap_sequence = " → ".join(s.capability_id for s in reversed(completed) if s.capability_id)
            
            # Find any meaningful output (LLM response or saved keys)
            artifact = ""
            for s in completed:
                r = s.execution_result or {}
                if r.get("response"):
                    artifact = r["response"][:300]
                    break
                elif r.get("key"):
                    artifact = f"saved to memory key='{r['key']}'"
                    break

            summary = (
                f"Goal completed: '{objective}'. "
                f"Capability sequence: {cap_sequence}. "
                f"Result: {artifact or 'no artifact captured'}. "
                f"Steps: {steps}."
            )
            self._semantic_memory.store(agent_id, summary)
        except Exception:
            pass  # Synthesis failure must never break goal completion

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

    def validate_goal_artifact(self, agent_id: str, goal_id: str) -> dict:
        """
        After a goal completes, verify that it produced a real artifact.

        Scans the execution chain for completed steps and checks:
          - memory_set: verifies the key exists in the live AgentOS memory
          - fs_write: verifies the file was written to disk
          - ollama_chat: verifies a non-empty response was produced
          - shell_exec: verifies exit_code == 0

        Returns:
          {"validated": bool, "artifact_type": str, "artifact_value": str, "checks": list}
        """
        chain = self.get_execution_chain(agent_id)
        # Filter to steps for this goal
        steps = [s for s in chain if s.goal_id == goal_id and s.step_status == "completed"]
        if not steps:
            return {"validated": False, "artifact_type": None, "artifact_value": None,
                    "checks": ["no completed steps found"]}

        checks = []
        for step in reversed(steps):
            r = step.execution_result or {}
            cap = step.capability_id or ""

            if cap == "memory_set" and r.get("ok") and r.get("key"):
                # Verify the key exists by calling memory_get
                if self._execution_engine:
                    result, status = self._execution_engine.execute(
                        agent_id, "memory_get", {"key": r["key"]}
                    )
                    if status == "success" and result.get("value"):
                        checks.append(f"memory key '{r['key']}' verified present")
                        return {
                            "validated": True,
                            "artifact_type": "memory",
                            "artifact_value": str(result["value"])[:200],
                            "checks": checks,
                        }
                    checks.append(f"memory key '{r['key']}' not found after write")

            elif cap == "fs_write" and r.get("ok") and r.get("path"):
                import os
                if os.path.exists(r["path"]) and os.path.getsize(r["path"]) > 0:
                    checks.append(f"file '{r['path']}' exists with content")
                    return {
                        "validated": True,
                        "artifact_type": "file",
                        "artifact_value": r["path"],
                        "checks": checks,
                    }
                checks.append(f"file '{r['path']}' missing or empty")

            elif cap == "ollama_chat" and r.get("response"):
                checks.append("ollama_chat produced non-empty response")
                return {
                    "validated": True,
                    "artifact_type": "llm_response",
                    "artifact_value": r["response"][:200],
                    "checks": checks,
                }

            elif cap == "shell_exec" and r.get("exit_code") == 0 and r.get("stdout", "").strip():
                checks.append("shell_exec produced output")
                return {
                    "validated": True,
                    "artifact_type": "shell_output",
                    "artifact_value": r["stdout"][:200],
                    "checks": checks,
                }

        checks.append("no verifiable artifact found in execution chain")
        return {"validated": False, "artifact_type": None, "artifact_value": None, "checks": checks}
