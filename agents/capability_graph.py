"""
Capability Graph — AgentOS v2.1.0.

Replace flat tool list with typed capability graph. Every capability has:
  - Input type signature (in embedding space, not JSON Schema)
  - Output type signature
  - Composition rules: what can feed into what

Agents navigate geometrically. "I need something that takes a path and returns content"
→ graph finds nearest capability by type + semantic distance.

New capabilities synthesized at runtime integrate automatically. Composition discovered, not declared.

Design:
  CapabilityRecord:
    id: str
    name: str
    description: str
    input_schema: str           # semantic: "a file path" not {"type": "string"}
    output_schema: str          # semantic: "file contents as text"
    implementation: callable
    embedding: np.ndarray       # (768,) embedding of description
    composition_tags: list[str] # ["io", "filesystem", "search"]
    introduced_by: str          # agent_id or "system"
    confidence: float           # 0.0-1.0: how reliable this capability is

  CapabilityGraph(capacity=10000):
    register(capability: CapabilityRecord) → capability_id
    find(query: str, top_k=5) → list[(CapabilityRecord, similarity)]
    compose(capability_ids: list) → CompositionPlan or None
    get(capability_id: str) → CapabilityRecord
    list_all(limit=100) → list[CapabilityRecord]

Storage:
  /agentOS/memory/capabilities/
    registry.jsonl             # {id, name, description, schema_in, schema_out, tags, confidence}
    embeddings.npy             # (N, 768) capability embeddings
    index.json                 # {capability_id: row_index}
    compositions.jsonl         # learned composition patterns
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Callable
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False

CAPABILITY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "capabilities"
DEFAULT_VECTOR_DIM = 768


@dataclass
class CapabilityRecord:
    """A single capability: semantic type signatures + implementation."""
    capability_id: str
    name: str
    description: str
    input_schema: str              # semantic description, not JSON
    output_schema: str             # semantic description, not JSON
    composition_tags: list = field(default_factory=list)
    introduced_by: str = "system"
    confidence: float = 1.0        # how reliable (0.0-1.0)
    created_at: float = field(default_factory=time.time)
    usage_count: int = 0
    last_used: float = 0.0


@dataclass
class CompositionPlan:
    """A plan to chain capabilities together."""
    capabilities: list[str]        # ordered list of capability IDs
    transformation_path: list[str] # semantic type path through transformations
    confidence: float              # 0.0-1.0: likelihood this composition works
    rationale: str                 # why these capabilities chain


class CapabilityGraph:
    """Navigate tools by semantic meaning, not by name."""

    def __init__(self, capacity: int = 10000, vector_dim: int = DEFAULT_VECTOR_DIM):
        self._capacity = capacity
        self._vector_dim = vector_dim
        self._lock = threading.RLock()  # Use RLock to allow reentrant locking
        self._embedder = None
        self._init_embedder()
        CAPABILITY_PATH.mkdir(parents=True, exist_ok=True)

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

    def _schema_similarity(self, query_schema: str, capability_schema: str) -> float:
        """Semantic similarity between type schemas."""
        q_emb = self._embed(query_schema)
        c_emb = self._embed(capability_schema)
        if q_emb is None or c_emb is None:
            return 0.0
        return self._cosine_similarity(q_emb, c_emb)

    # ── API ────────────────────────────────────────────────────────────────

    def register(self, cap: CapabilityRecord) -> str:
        """Register a new capability. Returns capability_id."""
        if cap.capability_id is None or cap.capability_id == "":
            cap.capability_id = f"cap-{uuid.uuid4().hex[:12]}"

        embedding = self._embed(cap.description)
        if embedding is None:
            raise ValueError("Embedding unavailable")

        with self._lock:
            # Append to registry
            registry_file = CAPABILITY_PATH / "registry.jsonl"
            registry_file.write_text(
                registry_file.read_text() + json.dumps(asdict(cap)) + "\n"
                if registry_file.exists()
                else json.dumps(asdict(cap)) + "\n"
            )

            # Append to embeddings
            embeddings_file = CAPABILITY_PATH / "embeddings.npy"
            if embeddings_file.exists():
                embeddings = np.load(embeddings_file)
                embeddings = np.vstack([embeddings, embedding.reshape(1, -1)])
            else:
                embeddings = embedding.reshape(1, -1)
            np.save(embeddings_file, embeddings)

            # Update index
            index_file = CAPABILITY_PATH / "index.json"
            index = json.loads(index_file.read_text()) if index_file.exists() else {}
            index[cap.capability_id] = len(embeddings) - 1
            index_file.write_text(json.dumps(index, indent=2))

        return cap.capability_id

    def find(self, query: str, top_k: int = 5, similarity_threshold: float = 0.5) -> list:
        """
        Find capabilities by semantic query.
        Returns list of (CapabilityRecord, similarity_score) tuples.
        """
        query_embedding = self._embed(query)
        if query_embedding is None:
            return []

        with self._lock:
            registry_file = CAPABILITY_PATH / "registry.jsonl"
            embeddings_file = CAPABILITY_PATH / "embeddings.npy"

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
                    cap_dict = json.loads(registry_lines[idx])
                    cap = CapabilityRecord(**cap_dict)
                    results.append((cap, sim))

            return results

    def find_by_types(self, input_schema: str, output_schema: str,
                      top_k: int = 5) -> list:
        """
        Find capabilities that transform input_schema → output_schema.
        More specific than semantic search — uses type signatures.
        """
        with self._lock:
            registry_file = CAPABILITY_PATH / "registry.jsonl"
            if not registry_file.exists():
                return []

            registry_lines = registry_file.read_text().strip().split("\n")
            candidates = []

            for line in registry_lines:
                cap_dict = json.loads(line)
                cap = CapabilityRecord(**cap_dict)

                # Score based on type compatibility
                input_sim = self._schema_similarity(input_schema, cap.input_schema)
                output_sim = self._schema_similarity(output_schema, cap.output_schema)
                combined_score = (input_sim + output_sim) / 2.0

                if combined_score > 0.5:
                    candidates.append((cap, combined_score))

            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[:top_k]

    def compose(self, capability_ids: list[str]) -> Optional[CompositionPlan]:
        """
        Verify that a chain of capabilities can compose (output of one matches input of next).
        Returns CompositionPlan if valid, None if chain breaks.
        """
        if len(capability_ids) < 2:
            return None

        with self._lock:
            capabilities = []
            for cap_id in capability_ids:
                cap = self.get(cap_id)
                if cap is None:
                    return None
                capabilities.append(cap)

            # Check each link in the chain
            transformation_path = [capabilities[0].input_schema]
            confidence = 1.0

            for i in range(len(capabilities) - 1):
                current_out = capabilities[i].output_schema
                next_in = capabilities[i + 1].input_schema
                link_confidence = self._schema_similarity(current_out, next_in)

                if link_confidence < 0.5:
                    # Chain breaks
                    return None

                confidence *= link_confidence
                transformation_path.append(current_out)

            transformation_path.append(capabilities[-1].output_schema)

            return CompositionPlan(
                capabilities=capability_ids,
                transformation_path=transformation_path,
                confidence=confidence,
                rationale=f"Transforms {transformation_path[0]} → {transformation_path[-1]}"
            )

    def get(self, capability_id: str) -> Optional[CapabilityRecord]:
        """Retrieve a capability by ID."""
        with self._lock:
            index_file = CAPABILITY_PATH / "index.json"
            registry_file = CAPABILITY_PATH / "registry.jsonl"

            if not index_file.exists() or not registry_file.exists():
                return None

            index = json.loads(index_file.read_text())
            if capability_id not in index:
                return None

            idx = index[capability_id]
            registry_lines = registry_file.read_text().strip().split("\n")

            if idx >= len(registry_lines):
                return None

            cap_dict = json.loads(registry_lines[idx])
            return CapabilityRecord(**cap_dict)

    def list_all(self, limit: int = 100) -> list:
        """List all registered capabilities."""
        with self._lock:
            registry_file = CAPABILITY_PATH / "registry.jsonl"
            if not registry_file.exists():
                return []

            try:
                capabilities = [
                    CapabilityRecord(**json.loads(line))
                    for line in registry_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                return capabilities[:limit]
            except Exception:
                return []

    def update_usage(self, capability_id: str) -> None:
        """Mark a capability as used (increment usage_count, update last_used)."""
        with self._lock:
            cap = self.get(capability_id)
            if cap is None:
                return

            cap.usage_count += 1
            cap.last_used = time.time()

            # Rewrite the registry line
            index_file = CAPABILITY_PATH / "index.json"
            registry_file = CAPABILITY_PATH / "registry.jsonl"

            index = json.loads(index_file.read_text())
            idx = index[capability_id]

            registry_lines = registry_file.read_text().strip().split("\n")
            registry_lines[idx] = json.dumps(asdict(cap))
            registry_file.write_text("\n".join(registry_lines) + "\n")

    def learn_composition(self, composition: CompositionPlan) -> None:
        """
        Record a successful composition pattern for future reuse.
        This is used by the synthesis engine to learn what chains work.
        """
        with self._lock:
            compositions_file = CAPABILITY_PATH / "compositions.jsonl"
            compositions_file.write_text(
                compositions_file.read_text() + json.dumps(asdict(composition)) + "\n"
                if compositions_file.exists()
                else json.dumps(asdict(composition)) + "\n"
            )

    def get_recommended_chains(self, input_type: str, output_type: str,
                               top_k: int = 5) -> list:
        """
        Return previously successful composition chains from input_type → output_type.
        Used by synthesis engine to avoid re-discovering good patterns.
        """
        with self._lock:
            compositions_file = CAPABILITY_PATH / "compositions.jsonl"
            if not compositions_file.exists():
                return []

            try:
                compositions = [
                    CompositionPlan(**json.loads(line))
                    for line in compositions_file.read_text().strip().split("\n")
                    if line.strip()
                ]

                # Filter by type compatibility and sort by confidence
                relevant = [
                    comp for comp in compositions
                    if (self._schema_similarity(input_type, comp.transformation_path[0]) > 0.6 and
                        self._schema_similarity(output_type, comp.transformation_path[-1]) > 0.6)
                ]
                relevant.sort(key=lambda c: c.confidence, reverse=True)
                return relevant[:top_k]
            except Exception:
                return []
