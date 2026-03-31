"""
Multi-Agent Transaction Coordinator — AgentOS v1.2.0.

Named locks prevent concurrent corruption but don't provide atomicity.
If an orchestrator writes 3 files and fails after the first, the system
is in a partial state. This module provides transactions:

  begin → stage ops (buffered, NOT applied) → commit (conflict-check + apply)
                                             → rollback (discard all staged ops)

Isolation level: read-committed.
  - Readers see last-committed state only.
  - Staged (uncommitted) writes are invisible to all agents.

Conflict detection: if any resource staged for write was modified by another
agent after txn_begin, commit returns {ok: false, conflicts: [...]}.

Timeout: each txn has a 60s timeout. Watchdog auto-rolls-back after expiry.

Supported op_types: fs_write | memory_set | message_send
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

TXN_TIMEOUT_SECONDS = 60
TXN_LOG_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "transactions.json"


@dataclass
class StagedOp:
    op_type: str        # fs_write | memory_set | message_send
    params: dict        # operation parameters (not yet applied)
    resource: str       # key for conflict detection (file path, memory key, etc.)
    staged_at: float = field(default_factory=time.time)


@dataclass
class TxnRecord:
    txn_id: str
    agent_id: str
    status: str          # open | committed | rolled_back
    ops_buffered: list   # list of StagedOp dicts
    created_at: float
    timeout_at: float
    committed_at: Optional[float] = None
    rolled_back_at: Optional[float] = None
    rollback_reason: Optional[str] = None
    conflict_resources: list = field(default_factory=list)


class TransactionCoordinator:
    """
    Manages agent transactions. Thread-safe.

    Agents call begin() → stage() → commit() or rollback().
    The coordinator buffers all staged ops and applies them atomically on commit.
    Auto-rolls-back open transactions after TXN_TIMEOUT_SECONDS.
    """

    def __init__(self, event_bus=None, registry=None, bus=None, heap_registry=None):
        self._lock = threading.Lock()
        self._txns: dict[str, TxnRecord] = {}
        self._event_bus = event_bus
        self._registry = registry   # AgentRegistry — for memory_set ops
        self._bus = bus             # MessageBus — for message_send ops
        self._heap_registry = heap_registry
        # Track last-write timestamps per resource for conflict detection
        # resource_path → (agent_id, timestamp)
        self._resource_writes: dict[str, tuple] = {}
        self._load()
        self._start_watchdog()

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    def set_subsystems(self, registry=None, bus=None, heap_registry=None) -> None:
        self._registry = registry
        self._bus = bus
        self._heap_registry = heap_registry

    # ── Public API ──────────────────────────────────────────────────────────

    def begin(self, agent_id: str) -> str:
        """
        Begin a transaction. Returns txn_id.
        Watchdog auto-rolls-back after TXN_TIMEOUT_SECONDS.
        """
        txn_id = str(uuid.uuid4())[:16]
        now = time.time()
        txn = TxnRecord(
            txn_id=txn_id,
            agent_id=agent_id,
            status="open",
            ops_buffered=[],
            created_at=now,
            timeout_at=now + TXN_TIMEOUT_SECONDS,
        )
        with self._lock:
            self._txns[txn_id] = txn
            self._save()
        return txn_id

    def stage(self, txn_id: str, op_type: str, params: dict) -> dict:
        """
        Buffer an operation. Does NOT apply to disk/state yet.
        op_type: fs_write | memory_set | message_send
        Returns {ok: True, staged: op_type} or {error: ...}.
        """
        if op_type not in ("fs_write", "memory_set", "message_send"):
            return {"error": f"Unknown op_type '{op_type}'. Use: fs_write | memory_set | message_send"}

        with self._lock:
            txn = self._txns.get(txn_id)
            if not txn:
                return {"error": f"Transaction '{txn_id}' not found"}
            if txn.status != "open":
                return {"error": f"Transaction '{txn_id}' is {txn.status}"}
            if time.time() > txn.timeout_at:
                return {"error": "Transaction has timed out"}

            resource = _resource_key(op_type, params)
            op = StagedOp(op_type=op_type, params=params, resource=resource)
            txn.ops_buffered.append(asdict(op))
            self._save()

        return {"ok": True, "staged": op_type, "resource": resource,
                "txn_id": txn_id, "ops_count": len(txn.ops_buffered)}

    def commit(self, txn_id: str) -> dict:
        """
        Commit transaction:
        1. Check for conflicts (resource written by another agent since begin)
        2. Apply all buffered ops in sequence — all-or-nothing
        3. Emit txn.committed or txn.conflict
        Returns {ok: bool, conflicts: list[str]}.
        """
        with self._lock:
            txn = self._txns.get(txn_id)
            if not txn:
                return {"error": f"Transaction '{txn_id}' not found"}
            if txn.status != "open":
                return {"error": f"Transaction '{txn_id}' is already {txn.status}"}
            if time.time() > txn.timeout_at:
                txn.status = "rolled_back"
                txn.rollback_reason = "timeout"
                txn.rolled_back_at = time.time()
                self._save()
                return {"error": "Transaction timed out during commit"}

            # 1. Conflict detection
            conflicts = []
            for op_dict in txn.ops_buffered:
                resource = op_dict["resource"]
                last = self._resource_writes.get(resource)
                if last and last[0] != txn.agent_id and last[1] > txn.created_at:
                    conflicts.append(resource)

            if conflicts:
                txn.status = "rolled_back"
                txn.rollback_reason = "conflict"
                txn.rolled_back_at = time.time()
                txn.conflict_resources = conflicts
                self._save()

        if conflicts:
            if self._event_bus:
                self._event_bus.emit("txn.conflict", txn.agent_id, {
                    "txn_id":    txn_id,
                    "agent_id":  txn.agent_id,
                    "conflicts": conflicts,
                })
                self._event_bus.emit("txn.rolled_back", txn.agent_id, {
                    "txn_id":  txn_id,
                    "reason":  "conflict",
                    "conflicts": conflicts,
                })
            return {"ok": False, "conflicts": conflicts}

        # 2. Apply ops — all-or-nothing
        applied = []
        rollback_reason = None
        with self._lock:
            txn = self._txns.get(txn_id)
            ops = [StagedOp(**d) for d in txn.ops_buffered]

        try:
            for op in ops:
                self._apply_op(op, txn.agent_id)
                applied.append(op)
                # Record write for future conflict detection
                with self._lock:
                    self._resource_writes[op.resource] = (txn.agent_id, time.time())
        except Exception as e:
            rollback_reason = f"op_failed: {e}"
            # Undo applied ops (best-effort)
            self._rollback_applied(applied)

        with self._lock:
            txn = self._txns.get(txn_id)
            if rollback_reason:
                txn.status = "rolled_back"
                txn.rollback_reason = rollback_reason
                txn.rolled_back_at = time.time()
            else:
                txn.status = "committed"
                txn.committed_at = time.time()
            self._save()

        if rollback_reason:
            if self._event_bus:
                self._event_bus.emit("txn.rolled_back", txn.agent_id, {
                    "txn_id": txn_id, "reason": rollback_reason,
                })
            return {"ok": False, "error": rollback_reason}

        if self._event_bus:
            self._event_bus.emit("txn.committed", txn.agent_id, {
                "txn_id":    txn_id,
                "ops_count": len(ops),
                "duration_ms": round((txn.committed_at - txn.created_at) * 1000),
            })

        return {"ok": True, "txn_id": txn_id, "ops_count": len(ops)}

    def rollback(self, txn_id: str, reason: str = "explicit") -> dict:
        """Discard all buffered ops. Transaction moves to rolled_back."""
        with self._lock:
            txn = self._txns.get(txn_id)
            if not txn:
                return {"error": f"Transaction '{txn_id}' not found"}
            if txn.status != "open":
                return {"error": f"Transaction '{txn_id}' is already {txn.status}"}
            txn.status = "rolled_back"
            txn.rollback_reason = reason
            txn.rolled_back_at = time.time()
            txn.ops_buffered = []
            self._save()

        if self._event_bus:
            self._event_bus.emit("txn.rolled_back", txn.agent_id, {
                "txn_id": txn_id, "reason": reason,
            })

        return {"ok": True, "txn_id": txn_id, "reason": reason}

    def status(self, txn_id: str) -> Optional[dict]:
        """Return txn status dict, or None if not found."""
        with self._lock:
            txn = self._txns.get(txn_id)
        if not txn:
            return None
        d = asdict(txn)
        d["ops_count"] = len(txn.ops_buffered)
        d["expires_in_seconds"] = max(0, round(txn.timeout_at - time.time()))
        return d

    def record_external_write(self, resource: str, agent_id: str) -> None:
        """
        Called by fs_write and memory_set outside a transaction to track
        resource modification timestamps for conflict detection.
        """
        with self._lock:
            self._resource_writes[resource] = (agent_id, time.time())

    # ── Internal ─────────────────────────────────────────────────────────────

    def _apply_op(self, op: StagedOp, agent_id: str) -> None:
        """Apply a single staged operation. Raises on failure."""
        if op.op_type == "fs_write":
            p = Path(op.params["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(op.params["content"], encoding="utf-8")

        elif op.op_type == "memory_set":
            if self._heap_registry:
                heap = self._heap_registry.get(agent_id)
                heap.alloc(
                    key=op.params["key"],
                    content=op.params["content"],
                    priority=op.params.get("priority", 5),
                )
            else:
                raise RuntimeError("HeapRegistry not available for memory_set")

        elif op.op_type == "message_send":
            if self._bus:
                self._bus.send(
                    from_id=agent_id,
                    to_id=op.params["to_id"],
                    content=op.params.get("content", {}),
                    msg_type=op.params.get("msg_type", "text"),
                )
            else:
                raise RuntimeError("MessageBus not available for message_send")

    def _rollback_applied(self, applied: list[StagedOp]) -> None:
        """Best-effort undo of ops applied before failure."""
        for op in reversed(applied):
            try:
                if op.op_type == "fs_write":
                    p = Path(op.params["path"])
                    if p.exists():
                        p.unlink()
            except Exception:
                pass

    def _start_watchdog(self) -> None:
        """Background thread that auto-rolls-back timed-out transactions."""
        def _watch():
            while True:
                time.sleep(5)
                now = time.time()
                with self._lock:
                    timed_out = [
                        txn for txn in self._txns.values()
                        if txn.status == "open" and now > txn.timeout_at
                    ]
                for txn in timed_out:
                    self.rollback(txn.txn_id, reason="timeout")

        threading.Thread(target=_watch, daemon=True, name="txn-watchdog").start()

    def _load(self) -> None:
        if not TXN_LOG_PATH.exists():
            return
        try:
            data = json.loads(TXN_LOG_PATH.read_text(encoding="utf-8"))
            for d in data.values():
                d.setdefault("conflict_resources", [])
                txn = TxnRecord(**d)
                # Don't restore open txns — they timed out during downtime
                if txn.status == "open":
                    txn.status = "rolled_back"
                    txn.rollback_reason = "server_restart"
                    txn.rolled_back_at = time.time()
                self._txns[txn.txn_id] = txn
        except Exception:
            pass

    def _save(self) -> None:
        TXN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Keep only last 200 transactions
        txns = sorted(self._txns.values(), key=lambda t: t.created_at, reverse=True)[:200]
        out = {t.txn_id: asdict(t) for t in txns}
        TXN_LOG_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")


def _resource_key(op_type: str, params: dict) -> str:
    """Canonical resource identifier for conflict detection."""
    if op_type == "fs_write":
        return f"fs:{params.get('path', '')}"
    if op_type == "memory_set":
        return f"mem:{params.get('key', '')}"
    if op_type == "message_send":
        # Messages don't have conflict resources (they're append-only)
        return f"msg:{params.get('to_id', '')}:{params.get('key', str(time.time()))}"
    return f"{op_type}:{str(params)[:64]}"
