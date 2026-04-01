"""
Agent Specialization — AgentOS v3.9.0.

Phase 6, primitive 4. Depends on v3.7.0 (Meta-Synthesis).

Every agent in the swarm is assigned the same capabilities, but each agent
accumulates different execution history. One agent happens to run more file
operations; another runs more reasoning tasks. Over time, they develop real
performance differences: different success rates, different latencies,
different knowledge on different topics.

This module makes those differences visible and actionable:
  - Profile: what is each agent good at?
  - Route: given a task, which agent should handle it?
  - Focus: bias an agent toward tasks where it excels

Design:
  SpecializationProfile:
    - Per-agent strength map: task_type → {success_rate, avg_duration, sample_count}
    - Weaknesses (task types the agent consistently fails at)
    - Specialization score: 0.0 (generalist) → 1.0 (highly specialized)

  SpecializationEngine:
    profile(agent_id) → SpecializationProfile
      # build from execution history + semantic memory topics
    update(agent_id, task_type, success, duration_ms)
      # incremental update after each task
    route(task_type, candidate_agent_ids) → agent_id
      # pick best specialist
    top_specialist(task_type) → Optional[agent_id]
      # best agent for this task type across all profiled agents
    compare_specializations(agent_ids) → dict
      # which agents specialize in what

Storage:
  /agentOS/memory/specialization/
    {agent_id}/
      profile.json          # current specialization profile
      history.jsonl         # per-task performance records
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

SPECIALIZATION_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "specialization"


@dataclass
class TaskPerformance:
    """Performance record for a single task execution."""
    record_id: str
    agent_id: str
    task_type: str          # semantic task category
    success: bool
    duration_ms: float
    capability_used: str    # which capability handled it
    timestamp: float = field(default_factory=time.time)


@dataclass
class SpecializationStrength:
    """How well an agent performs on one task type."""
    task_type: str
    success_rate: float     # 0.0 – 1.0
    avg_duration_ms: float  # lower is better
    sample_count: int       # how many observations


@dataclass
class SpecializationProfile:
    """Full specialization profile for one agent."""
    agent_id: str
    profile_id: str
    updated_at: float

    strengths: list         # list of SpecializationStrength dicts, sorted by success_rate desc
    weaknesses: list        # task_types where success_rate < 0.4
    best_task_type: Optional[str]   # what this agent is best at
    worst_task_type: Optional[str]  # what this agent struggles with most
    specialization_score: float     # 0.0 = generalist, 1.0 = specialist
    total_tasks: int


class SpecializationEngine:
    """
    Tracks agent performance across task types and routes work to
    the best-suited agent.
    """

    def __init__(self, execution_engine=None, storage_path: Path = None):
        self._engine = execution_engine
        self._lock = threading.Lock()
        self._storage_path = storage_path or SPECIALIZATION_PATH
        self._storage_path.mkdir(parents=True, exist_ok=True)
        # in-memory cache: agent_id → {task_type → [records]}
        self._cache: dict = {}

    # ------------------------------------------------------------------ #
    #  Core API                                                            #
    # ------------------------------------------------------------------ #

    def update(
        self,
        agent_id: str,
        task_type: str,
        success: bool,
        duration_ms: float,
        capability_used: str = "unknown",
    ) -> str:
        """
        Record one task outcome. Call after every execution to keep
        profiles current.
        """
        rec = TaskPerformance(
            record_id=str(uuid.uuid4())[:8],
            agent_id=agent_id,
            task_type=task_type,
            success=success,
            duration_ms=duration_ms,
            capability_used=capability_used,
        )

        with self._lock:
            self._cache.setdefault(agent_id, {})
            self._cache[agent_id].setdefault(task_type, []).append(asdict(rec))
            self._append_record(agent_id, rec)

        return rec.record_id

    def profile(self, agent_id: str) -> SpecializationProfile:
        """
        Build a SpecializationProfile from this agent's task history.

        Also pulls from execution_engine if available to fill gaps.
        """
        with self._lock:
            # ensure cache is populated from disk
            if agent_id not in self._cache:
                self._load_agent_cache(agent_id)
            records_by_type = dict(self._cache.get(agent_id, {}))

        # supplement with execution engine data if available
        if self._engine is not None and not records_by_type:
            records_by_type = self._infer_from_execution_history(agent_id)

        strengths_raw = []
        for task_type, records in records_by_type.items():
            if not records:
                continue
            successes = sum(1 for r in records if r.get("success", False))
            durations = [r.get("duration_ms", 0.0) for r in records]
            strengths_raw.append(SpecializationStrength(
                task_type=task_type,
                success_rate=successes / len(records),
                avg_duration_ms=sum(durations) / len(durations) if durations else 0.0,
                sample_count=len(records),
            ))

        strengths_raw.sort(key=lambda s: s.success_rate, reverse=True)

        weaknesses = [
            s.task_type for s in strengths_raw if s.success_rate < 0.4
        ]

        best = strengths_raw[0].task_type if strengths_raw else None
        worst = strengths_raw[-1].task_type if len(strengths_raw) > 1 else None

        # specialization score: how concentrated is performance variance?
        spec_score = self._compute_specialization_score(strengths_raw)

        total_tasks = sum(s.sample_count for s in strengths_raw)

        return SpecializationProfile(
            agent_id=agent_id,
            profile_id=str(uuid.uuid4())[:8],
            updated_at=time.time(),
            strengths=[asdict(s) for s in strengths_raw],
            weaknesses=weaknesses,
            best_task_type=best,
            worst_task_type=worst,
            specialization_score=spec_score,
            total_tasks=total_tasks,
        )

    def route(self, task_type: str, candidate_agent_ids: list) -> Optional[str]:
        """
        Pick the best agent for a task type from a list of candidates.

        Scoring: success_rate (60%) + sample_count bonus (20%) + speed (20%)
        Falls back to first candidate if none have data for this task type.
        """
        if not candidate_agent_ids:
            return None

        scored = []
        for agent_id in candidate_agent_ids:
            score = self._score_agent_for_task(agent_id, task_type)
            scored.append((agent_id, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        # if nobody has data, return the first candidate
        if all(s == 0.0 for _, s in scored):
            return candidate_agent_ids[0]

        return scored[0][0]

    def top_specialist(self, task_type: str) -> Optional[str]:
        """
        Find the best agent for this task type across all profiled agents.
        Returns None if no agent has data for this task type.
        """
        best_agent = None
        best_score = -1.0

        all_agents = self._all_profiled_agents()
        for agent_id in all_agents:
            score = self._score_agent_for_task(agent_id, task_type)
            if score > best_score:
                best_score = score
                best_agent = agent_id

        return best_agent if best_score > 0 else None

    def compare_specializations(self, agent_ids: list) -> dict:
        """
        For each task type observed in any agent's history,
        return which agent is best at it.

        Returns: {task_type: {best_agent, best_score, all_scores: {agent_id: score}}}
        """
        # gather all task types across all agents
        all_task_types = set()
        for aid in agent_ids:
            with self._lock:
                if aid not in self._cache:
                    self._load_agent_cache(aid)
                all_task_types.update(self._cache.get(aid, {}).keys())

        result = {}
        for task_type in all_task_types:
            all_scores = {
                aid: self._score_agent_for_task(aid, task_type)
                for aid in agent_ids
            }
            best = max(all_scores, key=lambda a: all_scores[a]) if all_scores else None
            result[task_type] = {
                "best_agent": best,
                "best_score": all_scores.get(best, 0.0) if best else 0.0,
                "all_scores": all_scores,
            }

        return result

    # ------------------------------------------------------------------ #
    #  Internals                                                           #
    # ------------------------------------------------------------------ #

    def _score_agent_for_task(self, agent_id: str, task_type: str) -> float:
        """Compute agent's score for a specific task type."""
        with self._lock:
            if agent_id not in self._cache:
                self._load_agent_cache(agent_id)
            records = self._cache.get(agent_id, {}).get(task_type, [])

        if not records:
            return 0.0

        n = len(records)
        success_rate = sum(1 for r in records if r.get("success", False)) / n
        durations = [r.get("duration_ms", 1000.0) for r in records]
        avg_dur = sum(durations) / len(durations) if durations else 1000.0

        # normalize duration: faster → higher score (cap at 5000ms range)
        speed_score = max(0.0, 1.0 - avg_dur / 5000.0)

        # sample bonus: more data → more trustworthy (log scale, cap at n=20)
        import math
        sample_bonus = min(math.log(n + 1) / math.log(21), 1.0)

        return round(success_rate * 0.6 + sample_bonus * 0.2 + speed_score * 0.2, 3)

    def _compute_specialization_score(self, strengths: list) -> float:
        """
        Measure how specialized vs generalist an agent is.
        High variance in success_rate across task types → high specialization.
        """
        if len(strengths) < 2:
            return 0.0

        rates = [s.success_rate for s in strengths]
        mean = sum(rates) / len(rates)
        variance = sum((r - mean) ** 2 for r in rates) / len(rates)
        # std dev normalized to [0, 1] (max std dev for binary is 0.5)
        return round(min(variance ** 0.5 / 0.5, 1.0), 3)

    def _infer_from_execution_history(self, agent_id: str) -> dict:
        """
        Build a rough task type map from execution engine history.
        Uses capability_id as a proxy for task type.
        """
        if self._engine is None:
            return {}

        history = self._engine.get_execution_history(agent_id, limit=200)
        by_type: dict = {}

        for entry in history:
            cap_id = entry.capability_id if hasattr(entry, "capability_id") else entry.get("capability_id", "unknown")
            status = entry.status if hasattr(entry, "status") else entry.get("status", "")
            dur = entry.duration_ms if hasattr(entry, "duration_ms") else entry.get("duration_ms", 0.0)
            success = (status == "success")

            rec = {
                "record_id": str(uuid.uuid4())[:8],
                "agent_id": agent_id,
                "task_type": cap_id,
                "success": success,
                "duration_ms": dur,
                "capability_used": cap_id,
                "timestamp": time.time(),
            }
            by_type.setdefault(cap_id, []).append(rec)

        return by_type

    def _all_profiled_agents(self) -> list:
        """Return all agent_ids that have history in the cache or on disk."""
        with self._lock:
            agents = set(self._cache.keys())

        # also scan disk
        if self._storage_path.exists():
            for agent_dir in self._storage_path.iterdir():
                if agent_dir.is_dir():
                    agents.add(agent_dir.name)

        return list(agents)

    # ------------------------------------------------------------------ #
    #  Persistence                                                         #
    # ------------------------------------------------------------------ #

    def _append_record(self, agent_id: str, rec: TaskPerformance) -> None:
        agent_dir = self._storage_path / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        history_path = agent_dir / "history.jsonl"
        with open(history_path, "a") as f:
            f.write(json.dumps(asdict(rec)) + "\n")

    def _load_agent_cache(self, agent_id: str) -> None:
        """Load disk records into cache. Must be called under self._lock."""
        history_path = self._storage_path / agent_id / "history.jsonl"
        if not history_path.exists():
            self._cache[agent_id] = {}
            return

        by_type: dict = {}
        for line in history_path.read_text().strip().splitlines():
            try:
                rec = json.loads(line)
                tt = rec.get("task_type", "unknown")
                by_type.setdefault(tt, []).append(rec)
            except Exception:
                continue

        self._cache[agent_id] = by_type

    def save_profile(self, agent_id: str) -> None:
        """Compute and save current profile snapshot to disk."""
        profile = self.profile(agent_id)
        agent_dir = self._storage_path / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "profile.json").write_text(json.dumps(asdict(profile), indent=2))

    def load_profile(self, agent_id: str) -> Optional[SpecializationProfile]:
        """Load the last saved profile from disk."""
        path = self._storage_path / agent_id / "profile.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return SpecializationProfile(**data)
        except Exception:
            return None
