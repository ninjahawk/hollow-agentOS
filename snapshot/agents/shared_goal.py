"""
Shared Goal Engine — AgentOS v3.27.0.

Multiple agents pursue one complex goal in parallel.

Coordinator decomposes a goal into N subtasks (via Ollama),
delegates each to a different agent via DelegationEngine,
then tracks progress until all subtasks complete.

SharedGoalEngine:
  create(coordinator_id, objective, agent_ids) → shared_goal_id
  check_progress(shared_goal_id) → SharedGoalStatus
  is_complete(shared_goal_id) → bool
  get_results(shared_goal_id) → list[dict]

Storage:
  /agentOS/memory/shared_goals/
    {shared_goal_id}.json      # metadata + subtask map
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List

MEMORY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory"))
SHARED_GOAL_PATH = MEMORY_PATH / "shared_goals"

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))


@dataclass
class SubtaskRecord:
    agent_id: str
    delegation_id: str
    objective: str
    status: str = "pending"   # pending / completed / failed
    result: Optional[dict] = None


@dataclass
class SharedGoalRecord:
    shared_goal_id: str
    coordinator_id: str
    objective: str
    subtasks: List[dict] = field(default_factory=list)   # list of SubtaskRecord dicts
    status: str = "active"    # active / completed / partial
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0


class SharedGoalEngine:
    """Coordinate multiple agents on a single complex goal."""

    def __init__(self, goal_engine=None, delegation_engine=None,
                 semantic_memory=None):
        self._goal_engine = goal_engine
        self._delegation = delegation_engine
        self._semantic_memory = semantic_memory
        SHARED_GOAL_PATH.mkdir(parents=True, exist_ok=True)

    # ── API ────────────────────────────────────────────────────────────────

    def create(self, coordinator_id: str, objective: str,
               agent_ids: List[str]) -> str:
        """
        Decompose objective into len(agent_ids) subtasks via Ollama,
        delegate each to the corresponding agent, return shared_goal_id.
        """
        shared_goal_id = f"sg-{uuid.uuid4().hex[:12]}"

        subtask_objectives = self._decompose(objective, len(agent_ids))
        # Pad/trim to exactly match agent count
        while len(subtask_objectives) < len(agent_ids):
            subtask_objectives.append(f"Support task for: {objective[:60]}")
        subtask_objectives = subtask_objectives[:len(agent_ids)]

        subtasks = []
        for agent_id, sub_obj in zip(agent_ids, subtask_objectives):
            del_id = None
            if self._delegation:
                del_id = self._delegation.delegate(
                    coordinator_id, agent_id, sub_obj,
                    context=f"Shared goal {shared_goal_id}: {objective[:100]}"
                )
            subtasks.append(SubtaskRecord(
                agent_id=agent_id,
                delegation_id=del_id or "",
                objective=sub_obj,
            ))

        record = SharedGoalRecord(
            shared_goal_id=shared_goal_id,
            coordinator_id=coordinator_id,
            objective=objective,
            subtasks=[asdict(s) for s in subtasks],
        )
        self._save(record)

        if self._semantic_memory:
            self._semantic_memory.store(
                coordinator_id,
                f"Shared goal {shared_goal_id} created: '{objective[:80]}' "
                f"({len(agent_ids)} agents)"
            )

        return shared_goal_id

    def check_progress(self, shared_goal_id: str) -> Optional[dict]:
        """
        Poll each subtask's delegation and return progress summary.
        Updates the shared goal record when subtasks complete.
        """
        record = self._load(shared_goal_id)
        if record is None:
            return None

        completed = 0
        failed = 0
        updated = False

        for st_dict in record.subtasks:
            st = SubtaskRecord(**st_dict)
            if st.status != "pending":
                if st.status == "completed":
                    completed += 1
                elif st.status == "failed":
                    failed += 1
                continue

            # Check via delegation engine
            if self._delegation and st.delegation_id:
                result = self._delegation.check_result(st.delegation_id)
                if result is not None:
                    st.status = "completed"
                    st.result = result
                    completed += 1
                    updated = True
                    # Update dict in place
                    st_dict.update(asdict(st))

        total = len(record.subtasks)
        if updated:
            if completed == total:
                record.status = "completed"
                record.completed_at = time.time()
                if self._semantic_memory:
                    self._semantic_memory.store(
                        record.coordinator_id,
                        f"Shared goal {shared_goal_id} COMPLETED: "
                        f"all {total} subtasks done"
                    )
            elif completed + failed == total:
                record.status = "partial"
            self._save(record)

        return {
            "shared_goal_id": shared_goal_id,
            "status": record.status,
            "total": total,
            "completed": completed,
            "failed": failed,
            "pending": total - completed - failed,
        }

    def is_complete(self, shared_goal_id: str) -> bool:
        progress = self.check_progress(shared_goal_id)
        return progress is not None and progress["status"] == "completed"

    def get_results(self, shared_goal_id: str) -> List[dict]:
        """Return all completed subtask results."""
        record = self._load(shared_goal_id)
        if record is None:
            return []
        return [
            {"agent": st["agent_id"], "objective": st["objective"],
             "result": st.get("result")}
            for st in record.subtasks
            if st.get("status") == "completed"
        ]

    # ── Internal ──────────────────────────────────────────────────────────

    def _decompose(self, objective: str, n: int) -> List[str]:
        """Ask Ollama to split objective into n parallel subtasks."""
        try:
            import httpx
            from agents.reasoning_layer import CONFIG_PATH

            cfg = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
            model = cfg.get("ollama", {}).get("default_model", "mistral-nemo:12b")

            prompt = (
                f"Split this goal into exactly {n} independent parallel subtasks.\n"
                f"Goal: {objective}\n\n"
                f"Rules: subtasks must be concrete and independently executable. "
                f"No subtask should depend on another's result.\n"
                f'Respond ONLY with JSON: {{"subtasks": ["<subtask1>", "<subtask2>", ...]}}'
            )

            resp = httpx.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": model, "prompt": prompt,
                      "stream": False, "format": "json"},
                timeout=OLLAMA_TIMEOUT,
            )
            resp.raise_for_status()
            raw = json.loads(resp.json().get("response", "{}"))
            tasks = raw.get("subtasks", [])
            if isinstance(tasks, list) and tasks:
                return [str(t) for t in tasks[:n]]
        except Exception:
            pass

        # Fallback: number the subtasks
        return [f"Subtask {i+1} of {n} for: {objective[:80]}" for i in range(n)]

    def _save(self, record: SharedGoalRecord) -> None:
        f = SHARED_GOAL_PATH / f"{record.shared_goal_id}.json"
        f.write_text(json.dumps(asdict(record), indent=2))

    def _load(self, shared_goal_id: str) -> Optional[SharedGoalRecord]:
        f = SHARED_GOAL_PATH / f"{shared_goal_id}.json"
        if not f.exists():
            return None
        try:
            d = json.loads(f.read_text())
            return SharedGoalRecord(**d)
        except Exception:
            return None
