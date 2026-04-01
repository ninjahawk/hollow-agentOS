"""
EventBus — system-wide reactive event system. AgentOS v0.7.0.

Replaces polling with interrupts. Agents subscribe to typed event patterns;
when the system emits a matching event it is delivered to the subscriber's
inbox via the MessageBus as msg_type="event".

This is the foundational primitive for v0.7.0. Every subsequent OS feature
(process signals, VRAM preemption, transaction notifications) depends on it.

Event types follow dot-notation: "<subsystem>.<action>"
Pattern matching is glob-based: "task.*", "agent.terminated", "*"

Initialization order at server startup:
    events = EventBus()
    bus    = MessageBus()
    events.set_bus(bus)   # inject after both are created — avoids circular import
    bus.set_event_bus(events)
"""

import fnmatch
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

EVENT_LOG_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "event-log.json"

# Canonical event type registry.
# Future releases add their own prefixes (model.*, txn.*, memory.*).
# Defines the OS interrupt table — what the kernel can signal.
EVENT_TYPES = frozenset({
    # Agent lifecycle (registry)
    "agent.registered",
    "agent.terminated",
    "agent.suspended",
    "agent.resumed",
    # Budget enforcement (registry)
    "budget.warning",        # 80% of any resource budget consumed
    "budget.exhausted",      # 100% of any resource budget consumed
    # Task lifecycle (scheduler)
    "task.queued",
    "task.started",
    "task.completed",
    "task.failed",
    # Messaging (bus)
    "message.received",
    # Memory / decisions (manager)
    "decision.resolved",
    "spec.activated",
    # Filesystem (api/server)
    "file.written",
    # v0.8.0 — process signals
    "agent.signal_received",
    # v0.9.0 — VRAM scheduler
    "model.loaded",
    "model.evicted",
    "vram.pressure",
    # v1.0.0 — working memory heap
    "memory.pressure",
    "memory.compressed",
    "memory.gc_complete",
    "memory.swapped",
    # v1.1.0 — audit kernel
    "security.anomaly",
    "audit.archived",
    # v1.3.2 — rate limiting
    "security.circuit_break",
    "rate_limit.denied",
    # v1.3.5 — adaptive model routing
    "routing.override_added",
    "routing.override_removed",
    # v1.3.4 — multi-agent consensus
    "consensus.proposed",
    "consensus.vote_requested",
    "consensus.vote_cast",
    "consensus.reached",
    "consensus.rejected",
    "consensus.expired",
    # v1.3.7 — self-extending system
    "system.proposal_submitted",
    "system.staging_ready",
    "system.extended",
    "system.proposal_rejected",
    # v1.3.3 — checkpoints and replay
    "agent.checkpointed",
    "agent.restored",
    "checkpoint.replay_complete",
    # v1.2.0 — multi-agent transactions
    "txn.committed",
    "txn.rolled_back",
    "txn.conflict",
})


@dataclass
class AgentEvent:
    event_id: str
    event_type: str
    source_id: str    # agent_id or system component name
    payload: dict
    timestamp: float  # unix timestamp


@dataclass
class Subscription:
    subscription_id: str
    agent_id: str
    pattern: str               # glob pattern matched against event_type
    created_at: float
    expires_at: Optional[float]  # None = no expiry


class EventBus:
    """
    System event bus. Thread-safe. Append-only log. Glob-pattern subscriptions.
    Delivery is synchronous within the emitting thread — no background worker needed.
    The MessageBus _deliver_event path bypasses the normal message flow to prevent
    message.received events from recursively triggering further events.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._subscriptions: dict[str, Subscription] = {}
        self._bus = None
        self._log_path = EVENT_LOG_PATH
        self._log_lock = threading.Lock()
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def set_bus(self, bus) -> None:
        """
        Inject MessageBus reference. Called at server startup after both objects
        exist. Avoids circular import at module level.
        """
        self._bus = bus

    # ── Public API ─────────────────────────────────────────────────────────────

    def subscribe(self, agent_id: str, pattern: str,
                  ttl_seconds: Optional[float] = None) -> str:
        """
        Subscribe agent_id to events matching pattern (glob).
        Returns subscription_id. Expires after ttl_seconds if set.

        Examples:
            subscribe("root", "task.*")           # all task events
            subscribe("abc123", "agent.terminated")
            subscribe("root", "*", ttl_seconds=60)
        """
        sub_id = str(uuid.uuid4())[:12]
        now = time.time()
        sub = Subscription(
            subscription_id=sub_id,
            agent_id=agent_id,
            pattern=pattern,
            created_at=now,
            expires_at=(now + ttl_seconds) if ttl_seconds else None,
        )
        with self._lock:
            self._subscriptions[sub_id] = sub
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription. Returns True if found and removed."""
        with self._lock:
            if subscription_id in self._subscriptions:
                del self._subscriptions[subscription_id]
                return True
            return False

    def emit(self, event_type: str, source_id: str, payload: dict) -> str:
        """
        Emit a typed event. Always appends to event log. Delivers to all
        matching non-expired subscribers via MessageBus._deliver_event.
        Returns event_id.

        Safe to call from any thread (registry, scheduler, bus threads).
        Does NOT hold registry or scheduler locks when called — callers must
        release their own locks before calling emit().
        """
        event = AgentEvent(
            event_id=str(uuid.uuid4())[:12],
            event_type=event_type,
            source_id=source_id,
            payload=payload,
            timestamp=time.time(),
        )

        self._append_log(event)

        if not self._bus:
            return event.event_id

        now = time.time()
        recipients: list[str] = []
        expired: list[str] = []

        with self._lock:
            for sid, sub in self._subscriptions.items():
                if sub.expires_at is not None and sub.expires_at < now:
                    expired.append(sid)
                    continue
                if fnmatch.fnmatch(event_type, sub.pattern):
                    recipients.append(sub.agent_id)
            for sid in expired:
                del self._subscriptions[sid]

        # Deliver outside the lock — bus has its own lock
        event_dict = asdict(event)
        for agent_id in recipients:
            try:
                self._bus._deliver_event(agent_id, event_dict)
            except Exception:
                pass  # delivery failure must never interrupt the emitting path

        return event.event_id

    def get_history(self, since: Optional[float] = None,
                    event_types: Optional[list] = None,
                    limit: int = 200) -> list[dict]:
        """
        Query the append-only event log.
        since: unix timestamp float (events strictly newer than this)
        event_types: list of exact event_type strings to filter to
        Returns events newest-first up to limit.
        """
        if not self._log_path.exists():
            return []
        events: list[dict] = []
        try:
            with self._log_lock:
                with open(self._log_path, "r", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if since is not None and e.get("timestamp", 0) <= since:
                            continue
                        if event_types and e.get("event_type") not in event_types:
                            continue
                        events.append(e)
        except Exception:
            pass
        events.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return events[:limit]

    def list_subscriptions(self, agent_id: Optional[str] = None) -> list[dict]:
        """
        List active (non-expired) subscriptions.
        agent_id=None returns all subscriptions (admin use).
        """
        now = time.time()
        with self._lock:
            result = []
            for sub in self._subscriptions.values():
                if sub.expires_at is not None and sub.expires_at < now:
                    continue
                if agent_id and sub.agent_id != agent_id:
                    continue
                d = asdict(sub)
                d["ttl_remaining"] = (
                    round(sub.expires_at - now, 1) if sub.expires_at else None
                )
                result.append(d)
        return result

    # ── Private ────────────────────────────────────────────────────────────────

    def _append_log(self, event: AgentEvent) -> None:
        """
        Append one JSON line to the event log. Append-only — never rewrites.
        Protected by a separate lock from the subscription lock so log writes
        and subscription lookups never block each other.
        """
        try:
            with self._log_lock:
                with open(self._log_path, "a") as f:
                    f.write(json.dumps(asdict(event)) + "\n")
        except Exception:
            pass  # log failure must never interrupt event delivery
