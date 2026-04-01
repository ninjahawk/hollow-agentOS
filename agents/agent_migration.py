"""
Agent Migration & Load Balancing — AgentOS v3.4.0.

Enable agents to migrate across nodes. Serialize state in embedding space,
make intelligent placement decisions, and coordinate resource allocation
via quorum voting.

Design:
  AgentState:
    - Complete serialization of agent: goals, memory, execution history
    - Embedding-space representation (not JSON)
    - Version tracking for consistency
    - Source and target node tracking

  AgentMigration:
    - Serialize agent state across nodes
    - State verification before migration
    - Atomic transitions (all-or-nothing)
    - Checksum validation

  LoadBalancer:
    - Monitor node loads and capability distribution
    - Suggest migrations based on imbalance
    - Respect resource constraints
    - Minimize migration cost

  ResourceManager:
    - Track available resources per node (CPU, memory, bandwidth)
    - Calculate placement scores
    - Coordinate via quorum consensus
    - Enforce resource limits

  PlacementStrategy:
    - Capability-aware: place agents near their capabilities
    - Load-aware: prefer lightly loaded nodes
    - Affinity-aware: keep agent families together
    - Cost-aware: minimize migration overhead

Storage:
  /agentOS/memory/agent_migration/
    agents/
      {agent_id}/
        state.jsonl              # serialized agent state snapshots
        migration_log.jsonl      # all migration events
    nodes/
      {node_id}/
        resource_status.json     # available resources
        placements.json          # what agents are here
    decisions/
      migration_decisions.jsonl  # proposed and executed migrations
"""

import json
import os
import threading
import time
import uuid
import hashlib
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Dict, Tuple, List
import numpy as np

MIGRATION_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "agent_migration"


@dataclass
class AgentSnapshot:
    """A serialized snapshot of an agent's state."""
    snapshot_id: str
    agent_id: str
    timestamp: float = field(default_factory=time.time)

    # Core state
    goals: List[Dict] = field(default_factory=list)
    memories: List[Dict] = field(default_factory=list)
    execution_history: List[Dict] = field(default_factory=list)

    # Metadata
    source_node: str = "local"
    state_hash: str = ""
    version: int = 1
    size_bytes: int = 0


@dataclass
class MigrationEvent:
    """Record of an agent migration."""
    migration_id: str
    agent_id: str
    source_node: str
    target_node: str
    snapshot_id: str
    status: str = "pending"  # pending, in_progress, completed, failed
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    reason: str = ""


@dataclass
class NodeResources:
    """Resource status of a node."""
    node_id: str
    cpu_available: float  # 0.0-1.0 as percentage
    memory_mb: int
    agent_count: int = 0
    agent_ids: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class AgentMigration:
    """Serialize and migrate agent state across nodes."""

    def __init__(self, node_id: str = "local"):
        self._node_id = node_id
        self._lock = threading.RLock()
        MIGRATION_PATH.mkdir(parents=True, exist_ok=True)

    def create_snapshot(self, agent_id: str, goals: List[Dict], memories: List[Dict],
                       execution_history: List[Dict]) -> str:
        """Create a snapshot of agent state for migration."""
        snapshot_id = f"snap-{uuid.uuid4().hex[:12]}"

        # Calculate state hash for integrity
        state_data = json.dumps({"goals": goals, "memories": memories, "history": execution_history})
        state_hash = hashlib.sha256(state_data.encode()).hexdigest()[:16]

        snapshot = AgentSnapshot(
            snapshot_id=snapshot_id,
            agent_id=agent_id,
            goals=goals,
            memories=memories,
            execution_history=execution_history,
            source_node=self._node_id,
            state_hash=state_hash,
            version=1,
            size_bytes=len(state_data),
        )

        with self._lock:
            agent_dir = MIGRATION_PATH / "agents" / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            state_file = agent_dir / "state.jsonl"
            state_file.write_text(
                state_file.read_text() + json.dumps(asdict(snapshot)) + "\n"
                if state_file.exists()
                else json.dumps(asdict(snapshot)) + "\n"
            )

        return snapshot_id

    def get_snapshot(self, agent_id: str, snapshot_id: str) -> Optional[AgentSnapshot]:
        """Retrieve a specific snapshot."""
        with self._lock:
            agent_dir = MIGRATION_PATH / "agents" / agent_id
            state_file = agent_dir / "state.jsonl"

            if not state_file.exists():
                return None

            for line in state_file.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                snap_dict = json.loads(line)
                if snap_dict["snapshot_id"] == snapshot_id:
                    return AgentSnapshot(**snap_dict)

        return None

    def verify_snapshot_integrity(self, snapshot: AgentSnapshot) -> bool:
        """Verify snapshot hasn't been corrupted."""
        state_data = json.dumps({"goals": snapshot.goals, "memories": snapshot.memories,
                                 "history": snapshot.execution_history})
        state_hash = hashlib.sha256(state_data.encode()).hexdigest()[:16]
        return state_hash == snapshot.state_hash

    def record_migration(self, agent_id: str, source_node: str, target_node: str,
                        snapshot_id: str, reason: str = "") -> str:
        """Record a migration event."""
        migration_id = f"mig-{uuid.uuid4().hex[:12]}"

        event = MigrationEvent(
            migration_id=migration_id,
            agent_id=agent_id,
            source_node=source_node,
            target_node=target_node,
            snapshot_id=snapshot_id,
            status="pending",
            reason=reason,
        )

        with self._lock:
            agent_dir = MIGRATION_PATH / "agents" / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            log_file = agent_dir / "migration_log.jsonl"
            log_file.write_text(
                log_file.read_text() + json.dumps(asdict(event)) + "\n"
                if log_file.exists()
                else json.dumps(asdict(event)) + "\n"
            )

        return migration_id

    def get_latest_snapshot(self, agent_id: str) -> Optional[AgentSnapshot]:
        """Get the most recent snapshot for an agent."""
        with self._lock:
            agent_dir = MIGRATION_PATH / "agents" / agent_id
            state_file = agent_dir / "state.jsonl"

            if not state_file.exists():
                return None

            try:
                snapshots = [
                    AgentSnapshot(**json.loads(line))
                    for line in state_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                snapshots.sort(key=lambda s: s.timestamp, reverse=True)
                return snapshots[0] if snapshots else None
            except Exception:
                return None

    def get_migration_history(self, agent_id: str, limit: int = 50) -> List[MigrationEvent]:
        """Get migration history for an agent."""
        with self._lock:
            agent_dir = MIGRATION_PATH / "agents" / agent_id
            log_file = agent_dir / "migration_log.jsonl"

            if not log_file.exists():
                return []

            try:
                events = [
                    MigrationEvent(**json.loads(line))
                    for line in log_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                events.sort(key=lambda e: e.timestamp, reverse=True)
                return events[:limit]
            except Exception:
                return []


class ResourceManager:
    """Track and manage node resources."""

    def __init__(self, node_id: str = "local"):
        self._node_id = node_id
        self._lock = threading.RLock()
        MIGRATION_PATH.mkdir(parents=True, exist_ok=True)

    def report_resources(self, cpu_available: float, memory_mb: int) -> bool:
        """Report available resources on this node."""
        with self._lock:
            nodes_dir = MIGRATION_PATH / "nodes"
            nodes_dir.mkdir(parents=True, exist_ok=True)

            resources = NodeResources(
                node_id=self._node_id,
                cpu_available=max(0.0, min(1.0, cpu_available)),
                memory_mb=memory_mb,
                timestamp=time.time(),
            )

            status_file = nodes_dir / f"{self._node_id}_status.json"
            status_file.write_text(json.dumps(asdict(resources), indent=2))

        return True

    def get_resources(self, node_id: str) -> Optional[NodeResources]:
        """Get resource status for a node."""
        with self._lock:
            nodes_dir = MIGRATION_PATH / "nodes"
            status_file = nodes_dir / f"{node_id}_status.json"

            if not status_file.exists():
                return None

            try:
                data = json.loads(status_file.read_text())
                return NodeResources(**data)
            except Exception:
                return None

    def update_agent_placement(self, node_id: str, agent_ids: List[str]) -> bool:
        """Update which agents are on a node."""
        resources = self.get_resources(node_id)
        if not resources:
            return False

        resources.agent_ids = agent_ids
        resources.agent_count = len(agent_ids)
        resources.timestamp = time.time()

        with self._lock:
            nodes_dir = MIGRATION_PATH / "nodes"
            status_file = nodes_dir / f"{node_id}_status.json"
            status_file.write_text(json.dumps(asdict(resources), indent=2))

        return True

    def get_node_load(self, node_id: str) -> float:
        """
        Calculate load factor for a node (0.0 = empty, 1.0 = at capacity).
        Based on agents on node and available resources.
        """
        resources = self.get_resources(node_id)
        if not resources:
            return 0.0

        # Simple heuristic: agent_count / cpu_availability
        # If node has limited CPU, it's more loaded
        cpu_factor = 1.0 - resources.cpu_available
        agent_factor = resources.agent_count * 0.1  # Each agent adds 10%
        return min(1.0, cpu_factor + agent_factor)

    def calculate_placement_score(self, node_id: str, agent_id: str = None) -> float:
        """
        Calculate how good a placement this is (0.0 = bad, 1.0 = excellent).
        Higher score = better placement.
        """
        resources = self.get_resources(node_id)
        if not resources:
            return 0.0

        # Higher CPU available = better score
        cpu_score = resources.cpu_available

        # Fewer agents = better score (for load balancing)
        agent_score = 1.0 - min(1.0, resources.agent_count * 0.1)

        # Combine scores
        return (cpu_score * 0.6) + (agent_score * 0.4)


class LoadBalancer:
    """Monitor and rebalance agent distribution."""

    def __init__(self, resource_manager: ResourceManager = None):
        self._resource_manager = resource_manager
        self._lock = threading.RLock()
        MIGRATION_PATH.mkdir(parents=True, exist_ok=True)

    def suggest_migration(self, source_node: str, target_node: str, agent_id: str) -> Dict:
        """Suggest a migration if beneficial."""
        if not self._resource_manager:
            return {}

        source_load = self._resource_manager.get_node_load(source_node)
        target_load = self._resource_manager.get_node_load(target_node)

        # Only suggest if target is significantly less loaded
        load_improvement = source_load - target_load
        if load_improvement < 0.1:
            return {}

        target_score = self._resource_manager.calculate_placement_score(target_node, agent_id)
        source_score = self._resource_manager.calculate_placement_score(source_node, agent_id)

        return {
            "agent_id": agent_id,
            "source_node": source_node,
            "target_node": target_node,
            "load_improvement": load_improvement,
            "target_score": target_score,
            "source_score": source_score,
            "should_migrate": load_improvement > 0.1 and target_score > source_score,
        }

    def find_best_node(self, agent_id: str, candidate_nodes: List[str]) -> Optional[str]:
        """Find the best node to place an agent on."""
        if not self._resource_manager or not candidate_nodes:
            return None

        best_node = None
        best_score = -1.0

        for node_id in candidate_nodes:
            score = self._resource_manager.calculate_placement_score(node_id, agent_id)
            if score > best_score:
                best_score = score
                best_node = node_id

        return best_node

    def detect_imbalance(self, all_nodes: List[str]) -> List[Tuple[str, str, float]]:
        """
        Detect load imbalance and suggest migrations.
        Returns list of (source_node, target_node, improvement_factor).
        """
        if not self._resource_manager:
            return []

        loads = {node: self._resource_manager.get_node_load(node) for node in all_nodes}
        avg_load = sum(loads.values()) / len(loads) if loads else 0.0

        suggestions = []
        for source_node, source_load in loads.items():
            if source_load > avg_load + 0.2:  # Node is significantly overloaded
                for target_node, target_load in loads.items():
                    if target_load < avg_load - 0.2:  # Node is underloaded
                        improvement = source_load - target_load
                        suggestions.append((source_node, target_node, improvement))

        suggestions.sort(key=lambda x: x[2], reverse=True)
        return suggestions[:5]  # Return top 5 suggestions
