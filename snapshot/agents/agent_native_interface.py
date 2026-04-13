"""
Agent-Native Interface — AgentOS v2.5.0.

Pure embedding-space interface for agent-to-OS communication.
No JSON, REST, or symbolic translation. Agents navigate by meaning.

Design:
  RequestVector: np.ndarray (768,)      # embedded intent
  ResponseVector: np.ndarray (768,)     # embedded result
  OperationRecord:
    operation_id: str
    agent_id: str
    request_embedding: np.ndarray
    request_intent: str                 # "what agent wanted"
    resolved_capability_id: str         # which capability matched
    response_embedding: np.ndarray
    response_data: dict                 # actual result
    confidence: float                   # 0.0-1.0 how well we understood
    execution_time_ms: float
    created_at: float

  AgentNativeInterface:
    request(agent_id, intent: str, context: dict) → (response: dict, confidence: float)
      # Agent submits intent as plain text + context
      # OS finds nearest capability, executes, returns result

    search_capabilities(agent_id, intent: str, top_k=5) → list[(capability_id, confidence)]
      # Agent wants to know what's possible given an intent

    explain_capability(capability_id) → dict
      # Get semantic explanation of what a capability does

    introspect_self(agent_id) → dict
      # Agent queries its own state, goals, memory

Storage:
  /agentOS/memory/native_interface/
    operations.jsonl        # execution history
    operation_embeddings.npy
    index.json
    execution_log.jsonl
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

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False

INTERFACE_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "native_interface"
DEFAULT_VECTOR_DIM = 768


@dataclass
class OperationRecord:
    """Record of an agent operation through the native interface."""
    operation_id: str
    agent_id: str
    request_intent: str                 # Plain text intent from agent
    context: dict = field(default_factory=dict)
    resolved_capability_id: Optional[str] = None
    response_data: dict = field(default_factory=dict)
    confidence: float = 0.0             # How well we understood the request
    execution_time_ms: float = 0.0
    created_at: float = field(default_factory=time.time)


class AgentNativeInterface:
    """Pure embedding-space interface: agents think, OS understands."""

    def __init__(self, vector_dim: int = DEFAULT_VECTOR_DIM, capability_graph=None):
        self._vector_dim = vector_dim
        self._lock = threading.RLock()
        self._embedder = None
        self._capability_graph = capability_graph  # Optional: for capability lookup
        self._init_embedder()
        INTERFACE_PATH.mkdir(parents=True, exist_ok=True)

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

    def request(self, agent_id: str, intent: str, context: dict = None) -> Tuple[dict, float]:
        """
        Process a pure semantic request from an agent.
        Agent submits intent (plain text) + context (optional data).
        Returns (response_data, confidence_score).
        """
        start_time = time.time()
        intent_embedding = self._embed(intent)
        if intent_embedding is None:
            return ({}, 0.0)

        operation_id = f"op-{uuid.uuid4().hex[:12]}"
        context = context or {}

        # If capability graph available, find matching capability
        resolved_capability_id = None
        response_data = {}
        confidence = 0.0

        if self._capability_graph:
            # Find capabilities matching this intent
            matching_caps = self._capability_graph.find(intent, top_k=1, similarity_threshold=0.5)
            if matching_caps:
                best_cap, sim = matching_caps[0]
                resolved_capability_id = best_cap.capability_id
                confidence = sim
                # In real implementation, execute capability here
                response_data = {
                    "resolved_capability_id": resolved_capability_id,
                    "capability": best_cap.name,
                    "resolved": True,
                    "intent_understood": intent,
                    "confidence": sim,
                }

        # Record operation
        execution_time_ms = (time.time() - start_time) * 1000
        record = OperationRecord(
            operation_id=operation_id,
            agent_id=agent_id,
            request_intent=intent,
            context=context,
            resolved_capability_id=resolved_capability_id,
            response_data=response_data,
            confidence=confidence,
            execution_time_ms=execution_time_ms,
        )

        with self._lock:
            agent_dir = INTERFACE_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            operations_file = agent_dir / "operations.jsonl"
            operations_file.write_text(
                operations_file.read_text() + json.dumps(asdict(record)) + "\n"
                if operations_file.exists()
                else json.dumps(asdict(record)) + "\n"
            )

        return (response_data, confidence)

    def search_capabilities(self, agent_id: str, intent: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """
        What can I do given this intent?
        Returns list of (capability_id, confidence) pairs.
        """
        if not self._capability_graph:
            return []

        matching_caps = self._capability_graph.find(intent, top_k=top_k, similarity_threshold=0.3)
        return [(cap.capability_id, sim) for cap, sim in matching_caps]

    def explain_capability(self, capability_id: str) -> dict:
        """
        Get semantic explanation: what does this capability do?
        """
        if not self._capability_graph:
            return {}

        cap = self._capability_graph.get(capability_id)
        if cap is None:
            return {}

        return {
            "capability_id": cap.capability_id,
            "name": cap.name,
            "description": cap.description,
            "input_schema": cap.input_schema,
            "output_schema": cap.output_schema,
            "confidence": cap.confidence,
            "tags": cap.composition_tags,
        }

    def introspect_goals(self, agent_id: str, goal_engine=None) -> List[dict]:
        """
        What are my current goals?
        Returns list of active goals.
        """
        if not goal_engine:
            return []

        goals = goal_engine.list_active(agent_id, limit=10)
        return [
            {
                "goal_id": g.goal_id,
                "objective": g.objective,
                "priority": g.priority,
                "progress": g.metrics,
            }
            for g in goals
        ]

    def introspect_memory(self, agent_id: str, semantic_memory=None) -> List[dict]:
        """
        What do I remember?
        Returns list of recent memories.
        """
        if not semantic_memory:
            return []

        memories = semantic_memory.list_agent_memories(agent_id, limit=10)
        return [
            {
                "memory_id": m.memory_id,
                "thought": m.thought,
                "timestamp": m.timestamp,
                "access_count": m.access_count,
            }
            for m in memories
        ]

    def introspect_proposals(self, agent_id: str, quorum=None) -> List[dict]:
        """
        What proposals are pending that I care about?
        Returns list of pending proposals.
        """
        if not quorum:
            return []

        # In real implementation, filter to agent's proposals
        pending = quorum.get_pending_proposals(limit=10)
        return [
            {
                "proposal_id": p.proposal_id,
                "proposer_id": p.proposer_id,
                "description": p.description,
                "status": p.status,
                "votes": len(p.votes),
            }
            for p in pending
        ]

    def get_operation_history(self, agent_id: str, limit: int = 50) -> List[dict]:
        """
        Get history of operations this agent has performed.
        """
        with self._lock:
            agent_dir = INTERFACE_PATH / agent_id
            if not agent_dir.exists():
                return []

            operations_file = agent_dir / "operations.jsonl"
            if not operations_file.exists():
                return []

            try:
                ops = [
                    json.loads(line)
                    for line in operations_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                ops.sort(key=lambda o: o["created_at"], reverse=True)
                return ops[:limit]
            except Exception:
                return []

    def get_interface_stats(self, agent_id: str) -> dict:
        """
        Statistics about this agent's interface usage.
        """
        history = self.get_operation_history(agent_id, limit=1000)

        if not history:
            return {
                "agent_id": agent_id,
                "total_operations": 0,
                "average_confidence": 0.0,
                "average_latency_ms": 0.0,
            }

        total_ops = len(history)
        avg_confidence = sum(op.get("confidence", 0.0) for op in history) / total_ops
        avg_latency = sum(op.get("execution_time_ms", 0.0) for op in history) / total_ops

        return {
            "agent_id": agent_id,
            "total_operations": total_ops,
            "average_confidence": avg_confidence,
            "average_latency_ms": avg_latency,
            "resolved_operations": sum(1 for op in history if op.get("resolved_capability_id")),
        }
