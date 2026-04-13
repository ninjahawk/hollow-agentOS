"""
Meta-Knowledge Synthesis — AgentOS v3.7.0.

Phase 6, primitive 2. Depends on v3.6.0 (Agent Introspection).

Single-agent introspection asks: what does THIS agent know?
Meta-synthesis asks: what does the SWARM know collectively?

The core operation: given N agents' execution histories and memories,
extract patterns that hold across agents — not just coincidences in one
agent's behavior, but repeating structures that tell us something true
about the problem domain.

Concrete examples of what this surfaces:
  "Agents that succeed on code tasks call cap-search before cap-write"
  "3 of 5 agents failed on fs_write when token budget was >80%"
  "Knowledge about rate limiting predicts success on admission tasks"

Design:
  MetaSynthesizer:
    synthesize(agent_ids) → SwarmKnowledgeBase
      # pull knowledge snapshots from all agents via introspector
      # extract cross-agent patterns by capability co-occurrence,
      # failure correlation, memory topic overlap

    query(swarm_kb, question: str) → list[SynthesizedPattern]
      # semantic search over discovered patterns

    top_patterns(swarm_kb, min_agents=2) → list[SynthesizedPattern]
      # patterns observed in >= min_agents agents

    agent_ranking(swarm_kb, task_type: str) → list[(agent_id, score)]
      # rank agents by likely success on a task type

Storage:
  /agentOS/memory/meta_synthesis/
    synthesis_{timestamp}.json    # point-in-time swarm knowledge snapshots
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False

META_SYNTHESIS_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "meta_synthesis"


@dataclass
class SynthesizedPattern:
    """A pattern discovered across multiple agents."""
    pattern_id: str
    description: str            # human-readable statement of the pattern
    pattern_type: str           # "capability_sequence" | "failure_correlation" |
                                # "knowledge_predicts_success" | "shared_topic"
    agent_count: int            # how many agents this was observed in
    agent_ids: list             # which agents contributed this pattern
    confidence: float           # 0.0-1.0: strength of evidence
    supporting_data: dict       # raw evidence behind the pattern
    discovered_at: float = field(default_factory=time.time)


@dataclass
class SwarmKnowledgeBase:
    """Collective knowledge synthesized from a swarm of agents."""
    synthesis_id: str
    synthesized_at: float
    agent_ids: list             # agents included in this synthesis
    patterns: list              # list of SynthesizedPattern dicts
    topic_map: dict             # topic → list of agent_ids that know it
    capability_rankings: dict   # capability_id → {success_rate, agent_count}
    failure_signatures: list    # common failure conditions across agents
    coverage_score: float       # 0.0-1.0: how complete is swarm knowledge


class MetaSynthesizer:
    """
    Extract knowledge patterns that hold across multiple agents.

    Operates on KnowledgeSnapshots produced by AgentIntrospector.
    No LLM inference — pure structural analysis of execution records.
    """

    def __init__(self, introspector=None, storage_path: Path = None):
        self._introspector = introspector
        self._lock = threading.Lock()
        self._embedder = None
        if EMBEDDING_AVAILABLE:
            try:
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                self._embedder = None
        self._storage_path = storage_path or META_SYNTHESIS_PATH
        self._storage_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    #  Core API                                                            #
    # ------------------------------------------------------------------ #

    def synthesize(self, agent_ids: list) -> SwarmKnowledgeBase:
        """
        Pull knowledge from all agents and extract cross-agent patterns.

        This is the primary entry point. Give it a list of agent IDs,
        it returns what the swarm collectively knows.
        """
        synthesis_id = str(uuid.uuid4())[:8]

        # Step 1: gather snapshots from all agents
        snapshots = []
        if self._introspector is not None:
            for aid in agent_ids:
                try:
                    snap = self._introspector.query_knowledge(aid)
                    snapshots.append(snap)
                except Exception:
                    continue
        else:
            # no introspector: return empty synthesis (still persisted)
            kb = self._empty_synthesis(synthesis_id, agent_ids)
            self._persist(kb)
            return kb

        if not snapshots:
            kb = self._empty_synthesis(synthesis_id, agent_ids)
            self._persist(kb)
            return kb

        # Step 2: extract patterns
        patterns = []
        patterns.extend(self._extract_capability_patterns(snapshots))
        patterns.extend(self._extract_failure_correlations(snapshots))
        patterns.extend(self._extract_shared_knowledge(snapshots))

        # Step 3: build topic map
        topic_map = self._build_topic_map(snapshots)

        # Step 4: capability rankings
        capability_rankings = self._build_capability_rankings(snapshots)

        # Step 5: failure signatures
        failure_signatures = self._extract_failure_signatures(snapshots)

        # Step 6: coverage score
        coverage_score = self._compute_coverage(snapshots)

        kb = SwarmKnowledgeBase(
            synthesis_id=synthesis_id,
            synthesized_at=time.time(),
            agent_ids=[s.agent_id for s in snapshots],
            patterns=[asdict(p) for p in patterns],
            topic_map=topic_map,
            capability_rankings=capability_rankings,
            failure_signatures=failure_signatures,
            coverage_score=coverage_score,
        )

        self._persist(kb)
        return kb

    def query(self, swarm_kb: SwarmKnowledgeBase, question: str) -> list:
        """
        Semantic search over discovered patterns.

        Returns patterns whose description is most relevant to the question.
        Falls back to keyword matching when embedder is unavailable.
        """
        if not swarm_kb.patterns:
            return []

        question_lower = question.lower().split()
        scored = []

        for p in swarm_kb.patterns:
            desc = p.get("description", "").lower()
            # keyword overlap score
            overlap = sum(1 for w in question_lower if w in desc and len(w) > 3)
            confidence = p.get("confidence", 0.0)
            score = overlap * 0.7 + confidence * 0.3
            if score > 0:
                scored.append((score, p))

        # if embedder available, re-rank by semantic similarity
        if self._embedder is not None and scored:
            try:
                q_emb = self._embedder.encode(question)
                for i, (score, p) in enumerate(scored):
                    p_emb = self._embedder.encode(p["description"])
                    sim = float(np.dot(q_emb, p_emb) / (
                        np.linalg.norm(q_emb) * np.linalg.norm(p_emb) + 1e-9
                    ))
                    scored[i] = (sim, p)
            except Exception:
                pass

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:10]]

    def top_patterns(self, swarm_kb: SwarmKnowledgeBase, min_agents: int = 2) -> list:
        """
        Return patterns observed in at least min_agents agents,
        sorted by confidence descending.
        """
        qualified = [
            p for p in swarm_kb.patterns
            if p.get("agent_count", 0) >= min_agents
        ]
        qualified.sort(key=lambda p: p.get("confidence", 0.0), reverse=True)
        return qualified

    def agent_ranking(self, swarm_kb: SwarmKnowledgeBase, task_type: str) -> list:
        """
        Rank agents by expected success on the given task type.

        Score = (success_rate × 0.6) + (relevant_topic_count × 0.4)
        """
        task_words = set(task_type.lower().split())
        scores = []

        for agent_id in swarm_kb.agent_ids:
            # success rate from capability rankings (weighted by agent participation)
            success_rate = 0.0
            cap_count = 0
            for cap_data in swarm_kb.capability_rankings.values():
                agent_caps = cap_data.get("by_agent", {})
                if agent_id in agent_caps:
                    success_rate += agent_caps[agent_id].get("success_rate", 0.0)
                    cap_count += 1
            if cap_count > 0:
                success_rate /= cap_count

            # topic relevance: how many of this agent's topics match task_type words
            relevant_topics = 0
            for topic, agents in swarm_kb.topic_map.items():
                if agent_id in agents and any(w in topic for w in task_words):
                    relevant_topics += 1
            topic_score = min(relevant_topics / max(len(task_words), 1), 1.0)

            combined = success_rate * 0.6 + topic_score * 0.4
            scores.append((agent_id, round(combined, 3)))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def diff(self, kb_a: SwarmKnowledgeBase, kb_b: SwarmKnowledgeBase) -> dict:
        """
        What changed between two synthesis snapshots?
        Returns new patterns, lost patterns, and changed capability rankings.
        """
        a_patterns = {p["pattern_id"]: p for p in kb_a.patterns}
        b_patterns = {p["pattern_id"]: p for p in kb_b.patterns}

        new_ids = set(b_patterns) - set(a_patterns)
        lost_ids = set(a_patterns) - set(b_patterns)

        new_caps = set(kb_b.capability_rankings) - set(kb_a.capability_rankings)
        changed_caps = {
            cap: {
                "before": kb_a.capability_rankings.get(cap, {}),
                "after": kb_b.capability_rankings[cap],
            }
            for cap in kb_b.capability_rankings
            if cap in kb_a.capability_rankings
            and kb_b.capability_rankings[cap] != kb_a.capability_rankings[cap]
        }

        return {
            "new_patterns": [b_patterns[i] for i in new_ids],
            "lost_patterns": [a_patterns[i] for i in lost_ids],
            "new_capabilities": list(new_caps),
            "changed_capabilities": changed_caps,
            "agent_count_change": len(kb_b.agent_ids) - len(kb_a.agent_ids),
            "coverage_change": round(kb_b.coverage_score - kb_a.coverage_score, 3),
        }

    # ------------------------------------------------------------------ #
    #  Pattern extraction internals                                        #
    # ------------------------------------------------------------------ #

    def _extract_capability_patterns(self, snapshots: list) -> list:
        """
        Find capabilities that co-occur frequently across agents.
        If cap-A and cap-B both appear in 3+ agents' top capabilities,
        that's a capability co-occurrence pattern.
        """
        patterns = []

        # count how many agents use each capability
        cap_agents: dict = {}
        for snap in snapshots:
            for cap in snap.top_capabilities:
                cap_id = cap["capability_id"] if isinstance(cap, dict) else str(cap)
                cap_agents.setdefault(cap_id, []).append(snap.agent_id)

        # capabilities used by multiple agents
        common_caps = {
            cap_id: agents
            for cap_id, agents in cap_agents.items()
            if len(agents) >= 2
        }

        if common_caps:
            top_cap = max(common_caps, key=lambda c: len(common_caps[c]))
            agents = common_caps[top_cap]
            patterns.append(SynthesizedPattern(
                pattern_id=str(uuid.uuid4())[:8],
                description=f"Capability '{top_cap}' is used by {len(agents)} agents — likely a core system operation",
                pattern_type="capability_sequence",
                agent_count=len(agents),
                agent_ids=agents,
                confidence=min(len(agents) / len(snapshots), 1.0),
                supporting_data={"capability": top_cap, "all_common": list(common_caps.keys())},
            ))

        return patterns

    def _extract_failure_correlations(self, snapshots: list) -> list:
        """
        Find conditions that correlate with failures across agents.
        If 3+ agents all failed with high token usage, that's a pattern.
        """
        patterns = []

        high_token_failures = []
        low_success_agents = []

        for snap in snapshots:
            if snap.success_rate < 0.5 and snap.total_executions > 0:
                low_success_agents.append(snap.agent_id)
            if snap.total_tokens > 10000 and snap.success_rate < 0.6:
                high_token_failures.append(snap.agent_id)

        if len(low_success_agents) >= 2:
            patterns.append(SynthesizedPattern(
                pattern_id=str(uuid.uuid4())[:8],
                description=f"{len(low_success_agents)} agents have success_rate < 50% — potential systemic capability gap",
                pattern_type="failure_correlation",
                agent_count=len(low_success_agents),
                agent_ids=low_success_agents,
                confidence=len(low_success_agents) / len(snapshots),
                supporting_data={"agents": low_success_agents, "threshold": 0.5},
            ))

        if len(high_token_failures) >= 2:
            patterns.append(SynthesizedPattern(
                pattern_id=str(uuid.uuid4())[:8],
                description=f"{len(high_token_failures)} agents fail more when token usage is high — budget pressure correlation",
                pattern_type="failure_correlation",
                agent_count=len(high_token_failures),
                agent_ids=high_token_failures,
                confidence=min(len(high_token_failures) / len(snapshots) + 0.2, 1.0),
                supporting_data={"agents": high_token_failures},
            ))

        return patterns

    def _extract_shared_knowledge(self, snapshots: list) -> list:
        """
        Find topics that appear in multiple agents' memory.
        Shared topics are likely foundational knowledge the swarm has converged on.
        """
        patterns = []

        topic_agents: dict = {}
        for snap in snapshots:
            for topic in snap.memory_topics:
                topic_agents.setdefault(topic, []).append(snap.agent_id)

        # topics present in 2+ agents
        shared = {
            t: agents for t, agents in topic_agents.items()
            if len(agents) >= 2
        }

        if shared:
            # report the most widely shared topic
            top_topic = max(shared, key=lambda t: len(shared[t]))
            agents = shared[top_topic]
            patterns.append(SynthesizedPattern(
                pattern_id=str(uuid.uuid4())[:8],
                description=f"Topic '{top_topic}' appears in {len(agents)} agents' memories — shared foundational knowledge",
                pattern_type="shared_topic",
                agent_count=len(agents),
                agent_ids=agents,
                confidence=len(agents) / len(snapshots),
                supporting_data={"topic": top_topic, "all_shared": list(shared.keys())},
            ))

        return patterns

    def _build_topic_map(self, snapshots: list) -> dict:
        """topic → [agent_ids that know it]"""
        topic_map: dict = {}
        for snap in snapshots:
            for topic in snap.memory_topics:
                topic_map.setdefault(topic, [])
                if snap.agent_id not in topic_map[topic]:
                    topic_map[topic].append(snap.agent_id)
        return topic_map

    def _build_capability_rankings(self, snapshots: list) -> dict:
        """
        capability_id → {
          success_rate: float (weighted avg across agents),
          agent_count: int,
          by_agent: {agent_id: {usage_count, success_rate}}
        }
        """
        rankings: dict = {}
        for snap in snapshots:
            for cap in snap.top_capabilities:
                if isinstance(cap, dict):
                    cap_id = cap.get("capability_id", "")
                    usage = cap.get("usage_count", 0)
                else:
                    cap_id = str(cap)
                    usage = 1
                if not cap_id:
                    continue

                if cap_id not in rankings:
                    rankings[cap_id] = {
                        "total_usage": 0,
                        "agent_count": 0,
                        "weighted_success": 0.0,
                        "by_agent": {},
                    }

                rankings[cap_id]["total_usage"] += usage
                rankings[cap_id]["agent_count"] += 1
                rankings[cap_id]["weighted_success"] += snap.success_rate
                rankings[cap_id]["by_agent"][snap.agent_id] = {
                    "usage_count": usage,
                    "success_rate": snap.success_rate,
                }

        # compute aggregate success_rate
        for cap_id, data in rankings.items():
            n = data["agent_count"]
            data["success_rate"] = data["weighted_success"] / n if n > 0 else 0.0
            del data["weighted_success"]

        return rankings

    def _extract_failure_signatures(self, snapshots: list) -> list:
        """Common failure conditions: recurring error types across agents."""
        error_counts: dict = {}
        for snap in snapshots:
            for failure in snap.recent_failures:
                err = failure.get("error", "") if isinstance(failure, dict) else str(failure)
                # coarse classification
                if "timeout" in err.lower() or "timed out" in err.lower():
                    error_counts["timeout"] = error_counts.get("timeout", 0) + 1
                elif "permission" in err.lower() or "denied" in err.lower():
                    error_counts["permission_denied"] = error_counts.get("permission_denied", 0) + 1
                elif "not found" in err.lower() or "missing" in err.lower():
                    error_counts["resource_missing"] = error_counts.get("resource_missing", 0) + 1
                else:
                    error_counts["other"] = error_counts.get("other", 0) + 1

        return [
            {"error_type": etype, "occurrence_count": count}
            for etype, count in sorted(error_counts.items(), key=lambda x: x[1], reverse=True)
        ]

    def _compute_coverage(self, snapshots: list) -> float:
        """
        Rough measure of how complete swarm knowledge is.
        Based on: agent count, memory density, capability diversity.
        """
        if not snapshots:
            return 0.0

        n = len(snapshots)
        # more agents → higher coverage (diminishing returns)
        agent_factor = min(n / 5.0, 1.0)

        # average memory count per agent (normalized)
        avg_memories = sum(s.memory_count for s in snapshots) / n
        memory_factor = min(avg_memories / 10.0, 1.0)

        # capability diversity
        all_caps = set()
        for s in snapshots:
            for c in s.top_capabilities:
                cap_id = c["capability_id"] if isinstance(c, dict) else str(c)
                all_caps.add(cap_id)
        cap_factor = min(len(all_caps) / 5.0, 1.0)

        return round((agent_factor * 0.4 + memory_factor * 0.3 + cap_factor * 0.3), 3)

    # ------------------------------------------------------------------ #
    #  Persistence                                                         #
    # ------------------------------------------------------------------ #

    def _persist(self, kb: SwarmKnowledgeBase) -> None:
        """Store synthesis snapshot to disk."""
        filename = f"synthesis_{int(kb.synthesized_at)}.json"
        path = self._storage_path / filename
        with self._lock:
            with open(path, "w") as f:
                json.dump(asdict(kb), f, indent=2)

    def load_latest(self) -> Optional[SwarmKnowledgeBase]:
        """Load the most recent synthesis from disk."""
        files = sorted(self._storage_path.glob("synthesis_*.json"), reverse=True)
        if not files:
            return None
        try:
            data = json.loads(files[0].read_text())
            return SwarmKnowledgeBase(**data)
        except Exception:
            return None

    def _empty_synthesis(self, synthesis_id: str, agent_ids: list) -> SwarmKnowledgeBase:
        return SwarmKnowledgeBase(
            synthesis_id=synthesis_id,
            synthesized_at=time.time(),
            agent_ids=agent_ids,
            patterns=[],
            topic_map={},
            capability_rankings={},
            failure_signatures=[],
            coverage_score=0.0,
        )
