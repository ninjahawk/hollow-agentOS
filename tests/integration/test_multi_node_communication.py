"""
Integration tests for AgentOS v3.1.0: Multi-Node Communication.

Tests verify semantic message passing, agent location discovery,
embedding-space network protocol, and network topology management.

Run:
    PYTHONPATH=. pytest tests/integration/test_multi_node_communication.py -v
"""

import pytest
import shutil
import tempfile
import os
from pathlib import Path

try:
    from agents.multi_node_communication import (
        NetworkMessage,
        NetworkRegistry,
        NetworkTopology,
        MessageBus,
    )

    MULTI_NODE_AVAILABLE = True
except ImportError:
    MULTI_NODE_AVAILABLE = False


@pytest.fixture(autouse=True)
def fresh_network_storage(monkeypatch):
    """Fresh temporary directory for network storage."""
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)

    import agents.multi_node_communication as net_module

    net_module.NETWORK_PATH = Path(tmpdir) / "network"

    yield

    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


@pytest.mark.skipif(not MULTI_NODE_AVAILABLE, reason="multi-node communication not available")
class TestNetworkRegistry:
    """Test agent registration and location discovery."""

    def test_register_agent_location(self):
        """Register agent and retrieve location."""
        registry = NetworkRegistry()
        registry.register_agent("agent-1", "localhost", 9000)

        location = registry.resolve_agent("agent-1")
        assert location == ("localhost", 9000)

    def test_register_multiple_agents(self):
        """Register multiple agents on different nodes."""
        registry = NetworkRegistry()

        registry.register_agent("agent-1", "node-a.local", 9000)
        registry.register_agent("agent-2", "node-b.local", 9001)
        registry.register_agent("agent-3", "node-a.local", 9000)

        assert registry.resolve_agent("agent-1") == ("node-a.local", 9000)
        assert registry.resolve_agent("agent-2") == ("node-b.local", 9001)
        assert registry.resolve_agent("agent-3") == ("node-a.local", 9000)

    def test_resolve_nonexistent_agent(self):
        """Resolving nonexistent agent returns None."""
        registry = NetworkRegistry()
        assert registry.resolve_agent("agent-nonexistent") is None

    def test_deregister_agent(self):
        """Deregister agent removes it from registry."""
        registry = NetworkRegistry()
        registry.register_agent("agent-1", "localhost", 9000)
        assert registry.resolve_agent("agent-1") is not None

        registry.deregister_agent("agent-1")
        assert registry.resolve_agent("agent-1") is None

    def test_list_agents(self):
        """List all registered agents."""
        registry = NetworkRegistry()
        registry.register_agent("agent-1", "node-a", 9000)
        registry.register_agent("agent-2", "node-b", 9001)

        agents = registry.list_agents()
        assert len(agents) == 2
        assert ("agent-1", "node-a", 9000) in agents
        assert ("agent-2", "node-b", 9001) in agents

    def test_list_nodes(self):
        """List all unique nodes."""
        registry = NetworkRegistry()
        registry.register_agent("agent-1", "node-a", 9000)
        registry.register_agent("agent-2", "node-a", 9000)
        registry.register_agent("agent-3", "node-b", 9001)

        nodes = registry.list_nodes()
        assert len(nodes) == 2
        assert ("node-a", 9000) in nodes
        assert ("node-b", 9001) in nodes

    def test_get_agents_on_node(self):
        """Get all agents running on specific node."""
        registry = NetworkRegistry()
        registry.register_agent("agent-1", "node-a", 9000)
        registry.register_agent("agent-2", "node-a", 9000)
        registry.register_agent("agent-3", "node-b", 9001)

        node_a_agents = registry.get_agents_on_node("node-a", 9000)
        assert set(node_a_agents) == {"agent-1", "agent-2"}

        node_b_agents = registry.get_agents_on_node("node-b", 9001)
        assert node_b_agents == ["agent-3"]

    def test_heartbeat_updates_timestamp(self):
        """Heartbeat updates last_heartbeat timestamp."""
        registry = NetworkRegistry()
        registry.register_agent("agent-1", "localhost", 9000)

        location1 = registry._registry["agent-1"]
        import time
        time.sleep(0.01)

        registry.heartbeat("agent-1")
        location2 = registry._registry["agent-1"]

        assert location2.last_heartbeat >= location1.last_heartbeat


@pytest.mark.skipif(not MULTI_NODE_AVAILABLE, reason="multi-node communication not available")
class TestNetworkTopology:
    """Test network topology management."""

    def test_add_node(self):
        """Add node to topology."""
        topo = NetworkTopology()
        topo.add_node("node-a", 9000)

        nodes = topo.get_all_nodes()
        assert ("node-a", 9000) in nodes

    def test_add_multiple_nodes(self):
        """Add multiple nodes."""
        topo = NetworkTopology()
        topo.add_node("node-a", 9000)
        topo.add_node("node-b", 9001)
        topo.add_node("node-c", 9002)

        nodes = topo.get_all_nodes()
        assert len(nodes) == 3

    def test_remove_node(self):
        """Remove node from topology."""
        topo = NetworkTopology()
        topo.add_node("node-a", 9000)
        topo.remove_node("node-a", 9000)

        nodes = topo.get_all_nodes()
        assert ("node-a", 9000) not in nodes

    def test_connect_nodes(self):
        """Connect two nodes."""
        topo = NetworkTopology()
        topo.connect_nodes("node-a", 9000, "node-b", 9001)

        assert topo.is_connected("node-a", 9000, "node-b", 9001)
        assert topo.is_connected("node-b", 9001, "node-a", 9000)

    def test_get_peers(self):
        """Get connected peers for a node."""
        topo = NetworkTopology()
        topo.connect_nodes("node-a", 9000, "node-b", 9001)
        topo.connect_nodes("node-a", 9000, "node-c", 9002)

        peers_a = topo.get_peers("node-a", 9000)
        assert ("node-b", 9001) in peers_a
        assert ("node-c", 9002) in peers_a
        assert len(peers_a) == 2

    def test_is_connected_false_for_unconnected(self):
        """Unconnected nodes return False."""
        topo = NetworkTopology()
        topo.add_node("node-a", 9000)
        topo.add_node("node-b", 9001)

        assert not topo.is_connected("node-a", 9000, "node-b", 9001)

    def test_network_mesh_topology(self):
        """Create mesh topology with multiple connections."""
        topo = NetworkTopology()

        nodes = [("node-a", 9000), ("node-b", 9001), ("node-c", 9002)]

        # Connect all nodes to each other (mesh)
        for i, (addr1, port1) in enumerate(nodes):
            for addr2, port2 in nodes[i + 1 :]:
                topo.connect_nodes(addr1, port1, addr2, port2)

        # Verify all connections
        for addr1, port1 in nodes:
            peers = topo.get_peers(addr1, port1)
            assert len(peers) == 2  # Connected to 2 other nodes


@pytest.mark.skipif(not MULTI_NODE_AVAILABLE, reason="multi-node communication not available")
class TestMessageBus:
    """Test message sending and receiving."""

    def test_send_message(self):
        """Send message from agent to agent."""
        bus = MessageBus()
        msg_id = bus.send_message(
            "agent-1",
            "agent-2",
            "Hello from agent-1",
        )

        assert msg_id.startswith("msg-")

    def test_receive_messages(self):
        """Receive messages for agent."""
        bus = MessageBus()
        bus.send_message("agent-1", "agent-2", "Hello")
        bus.send_message("agent-1", "agent-2", "World")

        messages = bus.receive_messages("agent-2")
        assert len(messages) == 2
        assert messages[0].message_text == "Hello"
        assert messages[1].message_text == "World"

    def test_receive_messages_empty_when_no_messages(self):
        """Receiving with no messages returns empty list."""
        bus = MessageBus()
        messages = bus.receive_messages("agent-nonexistent")
        assert messages == []

    def test_receive_respects_max_messages(self):
        """Receive respects max_messages limit."""
        bus = MessageBus()
        for i in range(10):
            bus.send_message("agent-1", "agent-2", f"Message {i}")

        messages = bus.receive_messages("agent-2", max_messages=3)
        assert len(messages) == 3

    def test_message_marks_delivered(self):
        """Receiving marks messages as delivered."""
        bus = MessageBus()
        bus.send_message("agent-1", "agent-2", "Test")

        messages = bus.receive_messages("agent-2")
        assert messages[0].delivered
        assert messages[0].delivery_timestamp is not None

    def test_message_history(self):
        """Get message history persists across calls."""
        bus = MessageBus()
        bus.send_message("agent-1", "agent-2", "Message 1")
        bus.send_message("agent-1", "agent-2", "Message 2")

        history = bus.get_message_history("agent-2")
        assert len(history) >= 2

    def test_inbox_retrieval(self):
        """Get delivered messages (inbox)."""
        bus = MessageBus()
        bus.send_message("agent-1", "agent-2", "Message 1")
        bus.receive_messages("agent-2")

        inbox = bus.get_inbox("agent-2")
        assert len(inbox) >= 1

    def test_outbox_retrieval(self):
        """Get sent messages (outbox)."""
        bus = MessageBus()
        bus.send_message("agent-1", "agent-2", "Outgoing")

        outbox = bus.get_outbox("agent-1")
        assert len(outbox) >= 1
        assert outbox[0].from_agent_id == "agent-1"


@pytest.mark.skipif(not MULTI_NODE_AVAILABLE, reason="multi-node communication not available")
class TestEmbeddingSpaceMessaging:
    """Test embedding-space message properties."""

    def test_message_embedding_storage(self):
        """Messages preserve embedding vectors."""
        bus = MessageBus()

        embedding = [0.1] * 768  # Mock 768-dim embedding

        bus.send_message(
            "agent-1",
            "agent-2",
            "Test message",
            message_embedding=embedding,
        )

        messages = bus.receive_messages("agent-2")
        assert messages[0].message_embedding == embedding

    def test_message_metadata_preservation(self):
        """Message metadata is preserved."""
        bus = MessageBus()

        metadata = {
            "priority": "high",
            "semantic_type": "goal_update",
            "requires_ack": True,
        }

        bus.send_message(
            "agent-1",
            "agent-2",
            "Test",
            metadata=metadata,
        )

        messages = bus.receive_messages("agent-2")
        assert messages[0].metadata == metadata


@pytest.mark.skipif(not MULTI_NODE_AVAILABLE, reason="multi-node communication not available")
class TestMultiAgentMessaging:
    """Test messaging between multiple agents."""

    def test_three_agent_communication(self):
        """Three agents communicate with each other."""
        bus = MessageBus()

        # Agent 1 -> Agent 2
        bus.send_message("agent-1", "agent-2", "Hello from 1")

        # Agent 2 -> Agent 3
        bus.send_message("agent-2", "agent-3", "Hello from 2")

        # Agent 3 -> Agent 1
        bus.send_message("agent-3", "agent-1", "Hello from 3")

        # Verify each agent receives their messages
        msg_2 = bus.receive_messages("agent-2")
        msg_3 = bus.receive_messages("agent-3")
        msg_1 = bus.receive_messages("agent-1")

        assert len(msg_2) == 1
        assert len(msg_3) == 1
        assert len(msg_1) == 1

    def test_star_topology_messaging(self):
        """Central agent receives from multiple agents."""
        bus = MessageBus()

        # Multiple agents send to central agent
        for i in range(1, 5):
            bus.send_message(f"agent-{i}", "central", f"Message from {i}")

        messages = bus.receive_messages("central")
        assert len(messages) == 4


@pytest.mark.skipif(not MULTI_NODE_AVAILABLE, reason="multi-node communication not available")
class TestFullDistributedScenario:
    """Test complete distributed communication scenario."""

    def test_full_distributed_scenario(self):
        """
        Complete scenario: 3 nodes, 6 agents, distributed communication.

        Node A (localhost:9000): agent-1, agent-2
        Node B (localhost:9001): agent-3, agent-4
        Node C (localhost:9002): agent-5, agent-6
        """
        registry = NetworkRegistry()
        topology = NetworkTopology()
        bus = MessageBus(registry)

        # Register agents on nodes
        agents = {
            "agent-1": ("localhost", 9000),
            "agent-2": ("localhost", 9000),
            "agent-3": ("localhost", 9001),
            "agent-4": ("localhost", 9001),
            "agent-5": ("localhost", 9002),
            "agent-6": ("localhost", 9002),
        }

        for agent_id, (addr, port) in agents.items():
            registry.register_agent(agent_id, addr, port)

        # Create mesh topology between nodes
        topology.connect_nodes("localhost", 9000, "localhost", 9001)
        topology.connect_nodes("localhost", 9001, "localhost", 9002)
        topology.connect_nodes("localhost", 9002, "localhost", 9000)

        # Agents communicate across nodes
        bus.send_message("agent-1", "agent-4", "Cross-node message 1")
        bus.send_message("agent-3", "agent-6", "Cross-node message 2")
        bus.send_message("agent-5", "agent-2", "Cross-node message 3")

        # Verify delivery
        msg_4 = bus.receive_messages("agent-4")
        msg_6 = bus.receive_messages("agent-6")
        msg_2 = bus.receive_messages("agent-2")

        assert len(msg_4) == 1
        assert len(msg_6) == 1
        assert len(msg_2) == 1

        # Verify registry
        registry_agents = registry.list_agents()
        assert len(registry_agents) == 6

        # Verify topology
        nodes = topology.get_all_nodes()
        assert len(nodes) == 3

        peers_a = topology.get_peers("localhost", 9000)
        assert len(peers_a) == 2
