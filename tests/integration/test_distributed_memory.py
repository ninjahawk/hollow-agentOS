#!/usr/bin/env python3
"""
Integration tests for Distributed Memory & Goals (v3.3.0).

Tests semantic memory replication, goal synchronization, and global capability
graph across multiple nodes.

Run:
    PYTHONPATH=. pytest tests/integration/test_distributed_memory.py -v
"""

import os
import sys
import tempfile
import shutil
import pytest
from pathlib import Path

from agents.distributed_memory import (
    DistributedMemory,
    DistributedGoalTracker,
    GlobalCapabilityGraph,
    ReplicatedMemory,
    DistributedGoal,
)

import agents.distributed_memory as mem_module


@pytest.fixture(autouse=True)
def setup_test_env():
    """Isolate each test with its own temporary directory."""
    tmpdir = tempfile.mkdtemp()
    os.environ["AGENTOS_MEMORY_PATH"] = tmpdir
    mem_module.DISTRIBUTED_MEMORY_PATH = Path(tmpdir) / "distributed_memory"

    yield tmpdir

    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


class TestDistributedMemory:
    """Test semantic memory replication across nodes."""

    def test_store_memory(self):
        """Store a memory on a node."""
        node1 = DistributedMemory(node_id="node-1")
        agent_id = "agent-001"

        mem_id = node1.store_memory(agent_id, "test memory thought")
        assert mem_id is not None
        assert mem_id.startswith("mem-")

    def test_retrieve_memory(self):
        """Retrieve a stored memory."""
        node1 = DistributedMemory(node_id="node-1")
        agent_id = "agent-001"

        mem_id = node1.store_memory(agent_id, "test memory")
        memory = node1.get_memory(agent_id, mem_id)

        assert memory is not None
        assert memory.memory_id == mem_id
        assert memory.thought == "test memory"
        assert memory.source_node == "node-1"

    def test_list_agent_memories(self):
        """List all memories for an agent."""
        node1 = DistributedMemory(node_id="node-1")
        agent_id = "agent-001"

        node1.store_memory(agent_id, "memory 1")
        node1.store_memory(agent_id, "memory 2")
        node1.store_memory(agent_id, "memory 3")

        memories = node1.list_agent_memories(agent_id)
        assert len(memories) == 3
        assert all(m.agent_id == agent_id for m in memories)

    def test_memory_replication(self):
        """Replicate memories from one node to another."""
        node1 = DistributedMemory(node_id="node-1")
        node2 = DistributedMemory(node_id="node-2")
        agent_id = "agent-001"

        # Store on node1
        mem_id_1 = node1.store_memory(agent_id, "memory on node 1")
        mem_id_2 = node1.store_memory(agent_id, "memory on node 1 v2")

        # Verify both are on node1
        memories_node1 = node1.list_agent_memories(agent_id)
        assert len(memories_node1) == 2

        # Sync to node2
        count, synced_ids = node1.sync_memories("node-1", "node-2", agent_id)
        assert count == 2
        assert mem_id_1 in synced_ids
        assert mem_id_2 in synced_ids

    def test_get_memories_from_node(self):
        """Get memories that originated from a specific node."""
        node1 = DistributedMemory(node_id="node-1")
        agent_id = "agent-001"

        # Store memories from multiple "nodes"
        mem1 = node1.store_memory(agent_id, "from node1")
        node1._node_id = "node-2"  # Fake another source
        mem2 = node1.store_memory(agent_id, "from node2")

        # Retrieve by source
        node1_mems = node1.get_memories_from_node(agent_id, "node-1")
        node2_mems = node1.get_memories_from_node(agent_id, "node-2")

        assert len(node1_mems) == 1
        assert len(node2_mems) == 1
        assert node1_mems[0].source_node == "node-1"
        assert node2_mems[0].source_node == "node-2"

    def test_conflict_resolution(self):
        """Resolve conflicting memory versions with last-write-wins."""
        node1 = DistributedMemory(node_id="node-1")

        # Create multiple versions of same memory
        versions = [
            ReplicatedMemory(
                memory_id="mem-1",
                agent_id="agent-001",
                thought="v1",
                source_node="node-1",
                timestamp=1.0,
            ),
            ReplicatedMemory(
                memory_id="mem-1",
                agent_id="agent-001",
                thought="v2",
                source_node="node-2",
                timestamp=2.0,
            ),
            ReplicatedMemory(
                memory_id="mem-1",
                agent_id="agent-001",
                thought="v3",
                source_node="node-1",
                timestamp=3.0,
            ),
        ]

        winner = node1.resolve_conflict("mem-1", versions)
        # Winner should be the v3 with latest timestamp
        assert winner.thought == "v3"
        # Timestamp gets updated to current time, so we just check it's greater than original
        assert winner.timestamp > 3.0
        # Version should increment
        assert winner.version > 1


class TestDistributedGoalTracker:
    """Test goal synchronization across nodes."""

    def test_create_goal(self):
        """Create a distributed goal."""
        tracker = DistributedGoalTracker(node_id="node-1")
        agent_id = "agent-001"

        goal_id = tracker.create_goal(
            agent_id,
            objective="achieve high performance",
            priority=8,
        )
        assert goal_id is not None
        assert goal_id.startswith("goal-")

    def test_retrieve_goal(self):
        """Retrieve a created goal."""
        tracker = DistributedGoalTracker(node_id="node-1")
        agent_id = "agent-001"

        goal_id = tracker.create_goal(agent_id, "test objective", priority=5)
        goal = tracker.get_goal(agent_id, goal_id)

        assert goal is not None
        assert goal.goal_id == goal_id
        assert goal.objective == "test objective"
        assert goal.priority == 5
        assert goal.status == "active"

    def test_list_active_goals(self):
        """List active goals for an agent."""
        tracker = DistributedGoalTracker(node_id="node-1")
        agent_id = "agent-001"

        tracker.create_goal(agent_id, "goal 1", priority=5)
        tracker.create_goal(agent_id, "goal 2", priority=8)
        tracker.create_goal(agent_id, "goal 3", priority=3)

        goals = tracker.list_active_goals(agent_id)
        assert len(goals) == 3
        # Verify sorted by priority (highest first)
        assert goals[0].priority >= goals[1].priority
        assert goals[1].priority >= goals[2].priority

    def test_update_goal_progress(self):
        """Update goal progress."""
        tracker = DistributedGoalTracker(node_id="node-1")
        agent_id = "agent-001"

        goal_id = tracker.create_goal(agent_id, "test goal")
        success = tracker.update_goal_progress(agent_id, goal_id, 0.5)

        assert success is True
        goal = tracker.get_goal(agent_id, goal_id)
        assert goal.progress == 0.5

    def test_goal_completion(self):
        """Mark a goal as complete by progress = 1.0."""
        tracker = DistributedGoalTracker(node_id="node-1")
        agent_id = "agent-001"

        goal_id = tracker.create_goal(agent_id, "test goal")
        tracker.update_goal_progress(agent_id, goal_id, 1.0)

        goal = tracker.get_goal(agent_id, goal_id)
        assert goal.progress == 1.0

    def test_goal_sync(self):
        """Synchronize goals between nodes."""
        tracker1 = DistributedGoalTracker(node_id="node-1")
        tracker2 = DistributedGoalTracker(node_id="node-2")
        agent_id = "agent-001"

        # Create goals on node1
        tracker1.create_goal(agent_id, "goal 1", priority=5)
        tracker1.create_goal(agent_id, "goal 2", priority=8)

        # Sync to node2
        count, synced_ids = tracker1.sync_goals("node-1", "node-2", agent_id)
        assert count == 2
        assert len(synced_ids) == 2


class TestGlobalCapabilityGraph:
    """Test capability graph across nodes."""

    def test_register_capability(self):
        """Register a capability on a node."""
        graph = GlobalCapabilityGraph(node_id="node-1")

        cap = graph.register_capability(
            "cap-001",
            "verify_system",
            "verify system status"
        )
        assert cap.capability_id == "cap-001"
        assert cap.name == "verify_system"
        assert cap.node_id == "node-1"
        assert cap.available is True

    def test_retrieve_capability(self):
        """Retrieve a registered capability."""
        graph = GlobalCapabilityGraph(node_id="node-1")

        graph.register_capability("cap-001", "verify_system", "verify system status")
        cap = graph.get_capability("cap-001")

        assert cap is not None
        assert cap.capability_id == "cap-001"
        assert cap.name == "verify_system"

    def test_list_available_capabilities(self):
        """List available capabilities."""
        graph = GlobalCapabilityGraph(node_id="node-1")

        graph.register_capability("cap-001", "verify_system", "verify status")
        graph.register_capability("cap-002", "optimize_db", "optimize database")
        graph.register_capability("cap-003", "cache_data", "cache frequently used data")

        caps = graph.list_available_capabilities()
        assert len(caps) == 3
        assert all(c.available for c in caps)

    def test_capability_availability_toggle(self):
        """Toggle capability availability."""
        graph = GlobalCapabilityGraph(node_id="node-1")

        graph.register_capability("cap-001", "verify_system", "verify status")
        graph.set_capability_availability("cap-001", False)

        cap = graph.get_capability("cap-001")
        assert cap.available is False

        graph.set_capability_availability("cap-001", True)
        cap = graph.get_capability("cap-001")
        assert cap.available is True

    def test_multi_node_capabilities(self):
        """Capabilities registered on different nodes."""
        graph1 = GlobalCapabilityGraph(node_id="node-1")
        graph2 = GlobalCapabilityGraph(node_id="node-2")

        # Register on different nodes
        graph1.register_capability("cap-001", "verify_system", "verify")
        graph2.register_capability("cap-002", "optimize_db", "optimize")

        # Both should be visible (3 total: cap-001, cap-002, and another from somewhere)
        all_caps = graph1.list_available_capabilities()
        cap_ids = set(c.capability_id for c in all_caps)
        assert "cap-001" in cap_ids
        assert "cap-002" in cap_ids

    def test_load_factor_update(self):
        """Update capability load factor."""
        graph = GlobalCapabilityGraph(node_id="node-1")

        graph.register_capability("cap-001", "verify_system", "verify")
        graph.update_load_factor("cap-001", 1.5)

        cap = graph.get_capability("cap-001")
        assert cap.load_factor == 1.5

    def test_find_best_node_for_capability(self):
        """Find node with lowest load for a capability."""
        graph1 = GlobalCapabilityGraph(node_id="node-1")
        graph2 = GlobalCapabilityGraph(node_id="node-2")

        # Register same capability on both nodes
        graph1.register_capability("cap-001", "verify_system", "verify")
        graph2.register_capability("cap-001", "verify_system", "verify")

        # Update loads
        graph1.update_load_factor("cap-001", 0.8)
        graph2.update_load_factor("cap-001", 0.5)

        # Should return node-2 (lower load)
        best_node = graph1.find_best_node_for_capability("cap-001")
        assert best_node == "node-2"
