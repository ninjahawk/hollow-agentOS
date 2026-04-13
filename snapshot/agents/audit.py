"""
Audit Kernel — AgentOS v1.1.0.

Every operation through a single audited boundary. Append-only log,
z-score anomaly detection, per-role baseline tracking.

Design constraints:
- append-only: log() never rewrites existing entries
- audit.log and audit-baselines.json are blocklisted for fs_write
- anomaly check after every 10 new entries per agent
- baseline established from first 50 operations per role
- z-score threshold: 3.0 for anomaly signal
- p99 overhead target: ≤5ms per operation (log is async-flushed)
"""

import json
import math
import os
import statistics
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

AUDIT_LOG_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "audit.log"
BASELINES_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "audit-baselines.json"

# Blocklisted paths — no agent may write these via API
AUDIT_PROTECTED_PATHS = {
    str(AUDIT_LOG_PATH),
    str(BASELINES_PATH),
}

# Anomaly detection metrics tracked per agent
_ANOMALY_METRICS = [
    "shell_calls_per_minute",
    "tokens_per_minute",
    "unique_op_types",
]

# How many entries to collect before starting anomaly checks
BASELINE_MIN_OPS = 50
ANOMALY_CHECK_EVERY = 10
ANOMALY_Z_THRESHOLD = 3.0
CIRCUIT_BREAK_Z_THRESHOLD = 5.0  # above this: trigger circuit breaker, not just alert


@dataclass
class AuditEntry:
    entry_id: str
    agent_id: str
    operation: str      # shell_exec | fs_read | fs_write | ollama_call |
                        # agent_register | agent_terminate | agent_spawn |
                        # message_send | memory_alloc | task_submit | lock_acquire | ...
    params: dict        # sanitized — no file content, no model output, only metadata
    result_code: str    # ok | denied | error | budget_exceeded
    tokens_charged: int
    duration_ms: float
    timestamp: float = field(default_factory=time.time)
    # v1.3.0: causal context for lineage tracing
    caused_by_task_id: Optional[str] = None
    parent_txn_id: Optional[str] = None
    call_depth: int = 0


@dataclass
class AnomalyReport:
    agent_id: str
    metric: str
    observed: float
    baseline: float
    z_score: float
    detected_at: float = field(default_factory=time.time)


class AuditLog:
    """
    Append-only audit log with z-score anomaly detection.
    Thread-safe. Log writes are synchronous (target p99 ≤5ms).
    """

    def __init__(self, event_bus=None):
        self._lock = threading.Lock()
        self._event_bus = event_bus
        self._circuit_break_cb = None   # called when z_score > CIRCUIT_BREAK_Z_THRESHOLD
        # In-memory index for fast queries (rebuilt from disk on startup)
        self._entries: list[AuditEntry] = []
        # Per-agent entry count for anomaly trigger
        self._agent_counts: dict[str, int] = {}
        # Baseline data loaded from disk
        self._baselines: dict[str, dict] = {}
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._load_baselines()
        self._load_entries()

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    def set_circuit_break_callback(self, cb) -> None:
        """
        Register a callable(agent_id, reason) called when anomaly z_score exceeds
        CIRCUIT_BREAK_Z_THRESHOLD. Wired in at startup to RateLimiter.circuit_break.
        """
        self._circuit_break_cb = cb

    # ── Core operations ──────────────────────────────────────────────────────

    def log(self, entry: AuditEntry) -> None:
        """
        Append entry to audit.log (newline-delimited JSON).
        Never rewrites existing content. Triggers anomaly check every 10 entries.
        """
        line = json.dumps(asdict(entry)) + "\n"
        with self._lock:
            with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line)
            self._entries.append(entry)
            self._agent_counts[entry.agent_id] = (
                self._agent_counts.get(entry.agent_id, 0) + 1
            )
            count = self._agent_counts[entry.agent_id]

        # Update baseline (first 50 ops per role)
        self._update_baseline(entry)

        # Anomaly check every 10 entries per agent
        if count % ANOMALY_CHECK_EVERY == 0:
            report = self.check_anomaly(entry.agent_id)
            if report:
                if report.z_score >= CIRCUIT_BREAK_Z_THRESHOLD and self._circuit_break_cb:
                    try:
                        self._circuit_break_cb(
                            report.agent_id,
                            f"anomaly: {report.metric} z={report.z_score:.1f}",
                        )
                    except Exception:
                        pass
                if self._event_bus:
                    self._event_bus.emit("security.anomaly", "root", {
                        "agent_id":  report.agent_id,
                        "metric":    report.metric,
                        "observed":  report.observed,
                        "baseline":  report.baseline,
                        "z_score":   report.z_score,
                    })

    def query(
        self,
        agent_id: Optional[str] = None,
        operation: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Filter entries by agent, operation, and time range."""
        with self._lock:
            entries = list(self._entries)
        results = []
        for e in reversed(entries):  # newest first
            if agent_id and e.agent_id != agent_id:
                continue
            if operation and e.operation != operation:
                continue
            if since and e.timestamp < since:
                continue
            if until and e.timestamp > until:
                continue
            results.append(asdict(e))
            if len(results) >= limit:
                break
        return results

    def stats(self, agent_id: str) -> dict:
        """Return op_counts, total_tokens, and anomaly_score for an agent."""
        with self._lock:
            entries = [e for e in self._entries if e.agent_id == agent_id]

        op_counts: dict[str, int] = {}
        total_tokens = 0
        for e in entries:
            op_counts[e.operation] = op_counts.get(e.operation, 0) + 1
            total_tokens += e.tokens_charged

        report = self.check_anomaly(agent_id)
        anomaly_score = round(report.z_score, 2) if report else 0.0

        return {
            "agent_id":      agent_id,
            "op_counts":     op_counts,
            "total_tokens":  total_tokens,
            "entry_count":   len(entries),
            "anomaly_score": anomaly_score,
        }

    def check_anomaly(self, agent_id: str) -> Optional[AnomalyReport]:
        """
        Compute z-scores for agent metrics vs per-role baseline.
        Returns AnomalyReport for the worst metric if z > 3.0, else None.
        """
        with self._lock:
            agent_entries = [e for e in self._entries if e.agent_id == agent_id]

        if len(agent_entries) < BASELINE_MIN_OPS:
            return None  # not enough data to compare

        # Compute recent metrics (last 60s window)
        now = time.time()
        window_start = now - 60.0
        recent = [e for e in agent_entries if e.timestamp >= window_start]
        if not recent:
            return None

        # shell_calls_per_minute
        shell_calls = sum(1 for e in recent if e.operation == "shell_exec")

        # tokens_per_minute
        tokens = sum(e.tokens_charged for e in recent)

        # unique_op_types in last minute
        unique_ops = len({e.operation for e in recent})

        observed = {
            "shell_calls_per_minute": float(shell_calls),
            "tokens_per_minute":      float(tokens),
            "unique_op_types":        float(unique_ops),
        }

        # Look up baseline (keyed by agent role — need to find it)
        # We stored baseline by entry_id; retrieve role from first entry
        first_op = agent_entries[0]
        role_key = f"role:{first_op.params.get('role', 'worker')}"
        baseline_data = self._baselines.get(role_key, {})

        worst: Optional[AnomalyReport] = None
        for metric in _ANOMALY_METRICS:
            if metric not in baseline_data:
                continue
            bl = baseline_data[metric]
            mean = bl.get("mean", 0.0)
            std  = bl.get("std", 0.0)
            if std < 0.001:
                continue
            obs = observed.get(metric, 0.0)
            z = abs(obs - mean) / std
            if z > ANOMALY_Z_THRESHOLD:
                if worst is None or z > worst.z_score:
                    worst = AnomalyReport(
                        agent_id=agent_id,
                        metric=metric,
                        observed=obs,
                        baseline=mean,
                        z_score=round(z, 2),
                    )
        return worst

    def anomaly_history(self, limit: int = 50) -> list[dict]:
        """Return recent security.anomaly events from event log (delegated to caller)."""
        # This is a placeholder — server.py returns this from the EventBus directly
        return []

    # ── Baseline management ──────────────────────────────────────────────────

    def _update_baseline(self, entry: AuditEntry) -> None:
        """
        Accumulate per-role baseline samples. After 50 ops, compute mean/std.
        Thread-safe via separate lock-free approach (baseline updates tolerate
        minor races — correctness over exact counts).
        """
        role = entry.params.get("role", "worker")
        role_key = f"role:{role}"

        with self._lock:
            bl = self._baselines.setdefault(role_key, {
                "_samples": {"shell_calls": [], "tokens": [], "unique_ops": []},
                "_count":   0,
            })
            # Ensure _count and _samples exist even if loaded from disk without them
            if "_count" not in bl:
                bl["_count"] = BASELINE_MIN_OPS + 1
            if "_samples" not in bl:
                bl["_samples"] = {"shell_calls": [], "tokens": [], "unique_ops": []}
            bl["_count"] += 1

            # Only accumulate for first 50 ops per role (baseline period)
            if bl["_count"] <= BASELINE_MIN_OPS:
                bl["_samples"]["shell_calls"].append(
                    1.0 if entry.operation == "shell_exec" else 0.0
                )
                bl["_samples"]["tokens"].append(float(entry.tokens_charged))
                bl["_samples"]["unique_ops"].append(1.0)  # simplified

                if bl["_count"] == BASELINE_MIN_OPS:
                    # Compute and freeze baseline stats
                    for metric, sample_key in [
                        ("shell_calls_per_minute", "shell_calls"),
                        ("tokens_per_minute", "tokens"),
                        ("unique_op_types", "unique_ops"),
                    ]:
                        samples = bl["_samples"][sample_key]
                        mean = statistics.mean(samples) * 60  # per-minute rate
                        std  = (statistics.stdev(samples) * 60
                                if len(samples) > 1 else 1.0)
                        bl[metric] = {"mean": round(mean, 4), "std": round(max(std, 0.01), 4)}

                    self._save_baselines()

    def _save_baselines(self) -> None:
        """Persist baselines dict to disk (called under self._lock)."""
        # Exclude internal sample arrays from the saved file
        to_save = {}
        for role_key, data in self._baselines.items():
            to_save[role_key] = {
                k: v for k, v in data.items()
                if not k.startswith("_")
            }
        BASELINES_PATH.write_text(json.dumps(to_save, indent=2), encoding="utf-8")

    def _load_baselines(self) -> None:
        if not BASELINES_PATH.exists():
            return
        try:
            data = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
            # Restore internal tracking fields that were excluded from the saved file.
            # A role that has computed stats is already past its baseline period.
            for role_key, bl in data.items():
                if "_count" not in bl:
                    # If stats exist, baseline is established — start count above threshold
                    has_stats = any(k in bl for k in ("shell_calls_per_minute",
                                                       "tokens_per_minute",
                                                       "unique_op_types"))
                    bl["_count"] = BASELINE_MIN_OPS + 1 if has_stats else 0
                if "_samples" not in bl:
                    bl["_samples"] = {"shell_calls": [], "tokens": [], "unique_ops": []}
            self._baselines = data
        except Exception:
            pass

    def _load_entries(self) -> None:
        """Load existing audit.log into memory index on startup."""
        if not AUDIT_LOG_PATH.exists():
            return
        try:
            with AUDIT_LOG_PATH.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        entry = AuditEntry(**d)
                        self._entries.append(entry)
                        self._agent_counts[entry.agent_id] = (
                            self._agent_counts.get(entry.agent_id, 0) + 1
                        )
                    except Exception:
                        pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helper: build an AuditEntry from route context
# ---------------------------------------------------------------------------

def make_entry(
    agent_id: str,
    operation: str,
    params: dict,
    result_code: str = "ok",
    tokens_charged: int = 0,
    duration_ms: float = 0.0,
    role: str = "worker",
    caused_by_task_id: Optional[str] = None,
    parent_txn_id: Optional[str] = None,
    call_depth: int = 0,
) -> AuditEntry:
    """
    Construct an AuditEntry with sanitized params.
    Adds agent role to params for baseline tracking.
    """
    safe_params = {k: v for k, v in params.items()
                   if k not in ("content", "response", "token_content")}
    safe_params["role"] = role
    return AuditEntry(
        entry_id=str(uuid.uuid4())[:12],
        agent_id=agent_id,
        operation=operation,
        params=safe_params,
        result_code=result_code,
        tokens_charged=tokens_charged,
        duration_ms=duration_ms,
        caused_by_task_id=caused_by_task_id,
        parent_txn_id=parent_txn_id,
        call_depth=call_depth,
    )
