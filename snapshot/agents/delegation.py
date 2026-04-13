"""
Delegation Engine — AgentOS v3.26.0.

Agent-to-agent task delegation with lineage tracking.

Agent A identifies a subtask, delegates it to Agent B by creating a goal
in B's name. The daemon picks it up naturally. On completion, the result
flows back to A's semantic memory via a delegation result record.

DelegationEngine:
  delegate(from_id, to_id, task, context) → delegation_id
  check_result(delegation_id) → Optional[dict]
  absorb_results(agent_id) → list[dict]  # pull completed delegations back
  get_lineage(agent_id) → list[DelegationRecord]

Storage:
  /agentOS/memory/delegations/
    {from_agent_id}/outbound.jsonl    # delegations sent
    {to_agent_id}/inbound.jsonl       # delegations received
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List

MEMORY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory"))
DELEGATION_PATH = MEMORY_PATH / "delegations"


@dataclass
class DelegationRecord:
    delegation_id: str
    from_agent: str
    to_agent: str
    task: str                   # the delegated objective
    context: str                # what A knows that B needs
    goal_id: str                # goal created in B's name
    status: str                 # pending / completed / failed
    result: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0


class DelegationEngine:
    """Route subtasks between agents with full lineage tracking."""

    def __init__(self, goal_engine=None, semantic_memory=None):
        self._goal_engine = goal_engine
        self._semantic_memory = semantic_memory
        self._lock = threading.Lock()
        DELEGATION_PATH.mkdir(parents=True, exist_ok=True)

    # ── Outbound ───────────────────────────────────────────────────────────

    def delegate(self, from_agent: str, to_agent: str,
                 task: str, context: str = "") -> Optional[str]:
        """
        Delegate a task from from_agent to to_agent.
        Creates a goal in to_agent's name and records the delegation.
        Returns delegation_id.
        """
        if not self._goal_engine:
            return None

        # Build the goal objective including context from delegating agent
        objective = task
        if context:
            objective = f"{task}\n[Context from {from_agent}: {context[:200]}]"

        goal_id = self._goal_engine.create(to_agent, objective)

        delegation_id = f"del-{uuid.uuid4().hex[:12]}"
        record = DelegationRecord(
            delegation_id=delegation_id,
            from_agent=from_agent,
            to_agent=to_agent,
            task=task,
            context=context,
            goal_id=goal_id,
            status="pending",
        )

        self._write_outbound(from_agent, record)
        self._write_inbound(to_agent, record)

        # Store delegation fact in from_agent's semantic memory
        if self._semantic_memory:
            self._semantic_memory.store(
                from_agent,
                f"Delegated to {to_agent}: '{task[:80]}' (del_id={delegation_id})"
            )

        return delegation_id

    # ── Result Absorption ──────────────────────────────────────────────────

    def check_result(self, delegation_id: str) -> Optional[dict]:
        """
        Check if a delegation has completed. Returns result dict or None.
        Scans all outbound delegation files for this ID.
        """
        if not self._goal_engine:
            return None

        record = self._find_record(delegation_id)
        if record is None:
            return None

        if record.status == "completed":
            return record.result

        # Check if the delegated goal has completed
        goal = self._goal_engine.get(record.to_agent, record.goal_id)
        if goal and goal.status == "completed":
            # Harvest result from to_agent's semantic memory
            result = self._harvest_result(record.to_agent, record.goal_id)
            self._mark_completed(record, result)
            return result

        return None

    def absorb_results(self, agent_id: str) -> List[dict]:
        """
        Pull all completed delegations back into from_agent's awareness.
        Stores each result in from_agent's semantic memory.
        Returns list of results absorbed.
        """
        outbound = self._read_outbound(agent_id)
        absorbed = []

        for record in outbound:
            if record.status != "pending":
                continue

            result = self.check_result(record.delegation_id)
            if result is not None:
                absorbed.append({
                    "delegation_id": record.delegation_id,
                    "to_agent": record.to_agent,
                    "task": record.task,
                    "result": result,
                })
                # Store in from_agent's semantic memory
                if self._semantic_memory:
                    self._semantic_memory.store(
                        agent_id,
                        f"Delegation result from {record.to_agent}: "
                        f"task='{record.task[:60]}' "
                        f"result={json.dumps(result)[:200]}"
                    )

        return absorbed

    # ── Lineage ────────────────────────────────────────────────────────────

    def get_lineage(self, agent_id: str) -> List[DelegationRecord]:
        """Return all delegations sent or received by this agent."""
        outbound = self._read_outbound(agent_id)
        inbound = self._read_inbound(agent_id)
        all_records = {r.delegation_id: r for r in outbound + inbound}
        return sorted(all_records.values(), key=lambda r: r.created_at, reverse=True)

    # ── Internal ───────────────────────────────────────────────────────────

    def _harvest_result(self, agent_id: str, goal_id: str) -> dict:
        """Extract result from agent's execution history."""
        if not self._semantic_memory:
            return {"ok": True, "result": "completed"}
        try:
            records = self._semantic_memory.search(
                agent_id, f"goal", top_k=5, similarity_threshold=0.2
            )
            for r in records:
                if goal_id[:8] in r.thought or "Goal completed" in r.thought:
                    return {"ok": True, "result": r.thought[:300]}
            return {"ok": True, "result": "goal completed"}
        except Exception:
            return {"ok": True, "result": "completed"}

    def _mark_completed(self, record: DelegationRecord, result: dict) -> None:
        record.status = "completed"
        record.result = result
        record.completed_at = time.time()
        self._rewrite_record(record.from_agent, "outbound.jsonl", record)
        self._rewrite_record(record.to_agent, "inbound.jsonl", record)
        # Push result into from_agent's semantic memory immediately
        if self._semantic_memory:
            self._semantic_memory.store(
                record.from_agent,
                f"Delegation result from {record.to_agent}: "
                f"task='{record.task[:60]}' "
                f"result={json.dumps(result)[:200]}"
            )

    def _find_record(self, delegation_id: str) -> Optional[DelegationRecord]:
        """Scan all agent outbound files for this delegation_id."""
        try:
            for agent_dir in DELEGATION_PATH.iterdir():
                if not agent_dir.is_dir():
                    continue
                f = agent_dir / "outbound.jsonl"
                if not f.exists():
                    continue
                for line in f.read_text().strip().split("\n"):
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("delegation_id") == delegation_id:
                        return DelegationRecord(**d)
        except Exception:
            pass
        return None

    def _write_outbound(self, agent_id: str, record: DelegationRecord) -> None:
        with self._lock:
            d = DELEGATION_PATH / agent_id
            d.mkdir(parents=True, exist_ok=True)
            f = d / "outbound.jsonl"
            existing = f.read_text() if f.exists() else ""
            f.write_text(existing + json.dumps(asdict(record)) + "\n")

    def _write_inbound(self, agent_id: str, record: DelegationRecord) -> None:
        with self._lock:
            d = DELEGATION_PATH / agent_id
            d.mkdir(parents=True, exist_ok=True)
            f = d / "inbound.jsonl"
            existing = f.read_text() if f.exists() else ""
            f.write_text(existing + json.dumps(asdict(record)) + "\n")

    def _read_outbound(self, agent_id: str) -> List[DelegationRecord]:
        f = DELEGATION_PATH / agent_id / "outbound.jsonl"
        if not f.exists():
            return []
        try:
            return [DelegationRecord(**json.loads(l))
                    for l in f.read_text().strip().split("\n") if l.strip()]
        except Exception:
            return []

    def _read_inbound(self, agent_id: str) -> List[DelegationRecord]:
        f = DELEGATION_PATH / agent_id / "inbound.jsonl"
        if not f.exists():
            return []
        try:
            return [DelegationRecord(**json.loads(l))
                    for l in f.read_text().strip().split("\n") if l.strip()]
        except Exception:
            return []

    def _rewrite_record(self, agent_id: str, filename: str,
                        updated: DelegationRecord) -> None:
        with self._lock:
            f = DELEGATION_PATH / agent_id / filename
            if not f.exists():
                return
            lines = [l for l in f.read_text().strip().split("\n") if l.strip()]
            new_lines = []
            for line in lines:
                d = json.loads(line)
                if d.get("delegation_id") == updated.delegation_id:
                    new_lines.append(json.dumps(asdict(updated)))
                else:
                    new_lines.append(line)
            f.write_text("\n".join(new_lines) + "\n")
