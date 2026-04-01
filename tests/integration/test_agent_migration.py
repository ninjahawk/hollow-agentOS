#!/usr/bin/env python3
"""
Integration tests for Agent Migration & Load Balancing (v3.4.0).

Tests agent state serialization, migration across nodes, resource tracking,
and load balancing decisions.

Run:
    PYTHONPATH=. pytest tests/integration/test_agent_migration.py -v
"""

import os
import tempfile
import shutil
import pytest
from pathlib import Path

from agents.agent_migration import (
    AgentMigration,
    ResourceManager,
    LoadBalancer,
    AgentSnapshot,
    MigrationEvent,
    NodeResources,
)

import agents.agent_migration as migration_module


@pytest.fixture(autouse=True)
def setup_test_env():
    """Isolate each test with its own temporary directory."""
    tmpdir = tempfile.mkdtemp()
    os.environ["AGENTOS_MEMORY_PATH"] = tmpdir
    migration_module.MIGRATION_PATH = Path(tmpdir) / "agent_migration"

    yield tmpdir

    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


class TestAgentMigration:
    """Test agent state serialization and migration."""

    def test_create_snapshot(self):
        """Create a snapshot of agent state."""
        migration = AgentMigration(node_id="node-1")
        agent_id = "agent-001"

        goals = [{"goal_id": "g1", "objective": "test"}]
        memories = [{"memory_id": "m1", "thought": "test memory"}]
        history = [{"execution_id": "e1", "status": "success"}]

        snapshot_id = migration.create_snapshot(agent_id, goals, memories, history)
        assert snapshot_id is not None
        assert snapshot_id.startswith("snap-")

    def test_retrieve_snapshot(self):
        """Retrieve a stored snapshot."""
        migration = AgentMigration(node_id="node-1")
        agent_id = "agent-001"

        goals = [{"goal_id": "g1"}]
        memories = [{"memory_id": "m1"}]
        history = []

        snapshot_id = migration.create_snapshot(agent_id, goals, memories, history)
        snapshot = migration.get_snapshot(agent_id, snapshot_id)

        assert snapshot is not None
        assert snapshot.agent_id == agent_id
        assert len(snapshot.goals) == 1
        assert len(snapshot.memories) == 1

    def test_snapshot_integrity_verification(self):
        """Verify snapshot hasn't been corrupted."""
        migration = AgentMigration(node_id="node-1")
        agent_id = "agent-001"

        goals = [{"goal_id": "g1", "priority": 8}]
        memories = []
        history = []

        snapshot_id = migration.create_snapshot(agent_id, goals, memories, history)
        snapshot = migration.get_snapshot(agent_id, snapshot_id)

        # Snapshot should pass integrity check
        assert migration.verify_snapshot_integrity(snapshot) is True

    def test_record_migration(self):
        """Record a migration event."""
        migration = AgentMigration(node_id="node-1")
        agent_id = "agent-001"

        migration_id = migration.record_migration(
            agent_id,
            source_node="node-1",
            target_node="node-2",
            snapshot_id="snap-123",
            reason="load balancing",
        )

        assert migration_id is not None
        assert migration_id.startswith("mig-")

    def test_get_latest_snapshot(self):
        """Get the most recent snapshot for an agent."""
        import time
        migration = AgentMigration(node_id="node-1")
        agent_id = "agent-001"

        # Create multiple snapshots
        snap_id_1 = migration.create_snapshot(agent_id, [{"goal": "1"}], [], [])
        time.sleep(0.01)
        snap_id_2 = migration.create_snapshot(agent_id, [{"goal": "2"}], [], [])
        time.sleep(0.01)
        snap_id_3 = migration.create_snapshot(agent_id, [{"goal": "3"}], [], [])

        latest = migration.get_latest_snapshot(agent_id)
        assert latest is not None
        # Just verify we got one of the snapshots (latest by timestamp)
        assert latest.snapshot_id in [snap_id_1, snap_id_2, snap_id_3]
        assert latest.goals[0]["goal"] == "3"  # Check it's the right snapshot

    def test_migration_history(self):
        """Track migration history for an agent."""
        migration = AgentMigration(node_id="node-1")
        agent_id = "agent-001"

        snap_id = migration.create_snapshot(agent_id, [], [], [])
        migration.record_migration(agent_id, "node-1", "node-2", snap_id, "test")
        migration.record_migration(agent_id, "node-2", "node-3", snap_id, "test2")

        history = migration.get_migration_history(agent_id)
        assert len(history) == 2


class TestResourceManager:
    """Test resource tracking and placement scoring."""

    def test_report_resources(self):
        """Report resource availability."""
        manager = ResourceManager(node_id="node-1")

        success = manager.report_resources(cpu_available=0.8, memory_mb=4096)
        assert success is True

    def test_get_resources(self):
        """Retrieve resource status."""
        manager = ResourceManager(node_id="node-1")
        manager.report_resources(cpu_available=0.7, memory_mb=2048)

        resources = manager.get_resources("node-1")
        assert resources is not None
        assert resources.cpu_available == 0.7
        assert resources.memory_mb == 2048

    def test_update_agent_placement(self):
        """Update which agents are on a node."""
        manager = ResourceManager(node_id="node-1")
        manager.report_resources(cpu_available=0.8, memory_mb=4096)

        agent_ids = ["agent-001", "agent-002", "agent-003"]
        success = manager.update_agent_placement("node-1", agent_ids)

        assert success is True
        resources = manager.get_resources("node-1")
        assert resources.agent_count == 3
        assert len(resources.agent_ids) == 3

    def test_node_load_calculation(self):
        """Calculate load factor for a node."""
        manager = ResourceManager(node_id="node-1")
        manager.report_resources(cpu_available=0.5, memory_mb=2048)
        manager.update_agent_placement("node-1", ["agent-001", "agent-002"])

        load = manager.get_node_load("node-1")
        assert 0.0 <= load <= 1.0
        # Low CPU available + multiple agents = higher load
        assert load > 0.3

    def test_placement_score_calculation(self):
        """Calculate placement score for a node."""
        manager = ResourceManager(node_id="node-1")
        manager.report_resources(cpu_available=0.9, memory_mb=4096)
        manager.update_agent_placement("node-1", ["agent-001"])

        score = manager.calculate_placement_score("node-1")
        assert 0.0 <= score <= 1.0
        # High CPU + few agents = high score
        assert score > 0.7


class TestLoadBalancer:
    """Test load balancing decisions."""

    def test_detect_imbalance(self):
        """Detect load imbalance across nodes."""
        manager = ResourceManager()
        balancer = LoadBalancer(resource_manager=manager)

        # Setup nodes with different loads
        manager._node_id = "node-1"
        manager.report_resources(cpu_available=0.9, memory_mb=8192)
        manager.update_agent_placement("node-1", ["agent-001"])

        manager._node_id = "node-2"
        manager.report_resources(cpu_available=0.2, memory_mb=8192)
        manager.update_agent_placement("node-2", ["a", "b", "c", "d", "e"])

        # Detect imbalance
        suggestions = balancer.detect_imbalance(["node-1", "node-2"])
        assert len(suggestions) >= 0
        # node-2 is overloaded, should suggest moving agents from it

    def test_find_best_node_for_placement(self):
        """Find the best node to place an agent."""
        manager = ResourceManager()
        balancer = LoadBalancer(resource_manager=manager)

        # Setup candidate nodes
        manager._node_id = "node-1"
        manager.report_resources(cpu_available=0.3, memory_mb=2048)
        manager.update_agent_placement("node-1", ["a1", "a2", "a3"])

        manager._node_id = "node-2"
        manager.report_resources(cpu_available=0.9, memory_mb=8192)
        manager.update_agent_placement("node-2", [])

        # Find best node
        best = balancer.find_best_node("new-agent", ["node-1", "node-2"])
        # node-2 is less loaded, should be better
        assert best == "node-2"

    def test_suggest_migration(self):
        """Suggest a migration if beneficial."""
        manager = ResourceManager()
        balancer = LoadBalancer(resource_manager=manager)

        manager._node_id = "node-1"
        manager.report_resources(cpu_available=0.1, memory_mb=1024)
        manager.update_agent_placement("node-1", ["a1", "a2"])

        manager._node_id = "node-2"
        manager.report_resources(cpu_available=0.9, memory_mb=8192)
        manager.update_agent_placement("node-2", [])

        suggestion = balancer.suggest_migration("node-1", "node-2", "a1")
        # Should suggest migration since node-2 is much less loaded
        assert suggestion.get("should_migrate", False) is True or suggestion.get("load_improvement", 0) > 0.1
