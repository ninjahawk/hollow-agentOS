"""
Persistent Goal Engine — AgentOS v2.2.0.

Replace task-based execution with goal-based autonomy. Agents set objectives once,
pursue them indefinitely. Goals decompose, persist, adapt.

Design:
  GoalRecord:
    goal_id: str
    agent_id: str
    objective: str                  # semantic: "optimize data pipeline latency"
    priority: int                   # 1-10: urgency
    status: str                     # 'active', 'paused', 'completed', 'abandoned'
    parent_goal_id: Optional[str]   # hierarchical decomposition
    subgoals: list[str]            # child goal IDs
    metrics: dict                   # progress measurements
    created_at: float
    updated_at: float
    completed_at: Optional[float]
    embedding: np.ndarray           # (768,) embedding of objective

  PersistentGoalEngine:
    create(agent_id, objective, priority=5) → goal_id
    get(goal_id) → GoalRecord
    list_active(agent_id) → list[GoalRecord]
    decompose(goal_id, subgoals: list[str]) → None
    update_progress(goal_id, metrics: dict) → None
    complete(goal_id) → None
    abandon(goal_id) → None
    pause(goal_id) → None
    resume(goal_id) → None
    get_next_focus(agent_id, top_k=1) → list[GoalRecord]
    search_goals(agent_id, query: str, top_k=5) → list[GoalRecord]

Storage:
  /agentOS/memory/goals/
    registry.jsonl              # {id, agent_id, objective, status, metrics, ...}
    embeddings.npy              # (N, 768) goal embeddings
    index.json                  # {goal_id: row_index}
    hierarchies.jsonl           # {goal_id, parent_id, subgoals}
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False

GOAL_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "goals"
DEFAULT_VECTOR_DIM = 768


def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically (write temp → rename) to avoid partial-write corruption."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)


@dataclass
class GoalRecord:
    """A single goal: objective, status, progress tracking."""
    goal_id: str
    agent_id: str
    objective: str                   # semantic description of goal
    priority: int = 5                # 1-10 urgency scale
    status: str = "active"           # active, paused, completed, abandoned
    parent_goal_id: Optional[str] = None  # for hierarchical goals
    subgoals: List[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)  # progress tracking
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    last_worked_on: Optional[float] = None


class PersistentGoalEngine:
    """Track and pursue long-term agent objectives that persist across context windows."""

    def __init__(self, vector_dim: int = DEFAULT_VECTOR_DIM):
        self._vector_dim = vector_dim
        self._lock = threading.RLock()
        self._embedder = None
        self._init_embedder()
        GOAL_PATH.mkdir(parents=True, exist_ok=True)

    def _init_embedder(self) -> None:
        """Load embedding model."""
        if not EMBEDDING_AVAILABLE:
            return
        try:
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:
            pass

    def _embed(self, text: str) -> Optional[np.ndarray]:
        """Embed text to vector."""
        if self._embedder is None:
            return None
        try:
            return np.array(self._embedder.encode(text, convert_to_numpy=True), dtype=np.float32)
        except Exception:
            return None

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    # ── API ────────────────────────────────────────────────────────────────

    def create(self, agent_id: str, objective: str, priority: int = 5) -> str:
        """
        Create a new goal. Returns goal_id.
        Goal starts in 'active' status and is tracked indefinitely until completed or abandoned.
        Embedding is optional — if unavailable, goal is still created (semantic search degraded).
        """
        embedding = self._embed(objective)  # May return None — handled below

        goal_id = f"goal-{uuid.uuid4().hex[:12]}"
        now = time.time()
        record = GoalRecord(
            goal_id=goal_id,
            agent_id=agent_id,
            objective=objective,
            priority=priority,
            status="active",
            created_at=now,
            updated_at=now,
        )

        with self._lock:
            agent_dir = GOAL_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            # Count existing lines to know what index this goal gets
            registry_file = agent_dir / "registry.jsonl"
            existing_count = 0
            if registry_file.exists():
                try:
                    lines = [
                        l for l in registry_file.read_text().splitlines() if l.strip()
                    ]
                    existing_count = len(lines)
                except Exception:
                    existing_count = 0

            # Atomic-safe append: open in append mode
            with open(registry_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(record)) + "\n")

            # Update index (goal_id → line number)
            index_file = agent_dir / "index.json"
            index = {}
            if index_file.exists():
                try:
                    index = json.loads(index_file.read_text())
                except Exception:
                    index = {}
            index[goal_id] = existing_count
            _atomic_write(index_file, json.dumps(index, indent=2))

            # Update embeddings only if available
            if embedding is not None:
                embeddings_file = agent_dir / "embeddings.npy"
                try:
                    if embeddings_file.exists():
                        embeddings = np.load(embeddings_file)
                        embeddings = np.vstack([embeddings, embedding.reshape(1, -1)])
                    else:
                        embeddings = embedding.reshape(1, -1)
                    np.save(embeddings_file, embeddings)
                except Exception:
                    pass  # embedding index is best-effort; goal is already stored

        return goal_id

    def get(self, agent_id: str, goal_id: str) -> Optional[GoalRecord]:
        """Retrieve a goal by ID."""
        with self._lock:
            agent_dir = GOAL_PATH / agent_id
            index_file = agent_dir / "index.json"
            registry_file = agent_dir / "registry.jsonl"

            if not index_file.exists() or not registry_file.exists():
                return None

            try:
                index = json.loads(index_file.read_text(encoding="utf-8"))
            except Exception:
                return None
            if goal_id not in index:
                return None

            idx = index[goal_id]
            try:
                registry_lines = [
                    l for l in registry_file.read_text(encoding="utf-8").splitlines()
                    if l.strip()
                ]
            except Exception:
                return None

            if idx >= len(registry_lines):
                return None

            try:
                goal_dict = json.loads(registry_lines[idx])
                return GoalRecord(**goal_dict)
            except Exception:
                return None

    def list_active(self, agent_id: str, limit: int = 100) -> List[GoalRecord]:
        """List active goals for an agent, sorted by priority."""
        with self._lock:
            agent_dir = GOAL_PATH / agent_id
            if not agent_dir.exists():
                return []

            registry_file = agent_dir / "registry.jsonl"
            if not registry_file.exists():
                return []

            goals = []
            try:
                raw = registry_file.read_text(encoding="utf-8")
            except Exception:
                return []

            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    if d.get("status") == "active":
                        goals.append(GoalRecord(**d))
                except Exception:
                    continue  # skip corrupt lines, don't lose all goals

            goals.sort(key=lambda g: (-g.priority, g.created_at))
            return goals[:limit]

    def search_goals(self, agent_id: str, query: str, top_k: int = 5,
                    similarity_threshold: float = 0.5) -> List[GoalRecord]:
        """
        Find goals by semantic similarity to query.
        Useful for agents to discover related ongoing objectives.
        """
        query_embedding = self._embed(query)
        if query_embedding is None:
            return []

        with self._lock:
            agent_dir = GOAL_PATH / agent_id
            if not agent_dir.exists():
                return []

            registry_file = agent_dir / "registry.jsonl"
            embeddings_file = agent_dir / "embeddings.npy"

            if not registry_file.exists() or not embeddings_file.exists():
                return []

            embeddings = np.load(embeddings_file)
            registry_lines = registry_file.read_text().strip().split("\n")

            # Compute similarities
            similarities = []
            for i, embedding in enumerate(embeddings):
                sim = self._cosine_similarity(query_embedding, embedding)
                if sim >= similarity_threshold:
                    similarities.append((i, sim))

            similarities.sort(key=lambda x: x[1], reverse=True)

            results = []
            for idx, sim in similarities[:top_k]:
                if idx < len(registry_lines):
                    goal_dict = json.loads(registry_lines[idx])
                    goal = GoalRecord(**goal_dict)
                    results.append(goal)

            return results

    def decompose(self, agent_id: str, goal_id: str, subgoals: List[str]) -> None:
        """
        Decompose a goal into subgoals.
        Subgoals should be semantic descriptions that will be turned into GoalRecords.
        Returns list of created subgoal IDs.
        """
        parent_goal = self.get(agent_id, goal_id)
        if parent_goal is None:
            return

        subgoal_ids = []
        for subgoal_desc in subgoals:
            # Create subgoal with same agent_id
            subgoal_id = self.create(
                agent_id,
                subgoal_desc,
                priority=parent_goal.priority  # Inherit priority
            )
            subgoal_ids.append(subgoal_id)

        # Update parent goal with subgoal references
        with self._lock:
            agent_dir = GOAL_PATH / agent_id
            index_file = agent_dir / "index.json"
            registry_file = agent_dir / "registry.jsonl"

            index = json.loads(index_file.read_text(encoding="utf-8"))
            idx = index[goal_id]
            registry_lines = registry_file.read_text(encoding="utf-8").strip().split("\n")
            goal_dict = json.loads(registry_lines[idx])
            goal_dict["subgoals"] = subgoal_ids
            goal_dict["updated_at"] = time.time()
            registry_lines[idx] = json.dumps(goal_dict)
            _atomic_write(registry_file, "\n".join(registry_lines) + "\n")

    def update_progress(self, agent_id: str, goal_id: str, metrics: dict) -> None:
        """
        Update goal progress metrics.
        Metrics can be arbitrary key-value pairs for tracking progress.
        """
        try:
            with self._lock:
                agent_dir = GOAL_PATH / agent_id
                index_file = agent_dir / "index.json"
                registry_file = agent_dir / "registry.jsonl"

                index = json.loads(index_file.read_text(encoding="utf-8"))
                if goal_id not in index:
                    return

                idx = index[goal_id]
                registry_lines = registry_file.read_text(encoding="utf-8").strip().split("\n")
                goal_dict = json.loads(registry_lines[idx])
                goal_dict["metrics"] = metrics
                goal_dict["updated_at"] = time.time()
                goal_dict["last_worked_on"] = time.time()
                registry_lines[idx] = json.dumps(goal_dict)
                _atomic_write(registry_file, "\n".join(registry_lines) + "\n")
        except Exception:
            pass  # Never crash the autonomy loop over a metric write failure

    def complete(self, agent_id: str, goal_id: str) -> None:
        """Mark a goal as completed."""
        try:
            with self._lock:
                agent_dir = GOAL_PATH / agent_id
                index_file = agent_dir / "index.json"
                registry_file = agent_dir / "registry.jsonl"

                index = json.loads(index_file.read_text(encoding="utf-8"))
                if goal_id not in index:
                    return

                idx = index[goal_id]
                registry_lines = registry_file.read_text(encoding="utf-8").strip().split("\n")
                goal_dict = json.loads(registry_lines[idx])
                goal_dict["status"] = "completed"
                goal_dict["updated_at"] = time.time()
                goal_dict["completed_at"] = time.time()
                registry_lines[idx] = json.dumps(goal_dict)
                _atomic_write(registry_file, "\n".join(registry_lines) + "\n")
        except Exception:
            pass

    def abandon(self, agent_id: str, goal_id: str) -> None:
        """Mark a goal as abandoned."""
        try:
            with self._lock:
                agent_dir = GOAL_PATH / agent_id
                index_file = agent_dir / "index.json"
                registry_file = agent_dir / "registry.jsonl"

                index = json.loads(index_file.read_text(encoding="utf-8"))
                if goal_id not in index:
                    return

                idx = index[goal_id]
                registry_lines = registry_file.read_text(encoding="utf-8").strip().split("\n")
                goal_dict = json.loads(registry_lines[idx])
                goal_dict["status"] = "abandoned"
                goal_dict["updated_at"] = time.time()
                registry_lines[idx] = json.dumps(goal_dict)
                _atomic_write(registry_file, "\n".join(registry_lines) + "\n")
        except Exception:
            pass

    def pause(self, agent_id: str, goal_id: str) -> None:
        """Pause a goal (temporarily stop pursuing it)."""
        with self._lock:
            agent_dir = GOAL_PATH / agent_id
            index_file = agent_dir / "index.json"
            registry_file = agent_dir / "registry.jsonl"

            index = json.loads(index_file.read_text(encoding="utf-8"))
            if goal_id not in index:
                return

            idx = index[goal_id]
            registry_lines = registry_file.read_text(encoding="utf-8").strip().split("\n")
            goal_dict = json.loads(registry_lines[idx])
            goal_dict["status"] = "paused"
            goal_dict["updated_at"] = time.time()
            registry_lines[idx] = json.dumps(goal_dict)
            _atomic_write(registry_file, "\n".join(registry_lines) + "\n")

    def resume(self, agent_id: str, goal_id: str) -> None:
        """Resume a paused goal."""
        with self._lock:
            agent_dir = GOAL_PATH / agent_id
            index_file = agent_dir / "index.json"
            registry_file = agent_dir / "registry.jsonl"

            index = json.loads(index_file.read_text(encoding="utf-8"))
            if goal_id not in index:
                return

            idx = index[goal_id]
            registry_lines = registry_file.read_text(encoding="utf-8").strip().split("\n")
            goal_dict = json.loads(registry_lines[idx])
            goal_dict["status"] = "active"
            goal_dict["updated_at"] = time.time()
            registry_lines[idx] = json.dumps(goal_dict)
            _atomic_write(registry_file, "\n".join(registry_lines) + "\n")

    def get_next_focus(self, agent_id: str, top_k: int = 1) -> List[GoalRecord]:
        """
        Return the most important active goals for an agent to focus on next.
        Sorted by: priority (desc), last_worked_on (asc), created_at (asc).
        """
        active_goals = self.list_active(agent_id, limit=100)
        if not active_goals:
            return []

        # Sort by priority (desc), then by how long since last worked on (asc)
        now = time.time()
        active_goals.sort(key=lambda g: (
            -g.priority,
            g.last_worked_on if g.last_worked_on else now
        ))

        return active_goals[:top_k]
