"""
Agent Introspection — AgentOS v3.6.0.

Meta-Intelligence Phase 6, primitive 1.

Agents can examine themselves and each other. Without introspection, meta-learning
is blind — there is nothing to reason about. This module answers three questions:

  1. What does an agent know?          → query_knowledge(agent_id)
  2. Why did an agent fail?            → explain_failure(agent_id, task_id)
  3. How do two agents differ?         → compare(agent_id_a, agent_id_b)
  4. What would an agent need to know? → knowledge_gap(agent_id, task_description)

Design constraints:
- Read-only. Introspection never modifies agent state.
- Works on execution history (real records), not simulated data.
- explain_failure does causal chain tracing: it follows the audit log backward
  from a failure to the root cause, not just "the task failed".
- compare() produces a diff, not a score. Scores hide information.

Storage:
  /agentOS/memory/introspection/
    {agent_id}/
      snapshots.jsonl       # point-in-time knowledge snapshots
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

INTROSPECTION_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "introspection"
DEFAULT_VECTOR_DIM = 768


@dataclass
class KnowledgeSnapshot:
    """What an agent knows at a point in time."""
    snapshot_id: str
    agent_id: str
    timestamp: float

    # Semantic memory
    memory_count: int
    memory_topics: list         # top themes extracted from stored thoughts
    recent_thoughts: list       # last N thoughts stored

    # Execution history
    total_executions: int
    success_rate: float
    top_capabilities: list      # most-used capability_ids with usage count
    recent_failures: list       # last N failed execution summaries

    # Audit profile
    op_distribution: dict       # operation → count
    total_tokens: int


@dataclass
class FailureExplanation:
    """Why an agent failed on a specific task."""
    explanation_id: str
    agent_id: str
    task_id: str                # execution_id or audit entry id
    timestamp: float

    root_cause: str             # best-effort diagnosis
    causal_chain: list          # sequence of events leading to failure
    contributing_factors: list  # other things that made it more likely
    missing_knowledge: list     # what the agent lacked
    similar_past_failures: list # semantically similar failures this agent had before
    suggested_remedies: list    # what could prevent this next time


@dataclass
class AgentDiff:
    """Difference between two agents' knowledge and capability profiles."""
    diff_id: str
    agent_a: str
    agent_b: str
    timestamp: float

    # Memory differences
    a_only_topics: list         # topics A knows that B doesn't
    b_only_topics: list         # topics B knows that A doesn't
    shared_topics: list         # topics both know

    # Execution differences
    a_success_rate: float
    b_success_rate: float
    a_strengths: list           # capabilities A succeeds at more than B
    b_strengths: list           # capabilities B succeeds at more than A

    # Summary
    overlap_score: float        # 0.0 = completely different, 1.0 = identical knowledge


@dataclass
class KnowledgeGap:
    """What an agent would need to know to handle a task."""
    gap_id: str
    agent_id: str
    task_description: str
    timestamp: float

    relevant_knowledge: list    # what the agent already has that applies
    missing_knowledge: list     # what the agent lacks
    suggested_capabilities: list  # capabilities that would fill the gap
    readiness_score: float      # 0.0 = not ready, 1.0 = fully equipped


class AgentIntrospector:
    """
    Read-only lens into agent knowledge and execution history.

    Composes SemanticMemory, ExecutionEngine, and AuditLog to produce
    structured answers to meta-level questions.
    """

    def __init__(self, semantic_memory=None, execution_engine=None,
                 audit_log=None, capability_graph=None, storage_path: Path = None):
        self._memory = semantic_memory
        self._engine = execution_engine
        self._audit = audit_log
        self._capability_graph = capability_graph
        self._lock = threading.Lock()
        self._embedder = None
        if EMBEDDING_AVAILABLE:
            try:
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                self._embedder = None
        self._storage_path = storage_path or INTROSPECTION_PATH
        self._storage_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    #  Core API                                                            #
    # ------------------------------------------------------------------ #

    def query_knowledge(self, agent_id: str) -> KnowledgeSnapshot:
        """What does this agent know right now?"""
        snapshot_id = str(uuid.uuid4())[:8]

        # --- semantic memory ---
        memory_count = 0
        memory_topics = []
        recent_thoughts = []
        if self._memory is not None:
            records = self._memory.list_agent_memories(agent_id, limit=100)
            memory_count = len(records)
            recent_thoughts = [
                r.thought if hasattr(r, "thought") else r.get("thought", "")
                for r in records[-5:]
            ]
            memory_topics = self._extract_topics([
                r.thought if hasattr(r, "thought") else r.get("thought", "")
                for r in records
            ])

        # --- execution history ---
        total_executions = 0
        success_rate = 0.0
        top_capabilities = []
        recent_failures = []
        if self._engine is not None:
            stats = self._engine.get_stats(agent_id)
            total_executions = stats.get("total_executions", 0)
            success_rate = stats.get("success_rate", 0.0)

            history = self._engine.get_execution_history(agent_id, limit=200)
            cap_counts: dict = {}
            for entry in history:
                cap_id = entry.capability_id if hasattr(entry, "capability_id") else entry.get("capability_id", "unknown")
                cap_counts[cap_id] = cap_counts.get(cap_id, 0) + 1
                status = entry.status if hasattr(entry, "status") else entry.get("status", "")
                if status in ("failed", "timeout"):
                    cap = entry.capability_id if hasattr(entry, "capability_id") else entry.get("capability_id", "")
                    err = entry.result if hasattr(entry, "result") else entry.get("result", {})
                    if isinstance(err, dict):
                        err = err.get("error", str(err))
                    elif err is None:
                        err = getattr(entry, "error", status)
                    recent_failures.append({"capability": cap, "error": str(err)[:120]})

            top_capabilities = sorted(cap_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            top_capabilities = [{"capability_id": c, "usage_count": n} for c, n in top_capabilities]
            recent_failures = recent_failures[-5:]

        # --- audit profile ---
        op_distribution = {}
        total_tokens = 0
        if self._audit is not None:
            astats = self._audit.stats(agent_id)
            op_distribution = astats.get("op_counts", {})
            total_tokens = astats.get("total_tokens", 0)

        snap = KnowledgeSnapshot(
            snapshot_id=snapshot_id,
            agent_id=agent_id,
            timestamp=time.time(),
            memory_count=memory_count,
            memory_topics=memory_topics,
            recent_thoughts=recent_thoughts,
            total_executions=total_executions,
            success_rate=success_rate,
            top_capabilities=top_capabilities,
            recent_failures=recent_failures,
            op_distribution=op_distribution,
            total_tokens=total_tokens,
        )
        self._persist_snapshot(snap)
        return snap

    def explain_failure(self, agent_id: str, task_id: str) -> FailureExplanation:
        """
        Why did this agent fail on this task?

        Traces backward through the audit log and execution history to build
        a causal chain. Returns structured diagnosis, not just the error message.
        """
        expl_id = str(uuid.uuid4())[:8]

        causal_chain = []
        contributing_factors = []
        missing_knowledge = []
        similar_past_failures = []
        root_cause = "unknown"

        # --- find the failure in execution history ---
        failed_entry = None
        if self._engine is not None:
            history = self._engine.get_execution_history(agent_id, limit=500)
            for entry in history:
                eid = entry.execution_id if hasattr(entry, "execution_id") else entry.get("execution_id", "")
                cap_id = entry.capability_id if hasattr(entry, "capability_id") else entry.get("capability_id", "")
                status = entry.status if hasattr(entry, "status") else entry.get("status", "")
                if eid == task_id or cap_id == task_id:
                    if status in ("failed", "timeout"):
                        failed_entry = entry
                        break

        if failed_entry is not None:
            result = failed_entry.result if hasattr(failed_entry, "result") else failed_entry.get("result", {})
            status_val = failed_entry.status if hasattr(failed_entry, "status") else failed_entry.get("status", "")
            if isinstance(result, dict):
                error_msg = result.get("error", str(result))
            elif result is None:
                # timeout case: status field has the reason, check engine error attr too
                error_msg = getattr(failed_entry, "error", status_val)
                if not error_msg or error_msg == status_val:
                    error_msg = f"execution {status_val}: timed out"
            else:
                error_msg = str(result)

            cap_id = failed_entry.capability_id if hasattr(failed_entry, "capability_id") else failed_entry.get("capability_id", "")
            duration = failed_entry.duration_ms if hasattr(failed_entry, "duration_ms") else failed_entry.get("duration_ms", 0)
            ts = failed_entry.timestamp if hasattr(failed_entry, "timestamp") else failed_entry.get("timestamp", 0)

            causal_chain.append({
                "step": 1,
                "event": "execution_failed",
                "capability": cap_id,
                "error": error_msg[:200],
                "timestamp": ts,
            })

            # classify root cause from error string
            root_cause = self._classify_failure(error_msg, cap_id)

            # check audit log for events around the same time
            if self._audit is not None:
                window_start = ts - 5.0
                window_end = ts + 1.0
                nearby = self._audit.query(
                    agent_id=agent_id,
                    since=window_start,
                    until=window_end,
                    limit=20,
                )
                for ae in nearby:
                    rc = ae.result_code if hasattr(ae, "result_code") else ae.get("result_code", "ok")
                    if rc in ("denied", "error", "budget_exceeded"):
                        op = ae.operation if hasattr(ae, "operation") else ae.get("operation", "")
                        contributing_factors.append({
                            "operation": op,
                            "result_code": rc,
                            "timestamp": ae.timestamp if hasattr(ae, "timestamp") else ae.get("timestamp", 0),
                        })

            # find similar past failures via semantic memory
            if self._memory is not None:
                query = f"failure in {cap_id}: {error_msg[:100]}"
                similar = self._memory.search(agent_id, query, top_k=3, similarity_threshold=0.5)
                for rec in similar:
                    thought = rec.get("thought", "") if isinstance(rec, dict) else getattr(rec, "thought", "")
                    if thought:
                        similar_past_failures.append(thought[:150])

            # determine what knowledge could have prevented this
            missing_knowledge = self._infer_missing_knowledge(root_cause, cap_id)

        suggested_remedies = self._suggest_remedies(root_cause, missing_knowledge)

        expl = FailureExplanation(
            explanation_id=expl_id,
            agent_id=agent_id,
            task_id=task_id,
            timestamp=time.time(),
            root_cause=root_cause,
            causal_chain=causal_chain,
            contributing_factors=contributing_factors,
            missing_knowledge=missing_knowledge,
            similar_past_failures=similar_past_failures,
            suggested_remedies=suggested_remedies,
        )
        return expl

    def compare(self, agent_id_a: str, agent_id_b: str) -> AgentDiff:
        """
        Diff two agents' knowledge and capability profiles.

        Returns concrete differences, not a scalar score.
        """
        diff_id = str(uuid.uuid4())[:8]

        snap_a = self.query_knowledge(agent_id_a)
        snap_b = self.query_knowledge(agent_id_b)

        # --- topic overlap ---
        topics_a = set(snap_a.memory_topics)
        topics_b = set(snap_b.memory_topics)
        shared = list(topics_a & topics_b)
        a_only = list(topics_a - topics_b)
        b_only = list(topics_b - topics_a)

        total_unique = len(topics_a | topics_b)
        overlap_score = len(shared) / total_unique if total_unique > 0 else 1.0

        # --- capability strengths ---
        caps_a = {c["capability_id"]: c["usage_count"] for c in snap_a.top_capabilities}
        caps_b = {c["capability_id"]: c["usage_count"] for c in snap_b.top_capabilities}

        a_strengths = [
            c for c in caps_a if caps_a.get(c, 0) > caps_b.get(c, 0)
        ]
        b_strengths = [
            c for c in caps_b if caps_b.get(c, 0) > caps_a.get(c, 0)
        ]

        diff = AgentDiff(
            diff_id=diff_id,
            agent_a=agent_id_a,
            agent_b=agent_id_b,
            timestamp=time.time(),
            a_only_topics=a_only,
            b_only_topics=b_only,
            shared_topics=shared,
            a_success_rate=snap_a.success_rate,
            b_success_rate=snap_b.success_rate,
            a_strengths=a_strengths,
            b_strengths=b_strengths,
            overlap_score=overlap_score,
        )
        return diff

    def knowledge_gap(self, agent_id: str, task_description: str) -> KnowledgeGap:
        """
        What would this agent need to know to handle this task?

        Searches the agent's semantic memory for relevant knowledge, then
        identifies what's missing and which capabilities could fill the gap.
        """
        gap_id = str(uuid.uuid4())[:8]

        relevant_knowledge = []
        missing_knowledge = []
        suggested_capabilities = []
        readiness_score = 0.0

        # what does the agent already have that's relevant?
        if self._memory is not None:
            relevant = self._memory.search(agent_id, task_description, top_k=5, similarity_threshold=0.4)
            relevant_knowledge = [
                {"thought": r.get("thought", "") if isinstance(r, dict) else getattr(r, "thought", ""),
                 "similarity": r.get("similarity", 0.0) if isinstance(r, dict) else 0.0}
                for r in relevant
            ]
            # knowledge is "present" if we found high-similarity matches
            has_relevant = any(k.get("similarity", 0) > 0.65 for k in relevant_knowledge)
            if not has_relevant:
                missing_knowledge.append(f"No prior knowledge matching: '{task_description[:80]}'")

        # what capabilities exist that match the task?
        if self._capability_graph is not None:
            caps = self._capability_graph.find(task_description, top_k=5)
            suggested_capabilities = [
                {"capability_id": c.id if hasattr(c, "id") else str(c),
                 "name": c.name if hasattr(c, "name") else str(c),
                 "similarity": float(sim)}
                for c, sim in caps
            ]
            if not suggested_capabilities:
                missing_knowledge.append(f"No capability found for: '{task_description[:80]}'")

        # readiness: mix of memory relevance and capability availability
        mem_score = min(len(relevant_knowledge) / 3.0, 1.0)
        cap_score = 1.0 if suggested_capabilities else 0.0
        readiness_score = (mem_score + cap_score) / 2.0

        gap = KnowledgeGap(
            gap_id=gap_id,
            agent_id=agent_id,
            task_description=task_description,
            timestamp=time.time(),
            relevant_knowledge=relevant_knowledge,
            missing_knowledge=missing_knowledge,
            suggested_capabilities=suggested_capabilities,
            readiness_score=readiness_score,
        )
        return gap

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _extract_topics(self, thoughts: list) -> list:
        """
        Extract coarse topic labels from a list of thought strings.
        No LLM — uses keyword frequency to avoid inference cost.
        """
        if not thoughts:
            return []

        # simple bag-of-words topic extraction
        stopwords = {
            "the", "a", "an", "is", "was", "are", "were", "be", "been",
            "to", "of", "in", "on", "at", "for", "with", "by", "from",
            "and", "or", "but", "not", "it", "this", "that", "i", "we",
            "agent", "agents", "task", "tasks", "system",
        }
        freq: dict = {}
        for thought in thoughts:
            for word in thought.lower().split():
                word = word.strip(".,;:!?\"'()")
                if len(word) > 3 and word not in stopwords:
                    freq[word] = freq.get(word, 0) + 1

        sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        return [w for w, _ in sorted_words[:8]]

    def _classify_failure(self, error_msg: str, capability_id: str) -> str:
        """Classify a failure error string into a root cause category."""
        err_lower = error_msg.lower()

        if "timeout" in err_lower or "timed out" in err_lower:
            return "timeout: capability exceeded time limit"
        if "permission" in err_lower or "denied" in err_lower or "forbidden" in err_lower:
            return "permission_denied: agent lacks required access"
        if "not found" in err_lower or "no such" in err_lower or "missing" in err_lower:
            return f"resource_missing: required resource not found for {capability_id}"
        if "budget" in err_lower or "token" in err_lower or "limit" in err_lower:
            return "budget_exhausted: agent ran out of token budget"
        if "connection" in err_lower or "network" in err_lower or "unreachable" in err_lower:
            return "connectivity: failed to reach required service"
        if "assertion" in err_lower or "assert" in err_lower:
            return "assertion_failed: result did not meet expected invariant"
        if "exception" in err_lower or "error" in err_lower or "traceback" in err_lower:
            return f"execution_error: unhandled exception in {capability_id}"

        return f"unknown_failure: {error_msg[:80]}"

    def _infer_missing_knowledge(self, root_cause: str, capability_id: str) -> list:
        """Based on root cause, infer what knowledge would have prevented it."""
        missing = []
        if "permission" in root_cause:
            missing.append(f"Access requirements for capability '{capability_id}'")
        if "resource_missing" in root_cause:
            missing.append(f"Location/existence check before using '{capability_id}'")
        if "budget_exhausted" in root_cause:
            missing.append("Token budget awareness before initiating expensive operations")
        if "timeout" in root_cause:
            missing.append(f"Expected duration range for '{capability_id}' to set appropriate limits")
        if "connectivity" in root_cause:
            missing.append("Network availability check or fallback strategy")
        if not missing:
            missing.append(f"Correct usage pattern for capability '{capability_id}'")
        return missing

    def _suggest_remedies(self, root_cause: str, missing_knowledge: list) -> list:
        """Translate root cause + missing knowledge into actionable suggestions."""
        remedies = []
        if "permission" in root_cause:
            remedies.append("Verify agent role has required capability before attempting")
        if "resource_missing" in root_cause:
            remedies.append("Add existence check before depending on resource")
        if "budget_exhausted" in root_cause:
            remedies.append("Check token budget via heap_stats before large operations; compress or gc if low")
        if "timeout" in root_cause:
            remedies.append("Use streaming or partial results for long-running capabilities")
        if "connectivity" in root_cause:
            remedies.append("Implement retry with backoff; check service health before calling")
        for mk in missing_knowledge:
            remedies.append(f"Store in semantic memory: '{mk}'")
        return remedies[:5]

    def _persist_snapshot(self, snap: KnowledgeSnapshot) -> None:
        """Append snapshot to per-agent storage."""
        agent_dir = self._storage_path / snap.agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        snap_path = agent_dir / "snapshots.jsonl"
        with self._lock:
            with open(snap_path, "a") as f:
                f.write(json.dumps(asdict(snap)) + "\n")

    def list_snapshots(self, agent_id: str, limit: int = 10) -> list:
        """Return the most recent knowledge snapshots for an agent."""
        snap_path = self._storage_path / agent_id / "snapshots.jsonl"
        if not snap_path.exists():
            return []
        lines = snap_path.read_text().strip().splitlines()
        recent = lines[-limit:]
        result = []
        for line in recent:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result
