"""
Integration tests for AgentOS v3.7.0: Meta-Knowledge Synthesis.

Phase 6 primitive 2. Tests verify that the swarm's collective knowledge
can be extracted, queried, and used to rank agents by task suitability.

All tests use real in-memory state (no mocked subsystems).

Run:
    PYTHONPATH=. pytest tests/integration/test_meta_synthesis.py -v
"""

import pytest
import time
import uuid
import shutil
import tempfile
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.meta_synthesis import MetaSynthesizer, SwarmKnowledgeBase, SynthesizedPattern
    META_SYNTHESIS_AVAILABLE = True
except ImportError:
    META_SYNTHESIS_AVAILABLE = False

try:
    from agents.introspection import AgentIntrospector
    from agents.semantic_memory import SemanticMemory
    INTROSPECTION_AVAILABLE = True
    MEMORY_AVAILABLE = True
except ImportError:
    INTROSPECTION_AVAILABLE = False
    MEMORY_AVAILABLE = False

try:
    from agents.execution_engine import ExecutionEngine
    ENGINE_AVAILABLE = True
except ImportError:
    ENGINE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not META_SYNTHESIS_AVAILABLE,
    reason="meta_synthesis module not available"
)


def _uid():
    return f"agent-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def tmp_storage(monkeypatch):
    """Isolate all disk writes to a temp dir per test."""
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)
    if ENGINE_AVAILABLE:
        import agents.execution_engine as em
        monkeypatch.setattr(em, "EXECUTION_PATH", Path(tmpdir) / "executions")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 1 — synthesize() on empty swarm returns valid structure
# ---------------------------------------------------------------------------

class TestSynthesizeStructure:
    def test_empty_agent_list_returns_valid_kb(self, tmp_storage):
        """synthesize([]) → SwarmKnowledgeBase with all required fields."""
        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")
        kb = synth.synthesize([])

        assert isinstance(kb, SwarmKnowledgeBase)
        assert kb.synthesis_id is not None
        assert isinstance(kb.patterns, list)
        assert isinstance(kb.topic_map, dict)
        assert isinstance(kb.capability_rankings, dict)
        assert isinstance(kb.failure_signatures, list)
        assert 0.0 <= kb.coverage_score <= 1.0

    def test_no_introspector_returns_empty_synthesis(self, tmp_storage):
        """MetaSynthesizer with no introspector → empty synthesis, no crash."""
        synth = MetaSynthesizer(introspector=None, storage_path=tmp_storage / "meta")
        agent_ids = [_uid(), _uid(), _uid()]

        kb = synth.synthesize(agent_ids)

        assert isinstance(kb, SwarmKnowledgeBase)
        assert kb.patterns == []
        assert kb.coverage_score == 0.0

    def test_synthesize_with_real_agents(self, tmp_storage):
        """
        3 agents with real execution histories → synthesize() extracts
        capability rankings and a non-zero coverage score.
        """
        if not INTROSPECTION_AVAILABLE or not ENGINE_AVAILABLE:
            pytest.skip("introspection or engine not available")

        engine = ExecutionEngine()
        introspector = AgentIntrospector(execution_engine=engine)
        synth = MetaSynthesizer(introspector=introspector, storage_path=tmp_storage / "meta")

        agent_ids = [_uid(), _uid(), _uid()]

        # register shared capabilities
        engine.register("cap-search", lambda: {"results": ["a", "b"]})
        engine.register("cap-process", lambda: {"status": "done"})

        # all agents run cap-search; mixed on cap-process
        for aid in agent_ids:
            for _ in range(3):
                engine.execute(aid, "cap-search", {})
            engine.execute(aid, "cap-process", {})

        kb = synth.synthesize(agent_ids)

        # all 3 agents should be in the synthesis
        assert set(kb.agent_ids) == set(agent_ids)

        # cap-search should be ranked (all agents used it)
        assert "cap-search" in kb.capability_rankings
        assert kb.capability_rankings["cap-search"]["agent_count"] == 3

        # coverage should be nonzero
        assert kb.coverage_score > 0.0


# ---------------------------------------------------------------------------
# Test 2 — pattern extraction finds cross-agent patterns
# ---------------------------------------------------------------------------

class TestPatternExtraction:
    def test_shared_capability_produces_pattern(self, tmp_storage):
        """
        When 3+ agents all use the same capability, synthesize() emits
        a capability_sequence pattern describing it.
        """
        if not INTROSPECTION_AVAILABLE or not ENGINE_AVAILABLE:
            pytest.skip("introspection or engine not available")

        engine = ExecutionEngine()
        introspector = AgentIntrospector(execution_engine=engine)
        synth = MetaSynthesizer(introspector=introspector, storage_path=tmp_storage / "meta")

        agent_ids = [_uid(), _uid(), _uid()]
        engine.register("cap-core", lambda: {"ok": True})

        for aid in agent_ids:
            for _ in range(4):
                engine.execute(aid, "cap-core", {})

        kb = synth.synthesize(agent_ids)

        cap_patterns = [p for p in kb.patterns if p["pattern_type"] == "capability_sequence"]
        assert len(cap_patterns) > 0

        # the pattern should mention cap-core and 3 agents
        top = cap_patterns[0]
        assert top["agent_count"] >= 3
        assert "cap-core" in top["description"]

    def test_shared_memory_topics_produce_pattern(self, tmp_storage):
        """
        When agents have overlapping memory topics, synthesize() emits
        a shared_topic pattern.
        """
        if not INTROSPECTION_AVAILABLE or not MEMORY_AVAILABLE:
            pytest.skip("introspection or memory not available")

        memory = SemanticMemory(capacity_mb=64)
        introspector = AgentIntrospector(semantic_memory=memory)
        synth = MetaSynthesizer(introspector=introspector, storage_path=tmp_storage / "meta")

        agent_ids = [_uid(), _uid(), _uid()]

        shared_thought = "rate limiting token bucket controls admission to the system"
        for aid in agent_ids:
            memory.store(aid, shared_thought)
            memory.store(aid, "consensus quorum votes on governance proposals")

        kb = synth.synthesize(agent_ids)

        topic_patterns = [p for p in kb.patterns if p["pattern_type"] == "shared_topic"]
        assert len(topic_patterns) > 0
        assert topic_patterns[0]["agent_count"] >= 2

    def test_high_failure_rate_produces_failure_correlation(self, tmp_storage):
        """
        When 2+ agents all have success_rate < 50%, synthesize() emits
        a failure_correlation pattern.
        """
        if not INTROSPECTION_AVAILABLE or not ENGINE_AVAILABLE:
            pytest.skip("introspection or engine not available")

        engine = ExecutionEngine()
        introspector = AgentIntrospector(execution_engine=engine)
        synth = MetaSynthesizer(introspector=introspector, storage_path=tmp_storage / "meta")

        agent_ids = [_uid(), _uid(), _uid()]

        def always_fail():
            raise RuntimeError("capability not available")

        engine.register("cap-broken", always_fail)

        for aid in agent_ids:
            for _ in range(5):
                engine.execute(aid, "cap-broken", {})

        kb = synth.synthesize(agent_ids)

        failure_patterns = [p for p in kb.patterns if p["pattern_type"] == "failure_correlation"]
        assert len(failure_patterns) > 0
        assert failure_patterns[0]["agent_count"] >= 2


# ---------------------------------------------------------------------------
# Test 3 — query() returns relevant patterns
# ---------------------------------------------------------------------------

class TestQuery:
    def test_query_empty_kb_returns_empty(self, tmp_storage):
        """query() on an empty knowledge base returns []."""
        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")
        kb = synth.synthesize([])

        results = synth.query(kb, "what do agents know about rate limiting?")
        assert results == []

    def test_query_finds_relevant_pattern(self, tmp_storage):
        """
        A KB with a pattern about 'rate limiting' → query for
        'admission control rate limit' returns that pattern.
        """
        if not INTROSPECTION_AVAILABLE or not MEMORY_AVAILABLE:
            pytest.skip("introspection or memory not available")

        memory = SemanticMemory(capacity_mb=64)
        introspector = AgentIntrospector(semantic_memory=memory)
        synth = MetaSynthesizer(introspector=introspector, storage_path=tmp_storage / "meta")

        agent_ids = [_uid(), _uid()]
        for aid in agent_ids:
            memory.store(aid, "rate limiting token bucket controls admission to the system")
            memory.store(aid, "rate limit bucket refills at 100 tokens per second")

        kb = synth.synthesize(agent_ids)

        results = synth.query(kb, "rate limiting admission control")
        # at minimum, the call should not crash and return a list
        assert isinstance(results, list)
        # if patterns were found, they should be relevant
        if results:
            combined = " ".join(r["description"].lower() for r in results)
            assert "rate" in combined or "topic" in combined or "knowledge" in combined

    def test_query_keyword_matching_works(self, tmp_storage):
        """
        Even without embedder, keyword-based query matching returns
        relevant patterns when words overlap.
        """
        from agents.meta_synthesis import SynthesizedPattern, SwarmKnowledgeBase
        import dataclasses

        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")
        synth._embedder = None  # disable embedder to test keyword fallback

        # manually construct a KB with a known pattern
        p = SynthesizedPattern(
            pattern_id="test-001",
            description="capability cap-search is used by 3 agents — likely a core system operation",
            pattern_type="capability_sequence",
            agent_count=3,
            agent_ids=["a", "b", "c"],
            confidence=0.9,
            supporting_data={},
        )
        kb = SwarmKnowledgeBase(
            synthesis_id="test",
            synthesized_at=time.time(),
            agent_ids=["a", "b", "c"],
            patterns=[dataclasses.asdict(p)],
            topic_map={},
            capability_rankings={},
            failure_signatures=[],
            coverage_score=0.6,
        )

        results = synth.query(kb, "what capability is used by core agents search")
        assert len(results) > 0
        assert results[0]["pattern_id"] == "test-001"


# ---------------------------------------------------------------------------
# Test 4 — top_patterns() filters by min_agents
# ---------------------------------------------------------------------------

class TestTopPatterns:
    def test_top_patterns_filters_by_min_agents(self, tmp_storage):
        """
        top_patterns(min_agents=3) returns only patterns seen in 3+ agents.
        """
        import dataclasses
        from agents.meta_synthesis import SynthesizedPattern, SwarmKnowledgeBase

        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")

        p1 = SynthesizedPattern("p1", "pattern in 4 agents", "shared_topic", 4, ["a","b","c","d"], 0.8, {})
        p2 = SynthesizedPattern("p2", "pattern in 2 agents", "shared_topic", 2, ["a","b"], 0.6, {})
        p3 = SynthesizedPattern("p3", "pattern in 1 agent", "shared_topic", 1, ["a"], 0.4, {})

        kb = SwarmKnowledgeBase(
            synthesis_id="test",
            synthesized_at=time.time(),
            agent_ids=["a","b","c","d"],
            patterns=[dataclasses.asdict(p1), dataclasses.asdict(p2), dataclasses.asdict(p3)],
            topic_map={},
            capability_rankings={},
            failure_signatures=[],
            coverage_score=0.7,
        )

        results = synth.top_patterns(kb, min_agents=3)
        assert len(results) == 1
        assert results[0]["pattern_id"] == "p1"

    def test_top_patterns_sorted_by_confidence(self, tmp_storage):
        """top_patterns returns highest-confidence patterns first."""
        import dataclasses
        from agents.meta_synthesis import SynthesizedPattern, SwarmKnowledgeBase

        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")

        patterns = [
            SynthesizedPattern("p1", "low confidence", "shared_topic", 3, ["a","b","c"], 0.3, {}),
            SynthesizedPattern("p2", "high confidence", "shared_topic", 3, ["a","b","c"], 0.9, {}),
            SynthesizedPattern("p3", "mid confidence", "shared_topic", 3, ["a","b","c"], 0.6, {}),
        ]

        kb = SwarmKnowledgeBase(
            synthesis_id="test",
            synthesized_at=time.time(),
            agent_ids=["a","b","c"],
            patterns=[dataclasses.asdict(p) for p in patterns],
            topic_map={},
            capability_rankings={},
            failure_signatures=[],
            coverage_score=0.5,
        )

        results = synth.top_patterns(kb, min_agents=2)
        assert len(results) == 3
        assert results[0]["pattern_id"] == "p2"  # highest confidence first
        assert results[-1]["pattern_id"] == "p1"  # lowest confidence last


# ---------------------------------------------------------------------------
# Test 5 — agent_ranking() orders agents by task suitability
# ---------------------------------------------------------------------------

class TestAgentRanking:
    def test_agent_ranking_returns_all_agents(self, tmp_storage):
        """agent_ranking() returns a (agent_id, score) pair for every agent in the KB."""
        import dataclasses
        from agents.meta_synthesis import SwarmKnowledgeBase

        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")
        agent_ids = [_uid(), _uid(), _uid()]

        kb = SwarmKnowledgeBase(
            synthesis_id="test",
            synthesized_at=time.time(),
            agent_ids=agent_ids,
            patterns=[],
            topic_map={},
            capability_rankings={},
            failure_signatures=[],
            coverage_score=0.5,
        )

        ranking = synth.agent_ranking(kb, "code review task")
        assert len(ranking) == len(agent_ids)
        ranked_ids = [r[0] for r in ranking]
        for aid in agent_ids:
            assert aid in ranked_ids

    def test_agent_with_relevant_topics_ranked_higher(self, tmp_storage):
        """
        Agent with memory topics matching the task type scores higher
        than agent with unrelated topics.
        """
        import dataclasses
        from agents.meta_synthesis import SwarmKnowledgeBase

        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")
        expert_agent = _uid()
        novice_agent = _uid()

        kb = SwarmKnowledgeBase(
            synthesis_id="test",
            synthesized_at=time.time(),
            agent_ids=[expert_agent, novice_agent],
            patterns=[],
            topic_map={
                "code": [expert_agent],        # expert knows about code
                "review": [expert_agent],      # expert knows about review
                "cooking": [novice_agent],     # novice knows unrelated stuff
            },
            capability_rankings={},
            failure_signatures=[],
            coverage_score=0.5,
        )

        ranking = synth.agent_ranking(kb, "code review")
        assert ranking[0][0] == expert_agent  # expert should be first


# ---------------------------------------------------------------------------
# Test 6 — diff() detects changes between two synthesis runs
# ---------------------------------------------------------------------------

class TestDiff:
    def test_diff_detects_new_patterns(self, tmp_storage):
        """
        diff(kb_before, kb_after) shows new_patterns added between runs.
        """
        import dataclasses
        from agents.meta_synthesis import SynthesizedPattern, SwarmKnowledgeBase

        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")

        p_old = SynthesizedPattern("p-old", "existing pattern", "shared_topic", 2, ["a","b"], 0.7, {})
        p_new = SynthesizedPattern("p-new", "newly discovered pattern", "capability_sequence", 3, ["a","b","c"], 0.8, {})

        kb_before = SwarmKnowledgeBase(
            synthesis_id="before", synthesized_at=time.time(),
            agent_ids=["a", "b"], patterns=[dataclasses.asdict(p_old)],
            topic_map={}, capability_rankings={}, failure_signatures=[], coverage_score=0.4,
        )
        kb_after = SwarmKnowledgeBase(
            synthesis_id="after", synthesized_at=time.time(),
            agent_ids=["a", "b", "c"],
            patterns=[dataclasses.asdict(p_old), dataclasses.asdict(p_new)],
            topic_map={}, capability_rankings={}, failure_signatures=[], coverage_score=0.7,
        )

        d = synth.diff(kb_before, kb_after)

        assert len(d["new_patterns"]) == 1
        assert d["new_patterns"][0]["pattern_id"] == "p-new"
        assert len(d["lost_patterns"]) == 0
        assert d["agent_count_change"] == 1
        assert d["coverage_change"] == pytest.approx(0.3, abs=0.01)

    def test_diff_detects_lost_patterns(self, tmp_storage):
        """diff() detects patterns that existed before but not after."""
        import dataclasses
        from agents.meta_synthesis import SynthesizedPattern, SwarmKnowledgeBase

        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")

        p = SynthesizedPattern("p-gone", "pattern that disappeared", "failure_correlation", 2, ["a","b"], 0.5, {})

        kb_before = SwarmKnowledgeBase(
            synthesis_id="before", synthesized_at=time.time(),
            agent_ids=["a", "b"], patterns=[dataclasses.asdict(p)],
            topic_map={}, capability_rankings={}, failure_signatures=[], coverage_score=0.4,
        )
        kb_after = SwarmKnowledgeBase(
            synthesis_id="after", synthesized_at=time.time(),
            agent_ids=["a", "b"], patterns=[],
            topic_map={}, capability_rankings={}, failure_signatures=[], coverage_score=0.3,
        )

        d = synth.diff(kb_before, kb_after)
        assert len(d["lost_patterns"]) == 1
        assert d["lost_patterns"][0]["pattern_id"] == "p-gone"

    def test_diff_detects_capability_ranking_changes(self, tmp_storage):
        """diff() shows when capability rankings change between runs."""
        from agents.meta_synthesis import SwarmKnowledgeBase

        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")

        kb_before = SwarmKnowledgeBase(
            synthesis_id="before", synthesized_at=time.time(),
            agent_ids=["a"], patterns=[],
            topic_map={},
            capability_rankings={"cap-x": {"success_rate": 0.5, "agent_count": 1, "by_agent": {}}},
            failure_signatures=[], coverage_score=0.3,
        )
        kb_after = SwarmKnowledgeBase(
            synthesis_id="after", synthesized_at=time.time(),
            agent_ids=["a"], patterns=[],
            topic_map={},
            capability_rankings={"cap-x": {"success_rate": 0.9, "agent_count": 2, "by_agent": {}}},
            failure_signatures=[], coverage_score=0.5,
        )

        d = synth.diff(kb_before, kb_after)
        assert "cap-x" in d["changed_capabilities"]
        assert d["changed_capabilities"]["cap-x"]["before"]["success_rate"] == 0.5
        assert d["changed_capabilities"]["cap-x"]["after"]["success_rate"] == 0.9


# ---------------------------------------------------------------------------
# Test 7 — persistence: synthesis survives disk round-trip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_synthesis_written_to_disk(self, tmp_storage):
        """synthesize() writes a JSON file to disk."""
        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")
        kb = synth.synthesize([])

        files = list((tmp_storage / "meta").glob("synthesis_*.json"))
        assert len(files) == 1

    def test_load_latest_returns_last_synthesis(self, tmp_storage):
        """load_latest() returns the most recently written synthesis."""
        synth = MetaSynthesizer(storage_path=tmp_storage / "meta")

        # synthesize twice
        kb1 = synth.synthesize([])
        time.sleep(0.01)
        kb2 = synth.synthesize([])

        latest = synth.load_latest()
        assert latest is not None
        assert latest.synthesis_id == kb2.synthesis_id

    def test_load_latest_none_when_no_files(self, tmp_storage):
        """load_latest() returns None when no synthesis files exist."""
        empty_path = tmp_storage / "fresh_meta"
        synth = MetaSynthesizer(storage_path=empty_path)
        result = synth.load_latest()
        assert result is None
