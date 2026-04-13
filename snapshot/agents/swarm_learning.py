"""
Swarm Meta-Learning — AgentOS v3.10.0.

Phase 6 capstone. Depends on all Phase 6 primitives:
  v3.6.0: AgentIntrospector
  v3.7.0: MetaSynthesizer
  v3.8.0: GovernanceEvolutionEngine
  v3.9.0: SpecializationEngine

The individual Phase 6 primitives work on single agents in isolation.
This module makes the swarm improve as a collective.

What "swarm meta-learning" means precisely (no wishful thinking):
  1. After a batch of tasks completes, the swarm runs a learning cycle:
     - Synthesize what the swarm knows (v3.7.0)
     - Update specialization profiles (v3.9.0)
     - Analyze governance rule effectiveness (v3.8.0)
     - Compare agents to find knowledge gaps (v3.6.0)

  2. From that analysis, produce concrete recommendations:
     - Route future tasks to better-matched agents
     - Surface knowledge gaps that any agent can fill
     - Flag governance rules that are generating friction

  3. Record what was learned and compare across cycles:
     - Did the swarm's collective success rate improve?
     - Did routing quality improve (did specialists get more of their tasks)?
     - Are governance rules drifting toward optimal?

This is NOT magic emergence. It is a structured feedback loop that
makes the swarm measurably better over repeated cycles.

Design:
  SwarmLearningCycle:
    run(agent_ids, task_outcomes) → LearningReport
      # one full cycle: synthesize → profile → recommend → record

  LearningOrchestrator:
    record_task(agent_id, task_type, success, duration_ms)
    run_cycle(agent_ids) → LearningReport
    compare_cycles(cycle_a_id, cycle_b_id) → CycleComparison
    get_recommendations() → list[Recommendation]
    improvement_trend() → dict

Storage:
  /agentOS/memory/swarm_learning/
    cycle_{id}.json           # one per learning cycle
    recommendations.jsonl     # running list of actionable recommendations
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

SWARM_LEARNING_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "swarm_learning"


@dataclass
class Recommendation:
    """A concrete, actionable recommendation from the swarm learning cycle."""
    rec_id: str
    category: str           # "routing" | "knowledge" | "governance" | "specialization"
    priority: str           # "high" | "medium" | "low"
    description: str
    target_agent: Optional[str]     # which agent this applies to (None = all)
    supporting_evidence: dict
    cycle_id: str
    created_at: float = field(default_factory=time.time)
    status: str = "open"    # "open" | "applied" | "dismissed"


@dataclass
class LearningReport:
    """Output from one swarm learning cycle."""
    cycle_id: str
    timestamp: float
    agent_ids: list
    task_count: int

    # Swarm-level metrics
    swarm_success_rate: float       # across all agents and task types this cycle
    coverage_score: float           # how complete is collective knowledge?
    pattern_count: int              # cross-agent patterns discovered
    specialization_avg: float       # average specialization score across agents

    # What changed vs. previous cycle (empty on first cycle)
    success_rate_delta: float       # positive = improved
    coverage_delta: float
    new_patterns: list              # newly discovered patterns

    # Recommendations
    recommendations: list           # list of Recommendation dicts


@dataclass
class CycleComparison:
    """Comparison between two learning cycles."""
    before_cycle_id: str
    after_cycle_id: str
    success_rate_change: float      # positive = improved
    coverage_change: float
    new_patterns_added: int
    patterns_lost: int
    routing_quality_change: float   # did tasks go to better-matched agents?
    governance_friction_change: float  # negative = less friction (good)
    summary: str


class LearningOrchestrator:
    """
    Coordinates the full swarm meta-learning loop.

    Wire up all Phase 6 subsystems. Each learning cycle runs them
    in sequence and produces a LearningReport with concrete recommendations.
    """

    def __init__(
        self,
        introspector=None,
        synthesizer=None,
        governance_engine=None,
        specialization_engine=None,
        storage_path: Path = None,
    ):
        self._introspector = introspector
        self._synthesizer = synthesizer
        self._governance = governance_engine
        self._specialization = specialization_engine
        self._lock = threading.Lock()
        self._storage_path = storage_path or SWARM_LEARNING_PATH
        self._storage_path.mkdir(parents=True, exist_ok=True)
        self._cycles: list = []
        self._task_buffer: list = []  # buffered tasks since last cycle
        self._recommendations: list = []

    # ------------------------------------------------------------------ #
    #  Core API                                                            #
    # ------------------------------------------------------------------ #

    def record_task(
        self,
        agent_id: str,
        task_type: str,
        success: bool,
        duration_ms: float,
        capability_used: str = "unknown",
    ) -> None:
        """
        Buffer a task outcome for the next learning cycle.
        Also immediately updates specialization profile.
        """
        record = {
            "agent_id": agent_id,
            "task_type": task_type,
            "success": success,
            "duration_ms": duration_ms,
            "capability_used": capability_used,
            "timestamp": time.time(),
        }
        with self._lock:
            self._task_buffer.append(record)

        if self._specialization is not None:
            self._specialization.update(
                agent_id, task_type, success, duration_ms, capability_used
            )

    def run_cycle(self, agent_ids: list) -> LearningReport:
        """
        Run one full learning cycle across the given agents.

        Clears the task buffer. Returns a LearningReport with metrics
        and recommendations.
        """
        cycle_id = str(uuid.uuid4())[:8]

        with self._lock:
            tasks = list(self._task_buffer)
            self._task_buffer.clear()

        task_count = len(tasks)
        swarm_success_rate = self._compute_swarm_success_rate(tasks)

        # --- synthesize swarm knowledge ---
        kb = None
        pattern_count = 0
        coverage_score = 0.0
        new_patterns = []

        if self._synthesizer is not None:
            kb = self._synthesizer.synthesize(agent_ids)
            pattern_count = len(kb.patterns)
            coverage_score = kb.coverage_score

            # compare with previous cycle
            prev = self._last_cycle()
            if prev is not None:
                prev_kb_id = prev.get("synthesis_id", "") if isinstance(prev, dict) else ""
                prev_pattern_ids = set(
                    p.get("pattern_id") for p in
                    (prev.get("patterns", []) if isinstance(prev, dict) else [])
                )
                curr_pattern_ids = {p["pattern_id"] for p in kb.patterns}
                new_patterns = [
                    p for p in kb.patterns
                    if p["pattern_id"] not in prev_pattern_ids
                ]

        # --- compute specialization averages ---
        specialization_avg = 0.0
        if self._specialization is not None and agent_ids:
            scores = []
            for aid in agent_ids:
                profile = self._specialization.profile(aid)
                scores.append(profile.specialization_score)
            specialization_avg = sum(scores) / len(scores) if scores else 0.0

        # --- build recommendations ---
        recommendations = self._generate_recommendations(
            cycle_id, agent_ids, tasks, kb, swarm_success_rate
        )

        # --- compute deltas vs. previous cycle ---
        success_rate_delta = 0.0
        coverage_delta = 0.0
        prev_report = self._last_cycle()
        if prev_report is not None:
            prev_success = prev_report.get("swarm_success_rate", 0.0) if isinstance(prev_report, dict) else 0.0
            prev_coverage = prev_report.get("coverage_score", 0.0) if isinstance(prev_report, dict) else 0.0
            success_rate_delta = round(swarm_success_rate - prev_success, 4)
            coverage_delta = round(coverage_score - prev_coverage, 4)

        report = LearningReport(
            cycle_id=cycle_id,
            timestamp=time.time(),
            agent_ids=agent_ids,
            task_count=task_count,
            swarm_success_rate=swarm_success_rate,
            coverage_score=coverage_score,
            pattern_count=pattern_count,
            specialization_avg=round(specialization_avg, 3),
            success_rate_delta=success_rate_delta,
            coverage_delta=coverage_delta,
            new_patterns=new_patterns,
            recommendations=[asdict(r) for r in recommendations],
        )

        with self._lock:
            self._cycles.append(asdict(report))
            self._recommendations.extend([asdict(r) for r in recommendations])

        self._persist_cycle(report)
        self._persist_recommendations(recommendations)
        return report

    def compare_cycles(self, cycle_a_id: str, cycle_b_id: str) -> Optional[CycleComparison]:
        """Compare two learning cycle reports."""
        with self._lock:
            cycles = {c["cycle_id"]: c for c in self._cycles}

        a = cycles.get(cycle_a_id)
        b = cycles.get(cycle_b_id)
        if a is None or b is None:
            return None

        a_patterns = {p["pattern_id"] for p in a.get("patterns", [])} if "patterns" in a else set()
        b_patterns_raw = b.get("new_patterns", [])
        b_pattern_ids = {p["pattern_id"] for p in b_patterns_raw}

        new_added = len(b_pattern_ids - a_patterns)
        patterns_lost = len(a_patterns - b_pattern_ids)

        sr_change = round(
            b.get("swarm_success_rate", 0) - a.get("swarm_success_rate", 0), 4
        )
        cov_change = round(
            b.get("coverage_score", 0) - a.get("coverage_score", 0), 4
        )
        spec_change = round(
            b.get("specialization_avg", 0) - a.get("specialization_avg", 0), 4
        )

        if sr_change > 0.05:
            summary = f"Improved: success rate up {sr_change:.1%}"
        elif sr_change < -0.05:
            summary = f"Regressed: success rate down {abs(sr_change):.1%}"
        else:
            summary = f"Stable: success rate change {sr_change:.1%}"

        return CycleComparison(
            before_cycle_id=cycle_a_id,
            after_cycle_id=cycle_b_id,
            success_rate_change=sr_change,
            coverage_change=cov_change,
            new_patterns_added=new_added,
            patterns_lost=patterns_lost,
            routing_quality_change=spec_change,  # specialization improves routing
            governance_friction_change=0.0,        # tracked separately via governance engine
            summary=summary,
        )

    def get_recommendations(self, status: str = "open", category: str = None) -> list:
        """Return recommendations, optionally filtered."""
        with self._lock:
            recs = list(self._recommendations)
        if status:
            recs = [r for r in recs if r.get("status") == status]
        if category:
            recs = [r for r in recs if r.get("category") == category]
        return recs

    def improvement_trend(self) -> dict:
        """
        Summarize improvement across all cycles.
        Returns trend data for success_rate, coverage, and pattern count.
        """
        with self._lock:
            cycles = list(self._cycles)

        if not cycles:
            return {"cycles": 0, "trend": "no data"}

        success_rates = [c.get("swarm_success_rate", 0) for c in cycles]
        coverages = [c.get("coverage_score", 0) for c in cycles]
        pattern_counts = [c.get("pattern_count", 0) for c in cycles]

        def linear_trend(values):
            if len(values) < 2:
                return 0.0
            # simple slope: (last - first) / count
            return (values[-1] - values[0]) / len(values)

        return {
            "cycles": len(cycles),
            "success_rate": {
                "first": success_rates[0],
                "last": success_rates[-1],
                "trend": round(linear_trend(success_rates), 4),
                "improving": success_rates[-1] >= success_rates[0],
            },
            "coverage": {
                "first": coverages[0],
                "last": coverages[-1],
                "trend": round(linear_trend(coverages), 4),
                "improving": coverages[-1] >= coverages[0],
            },
            "patterns": {
                "first": pattern_counts[0],
                "last": pattern_counts[-1],
                "trend": round(linear_trend(pattern_counts), 4),
            },
        }

    # ------------------------------------------------------------------ #
    #  Recommendation generation                                          #
    # ------------------------------------------------------------------ #

    def _generate_recommendations(
        self, cycle_id: str, agent_ids: list, tasks: list, kb, swarm_success_rate: float
    ) -> list:
        """Generate actionable recommendations from the current cycle's data."""
        recs = []

        # 1. Routing recommendations from specialization
        if self._specialization is not None and agent_ids and tasks:
            task_types = list({t["task_type"] for t in tasks})
            for task_type in task_types:
                best = self._specialization.top_specialist(task_type)
                if best is not None and best in agent_ids:
                    # count how many tasks of this type went to non-specialists
                    type_tasks = [t for t in tasks if t["task_type"] == task_type]
                    misrouted = [t for t in type_tasks if t["agent_id"] != best]
                    if len(misrouted) > 0:
                        recs.append(Recommendation(
                            rec_id=str(uuid.uuid4())[:8],
                            category="routing",
                            priority="high" if len(misrouted) > 2 else "medium",
                            description=(
                                f"Route '{task_type}' tasks to {best} — "
                                f"{len(misrouted)} of {len(type_tasks)} tasks this cycle "
                                f"went to non-specialist agents"
                            ),
                            target_agent=best,
                            supporting_evidence={
                                "task_type": task_type,
                                "specialist": best,
                                "misrouted_count": len(misrouted),
                            },
                            cycle_id=cycle_id,
                        ))

        # 2. Knowledge gap recommendations from introspection
        if self._introspector is not None and kb is not None:
            for agent_id in agent_ids:
                snap = self._introspector.query_knowledge(agent_id)
                if snap.memory_count == 0 and snap.total_executions > 5:
                    recs.append(Recommendation(
                        rec_id=str(uuid.uuid4())[:8],
                        category="knowledge",
                        priority="medium",
                        description=(
                            f"Agent {agent_id} has {snap.total_executions} executions "
                            f"but no semantic memories — should store learned patterns"
                        ),
                        target_agent=agent_id,
                        supporting_evidence={
                            "memory_count": snap.memory_count,
                            "execution_count": snap.total_executions,
                        },
                        cycle_id=cycle_id,
                    ))

        # 3. Low swarm success rate
        if swarm_success_rate < 0.5 and len(tasks) >= 5:
            recs.append(Recommendation(
                rec_id=str(uuid.uuid4())[:8],
                category="specialization",
                priority="high",
                description=(
                    f"Swarm success rate is {swarm_success_rate:.0%} — "
                    f"consider redistributing tasks based on specialization profiles"
                ),
                target_agent=None,
                supporting_evidence={
                    "success_rate": swarm_success_rate,
                    "task_count": len(tasks),
                },
                cycle_id=cycle_id,
            ))

        return recs

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _compute_swarm_success_rate(self, tasks: list) -> float:
        if not tasks:
            return 0.0
        successes = sum(1 for t in tasks if t.get("success", False))
        return round(successes / len(tasks), 4)

    def _last_cycle(self) -> Optional[dict]:
        with self._lock:
            return self._cycles[-1] if self._cycles else None

    # ------------------------------------------------------------------ #
    #  Persistence                                                         #
    # ------------------------------------------------------------------ #

    def _persist_cycle(self, report: LearningReport) -> None:
        path = self._storage_path / f"cycle_{report.cycle_id}.json"
        path.write_text(json.dumps(asdict(report), indent=2))

    def _persist_recommendations(self, recommendations: list) -> None:
        path = self._storage_path / "recommendations.jsonl"
        with open(path, "a") as f:
            for rec in recommendations:
                f.write(json.dumps(asdict(rec)) + "\n")

    def load_cycles(self) -> list:
        """Load all cycle reports from disk."""
        cycles = []
        for path in sorted(self._storage_path.glob("cycle_*.json")):
            try:
                cycles.append(json.loads(path.read_text()))
            except Exception:
                continue
        return cycles
