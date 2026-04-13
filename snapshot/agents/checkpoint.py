"""
Agent Checkpoints and Replay — AgentOS v1.3.3.

Agents fail mid-task. SIGPAUSE suspends but doesn't capture full state for
restoration on a different worker. This module provides checkpoints:

  save → serialize agent state (memory, inbox, task) → persist to disk
  restore → reload state, overwrite current heap/inbox/agent record
  diff → what changed between two checkpoints
  replay → run a task N times from the same checkpoint; measure consistency

Auto-checkpointing:
  - Before each transaction commit: checkpoint all agents in the transaction
  - On SIGPAUSE: checkpoint before suspending
  - After any task that takes > 30 seconds

Checkpoint format: /agentOS/memory/checkpoints/{agent_id}/{checkpoint_id}.json
"""

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

CHECKPOINT_DIR = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "checkpoints"

AUTO_CHECKPOINT_TASK_SECONDS = 30.0  # tasks longer than this trigger auto-checkpoint


@dataclass
class AgentCheckpoint:
    checkpoint_id: str
    agent_id: str
    label: Optional[str]
    created_at: float
    # Snapshot of the working memory heap at checkpoint time
    memory_snapshot: dict          # {key: MemoryObject dict}
    # Unread messages in inbox at checkpoint time
    inbox_snapshot: list[dict]
    # Current task info (if any) — partial output, task_id
    current_task_snapshot: Optional[dict]
    # Agent registry record fields (status, usage, metadata, locks)
    agent_state: dict
    # SHA-256 of memory_snapshot JSON — fast equality check
    context_window_hash: str


@dataclass
class ReplayResult:
    checkpoint_id: str
    task_description: str
    n_runs: int
    responses: list[str]
    consistency_score: float       # 0.0–1.0: fraction of response pairs that agree
    divergence_points: list[str]   # list of run indices where response differed from run 0
    duration_ms: float


def _hash_snapshot(snapshot: dict) -> str:
    """SHA-256 of the JSON-serialized snapshot for fast equality checks."""
    content = json.dumps(snapshot, sort_keys=True, default=str)
    return hashlib.sha256(content.encode()).hexdigest()


def _similarity(a: str, b: str) -> float:
    """
    Simple overlap-based similarity: Jaccard on word sets.
    Good enough for measuring factual consistency across replay runs.
    """
    if not a and not b:
        return 1.0
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a and not words_b:
        return 1.0
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union else 0.0


class CheckpointManager:
    """
    Save, restore, diff, and replay agent checkpoints.
    Thread-safe. One instance per server.

    Depends on:
      - HeapRegistry  (memory.heap) — reading/restoring memory state
      - AgentRegistry (agents.registry) — reading/restoring agent state
      - MessageBus    (agents.bus) — reading inbox snapshot
      - TaskScheduler (agents.scheduler) — reading current task state for snapshot
      - EventBus      (agents.events) — emitting agent.checkpointed / agent.restored
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._heap_registry = None
        self._registry = None
        self._bus = None
        self._scheduler = None
        self._events = None
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    def set_subsystems(
        self,
        heap_registry=None,
        registry=None,
        bus=None,
        scheduler=None,
        events=None,
    ) -> None:
        self._heap_registry = heap_registry
        self._registry = registry
        self._bus = bus
        self._scheduler = scheduler
        self._events = events

    # ── Core operations ──────────────────────────────────────────────────────

    def save(self, agent_id: str, label: Optional[str] = None) -> str:
        """
        Save a checkpoint for agent_id. Returns checkpoint_id.
        Captures: memory heap, unread inbox, current task, agent record fields.
        """
        checkpoint_id = str(uuid.uuid4())[:16]

        # Snapshot memory heap
        memory_snapshot: dict = {}
        if self._heap_registry:
            heap = self._heap_registry.get(agent_id)
            with heap._lock:
                for key, obj in heap._objects.items():
                    memory_snapshot[key] = asdict(obj)

        # Snapshot unread inbox
        inbox_snapshot: list[dict] = []
        if self._bus:
            try:
                msgs = self._bus.receive(agent_id, unread_only=True, limit=200)
                inbox_snapshot = msgs
            except Exception:
                pass

        # Snapshot current task
        current_task_snapshot: Optional[dict] = None
        if self._scheduler and self._registry:
            agent = self._registry.get(agent_id)
            if agent and agent.current_task:
                task = self._scheduler.get_task(agent.current_task)
                if task:
                    current_task_snapshot = {
                        "task_id":   task.task_id,
                        "status":    task.status,
                        "prompt":    task.prompt,
                        "started_at": task.started_at,
                        "result":    task.result,
                        "error":     task.error,
                    }

        # Snapshot agent record (subset — no token_hash)
        agent_state: dict = {}
        if self._registry:
            agent = self._registry.get(agent_id)
            if agent:
                agent_state = {
                    "status":       agent.status,
                    "usage":        dict(agent.usage),
                    "metadata":     dict(agent.metadata),
                    "locks":        dict(agent.locks),
                    "current_task": agent.current_task,
                }

        context_window_hash = _hash_snapshot(memory_snapshot)

        chk = AgentCheckpoint(
            checkpoint_id=checkpoint_id,
            agent_id=agent_id,
            label=label,
            created_at=time.time(),
            memory_snapshot=memory_snapshot,
            inbox_snapshot=inbox_snapshot,
            current_task_snapshot=current_task_snapshot,
            agent_state=agent_state,
            context_window_hash=context_window_hash,
        )

        # Persist
        agent_dir = CHECKPOINT_DIR / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        path = agent_dir / f"{checkpoint_id}.json"
        path.write_text(json.dumps(asdict(chk), indent=2, default=str), encoding="utf-8")

        if self._events:
            self._events.emit("agent.checkpointed", agent_id, {
                "agent_id":      agent_id,
                "checkpoint_id": checkpoint_id,
                "label":         label,
                "memory_keys":   list(memory_snapshot.keys()),
            })

        return checkpoint_id

    def restore(self, agent_id: str, checkpoint_id: str) -> bool:
        """
        Restore agent from checkpoint: overwrite heap state and agent metadata.
        Returns True on success, False if checkpoint not found.
        """
        path = CHECKPOINT_DIR / agent_id / f"{checkpoint_id}.json"
        if not path.exists():
            return False

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False

        # Restore memory heap
        if self._heap_registry:
            heap = self._heap_registry.get(agent_id)
            memory_snapshot = data.get("memory_snapshot", {})
            with heap._lock:
                heap._objects.clear()
                for key, obj_dict in memory_snapshot.items():
                    try:
                        from memory.heap import MemoryObject
                        heap._objects[key] = MemoryObject(**obj_dict)
                    except Exception:
                        pass
                heap._save()

        # Restore agent metadata / usage (not status — don't change active→suspended)
        if self._registry:
            agent_state = data.get("agent_state", {})
            with self._registry._lock:
                agent = self._registry._agents.get(agent_id)
                if agent and agent_state:
                    agent.usage = agent_state.get("usage", agent.usage)
                    agent.metadata = agent_state.get("metadata", agent.metadata)
                    agent.locks = agent_state.get("locks", agent.locks)
                    self._registry._save()

        if self._events:
            self._events.emit("agent.restored", agent_id, {
                "agent_id":      agent_id,
                "checkpoint_id": checkpoint_id,
                "memory_keys":   list(data.get("memory_snapshot", {}).keys()),
            })

        return True

    def list_checkpoints(self, agent_id: str) -> list[dict]:
        """Return list of checkpoint metadata for agent_id (newest first)."""
        agent_dir = CHECKPOINT_DIR / agent_id
        if not agent_dir.exists():
            return []

        results = []
        for path in sorted(agent_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append({
                    "checkpoint_id":       data["checkpoint_id"],
                    "agent_id":            data["agent_id"],
                    "label":               data.get("label"),
                    "created_at":          data["created_at"],
                    "context_window_hash": data.get("context_window_hash", ""),
                    "memory_key_count":    len(data.get("memory_snapshot", {})),
                    "inbox_message_count": len(data.get("inbox_snapshot", [])),
                    "has_task_snapshot":   data.get("current_task_snapshot") is not None,
                })
            except Exception:
                pass

        return results

    def get_checkpoint(self, checkpoint_id: str) -> Optional[dict]:
        """
        Load a checkpoint by ID. Searches all agent directories.
        Returns the full checkpoint dict, or None if not found.
        """
        if not CHECKPOINT_DIR.exists():
            return None
        for agent_dir in CHECKPOINT_DIR.iterdir():
            path = agent_dir / f"{checkpoint_id}.json"
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    return None
        return None

    def diff(self, checkpoint_a_id: str, checkpoint_b_id: str) -> dict:
        """
        Diff two checkpoints. Returns:
          - new_memory_keys: keys in B not in A
          - removed_memory_keys: keys in A not in B
          - changed_memory_keys: keys in both with different content
          - new_inbox_messages: messages in B inbox not in A
          - agent_state_changes: fields that differ between A and B
        """
        a = self.get_checkpoint(checkpoint_a_id)
        b = self.get_checkpoint(checkpoint_b_id)
        if not a or not b:
            missing = []
            if not a:
                missing.append(checkpoint_a_id)
            if not b:
                missing.append(checkpoint_b_id)
            return {"error": f"Checkpoint(s) not found: {missing}"}

        mem_a = a.get("memory_snapshot", {})
        mem_b = b.get("memory_snapshot", {})

        keys_a = set(mem_a.keys())
        keys_b = set(mem_b.keys())

        new_keys     = sorted(keys_b - keys_a)
        removed_keys = sorted(keys_a - keys_b)
        changed_keys = sorted(
            k for k in keys_a & keys_b
            if mem_a[k].get("content") != mem_b[k].get("content")
        )

        # Inbox diff: messages in B not present in A (by content hash)
        def _msg_ids(snapshot):
            return {
                hashlib.sha256(json.dumps(m, sort_keys=True, default=str).encode()).hexdigest()
                for m in snapshot
            }

        ids_a = _msg_ids(a.get("inbox_snapshot", []))
        ids_b = _msg_ids(b.get("inbox_snapshot", []))
        new_inbox_count = len(ids_b - ids_a)

        # Agent state diff
        state_a = a.get("agent_state", {})
        state_b = b.get("agent_state", {})
        state_changes = {}
        for k in set(state_a) | set(state_b):
            if state_a.get(k) != state_b.get(k):
                state_changes[k] = {"before": state_a.get(k), "after": state_b.get(k)}

        return {
            "checkpoint_a": checkpoint_a_id,
            "checkpoint_b": checkpoint_b_id,
            "new_memory_keys":     new_keys,
            "removed_memory_keys": removed_keys,
            "changed_memory_keys": changed_keys,
            "new_inbox_messages":  new_inbox_count,
            "agent_state_changes": state_changes,
            "context_hash_changed": a.get("context_window_hash") != b.get("context_window_hash"),
        }

    def replay(
        self,
        checkpoint_id: str,
        task_description: str,
        n_runs: int = 3,
    ) -> ReplayResult:
        """
        Restore agent to checkpoint, run task_description N times via scheduler,
        measure response consistency.
        Returns ReplayResult with consistency_score and divergence_points.
        """
        start_ms = time.time() * 1000

        chk = self.get_checkpoint(checkpoint_id)
        if not chk:
            return ReplayResult(
                checkpoint_id=checkpoint_id,
                task_description=task_description,
                n_runs=n_runs,
                responses=[],
                consistency_score=0.0,
                divergence_points=["checkpoint not found"],
                duration_ms=0.0,
            )

        agent_id = chk["agent_id"]
        responses: list[str] = []

        for i in range(n_runs):
            # Restore to checkpoint state before each run
            self.restore(agent_id, checkpoint_id)

            if not self._scheduler:
                responses.append("")
                continue

            try:
                # submit() with wait=True blocks until done, returns Task
                task = self._scheduler.submit(
                    description=task_description,
                    submitted_by=agent_id,
                    wait=True,
                )
                if task and task.status == "done" and task.result:
                    responses.append(task.result.get("response", ""))
                else:
                    responses.append("")
            except Exception as e:
                responses.append(f"[error: {e}]")

        # Compute consistency: average pairwise similarity against run 0
        divergence_points: list[str] = []
        if responses:
            baseline = responses[0]
            similarities = []
            for i, resp in enumerate(responses[1:], start=1):
                sim = _similarity(baseline, resp)
                similarities.append(sim)
                if sim < 0.8:
                    divergence_points.append(f"run_{i}")
            consistency_score = (
                sum(similarities) / len(similarities) if similarities else 1.0
            )
        else:
            consistency_score = 0.0

        duration_ms = round(time.time() * 1000 - start_ms, 1)

        if self._events:
            self._events.emit("checkpoint.replay_complete", agent_id, {
                "checkpoint_id":    checkpoint_id,
                "agent_id":         agent_id,
                "n_runs":           n_runs,
                "consistency_score": round(consistency_score, 4),
                "divergence_points": divergence_points,
            })

        return ReplayResult(
            checkpoint_id=checkpoint_id,
            task_description=task_description,
            n_runs=n_runs,
            responses=responses,
            consistency_score=round(consistency_score, 4),
            divergence_points=divergence_points,
            duration_ms=duration_ms,
        )
