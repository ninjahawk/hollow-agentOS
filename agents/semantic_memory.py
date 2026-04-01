"""
Semantic Memory — AgentOS v2.0.0.

Replace key-value storage with vector-native memory. Every object stored as an embedding.
Retrieval by cosine similarity, not key lookup.

An agent stores a thought: embed("the rate limiter failed at 3AM because bucket depth was wrong").
Later it searches: embed("what went wrong with rate limiting") and memory surfaces automatically.

Memory is semantic, not symbolic. No naming schemes. No schema management.

Design:
  SemanticMemory(capacity_mb=512, vector_dim=768)
    store(agent_id, thought: str, metadata: dict = None) → memory_id
      # embed the thought, store embedding + metadata, manage capacity via LRU

    search(agent_id, query: str, top_k=5, similarity_threshold=0.7) → list[MemoryRecord]
      # embed the query, find nearest neighbors by cosine similarity

    recall(agent_id, memory_id) → Optional[MemoryRecord]
      # retrieve specific memory by ID (for recent/repeated access)

    forget(agent_id, memory_id) → bool
      # explicitly delete a memory (agent-controlled forgetting)

    consolidate(agent_id) → int
      # merge similar memories, compress old ones (like sleep consolidation)
      # returns number of consolidations performed

Storage:
  /agentOS/memory/semantic/
    {agent_id}/
      embeddings.npy         # (N, 768) matrix of stored embeddings
      metadata.jsonl         # one JSON per line: {id, thought, metadata, timestamp, access_count}
      index.json             # mapping from memory_id to row index (for fast recall)
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_MODEL_AVAILABLE = True
except ImportError:
    EMBEDDING_MODEL_AVAILABLE = False

SEMANTIC_MEMORY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "semantic"
DEFAULT_VECTOR_DIM = 768
DEFAULT_CAPACITY_MB = 512
SIMILARITY_THRESHOLD = 0.7


@dataclass
class MemoryRecord:
    """A single memory: thought + embedding + metadata."""
    memory_id: str
    agent_id: str
    thought: str                    # original text
    metadata: dict                  # user-provided context
    timestamp: float                # when stored
    access_count: int               # how many times recalled
    last_accessed: float            # for LRU eviction


class SemanticMemory:
    """Vector-native memory for agents. Think in embeddings, remember in embeddings."""

    def __init__(self, capacity_mb: int = DEFAULT_CAPACITY_MB, vector_dim: int = DEFAULT_VECTOR_DIM):
        self._capacity_mb = capacity_mb
        self._vector_dim = vector_dim
        self._lock = threading.Lock()
        self._embedder = None
        self._init_embedder()
        SEMANTIC_MEMORY_PATH.mkdir(parents=True, exist_ok=True)

    def _init_embedder(self) -> None:
        """Load the embedding model. nomic-embed-text if available (fast, 768d), else None."""
        if not EMBEDDING_MODEL_AVAILABLE:
            return
        try:
            # nomic-embed-text: fast, 768-dimensional, excellent quality
            # Falls back to all-MiniLM-L6-v2 if nomic not available
            self._embedder = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5")
        except Exception:
            try:
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                pass

    def _embed(self, text: str) -> Optional[np.ndarray]:
        """Convert text to 768-dimensional embedding. Returns None if embedder unavailable."""
        if self._embedder is None:
            return None
        try:
            return np.array(self._embedder.encode(text, convert_to_numpy=True), dtype=np.float32)
        except Exception:
            return None

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    # ── API ────────────────────────────────────────────────────────────────

    def store(self, agent_id: str, thought: str, metadata: dict = None) -> str:
        """
        Store a thought. Returns the memory_id.
        Embedding is computed automatically. Metadata is optional (agent context).
        """
        embedding = self._embed(thought)
        if embedding is None:
            raise ValueError("Embedding unavailable — install sentence-transformers")

        memory_id = f"mem-{uuid.uuid4().hex[:12]}"
        now = time.time()
        record = MemoryRecord(
            memory_id=memory_id,
            agent_id=agent_id,
            thought=thought,
            metadata=metadata or {},
            timestamp=now,
            access_count=0,
            last_accessed=now,
        )

        with self._lock:
            agent_dir = SEMANTIC_MEMORY_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            # Append to metadata log
            metadata_file = agent_dir / "metadata.jsonl"
            metadata_file.write_text(
                metadata_file.read_text() + json.dumps(asdict(record)) + "\n"
                if metadata_file.exists()
                else json.dumps(asdict(record)) + "\n"
            )

            # Append to embeddings matrix
            embeddings_file = agent_dir / "embeddings.npy"
            if embeddings_file.exists():
                embeddings = np.load(embeddings_file)
                embeddings = np.vstack([embeddings, embedding.reshape(1, -1)])
            else:
                embeddings = embedding.reshape(1, -1)
            np.save(embeddings_file, embeddings)

            # Update index
            index_file = agent_dir / "index.json"
            index = json.loads(index_file.read_text()) if index_file.exists() else {}
            index[memory_id] = len(embeddings) - 1
            index_file.write_text(json.dumps(index, indent=2))

            self._maybe_evict(agent_id)

        return memory_id

    def search(self, agent_id: str, query: str, top_k: int = 5,
               similarity_threshold: float = SIMILARITY_THRESHOLD) -> list:
        """
        Search memory by semantic similarity. Returns list of MemoryRecords sorted by relevance.
        """
        query_embedding = self._embed(query)
        if query_embedding is None:
            return []

        with self._lock:
            agent_dir = SEMANTIC_MEMORY_PATH / agent_id
            if not agent_dir.exists():
                return []

            embeddings_file = agent_dir / "embeddings.npy"
            metadata_file = agent_dir / "metadata.jsonl"

            if not embeddings_file.exists() or not metadata_file.exists():
                return []

            embeddings = np.load(embeddings_file)
            metadata_lines = metadata_file.read_text().strip().split("\n")

            # Compute similarities
            similarities = []
            for i, embedding in enumerate(embeddings):
                sim = self._cosine_similarity(query_embedding, embedding)
                if sim >= similarity_threshold:
                    similarities.append((i, sim))

            # Sort by similarity (descending)
            similarities.sort(key=lambda x: x[1], reverse=True)

            # Build results
            results = []
            for idx, sim in similarities[:top_k]:
                if idx < len(metadata_lines):
                    record_dict = json.loads(metadata_lines[idx])
                    record = MemoryRecord(**record_dict)
                    # Update access count and timestamp (in place)
                    record.access_count += 1
                    record.last_accessed = time.time()
                    # Persist update
                    self._update_metadata_line(agent_id, idx, record)
                    results.append(record)

            return results

    def recall(self, agent_id: str, memory_id: str) -> Optional[MemoryRecord]:
        """
        Retrieve a specific memory by ID (O(1) via index).
        Updates access count and timestamp.
        """
        with self._lock:
            agent_dir = SEMANTIC_MEMORY_PATH / agent_id
            if not agent_dir.exists():
                return None

            index_file = agent_dir / "index.json"
            metadata_file = agent_dir / "metadata.jsonl"

            if not index_file.exists() or not metadata_file.exists():
                return None

            index = json.loads(index_file.read_text())
            if memory_id not in index:
                return None

            idx = index[memory_id]
            metadata_lines = metadata_file.read_text().strip().split("\n")

            if idx >= len(metadata_lines):
                return None

            record_dict = json.loads(metadata_lines[idx])
            record = MemoryRecord(**record_dict)
            record.access_count += 1
            record.last_accessed = time.time()
            self._update_metadata_line(agent_id, idx, record)

            return record

    def forget(self, agent_id: str, memory_id: str) -> bool:
        """
        Explicitly delete a memory (agent-controlled forgetting).
        Marks the entry as deleted (doesn't reindex).
        """
        with self._lock:
            agent_dir = SEMANTIC_MEMORY_PATH / agent_id
            if not agent_dir.exists():
                return False

            index_file = agent_dir / "index.json"
            metadata_file = agent_dir / "metadata.jsonl"

            if not index_file.exists():
                return False

            index = json.loads(index_file.read_text())
            if memory_id not in index:
                return False

            idx = index[memory_id]
            del index[memory_id]
            index_file.write_text(json.dumps(index, indent=2))

            # Mark in metadata as deleted (soft delete)
            metadata_lines = metadata_file.read_text().strip().split("\n")
            if idx < len(metadata_lines):
                record_dict = json.loads(metadata_lines[idx])
                record_dict["thought"] = "[DELETED]"
                metadata_lines[idx] = json.dumps(record_dict)
                metadata_file.write_text("\n".join(metadata_lines) + "\n")

            return True

    def consolidate(self, agent_id: str) -> int:
        """
        Merge similar memories, compress old ones (like sleep consolidation).
        Returns number of consolidations performed.
        """
        # Simplified: remove memories older than 30 days with access_count < 2
        # In production: use clustering to merge similar memories
        cutoff = time.time() - (30 * 86400)
        count = 0

        with self._lock:
            agent_dir = SEMANTIC_MEMORY_PATH / agent_id
            if not agent_dir.exists():
                return 0

            metadata_file = agent_dir / "metadata.jsonl"
            if not metadata_file.exists():
                return 0

            metadata_lines = metadata_file.read_text().strip().split("\n")
            new_lines = []
            index = {}

            for i, line in enumerate(metadata_lines):
                record_dict = json.loads(line)
                record = MemoryRecord(**record_dict)

                # Keep if recent, frequently accessed, or explicitly important
                if record.timestamp > cutoff or record.access_count >= 2 or record.metadata.get("important"):
                    new_lines.append(line)
                    index[record.memory_id] = len(new_lines) - 1
                else:
                    count += 1

            # Rewrite metadata and index
            metadata_file.write_text("\n".join(new_lines) + "\n" if new_lines else "")
            (agent_dir / "index.json").write_text(json.dumps(index, indent=2))

        return count

    def list_agent_memories(self, agent_id: str, limit: int = 50) -> list:
        """List recent memories for an agent (ordered by timestamp, newest first)."""
        with self._lock:
            agent_dir = SEMANTIC_MEMORY_PATH / agent_id
            if not agent_dir.exists():
                return []

            metadata_file = agent_dir / "metadata.jsonl"
            if not metadata_file.exists():
                return []

            try:
                records = [
                    MemoryRecord(**json.loads(line))
                    for line in metadata_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                records.sort(key=lambda r: r.timestamp, reverse=True)
                return records[:limit]
            except Exception:
                return []

    # ── Internal helpers ────────────────────────────────────────────────────

    def _update_metadata_line(self, agent_id: str, idx: int, record: MemoryRecord) -> None:
        """Update a single line in the metadata JSONL file."""
        agent_dir = SEMANTIC_MEMORY_PATH / agent_id
        metadata_file = agent_dir / "metadata.jsonl"
        metadata_lines = metadata_file.read_text().strip().split("\n")
        if idx < len(metadata_lines):
            metadata_lines[idx] = json.dumps(asdict(record))
            metadata_file.write_text("\n".join(metadata_lines) + "\n")

    def _maybe_evict(self, agent_id: str) -> None:
        """
        Check if memory exceeds capacity. If so, evict least-recently-accessed items.
        Capacity is in MB, measured by embeddings file size.
        """
        agent_dir = SEMANTIC_MEMORY_PATH / agent_id
        embeddings_file = agent_dir / "embeddings.npy"

        if not embeddings_file.exists():
            return

        size_mb = embeddings_file.stat().st_size / (1024 * 1024)
        if size_mb < self._capacity_mb:
            return

        # Evict until below capacity: remove LRU items
        metadata_file = agent_dir / "metadata.jsonl"
        metadata_lines = metadata_file.read_text().strip().split("\n")
        records = [MemoryRecord(**json.loads(line)) for line in metadata_lines if line.strip()]
        records.sort(key=lambda r: r.last_accessed)

        index = json.loads((agent_dir / "index.json").read_text())
        evicted = 0
        embeddings = np.load(embeddings_file)

        # Remove oldest 20% of memories
        target_count = int(len(records) * 0.8)
        to_remove = set(r.memory_id for r in records[target_count:])

        new_records = [r for r in records if r.memory_id not in to_remove]
        new_index = {r.memory_id: i for i, r in enumerate(new_records)}
        new_embeddings = np.array([embeddings[i] for i in sorted(new_index.values())])

        # Rewrite
        metadata_file.write_text("\n".join(json.dumps(asdict(r)) for r in new_records) + "\n")
        np.save(embeddings_file, new_embeddings)
        (agent_dir / "index.json").write_text(json.dumps(new_index, indent=2))
