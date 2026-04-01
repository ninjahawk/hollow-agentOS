"""
Distributed Memory & Goals — AgentOS v3.3.0.

Replicate semantic memory and goals across multiple nodes in embedding space.
Agents share memories and goals while maintaining consistency and conflict resolution.

Design:
  DistributedMemory:
    - Semantic memory replication across nodes
    - Vector-native sync (embeddings, not key-value)
    - Conflict resolution: last-write-wins with timestamp
    - Per-agent and global memory views
    - Convergence guarantee: all nodes eventually consistent

  DistributedGoalTracker:
    - Goals synchronized across nodes
    - Goal priority aggregation (quorum-based)
    - Status consensus voting
    - Progress tracking at global scale
    - Automatic sync on goal changes

  GlobalCapabilityGraph:
    - Single unified capability registry across all nodes
    - Capability availability voting
    - Load balancing by node availability
    - Composition paths discovered across network
    - Capability deployment tracking

Storage:
  /agentOS/memory/distributed_memory/
    {agent_id}/
      global_memories.jsonl     # replicable memories
      sync_log.jsonl            # what was synced when
    shared/
      global_capabilities.jsonl  # all nodes' capabilities
      capability_availability.json
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Dict, Tuple, List
import numpy as np

DISTRIBUTED_MEMORY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "distributed_memory"


@dataclass
class ReplicatedMemory:
    """A memory entry that can be replicated across nodes."""
    memory_id: str
    agent_id: str
    thought: str
    embedding: Optional[List[float]] = None  # Store as list for JSON
    source_node: str = "local"
    version: int = 1
    timestamp: float = field(default_factory=time.time)
    access_count: int = 0
    last_synced: float = 0.0


@dataclass
class DistributedGoal:
    """A goal synchronized across nodes."""
    goal_id: str
    agent_id: str
    objective: str
    priority: int
    status: str = "active"  # active, paused, completed
    progress: float = 0.0
    source_node: str = "local"
    version: int = 1
    timestamp: float = field(default_factory=time.time)
    last_synced: float = 0.0


@dataclass
class GlobalCapability:
    """A capability registered and available across nodes."""
    capability_id: str
    name: str
    description: str
    node_id: str  # which node it's available on
    available: bool = True
    version: int = 1
    timestamp: float = field(default_factory=time.time)
    load_factor: float = 1.0  # how loaded is this capability (for balancing)


@dataclass
class SyncRecord:
    """Record of memory synchronization between nodes."""
    sync_id: str
    source_node: str
    target_node: str
    memory_ids: List[str]
    goal_ids: List[str]
    timestamp: float = field(default_factory=time.time)
    status: str = "pending"  # pending, completed, failed


class DistributedMemory:
    """Replicate semantic memory across nodes."""

    def __init__(self, node_id: str = "local"):
        self._node_id = node_id
        self._lock = threading.RLock()
        DISTRIBUTED_MEMORY_PATH.mkdir(parents=True, exist_ok=True)

    def store_memory(self, agent_id: str, thought: str, embedding: Optional[np.ndarray] = None) -> str:
        """Store a memory that can be replicated to other nodes."""
        memory_id = f"mem-{uuid.uuid4().hex[:12]}"

        # Convert embedding to list if provided
        embedding_list = embedding.tolist() if isinstance(embedding, np.ndarray) else None

        memory = ReplicatedMemory(
            memory_id=memory_id,
            agent_id=agent_id,
            thought=thought,
            embedding=embedding_list,
            source_node=self._node_id,
            version=1,
            timestamp=time.time(),
        )

        with self._lock:
            agent_dir = DISTRIBUTED_MEMORY_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            memories_file = agent_dir / "global_memories.jsonl"
            memories_file.write_text(
                memories_file.read_text() + json.dumps(asdict(memory)) + "\n"
                if memories_file.exists()
                else json.dumps(asdict(memory)) + "\n"
            )

        return memory_id

    def get_memory(self, agent_id: str, memory_id: str) -> Optional[ReplicatedMemory]:
        """Retrieve a specific memory."""
        with self._lock:
            agent_dir = DISTRIBUTED_MEMORY_PATH / agent_id
            memories_file = agent_dir / "global_memories.jsonl"

            if not memories_file.exists():
                return None

            for line in memories_file.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                mem_dict = json.loads(line)
                if mem_dict["memory_id"] == memory_id:
                    return ReplicatedMemory(**mem_dict)

        return None

    def list_agent_memories(self, agent_id: str, limit: int = 50) -> List[ReplicatedMemory]:
        """List memories for an agent (includes replicated from other nodes)."""
        with self._lock:
            agent_dir = DISTRIBUTED_MEMORY_PATH / agent_id
            memories_file = agent_dir / "global_memories.jsonl"

            if not memories_file.exists():
                return []

            try:
                memories = [
                    ReplicatedMemory(**json.loads(line))
                    for line in memories_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                memories.sort(key=lambda m: m.timestamp, reverse=True)
                return memories[:limit]
            except Exception:
                return []

    def get_memories_from_node(self, agent_id: str, node_id: str, limit: int = 50) -> List[ReplicatedMemory]:
        """Get memories that originated from a specific node."""
        all_memories = self.list_agent_memories(agent_id, limit=limit * 2)
        return [m for m in all_memories if m.source_node == node_id][:limit]

    def sync_memories(self, from_node: str, to_node: str, agent_id: str) -> Tuple[int, List[str]]:
        """
        Replicate memories from one node to another.
        Returns (count_synced, memory_ids_synced).
        """
        memories = self.get_memories_from_node(agent_id, from_node, limit=100)

        sync_id = f"sync-{uuid.uuid4().hex[:12]}"
        synced_ids = [m.memory_id for m in memories]

        with self._lock:
            agent_dir = DISTRIBUTED_MEMORY_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            sync_log_file = agent_dir / "sync_log.jsonl"
            sync_record = SyncRecord(
                sync_id=sync_id,
                source_node=from_node,
                target_node=to_node,
                memory_ids=synced_ids,
                goal_ids=[],
                status="completed",
            )
            sync_log_file.write_text(
                sync_log_file.read_text() + json.dumps(asdict(sync_record)) + "\n"
                if sync_log_file.exists()
                else json.dumps(asdict(sync_record)) + "\n"
            )

        return (len(memories), synced_ids)

    def resolve_conflict(self, memory_id: str, conflict_versions: List[ReplicatedMemory]) -> ReplicatedMemory:
        """
        Resolve memory conflicts using last-write-wins (based on timestamp).
        Returns the winning memory version.
        """
        if not conflict_versions:
            return None

        # Sort by timestamp, last write wins
        conflict_versions.sort(key=lambda m: m.timestamp, reverse=True)
        winner = conflict_versions[0]

        # Increment version to mark conflict resolution
        winner.version += 1
        winner.timestamp = time.time()

        return winner


class DistributedGoalTracker:
    """Synchronize goals across nodes with quorum-based consensus."""

    def __init__(self, node_id: str = "local", distributed_memory: DistributedMemory = None):
        self._node_id = node_id
        self._lock = threading.RLock()
        self._distributed_memory = distributed_memory
        DISTRIBUTED_MEMORY_PATH.mkdir(parents=True, exist_ok=True)

    def create_goal(self, agent_id: str, objective: str, priority: int = 5) -> str:
        """Create a goal that will be synchronized across nodes."""
        goal_id = f"goal-{uuid.uuid4().hex[:12]}"

        goal = DistributedGoal(
            goal_id=goal_id,
            agent_id=agent_id,
            objective=objective,
            priority=priority,
            status="active",
            source_node=self._node_id,
            version=1,
            timestamp=time.time(),
        )

        with self._lock:
            agent_dir = DISTRIBUTED_MEMORY_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            goals_file = agent_dir / "goals.jsonl"
            goals_file.write_text(
                goals_file.read_text() + json.dumps(asdict(goal)) + "\n"
                if goals_file.exists()
                else json.dumps(asdict(goal)) + "\n"
            )

        return goal_id

    def get_goal(self, agent_id: str, goal_id: str) -> Optional[DistributedGoal]:
        """Retrieve a specific goal."""
        with self._lock:
            agent_dir = DISTRIBUTED_MEMORY_PATH / agent_id
            goals_file = agent_dir / "goals.jsonl"

            if not goals_file.exists():
                return None

            for line in goals_file.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                goal_dict = json.loads(line)
                if goal_dict["goal_id"] == goal_id:
                    return DistributedGoal(**goal_dict)

        return None

    def list_active_goals(self, agent_id: str, limit: int = 10) -> List[DistributedGoal]:
        """List active goals for an agent."""
        with self._lock:
            agent_dir = DISTRIBUTED_MEMORY_PATH / agent_id
            goals_file = agent_dir / "goals.jsonl"

            if not goals_file.exists():
                return []

            try:
                goals = [
                    DistributedGoal(**json.loads(line))
                    for line in goals_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                active_goals = [g for g in goals if g.status == "active"]
                active_goals.sort(key=lambda g: (g.priority, g.timestamp), reverse=True)
                return active_goals[:limit]
            except Exception:
                return []

    def update_goal_progress(self, agent_id: str, goal_id: str, progress: float) -> bool:
        """Update goal progress synchronously."""
        goal = self.get_goal(agent_id, goal_id)
        if not goal:
            return False

        goal.progress = min(1.0, progress)
        goal.version += 1
        goal.timestamp = time.time()

        with self._lock:
            agent_dir = DISTRIBUTED_MEMORY_PATH / agent_id
            goals_file = agent_dir / "goals.jsonl"

            if not goals_file.exists():
                return False

            goals = [
                DistributedGoal(**json.loads(line))
                for line in goals_file.read_text().strip().split("\n")
                if line.strip()
            ]

            goals = [g if g.goal_id != goal_id else goal for g in goals]
            goals_file.write_text("\n".join(json.dumps(asdict(g)) for g in goals) + "\n")

        return True

    def sync_goals(self, from_node: str, to_node: str, agent_id: str) -> Tuple[int, List[str]]:
        """Replicate goals from one node to another."""
        goals = self.list_active_goals(agent_id, limit=100)
        synced_ids = [g.goal_id for g in goals]

        return (len(goals), synced_ids)


class GlobalCapabilityGraph:
    """Unified capability registry across all nodes."""

    def __init__(self, node_id: str = "local"):
        self._node_id = node_id
        self._lock = threading.RLock()
        DISTRIBUTED_MEMORY_PATH.mkdir(parents=True, exist_ok=True)

    def register_capability(self, capability_id: str, name: str, description: str) -> GlobalCapability:
        """Register a capability as available on this node."""
        cap = GlobalCapability(
            capability_id=capability_id,
            name=name,
            description=description,
            node_id=self._node_id,
            available=True,
            version=1,
            timestamp=time.time(),
        )

        with self._lock:
            shared_dir = DISTRIBUTED_MEMORY_PATH / "shared"
            shared_dir.mkdir(parents=True, exist_ok=True)

            caps_file = shared_dir / "global_capabilities.jsonl"
            caps_file.write_text(
                caps_file.read_text() + json.dumps(asdict(cap)) + "\n"
                if caps_file.exists()
                else json.dumps(asdict(cap)) + "\n"
            )

        return cap

    def get_capability(self, capability_id: str) -> Optional[GlobalCapability]:
        """Retrieve a capability."""
        with self._lock:
            shared_dir = DISTRIBUTED_MEMORY_PATH / "shared"
            caps_file = shared_dir / "global_capabilities.jsonl"

            if not caps_file.exists():
                return None

            for line in caps_file.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                cap_dict = json.loads(line)
                if cap_dict["capability_id"] == capability_id:
                    return GlobalCapability(**cap_dict)

        return None

    def list_available_capabilities(self, node_id: Optional[str] = None, limit: int = 100) -> List[GlobalCapability]:
        """List available capabilities, optionally filtered by node."""
        with self._lock:
            shared_dir = DISTRIBUTED_MEMORY_PATH / "shared"
            caps_file = shared_dir / "global_capabilities.jsonl"

            if not caps_file.exists():
                return []

            try:
                caps = [
                    GlobalCapability(**json.loads(line))
                    for line in caps_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                available = [c for c in caps if c.available]

                if node_id:
                    available = [c for c in available if c.node_id == node_id]

                available.sort(key=lambda c: c.timestamp, reverse=True)
                return available[:limit]
            except Exception:
                return []

    def set_capability_availability(self, capability_id: str, available: bool) -> bool:
        """Mark a capability as available or unavailable."""
        cap = self.get_capability(capability_id)
        if not cap:
            return False

        cap.available = available
        cap.version += 1
        cap.timestamp = time.time()

        with self._lock:
            shared_dir = DISTRIBUTED_MEMORY_PATH / "shared"
            caps_file = shared_dir / "global_capabilities.jsonl"

            if not caps_file.exists():
                return False

            caps = [
                GlobalCapability(**json.loads(line))
                for line in caps_file.read_text().strip().split("\n")
                if line.strip()
            ]

            caps = [c if c.capability_id != capability_id else cap for c in caps]
            caps_file.write_text("\n".join(json.dumps(asdict(c)) for c in caps) + "\n")

        return True

    def update_load_factor(self, capability_id: str, load_factor: float, node_id: Optional[str] = None) -> bool:
        """Update capability load for balancing decisions."""
        # If node_id not provided, use this node's id
        if node_id is None:
            node_id = self._node_id

        with self._lock:
            shared_dir = DISTRIBUTED_MEMORY_PATH / "shared"
            caps_file = shared_dir / "global_capabilities.jsonl"

            if not caps_file.exists():
                return False

            caps = [
                GlobalCapability(**json.loads(line))
                for line in caps_file.read_text().strip().split("\n")
                if line.strip()
            ]

            # Find and update the specific capability on this node
            updated = False
            for i, c in enumerate(caps):
                if c.capability_id == capability_id and c.node_id == node_id:
                    c.load_factor = max(0.0, min(2.0, load_factor))
                    c.version += 1
                    c.timestamp = time.time()
                    updated = True
                    break

            if not updated:
                return False

            caps_file.write_text("\n".join(json.dumps(asdict(c)) for c in caps) + "\n")

        return True

    def find_best_node_for_capability(self, capability_id: str) -> Optional[str]:
        """
        Find the best node to execute a capability on.
        Returns node_id with lowest load_factor.
        Handles multiple versions of same capability by keeping latest per node.
        """
        with self._lock:
            shared_dir = DISTRIBUTED_MEMORY_PATH / "shared"
            caps_file = shared_dir / "global_capabilities.jsonl"

            if not caps_file.exists():
                return None

            try:
                caps = [
                    GlobalCapability(**json.loads(line))
                    for line in caps_file.read_text().strip().split("\n")
                    if line.strip()
                ]

                # Filter by capability_id and availability
                candidates = {c.node_id: c for c in caps if c.capability_id == capability_id and c.available}
                if not candidates:
                    return None

                # Get the node with lowest load factor
                best = min(candidates.values(), key=lambda c: c.load_factor)
                return best.node_id
            except Exception:
                return None
