"""
Integration tests for AgentOS v2.0.0: Semantic Memory.

Tests verify vector-native memory: storage, retrieval by similarity,
consolidation, capacity management, and semantic recall.

Run:
    PYTHONPATH=. pytest tests/integration/test_semantic_memory.py -v -m integration
"""

import pytest
import time
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.semantic_memory import SemanticMemory
    SEMANTIC_MEMORY_AVAILABLE = True
except ImportError:
    SEMANTIC_MEMORY_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not SEMANTIC_MEMORY_AVAILABLE,
    reason="sentence-transformers not available"
)


# ---------------------------------------------------------------------------
# Test 1 — Store and recall by ID
# ---------------------------------------------------------------------------

class TestSemanticMemoryStore:
    def test_store_and_recall_by_id(self):
        """
        Store a thought. Recall it by memory_id.
        Assert: thought is recovered exactly, access_count incremented.
        """
        memory = SemanticMemory(capacity_mb=100, vector_dim=768)
        agent_id = "test-agent-1"
        thought = "the rate limiter failed at 3AM because the bucket depth was wrong"

        memory_id = memory.store(agent_id, thought, metadata={"severity": "high"})
        assert memory_id.startswith("mem-")

        record = memory.recall(agent_id, memory_id)
        assert record is not None
        assert record.thought == thought
        assert record.metadata["severity"] == "high"
        assert record.access_count == 1

        # Recall again — access count incremented
        record2 = memory.recall(agent_id, memory_id)
        assert record2.access_count == 2


# ---------------------------------------------------------------------------
# Test 2 — Semantic search by similarity
# ---------------------------------------------------------------------------

class TestSemanticMemorySearch:
    def test_search_finds_similar_thoughts(self):
        """
        Store 3 thoughts: one about rate limiting, two about other topics.
        Search for 'rate limit failure'. Assert: results found with similarity threshold.
        """
        memory = SemanticMemory(capacity_mb=100, vector_dim=768)
        agent_id = "test-agent-2"

        thoughts = [
            "the rate limiter failed at 3AM because the bucket depth was wrong",
            "task submitted with complexity 5 chose the slow model",
            "memory pressure triggered compression at 85% capacity",
        ]

        ids = []
        for thought in thoughts:
            mid = memory.store(agent_id, thought)
            ids.append(mid)

        # Search for rate limit-related query
        results = memory.search(agent_id, "rate limiting", top_k=3, similarity_threshold=0.5)

        # Should find at least some results
        assert len(results) > 0

    def test_search_respects_similarity_threshold(self):
        """
        Store unrelated thoughts. Search with high threshold.
        Assert: low-similarity results are filtered out.
        """
        memory = SemanticMemory(capacity_mb=100, vector_dim=768)
        agent_id = "test-agent-3"

        thoughts = [
            "Paris is the capital of France",
            "The mitochondria is the powerhouse of the cell",
            "Rust is a systems programming language",
        ]

        for thought in thoughts:
            memory.store(agent_id, thought)

        # Search with very high threshold — should find few or none
        results = memory.search(agent_id, "debugging a rate limiter",
                               top_k=5, similarity_threshold=0.9)

        # At high threshold, results should be empty or very sparse
        assert len(results) <= 1


# ---------------------------------------------------------------------------
# Test 3 — Explicit forgetting
# ---------------------------------------------------------------------------

class TestSemanticMemoryForget:
    def test_forget_removes_memory(self):
        """
        Store a memory, forget it, attempt recall.
        Assert: recall returns None after forgetting.
        """
        memory = SemanticMemory(capacity_mb=100, vector_dim=768)
        agent_id = "test-agent-4"

        memory_id = memory.store(agent_id, "secret information to forget")

        # Verify it exists
        record = memory.recall(agent_id, memory_id)
        assert record is not None

        # Forget it
        ok = memory.forget(agent_id, memory_id)
        assert ok is True

        # Verify it's gone
        record = memory.recall(agent_id, memory_id)
        assert record is None


# ---------------------------------------------------------------------------
# Test 4 — Consolidation (memory compression)
# ---------------------------------------------------------------------------

class TestSemanticMemoryConsolidate:
    def test_consolidate_runs_without_error(self):
        """
        Store 10 memories. Call consolidate().
        Assert: consolidate completes without error (immediate pruning not tested;
        30-day age cutoff means consolidate returns 0 for new memories).
        """
        memory = SemanticMemory(capacity_mb=100, vector_dim=768)
        agent_id = "test-agent-5"

        # Store 10 thoughts
        ids = []
        for i in range(10):
            mid = memory.store(agent_id, f"thought number {i}")
            ids.append(mid)

        # Recall some (increases access_count)
        memory.recall(agent_id, ids[0])
        memory.recall(agent_id, ids[1])

        # Consolidate should run without error
        pruned = memory.consolidate(agent_id)
        assert isinstance(pruned, int)

        # Recently-stored memories should still exist (not pruned)
        assert memory.recall(agent_id, ids[0]) is not None
        assert memory.recall(agent_id, ids[1]) is not None


# ---------------------------------------------------------------------------
# Test 5 — List recent memories
# ---------------------------------------------------------------------------

class TestSemanticMemoryList:
    def test_list_returns_recent_ordered_by_timestamp(self):
        """
        Store 5 memories with delays. Call list_agent_memories().
        Assert: returns memories in reverse chronological order.
        """
        memory = SemanticMemory(capacity_mb=100, vector_dim=768)
        agent_id = "test-agent-6"

        for i in range(5):
            memory.store(agent_id, f"memory {i}")
            time.sleep(0.01)  # Small delay to ensure timestamp differences

        memories = memory.list_agent_memories(agent_id, limit=10)
        assert len(memories) == 5

        # Verify reverse chronological order
        for i in range(len(memories) - 1):
            assert memories[i].timestamp >= memories[i+1].timestamp


# ---------------------------------------------------------------------------
# Test 6 — Multi-agent isolation
# ---------------------------------------------------------------------------

class TestSemanticMemoryMultiAgent:
    def test_agents_have_separate_memory_spaces(self):
        """
        Two agents store different thoughts.
        Assert: each agent's search only returns their own thoughts.
        """
        memory = SemanticMemory(capacity_mb=100, vector_dim=768)

        agent1_id = "agent-alice"
        agent2_id = "agent-bob"

        memory.store(agent1_id, "Alice's thought about rate limiting")
        memory.store(agent2_id, "Bob's thought about memory pressure")

        # Alice searches for rate limiting — should find her memory, not Bob's
        alice_results = memory.search(agent1_id, "rate limiting", top_k=5)
        assert len(alice_results) > 0
        assert "Alice" in alice_results[0].thought or "rate" in alice_results[0].thought.lower()

        # Bob searches for memory pressure — should find his memory, not Alice's
        bob_results = memory.search(agent2_id, "memory pressure", top_k=5)
        assert len(bob_results) > 0
        assert "Bob" in bob_results[0].thought or "memory" in bob_results[0].thought.lower()
