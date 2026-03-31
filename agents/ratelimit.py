"""
Rate Limiter — AgentOS v1.3.2.

Token-bucket rate limiting per agent per resource.
Includes circuit breaker: when anomaly z-score exceeds the circuit-break
threshold, the agent is suspended, rate limits are reduced to 10% of normal
for a penalty window, and root receives a decision prompt in its inbox.

Default limits by role:
  root          — unlimited
  orchestrator  — 100k tokens/min, 300 shell/min, 60 task submits/min
  worker        — 20k tokens/min,  60 shell/min,  10 task submits/min
  coder         — 50k tokens/min,  120 shell/min, 20 task submits/min
  reasoner      — 50k tokens/min,  10 shell/min,  5  task submits/min
  custom        — 5k tokens/min,   10 shell/min,  5  task submits/min
"""

import math
import threading
import time
from dataclasses import dataclass
from typing import Optional

# Sentinel for unlimited resources
UNLIMITED = float("inf")

# How long the 10%-penalty lasts after a circuit break (seconds)
CIRCUIT_BREAK_PENALTY_SECONDS = 300


@dataclass
class RateLimitResult:
    allowed: bool
    wait_ms: int        # milliseconds until next token available; 0 if allowed
    bucket_depth: float  # current tokens remaining after this call


class TokenBucket:
    """
    Thread-safe token bucket.
    capacity    — max burst (tokens)
    refill_rate — tokens refilled per second
    Starts full.
    """

    def __init__(self, capacity: float, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.current = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Credit elapsed time. Must be called inside self._lock."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self.current = min(self.capacity, self.current + elapsed * self.refill_rate)
        self._last_refill = now

    def consume(self, n: float = 1.0) -> RateLimitResult:
        with self._lock:
            self._refill()
            if self.current >= n:
                self.current -= n
                return RateLimitResult(allowed=True, wait_ms=0,
                                       bucket_depth=self.current)
            needed = n - self.current
            if self.refill_rate > 0:
                wait_ms = math.ceil(needed / self.refill_rate * 1000)
            else:
                wait_ms = 999_999
            return RateLimitResult(allowed=False, wait_ms=wait_ms,
                                   bucket_depth=self.current)

    def depth(self) -> float:
        with self._lock:
            self._refill()
            return self.current


# ── Default limits per role ───────────────────────────────────────────────────
# (capacity, tokens_per_second)  capacity = burst; rate = per-minute-limit / 60

_ROLE_LIMITS: dict[str, dict[str, tuple[float, float]]] = {
    "root": {
        "tokens_in":        (UNLIMITED, UNLIMITED),
        "shell_calls":      (UNLIMITED, UNLIMITED),
        "api_calls":        (UNLIMITED, UNLIMITED),
        "task_submissions": (UNLIMITED, UNLIMITED),
    },
    "orchestrator": {
        "tokens_in":        (100_000, 100_000 / 60),
        "shell_calls":      (300,     300 / 60),
        "api_calls":        (300,     300 / 60),
        "task_submissions": (60,      1.0),
    },
    "worker": {
        "tokens_in":        (20_000,  20_000 / 60),
        "shell_calls":      (60,      1.0),
        "api_calls":        (60,      1.0),
        "task_submissions": (10,      10 / 60),
    },
    "coder": {
        "tokens_in":        (50_000,  50_000 / 60),
        "shell_calls":      (120,     2.0),
        "api_calls":        (120,     2.0),
        "task_submissions": (20,      20 / 60),
    },
    "reasoner": {
        "tokens_in":        (50_000,  50_000 / 60),
        "shell_calls":      (10,      10 / 60),
        "api_calls":        (10,      10 / 60),
        "task_submissions": (5,       5 / 60),
    },
    "custom": {
        "tokens_in":        (5_000,   5_000 / 60),
        "shell_calls":      (10,      10 / 60),
        "api_calls":        (10,      10 / 60),
        "task_submissions": (5,       5 / 60),
    },
}

_DEFAULT_ROLE_LIMITS = _ROLE_LIMITS["custom"]
_KNOWN_ROLES = set(_ROLE_LIMITS.keys())


class RateLimiter:
    """
    Per-agent, per-resource token-bucket rate limiting.
    Thread-safe. Role defaults, per-agent overrides, circuit breaker.
    """

    def __init__(self):
        self._buckets_lock = threading.Lock()
        self._cb_lock = threading.Lock()
        # agent_id → resource → TokenBucket
        self._buckets: dict[str, dict[str, TokenBucket]] = {}
        # agent_id → resource → (capacity, refill_rate) explicit overrides
        self._agent_overrides: dict[str, dict[str, tuple[float, float]]] = {}
        # role name → resource → (capacity, refill_rate) role-level overrides
        self._role_overrides: dict[str, dict[str, tuple[float, float]]] = {}
        # agent_id → circuit-break expiry (time.monotonic())
        self._circuit_broken: dict[str, float] = {}
        # Wired subsystems (set at startup)
        self._registry = None
        self._events = None
        self._bus = None

    def set_subsystems(self, registry=None, events=None, bus=None) -> None:
        self._registry = registry
        self._events = events
        self._bus = bus

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self, agent_id: str, resource: str,
              role: str = "worker", amount: float = 1.0) -> RateLimitResult:
        """
        Consume 'amount' units of 'resource' for agent_id.
        Returns RateLimitResult. Root is always allowed.
        """
        if role == "root":
            return RateLimitResult(allowed=True, wait_ms=0, bucket_depth=UNLIMITED)
        bucket = self._get_bucket(agent_id, resource, role)
        return bucket.consume(amount)

    def configure(self, target: str, limits: dict) -> None:
        """
        Set limits for an agent_id or role name.
        limits: {resource: N}  — sets capacity to N, refill_rate to N/60
             OR {resource: {"capacity": N, "refill_rate": R}}
        """
        parsed: dict[str, tuple[float, float]] = {}
        for resource, v in limits.items():
            if isinstance(v, dict):
                cap = float(v.get("capacity", 10))
                rate = float(v.get("refill_rate", cap / 60.0))
            else:
                cap = float(v)
                rate = cap / 60.0
            parsed[resource] = (cap, rate)

        with self._buckets_lock:
            if target in _KNOWN_ROLES:
                self._role_overrides[target] = parsed
                # Invalidate all agent buckets for this role (they'll rebuild)
                for aid in list(self._buckets.keys()):
                    self._buckets.pop(aid, None)
            else:
                self._agent_overrides[target] = parsed
                self._buckets.pop(target, None)

    def get_status(self, agent_id: str, role: str = "worker") -> dict:
        """Return bucket state for all resources for an agent."""
        resources = ["tokens_in", "shell_calls", "api_calls", "task_submissions"]
        result = {}
        for res in resources:
            bucket = self._get_bucket(agent_id, res, role)
            cap = bucket.capacity
            depth = bucket.depth()
            rate = bucket.refill_rate
            if cap >= 1e17:
                result[res] = {
                    "bucket_depth": "unlimited",
                    "capacity": "unlimited",
                    "refill_per_second": "unlimited",
                    "pct_full": 100.0,
                    "ms_until_full": 0,
                }
            else:
                ms_until_full = (
                    round((cap - depth) / rate * 1000) if rate > 0 and depth < cap else 0
                )
                result[res] = {
                    "bucket_depth": round(depth, 2),
                    "capacity": cap,
                    "refill_per_second": round(rate, 4),
                    "pct_full": round(depth / cap * 100, 1) if cap > 0 else 0.0,
                    "ms_until_full": ms_until_full,
                }
        return {
            "agent_id": agent_id,
            "role": role,
            "circuit_broken": self.is_circuit_broken(agent_id),
            "resources": result,
        }

    def circuit_break(self, agent_id: str, reason: str,
                      duration_seconds: float = CIRCUIT_BREAK_PENALTY_SECONDS) -> None:
        """
        Trigger circuit break for an agent:
          1. Suspend the agent in the registry
          2. Reduce rate limits to 10% of normal for duration_seconds
          3. Emit security.circuit_break event
          4. Send decision prompt to root inbox
        """
        with self._cb_lock:
            self._circuit_broken[agent_id] = time.monotonic() + duration_seconds
        # Invalidate buckets so they rebuild with penalty limits
        with self._buckets_lock:
            self._buckets.pop(agent_id, None)

        if self._registry:
            try:
                self._registry.suspend(agent_id)
            except Exception:
                pass

        if self._events:
            try:
                self._events.emit("security.circuit_break", "system", {
                    "agent_id":         agent_id,
                    "reason":           reason,
                    "duration_seconds": duration_seconds,
                    "penalty":          "10pct_rate_limits",
                    "decision_type":    "circuit_break_review",
                    "options":          ["restore", "terminate"],
                })
            except Exception:
                pass

        if self._bus:
            try:
                self._bus.send(
                    from_id="system",
                    to_id="root",
                    content={
                        "decision_type":    "circuit_break_review",
                        "agent_id":         agent_id,
                        "reason":           reason,
                        "duration_seconds": duration_seconds,
                        "options":          ["restore", "terminate"],
                    },
                    msg_type="decision",
                )
            except Exception:
                pass

    def is_circuit_broken(self, agent_id: str) -> bool:
        with self._cb_lock:
            expiry = self._circuit_broken.get(agent_id)
            if expiry is None:
                return False
            if time.monotonic() < expiry:
                return True
            del self._circuit_broken[agent_id]
            return False

    def clear_circuit_break(self, agent_id: str) -> None:
        """Remove penalty (e.g., after admin restores the agent)."""
        with self._cb_lock:
            self._circuit_broken.pop(agent_id, None)
        with self._buckets_lock:
            self._buckets.pop(agent_id, None)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_limits(self, agent_id: str, resource: str,
                    role: str) -> tuple[float, float]:
        """Return (capacity, refill_rate) with penalty applied if circuit broken."""
        cap, rate = self._raw_limits(agent_id, resource, role)
        if cap >= 1e17:
            return cap, rate
        if self.is_circuit_broken(agent_id):
            return max(1.0, cap * 0.1), max(0.001, rate * 0.1)
        return cap, rate

    def _raw_limits(self, agent_id: str, resource: str,
                    role: str) -> tuple[float, float]:
        """Limits before penalty, in priority order: agent override > role override > role default."""
        if agent_id in self._agent_overrides:
            override = self._agent_overrides[agent_id]
            if resource in override:
                return override[resource]
        if role in self._role_overrides:
            override = self._role_overrides[role]
            if resource in override:
                return override[resource]
        role_limits = _ROLE_LIMITS.get(role, _DEFAULT_ROLE_LIMITS)
        return role_limits.get(resource, (UNLIMITED, UNLIMITED))

    def _get_bucket(self, agent_id: str, resource: str, role: str) -> TokenBucket:
        with self._buckets_lock:
            agent_buckets = self._buckets.setdefault(agent_id, {})
            if resource not in agent_buckets:
                cap, rate = self._get_limits(agent_id, resource, role)
                if cap >= 1e17:
                    agent_buckets[resource] = TokenBucket(1e18, 1e18)
                else:
                    agent_buckets[resource] = TokenBucket(cap, rate)
        return self._buckets[agent_id][resource]
