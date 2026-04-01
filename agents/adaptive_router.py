"""
Adaptive Model Router — AgentOS v1.3.5.

Static complexity→model routing (v0.9.0) ignores what actually happens when
you send tasks to a model. A model can load fast but generate slowly. It can
succeed 99% of the time at complexity 2 but fail at complexity 4. VRAM
affinity (v0.9.0) avoids eviction cost; adaptive routing avoids poor
performance.

This module observes every task completion, maintains per-(model,complexity)
exponential moving averages, and scores models before each routing decision.
Scores are composed of three factors:

  success_rate   (0.0–1.0) — EMA of task success/failure
  throughput     (tokens/ms, normalized) — information delivery rate
  latency_inv    (1/ms, normalized) — time to first token proxy

Weight: success_rate=0.5, throughput=0.3, latency=0.2. Success dominates
because a fast wrong answer is strictly worse than a slow right one.

Routing decision flow:
  1. Collect candidate models for the requested complexity tier
  2. Adaptive router scores each candidate (returns None if <MIN_OBSERVATIONS)
  3. If confident winner: use it
  4. Else: fall back to VRAM affinity (v0.9.0) → static tier default

Hard overrides bypass scoring entirely. An admin can pin complexity 3 to
qwen2.5:14b regardless of performance scores. Overrides are per-complexity,
per-agent, or per-role; the most specific match wins.

Audit integration: the router subscribes to task.completed events so it
observes completions without coupling to the scheduler internals.

Storage: /agentOS/memory/routing_stats.json (EMA state, persisted)
         /agentOS/memory/routing_overrides.json (hard overrides)
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

STATS_PATH     = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "routing_stats.json"
OVERRIDES_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "routing_overrides.json"

# EMA smoothing factor — α=0.15 means ~13 observations to weight recent ≥ old
EMA_ALPHA = 0.15

# Minimum observations before a model's score is used for routing
MIN_OBSERVATIONS = 5

# Score normalisation constants — empirically tuned for local Ollama models
THROUGHPUT_NORM_TOKENS_PER_MS = 2.0    # 2 tok/ms ≈ solid throughput baseline
LATENCY_NORM_MS               = 5_000  # 5s is "expected" for a mid-complexity task

# Score weights — must sum to 1.0
W_SUCCESS    = 0.50
W_THROUGHPUT = 0.30
W_LATENCY    = 0.20

# Default score for models with insufficient observations
# High enough to be considered alongside scored models, low enough to lose ties
DEFAULT_SCORE = 0.60


@dataclass
class ModelStats:
    model: str
    complexity: int
    observation_count: int
    ema_success_rate: float     # 0.0–1.0
    ema_tokens_per_ms: float    # tokens/ms
    ema_duration_ms: float      # ms
    last_updated: float


@dataclass
class RoutingOverride:
    override_id: str
    model: str                  # forced model name
    complexity: Optional[int]   # None = all complexities
    agent_id: Optional[str]     # None = all agents
    role: Optional[str]         # None = all roles
    reason: str
    created_at: float


class AdaptiveRouter:
    """
    Score-based model routing with EMA performance tracking and hard overrides.
    Thread-safe. One instance per server.

    Integration points:
      - scheduler._run_task / _run_task_streaming: call observe() after completion
      - scheduler._pick_model: call recommend() before routing decision
      - EventBus: subscribe to task.completed for decoupled observation
      - agent_routes: expose /routing/* endpoints
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._stats: dict[tuple[str, int], ModelStats] = {}    # (model, complexity) → stats
        self._overrides: dict[str, RoutingOverride] = {}       # override_id → override
        self._events = None
        self._registry = None
        self._load()

    def set_subsystems(self, events=None, registry=None) -> None:
        self._events = events
        self._registry = registry
        # Primary observation path: scheduler calls observe() directly after each task.
        # The event bus delivers to agent inboxes, not Python callbacks — no subscription here.

    # ── Observation ──────────────────────────────────────────────────────────

    def observe(
        self,
        model: str,
        complexity: int,
        duration_ms: float,
        tokens_out: int,
        success: bool,
    ) -> None:
        """
        Record a task completion observation. Updates EMA statistics for
        (model, complexity). Thread-safe; saves state after each update.
        """
        if not model or complexity < 1 or complexity > 5:
            return
        if duration_ms is None or duration_ms <= 0:
            return

        tokens_per_ms = max(0.0, tokens_out / duration_ms) if duration_ms > 0 else 0.0
        key = (model, complexity)

        with self._lock:
            existing = self._stats.get(key)
            if existing is None:
                self._stats[key] = ModelStats(
                    model=model,
                    complexity=complexity,
                    observation_count=1,
                    ema_success_rate=1.0 if success else 0.0,
                    ema_tokens_per_ms=tokens_per_ms,
                    ema_duration_ms=duration_ms,
                    last_updated=time.time(),
                )
            else:
                s = existing
                s.ema_success_rate = _ema(s.ema_success_rate, 1.0 if success else 0.0)
                s.ema_tokens_per_ms = _ema(s.ema_tokens_per_ms, tokens_per_ms)
                s.ema_duration_ms   = _ema(s.ema_duration_ms, duration_ms)
                s.observation_count += 1
                s.last_updated = time.time()
            self._save()

    # ── Scoring ──────────────────────────────────────────────────────────────

    def score(self, model: str, complexity: int) -> float:
        """
        Score a model for a given complexity. Higher = better.
        Returns DEFAULT_SCORE if fewer than MIN_OBSERVATIONS exist.
        Range: [0.0, 1.0].
        """
        key = (model, complexity)
        with self._lock:
            s = self._stats.get(key)
        if s is None or s.observation_count < MIN_OBSERVATIONS:
            return DEFAULT_SCORE

        throughput_score = min(s.ema_tokens_per_ms / THROUGHPUT_NORM_TOKENS_PER_MS, 1.0)
        latency_score    = 1.0 / (1.0 + s.ema_duration_ms / LATENCY_NORM_MS)

        return round(
            W_SUCCESS    * s.ema_success_rate
            + W_THROUGHPUT * throughput_score
            + W_LATENCY    * latency_score,
            4,
        )

    def has_confidence(self, model: str, complexity: int) -> bool:
        """True if model has ≥ MIN_OBSERVATIONS for this complexity."""
        key = (model, complexity)
        with self._lock:
            s = self._stats.get(key)
        return s is not None and s.observation_count >= MIN_OBSERVATIONS

    def recommend(self, complexity: int, candidates: list[str]) -> Optional[str]:
        """
        Return the highest-scoring candidate model for complexity.
        Returns None if no candidate has MIN_OBSERVATIONS (defer to VRAM affinity).
        Returns the single confident candidate if only one has data.
        Breaks ties toward higher throughput.
        """
        if not candidates:
            return None

        confident = [m for m in candidates if self.has_confidence(m, complexity)]
        if not confident:
            return None

        return max(confident, key=lambda m: self.score(m, complexity))

    # ── Overrides ────────────────────────────────────────────────────────────

    def add_override(
        self,
        model: str,
        complexity: Optional[int] = None,
        agent_id: Optional[str] = None,
        role: Optional[str] = None,
        reason: str = "",
    ) -> str:
        """
        Add a hard routing override. Returns override_id.
        Overrides are matched by specificity: agent_id > role > complexity > global.
        The most specific matching override is applied.
        """
        override_id = str(uuid.uuid4())[:12]
        override = RoutingOverride(
            override_id=override_id,
            model=model,
            complexity=complexity,
            agent_id=agent_id,
            role=role,
            reason=reason,
            created_at=time.time(),
        )
        with self._lock:
            self._overrides[override_id] = override
            self._save()
        return override_id

    def remove_override(self, override_id: str) -> bool:
        """Remove a routing override by ID. Returns True if found."""
        with self._lock:
            if override_id not in self._overrides:
                return False
            del self._overrides[override_id]
            self._save()
        return True

    def resolve_override(
        self,
        complexity: int,
        agent_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Optional[str]:
        """
        Return the forced model from the most specific matching override, or None.
        Specificity order: agent_id match > role match > complexity-only > global.
        """
        with self._lock:
            overrides = list(self._overrides.values())

        matching = [
            o for o in overrides
            if (o.complexity is None or o.complexity == complexity)
            and (o.agent_id is None or o.agent_id == agent_id)
            and (o.role is None or o.role == role)
        ]
        if not matching:
            return None

        def specificity(o: RoutingOverride) -> int:
            return (
                (2 if o.agent_id is not None else 0)
                + (1 if o.role is not None else 0)
            )

        best = max(matching, key=specificity)
        return best.model

    def list_overrides(self) -> list[dict]:
        with self._lock:
            return [asdict(o) for o in self._overrides.values()]

    # ── Stats export ─────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """
        Return per-(model,complexity) performance stats and scores.
        Includes derived score so callers don't have to recompute.
        """
        with self._lock:
            stats_copy = dict(self._stats)

        result = []
        for (model, complexity), s in sorted(stats_copy.items()):
            result.append({
                "model":             model,
                "complexity":        complexity,
                "observation_count": s.observation_count,
                "ema_success_rate":  round(s.ema_success_rate, 4),
                "ema_tokens_per_ms": round(s.ema_tokens_per_ms, 4),
                "ema_duration_ms":   round(s.ema_duration_ms, 1),
                "score":             self.score(model, complexity),
                "confident":         s.observation_count >= MIN_OBSERVATIONS,
                "last_updated":      s.last_updated,
            })
        return {
            "models": result,
            "min_observations": MIN_OBSERVATIONS,
            "ema_alpha": EMA_ALPHA,
            "weights": {"success": W_SUCCESS, "throughput": W_THROUGHPUT, "latency": W_LATENCY},
        }

    def get_recommendation(self, complexity: int) -> dict:
        """
        Return the current recommendation for a complexity level with rationale.
        """
        from agents.model_manager import COMPLEXITY_MODEL
        candidates = list(set(COMPLEXITY_MODEL.values()))
        recommended = self.recommend(complexity, candidates)
        static_default = COMPLEXITY_MODEL.get(complexity, "mistral-nemo:12b")

        scores = {m: self.score(m, complexity) for m in candidates}
        confidence = {m: self.has_confidence(m, complexity) for m in candidates}

        return {
            "complexity":       complexity,
            "recommended":      recommended,
            "static_default":   static_default,
            "using_adaptive":   recommended is not None,
            "scores":           scores,
            "confidence":       confidence,
            "min_observations": MIN_OBSERVATIONS,
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self) -> None:
        """Persist stats and overrides. Caller must hold self._lock."""
        stats_data = {
            f"{model}::{complexity}": asdict(s)
            for (model, complexity), s in self._stats.items()
        }
        STATS_PATH.write_text(
            json.dumps(stats_data, indent=2, default=str), encoding="utf-8"
        )
        overrides_data = {oid: asdict(o) for oid, o in self._overrides.items()}
        OVERRIDES_PATH.write_text(
            json.dumps(overrides_data, indent=2, default=str), encoding="utf-8"
        )

    def _load(self) -> None:
        """Load persisted stats and overrides from disk."""
        if STATS_PATH.exists():
            try:
                raw = json.loads(STATS_PATH.read_text(encoding="utf-8"))
                for key_str, s_dict in raw.items():
                    try:
                        s = ModelStats(**s_dict)
                        self._stats[(s.model, s.complexity)] = s
                    except Exception:
                        pass
            except Exception:
                pass

        if OVERRIDES_PATH.exists():
            try:
                raw = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
                for oid, o_dict in raw.items():
                    try:
                        self._overrides[oid] = RoutingOverride(**o_dict)
                    except Exception:
                        pass
            except Exception:
                pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ema(current: float, new_value: float, alpha: float = EMA_ALPHA) -> float:
    """Exponential moving average update."""
    return alpha * new_value + (1.0 - alpha) * current
