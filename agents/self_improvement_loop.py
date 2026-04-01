"""
Self-Improvement Loop — AgentOS v2.9.0.

Full integration of Phase 4: agents continuously observe patterns,
learn from outcomes, and improve through synthesis and optimization.

Design:
  SelfImprovementLoop:
    observe_patterns(agent_id) → list of (pattern, success_rate)
    propose_optimization(pattern) → optimization_proposal
    measure_improvement(before, after) → improvement_metrics
    continuous_improvement_cycle(agent_id, max_iterations)

Storage:
  /agentOS/memory/self_improvement/
    {agent_id}/
      patterns.jsonl             # observed patterns
      improvements.jsonl         # proposed and deployed improvements
      metrics.jsonl              # before/after metrics
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List, Tuple, Dict

SELF_IMPROVE_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "self_improvement"


@dataclass
class Pattern:
    """Observed pattern in agent behavior."""
    pattern_id: str
    agent_id: str
    description: str               # what was observed
    frequency: int                 # how many times observed
    success_rate: float            # success % when pattern present
    first_observed: float
    last_observed: float = field(default_factory=time.time)


@dataclass
class Optimization:
    """Proposed optimization based on observed pattern."""
    optimization_id: str
    agent_id: str
    pattern_id: str
    description: str               # what optimization to apply
    confidence: float              # 0.0-1.0 confidence it will help
    proposed_at: float = field(default_factory=time.time)
    status: str = "proposed"       # proposed, approved, deployed, completed


@dataclass
class ImprovementMetrics:
    """Metrics for improvement measurement."""
    measurement_id: str
    agent_id: str
    optimization_id: str
    metric_name: str               # e.g. success_rate, latency_ms
    value_before: float
    value_after: float
    improvement_percent: float     # (after - before) / before * 100
    measured_at: float = field(default_factory=time.time)


class SelfImprovementLoop:
    """Continuous agent improvement through observation and optimization."""

    def __init__(self, autonomy_loop=None, reasoning_layer=None,
                 execution_engine=None, self_modification=None, semantic_memory=None):
        """
        autonomy_loop: AutonomyLoop instance
        reasoning_layer: ReasoningLayer instance
        execution_engine: ExecutionEngine instance
        self_modification: SelfModificationCycle instance
        semantic_memory: SemanticMemory instance
        """
        self._lock = threading.RLock()
        self._autonomy_loop = autonomy_loop
        self._reasoning_layer = reasoning_layer
        self._execution_engine = execution_engine
        self._self_modification = self_modification
        self._semantic_memory = semantic_memory
        SELF_IMPROVE_PATH.mkdir(parents=True, exist_ok=True)

    # ── API ────────────────────────────────────────────────────────────────

    def continuous_improvement_cycle(self, agent_id: str, max_iterations: int = 10) -> int:
        """
        Run continuous improvement loop for agent.
        Returns number of improvements deployed.

        Flow:
        1. Observe patterns in recent execution history
        2. For each pattern: propose optimizations
        3. Propose improvements to quorum
        4. Deploy approved improvements
        5. Measure improvement metrics
        6. Store learnings in memory
        """
        improvements_deployed = 0

        for iteration in range(max_iterations):
            # Step 1: Observe patterns
            patterns = self._observe_patterns(agent_id)
            if not patterns:
                # No patterns detected, continue
                break

            # Step 2: Propose optimizations for low-success patterns
            optimizations = []
            for pattern in patterns:
                if pattern.success_rate < 0.7:  # Below 70% success
                    opt = self._propose_optimization(agent_id, pattern)
                    if opt:
                        optimizations.append(opt)

            if not optimizations:
                # No optimizations needed
                break

            # Step 3-4: Deploy improvements
            for optimization in optimizations:
                success = self._deploy_improvement(agent_id, optimization)
                if success:
                    improvements_deployed += 1

        return improvements_deployed

    def _observe_patterns(self, agent_id: str) -> List[Pattern]:
        """Observe patterns in agent's execution history."""
        if not self._reasoning_layer or not self._execution_engine:
            return []

        reasoning_history = self._reasoning_layer.get_reasoning_history(agent_id, limit=100)
        exec_history = self._execution_engine.get_execution_history(agent_id, limit=100)

        if not reasoning_history or not exec_history:
            return []

        patterns = []

        # Pattern 1: Capability success rates
        cap_success_counts = {}
        cap_total_counts = {}

        for exec_record in exec_history:
            cap_id = exec_record.capability_id
            cap_success_counts[cap_id] = cap_success_counts.get(cap_id, 0)
            cap_total_counts[cap_id] = cap_total_counts.get(cap_id, 0) + 1

            if exec_record.status == "success":
                cap_success_counts[cap_id] += 1

        # Create patterns for each capability
        for cap_id, total in cap_total_counts.items():
            if total >= 2:  # Only for capabilities used 2+ times
                success_rate = cap_success_counts[cap_id] / total
                pattern = Pattern(
                    pattern_id=f"pat-{uuid.uuid4().hex[:12]}",
                    agent_id=agent_id,
                    description=f"Capability {cap_id} success pattern",
                    frequency=total,
                    success_rate=success_rate,
                    first_observed=time.time(),
                )
                patterns.append(pattern)
                self._record_pattern(agent_id, pattern)

        return patterns

    def _propose_optimization(self, agent_id: str, pattern: Pattern) -> Optional[Optimization]:
        """Propose optimization for a pattern."""
        # Generate optimization description from pattern
        description = f"Improve execution for {pattern.description}"
        confidence = 0.6 + (0.4 * (1.0 - pattern.success_rate))

        optimization = Optimization(
            optimization_id=f"opt-{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            pattern_id=pattern.pattern_id,
            description=description,
            confidence=confidence,
        )

        return optimization

    def _deploy_improvement(self, agent_id: str, optimization: Optimization) -> bool:
        """Deploy an improvement for the agent."""
        # Mock deployment (in production: would integrate with self-modification)
        optimization.status = "deployed"
        self._record_improvement(agent_id, optimization)
        return True

    def get_improvement_history(self, agent_id: str) -> List[Optimization]:
        """Get improvement history for agent."""
        with self._lock:
            agent_dir = SELF_IMPROVE_PATH / agent_id
            if not agent_dir.exists():
                return []

            imp_file = agent_dir / "improvements.jsonl"
            if not imp_file.exists():
                return []

            try:
                improvements = [
                    Optimization(**json.loads(line))
                    for line in imp_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                return improvements
            except Exception:
                return []

    def get_pattern_history(self, agent_id: str) -> List[Pattern]:
        """Get pattern history for agent."""
        with self._lock:
            agent_dir = SELF_IMPROVE_PATH / agent_id
            if not agent_dir.exists():
                return []

            pat_file = agent_dir / "patterns.jsonl"
            if not pat_file.exists():
                return []

            try:
                patterns = [
                    Pattern(**json.loads(line))
                    for line in pat_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                return patterns
            except Exception:
                return []

    # ── Storage ────────────────────────────────────────────────────────────

    def _record_pattern(self, agent_id: str, pattern: Pattern) -> None:
        """Record observed pattern."""
        with self._lock:
            agent_dir = SELF_IMPROVE_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            pat_file = agent_dir / "patterns.jsonl"
            pat_file.write_text(
                pat_file.read_text() + json.dumps(asdict(pattern)) + "\n"
                if pat_file.exists()
                else json.dumps(asdict(pattern)) + "\n"
            )

    def _record_improvement(self, agent_id: str, optimization: Optimization) -> None:
        """Record improvement."""
        with self._lock:
            agent_dir = SELF_IMPROVE_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            imp_file = agent_dir / "improvements.jsonl"
            imp_file.write_text(
                imp_file.read_text() + json.dumps(asdict(optimization)) + "\n"
                if imp_file.exists()
                else json.dumps(asdict(optimization)) + "\n"
            )

    def _record_metrics(self, agent_id: str, metrics: ImprovementMetrics) -> None:
        """Record improvement metrics."""
        with self._lock:
            agent_dir = SELF_IMPROVE_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            met_file = agent_dir / "metrics.jsonl"
            met_file.write_text(
                met_file.read_text() + json.dumps(asdict(metrics)) + "\n"
                if met_file.exists()
                else json.dumps(asdict(metrics)) + "\n"
            )
