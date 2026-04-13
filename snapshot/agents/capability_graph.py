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

EMBEDDING_AVAILABLE = True  # always available via Ollama

CAPABILITY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "capabilities"
DEFAULT_VECTOR_DIM = 768
_OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_HOST", os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")) + "/api/embeddings"
_EMBED_MODEL = "nomic-embed-text"


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
        pass  # embedder is Ollama, no init needed

    def _embed(self, text: str) -> Optional[np.ndarray]:
        """Embed text via Ollama nomic-embed-text.
        Retries on 503 (queue full) up to 3 times with backoff.
        """
        import time
        import httpx
        for attempt in range(3):
            try:
                r = httpx.post(_OLLAMA_EMBED_URL,
                               json={"model": _EMBED_MODEL, "prompt": text},
                               timeout=15)
                if r.status_code == 503 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                emb = r.json().get("embedding")
                if emb:
                    return np.array(emb, dtype=np.float32)
            except Exception:
                pass
            break
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

        # Skip if already registered (prevents duplicate growth on repeated calls)
        index_file = CAPABILITY_PATH / "index.json"
        with self._lock:
            index = json.loads(index_file.read_text()) if index_file.exists() else {}
            if cap.capability_id in index:
                return cap.capability_id

        embedding = self._embed(cap.description)  # None if embedder unavailable

        with self._lock:
            index = json.loads(index_file.read_text()) if index_file.exists() else {}
            if cap.capability_id in index:
                return cap.capability_id  # double-check after embedding

            # Append to registry
            registry_file = CAPABILITY_PATH / "registry.jsonl"
            registry_file.write_text(
                registry_file.read_text() + json.dumps(asdict(cap)) + "\n"
                if registry_file.exists()
                else json.dumps(asdict(cap)) + "\n"
            )

            # Append to embeddings only if available — capability works without it,
            # it just won't surface in semantic search until embeddings are rebuilt
            if embedding is not None:
                embeddings_file = CAPABILITY_PATH / "embeddings.npy"
                if embeddings_file.exists():
                    embeddings = np.load(embeddings_file)
                    embeddings = np.vstack([embeddings, embedding.reshape(1, -1)])
                else:
                    embeddings = embedding.reshape(1, -1)
                np.save(embeddings_file, embeddings)
                index[cap.capability_id] = len(embeddings) - 1
            else:
                index[cap.capability_id] = -1  # registered but not semantically indexed

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
            index_file = CAPABILITY_PATH / "index.json"

            if not registry_file.exists() or not embeddings_file.exists():
                return []

            embeddings = np.load(embeddings_file)

            # Build reverse map: embedding_row → capability_id
            row_to_id = {}
            if index_file.exists():
                idx_map = json.loads(index_file.read_text())
                for cap_id, row in idx_map.items():
                    if row >= 0:
                        row_to_id[row] = cap_id

            # Build id → registry dict for fast lookup
            id_to_cap = {}
            for line in registry_file.read_text().strip().split("\n"):
                if line.strip():
                    try:
                        d = json.loads(line)
                        id_to_cap[d["capability_id"]] = d
                    except Exception:
                        pass

            # Compute similarities
            similarities = []
            for i, embedding in enumerate(embeddings):
                sim = self._cosine_similarity(query_embedding, embedding)
                if sim >= similarity_threshold:
                    similarities.append((i, sim))

            similarities.sort(key=lambda x: x[1], reverse=True)

            results = []
            for row, sim in similarities[:top_k]:
                cap_id = row_to_id.get(row)
                if cap_id and cap_id in id_to_cap:
                    cap = CapabilityRecord(**id_to_cap[cap_id])
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

            # Scan registry by capability_id — avoids row/line offset bugs
            for line in registry_file.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    cap_dict = json.loads(line)
                    if cap_dict.get("capability_id") == capability_id:
                        return CapabilityRecord(**cap_dict)
                except Exception:
                    pass
            return None

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

            # Rewrite the matching registry line by capability_id
            registry_file = CAPABILITY_PATH / "registry.jsonl"
            updated_cap_json = json.dumps(asdict(cap))
            registry_lines = registry_file.read_text().strip().split("\n")
            new_lines = []
            for line in registry_lines:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    new_lines.append(updated_cap_json if d.get("capability_id") == capability_id else line)
                except Exception:
                    new_lines.append(line)
            registry_file.write_text("\n".join(new_lines) + "\n")

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
