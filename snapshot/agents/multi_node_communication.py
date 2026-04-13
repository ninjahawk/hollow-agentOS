"""
Multi-Node Communication — AgentOS v3.1.0.

Scale agents across machines with embedding-space message passing.

Design:
  Semantic message passing in embedding space (no REST/JSON translation).

  NetworkMessage:
    message_id: str              # unique identifier
    from_agent_id: str           # sender
    to_agent_id: str             # recipient (or broadcast)
    message_embedding: list      # 768-dim embedding vector
    message_text: str            # human-readable content
    metadata: dict               # routing, priority, etc.
    timestamp: float

  NetworkRegistry:
    register_agent(agent_id, node_address, port) → None
    resolve_agent(agent_id) → (node_address, port)
    deregister_agent(agent_id) → None
    list_agents() → list of (agent_id, node_address, port)
    list_nodes() → list of unique nodes

  MessageBus:
    send_message(message) → delivery_id
    receive_messages(agent_id, max_messages=10) → list[NetworkMessage]
    get_message_history(agent_id) → list[NetworkMessage]

  NetworkTopology:
    add_node(node_address, port) → None
    remove_node(node_address, port) → None
    get_peers(node_address) → list of (address, port)
    is_connected(node_a, node_b) → bool

Storage:
  /agentOS/memory/network/
    registry.jsonl             # agent → location mappings
    topology.jsonl             # node connections
    messages/
      {agent_id}/
        inbox.jsonl            # received messages
        sent.jsonl             # sent messages history
        history.jsonl          # all messages
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Set

NETWORK_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "network"


@dataclass
class NetworkMessage:
    """Message passed between agents in embedding space."""
    message_id: str
    from_agent_id: str
    to_agent_id: str                    # or "*" for broadcast
    message_embedding: List[float]      # 768-dim embedding
    message_text: str                   # human-readable content
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    delivered: bool = False
    delivery_timestamp: Optional[float] = None


@dataclass
class AgentLocation:
    """Where an agent is running."""
    agent_id: str
    node_address: str
    port: int
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)


@dataclass
class NetworkNode:
    """A node in the distributed network."""
    node_address: str
    port: int
    registered_at: float = field(default_factory=time.time)
    is_active: bool = True


class NetworkRegistry:
    """Distributed agent registry: agent_id → (node_address, port)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._registry = {}  # agent_id → AgentLocation
        NETWORK_PATH.mkdir(parents=True, exist_ok=True)
        self._load_registry()

    def register_agent(self, agent_id: str, node_address: str, port: int) -> None:
        """Register agent location."""
        with self._lock:
            location = AgentLocation(
                agent_id=agent_id,
                node_address=node_address,
                port=port,
            )
            self._registry[agent_id] = location
            self._persist_registry()

    def resolve_agent(self, agent_id: str) -> Optional[Tuple[str, int]]:
        """Resolve agent location: (node_address, port) or None."""
        with self._lock:
            location = self._registry.get(agent_id)
            if location:
                return (location.node_address, location.port)
            return None

    def deregister_agent(self, agent_id: str) -> None:
        """Deregister agent."""
        with self._lock:
            self._registry.pop(agent_id, None)
            self._persist_registry()

    def heartbeat(self, agent_id: str) -> None:
        """Update last heartbeat for agent."""
        with self._lock:
            if agent_id in self._registry:
                self._registry[agent_id].last_heartbeat = time.time()

    def list_agents(self) -> List[Tuple[str, str, int]]:
        """List all registered agents: [(agent_id, node_address, port), ...]"""
        with self._lock:
            return [
                (loc.agent_id, loc.node_address, loc.port)
                for loc in self._registry.values()
            ]

    def list_nodes(self) -> Set[Tuple[str, int]]:
        """List all unique nodes."""
        with self._lock:
            return set((loc.node_address, loc.port) for loc in self._registry.values())

    def get_agents_on_node(self, node_address: str, port: int) -> List[str]:
        """Get all agents running on specific node."""
        with self._lock:
            return [
                agent_id
                for agent_id, loc in self._registry.items()
                if loc.node_address == node_address and loc.port == port
            ]

    def _persist_registry(self) -> None:
        """Persist registry to disk."""
        registry_file = NETWORK_PATH / "registry.jsonl"
        with self._lock:
            content = ""
            for agent_id, location in self._registry.items():
                content += json.dumps(asdict(location)) + "\n"
            if content:
                registry_file.write_text(content)

    def _load_registry(self) -> None:
        """Load registry from disk."""
        registry_file = NETWORK_PATH / "registry.jsonl"
        if registry_file.exists():
            try:
                for line in registry_file.read_text().strip().split("\n"):
                    if line.strip():
                        data = json.loads(line)
                        location = AgentLocation(**data)
                        self._registry[location.agent_id] = location
            except Exception:
                pass


class NetworkTopology:
    """Manage network topology and node connections."""

    def __init__(self):
        self._lock = threading.RLock()
        self._nodes = {}  # (address, port) → NetworkNode
        self._edges = set()  # set of ((addr1, port1), (addr2, port2))
        NETWORK_PATH.mkdir(parents=True, exist_ok=True)
        self._load_topology()

    def add_node(self, node_address: str, port: int) -> None:
        """Add node to network."""
        with self._lock:
            key = (node_address, port)
            if key not in self._nodes:
                self._nodes[key] = NetworkNode(node_address, port)
                self._persist_topology()

    def remove_node(self, node_address: str, port: int) -> None:
        """Remove node from network."""
        with self._lock:
            key = (node_address, port)
            self._nodes.pop(key, None)
            # Remove all edges involving this node
            self._edges = {
                (a, b) for a, b in self._edges
                if a != key and b != key
            }
            self._persist_topology()

    def connect_nodes(self, addr1: str, port1: int, addr2: str, port2: int) -> None:
        """Create connection between two nodes."""
        with self._lock:
            key1 = (addr1, port1)
            key2 = (addr2, port2)

            # Ensure both nodes exist
            if key1 not in self._nodes:
                self.add_node(addr1, port1)
            if key2 not in self._nodes:
                self.add_node(addr2, port2)

            # Add bidirectional edge
            self._edges.add((key1, key2))
            self._edges.add((key2, key1))
            self._persist_topology()

    def get_peers(self, node_address: str, port: int) -> List[Tuple[str, int]]:
        """Get connected peers for a node."""
        with self._lock:
            key = (node_address, port)
            peers = []
            for (a, b) in self._edges:
                if a == key:
                    peers.append(b)
            return peers

    def is_connected(self, addr1: str, port1: int, addr2: str, port2: int) -> bool:
        """Check if two nodes are connected."""
        with self._lock:
            key1 = (addr1, port1)
            key2 = (addr2, port2)
            return (key1, key2) in self._edges

    def get_all_nodes(self) -> List[Tuple[str, int]]:
        """Get all nodes in topology."""
        with self._lock:
            return list(self._nodes.keys())

    def _persist_topology(self) -> None:
        """Persist topology to disk."""
        topo_file = NETWORK_PATH / "topology.jsonl"
        with self._lock:
            content = ""
            for (addr, port), node in self._nodes.items():
                content += json.dumps(asdict(node)) + "\n"
            if content:
                topo_file.write_text(content)

    def _load_topology(self) -> None:
        """Load topology from disk."""
        topo_file = NETWORK_PATH / "topology.jsonl"
        if topo_file.exists():
            try:
                for line in topo_file.read_text().strip().split("\n"):
                    if line.strip():
                        data = json.loads(line)
                        node = NetworkNode(**data)
                        self._nodes[(node.node_address, node.port)] = node
            except Exception:
                pass


class MessageBus:
    """Send and receive messages in embedding space."""

    def __init__(self, registry: Optional[NetworkRegistry] = None):
        self._lock = threading.RLock()
        self._registry = registry or NetworkRegistry()
        self._message_queues = {}  # agent_id → list of messages
        NETWORK_PATH.mkdir(parents=True, exist_ok=True)
        self._load_messages()

    def send_message(
        self,
        from_agent_id: str,
        to_agent_id: str,
        message_text: str,
        message_embedding: Optional[List[float]] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """
        Send message from one agent to another.
        Returns delivery_id.
        """
        message_id = f"msg-{uuid.uuid4().hex[:12]}"

        # Use zero embedding if not provided (for tests)
        if message_embedding is None:
            message_embedding = [0.0] * 768

        message = NetworkMessage(
            message_id=message_id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            message_embedding=message_embedding,
            message_text=message_text,
            metadata=metadata or {},
        )

        # Queue message for recipient
        with self._lock:
            if to_agent_id not in self._message_queues:
                self._message_queues[to_agent_id] = []
            self._message_queues[to_agent_id].append(message)

            # Persist message
            self._persist_message(message)

        return message_id

    def receive_messages(
        self, agent_id: str, max_messages: int = 10
    ) -> List[NetworkMessage]:
        """Receive messages for agent (non-blocking)."""
        with self._lock:
            if agent_id not in self._message_queues:
                return []

            queue = self._message_queues[agent_id]
            messages = queue[:max_messages]

            # Mark as delivered
            for msg in messages:
                msg.delivered = True
                msg.delivery_timestamp = time.time()

            # Remove from queue
            self._message_queues[agent_id] = queue[max_messages:]

            return messages

    def get_message_history(self, agent_id: str) -> List[NetworkMessage]:
        """Get all messages for agent (from disk)."""
        with self._lock:
            agent_msg_dir = NETWORK_PATH / "messages" / agent_id
            if not agent_msg_dir.exists():
                return []

            history_file = agent_msg_dir / "history.jsonl"
            if not history_file.exists():
                return []

            try:
                messages = [
                    NetworkMessage(**json.loads(line))
                    for line in history_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                messages.sort(key=lambda m: m.timestamp)
                return messages
            except Exception:
                return []

    def get_inbox(self, agent_id: str) -> List[NetworkMessage]:
        """Get received messages (delivered)."""
        with self._lock:
            agent_msg_dir = NETWORK_PATH / "messages" / agent_id
            if not agent_msg_dir.exists():
                return []

            inbox_file = agent_msg_dir / "inbox.jsonl"
            if not inbox_file.exists():
                return []

            try:
                messages = [
                    NetworkMessage(**json.loads(line))
                    for line in inbox_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                return messages
            except Exception:
                return []

    def get_outbox(self, agent_id: str) -> List[NetworkMessage]:
        """Get sent messages."""
        with self._lock:
            agent_msg_dir = NETWORK_PATH / "messages" / agent_id
            if not agent_msg_dir.exists():
                return []

            sent_file = agent_msg_dir / "sent.jsonl"
            if not sent_file.exists():
                return []

            try:
                messages = [
                    NetworkMessage(**json.loads(line))
                    for line in sent_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                return messages
            except Exception:
                return []

    def _persist_message(self, message: NetworkMessage) -> None:
        """Persist message to disk."""
        # Store in recipient's inbox
        agent_msg_dir = NETWORK_PATH / "messages" / message.to_agent_id
        agent_msg_dir.mkdir(parents=True, exist_ok=True)

        inbox_file = agent_msg_dir / "inbox.jsonl"
        history_file = agent_msg_dir / "history.jsonl"

        msg_json = json.dumps(asdict(message)) + "\n"

        inbox_file.write_text(
            inbox_file.read_text() + msg_json if inbox_file.exists() else msg_json
        )
        history_file.write_text(
            history_file.read_text() + msg_json if history_file.exists() else msg_json
        )

        # Store in sender's outbox
        sender_msg_dir = NETWORK_PATH / "messages" / message.from_agent_id
        sender_msg_dir.mkdir(parents=True, exist_ok=True)

        sent_file = sender_msg_dir / "sent.jsonl"
        sent_history_file = sender_msg_dir / "history.jsonl"

        sent_file.write_text(
            sent_file.read_text() + msg_json if sent_file.exists() else msg_json
        )
        sent_history_file.write_text(
            sent_history_file.read_text() + msg_json if sent_history_file.exists() else msg_json
        )

    def _load_messages(self) -> None:
        """Load queued messages from disk."""
        msg_dir = NETWORK_PATH / "messages"
        if not msg_dir.exists():
            return

        try:
            for agent_dir in msg_dir.iterdir():
                if not agent_dir.is_dir():
                    continue

                agent_id = agent_dir.name
                inbox_file = agent_dir / "inbox.jsonl"

                if inbox_file.exists():
                    messages = []
                    for line in inbox_file.read_text().strip().split("\n"):
                        if line.strip():
                            messages.append(NetworkMessage(**json.loads(line)))

                    if messages and agent_id not in self._message_queues:
                        self._message_queues[agent_id] = messages
        except Exception:
            pass
