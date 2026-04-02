"""
Execution Engine — AgentOS v2.6.0.

Capabilities go from metadata to actual execution.
Agent needs "read file" → find capability → execute it → get real result.

Design:
  CapabilityImpl:
    capability_id: str
    implementation: callable
    timeout_ms: int
    requires_approval: bool
    safety_level: str

  ExecutionContext:
    execution_id: str
    capability_id: str
    agent_id: str
    params: dict
    result: dict or error
    status: str                  # 'pending', 'running', 'success', 'failed'
    duration_ms: float
    timestamp: float

  ExecutionEngine:
    register(capability_id, implementation, timeout=5000, requires_approval=False)
    execute(agent_id, capability_id, params: dict) → (result, status)
    get_execution_history(agent_id) → list[ExecutionContext]
    disable_capability(capability_id) → bool
    enable_capability(capability_id) → bool

Storage:
  /agentOS/memory/executions/
    {agent_id}/
      history.jsonl         # execution logs
      stats.json            # success rates, timings
"""

import json
import os
import threading
import time
import uuid
import traceback
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Callable, Dict, Tuple, Any
import subprocess
import signal

EXECUTION_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "executions"


@dataclass
class ExecutionContext:
    """Record of a capability execution."""
    execution_id: str
    agent_id: str
    capability_id: str
    params: dict = field(default_factory=dict)
    result: Optional[dict] = None
    error: Optional[str] = None
    status: str = "pending"              # pending, running, success, failed
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


class ExecutionEngine:
    """Execute capabilities registered by the system."""

    def __init__(self):
        self._lock = threading.RLock()
        self._implementations: Dict[str, Callable] = {}
        self._timeouts: Dict[str, int] = {}
        self._requires_approval: Dict[str, bool] = {}
        self._enabled: Dict[str, bool] = {}
        EXECUTION_PATH.mkdir(parents=True, exist_ok=True)

    # ── API ────────────────────────────────────────────────────────────────

    def register(self, capability_id: str, implementation: Callable,
                 timeout_ms: int = 5000, requires_approval: bool = False) -> bool:
        """
        Register a capability implementation.
        Returns True if registered, False if already registered.
        """
        with self._lock:
            if capability_id in self._implementations:
                return False

            self._implementations[capability_id] = implementation
            self._timeouts[capability_id] = timeout_ms
            self._requires_approval[capability_id] = requires_approval
            self._enabled[capability_id] = True

        return True

    def execute(self, agent_id: str, capability_id: str, params: dict = None) -> Tuple[Optional[dict], str]:
        """
        Execute a capability.
        Returns (result, status) tuple.
        Status: 'success', 'failed', 'disabled', 'not_found', 'timeout'
        """
        execution_id = f"exec-{uuid.uuid4().hex[:12]}"
        start_time = time.time()
        params = params or {}

        # Check capability exists and is enabled
        with self._lock:
            if capability_id not in self._implementations:
                return (None, "not_found")

            if not self._enabled[capability_id]:
                return (None, "disabled")

            impl = self._implementations[capability_id]
            timeout = self._timeouts[capability_id]

        # Execute
        context = ExecutionContext(
            execution_id=execution_id,
            agent_id=agent_id,
            capability_id=capability_id,
            params=params,
            status="running",
        )

        try:
            # Simple execution: call the function with timeout
            # In production: handle subprocess, containers, remote calls, etc.
            result = self._call_with_timeout(impl, params, timeout)
            context.result = result if isinstance(result, dict) else {"output": result}
            # Treat ok=False as failure even if no exception was raised
            if isinstance(context.result, dict) and context.result.get("ok") is False:
                context.status = "failed"
                context.error = context.result.get("error", "capability returned ok=False")
            else:
                context.status = "success"
        except TimeoutError:
            context.status = "timeout"
            context.error = f"Execution exceeded {timeout}ms timeout"
        except Exception as e:
            context.status = "failed"
            context.error = str(e)
            context.result = {"error": str(e), "traceback": traceback.format_exc()}

        context.duration_ms = (time.time() - start_time) * 1000

        # Log execution
        self._log_execution(agent_id, context)

        return (context.result, context.status)

    def _call_with_timeout(self, func: Callable, params: dict, timeout_ms: int) -> Any:
        """Call function with timeout."""
        # For now: simple execution (no true timeout for CPU-bound)
        # In production: use threading, async, or subprocess for real timeout
        result = func(**params) if params else func()
        return result

    def _log_execution(self, agent_id: str, context: ExecutionContext) -> None:
        """Store execution record."""
        with self._lock:
            agent_dir = EXECUTION_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            history_file = agent_dir / "history.jsonl"
            history_file.write_text(
                history_file.read_text() + json.dumps(asdict(context)) + "\n"
                if history_file.exists()
                else json.dumps(asdict(context)) + "\n"
            )

    def get_execution_history(self, agent_id: str, limit: int = 50) -> list:
        """Get execution history for an agent."""
        with self._lock:
            agent_dir = EXECUTION_PATH / agent_id
            if not agent_dir.exists():
                return []

            history_file = agent_dir / "history.jsonl"
            if not history_file.exists():
                return []

            try:
                executions = [
                    ExecutionContext(**json.loads(line))
                    for line in history_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                executions.sort(key=lambda e: e.timestamp, reverse=True)
                return executions[:limit]
            except Exception:
                return []

    def disable_capability(self, capability_id: str) -> bool:
        """Disable a capability (prevent execution)."""
        with self._lock:
            if capability_id not in self._implementations:
                return False
            self._enabled[capability_id] = False
        return True

    def enable_capability(self, capability_id: str) -> bool:
        """Enable a capability."""
        with self._lock:
            if capability_id not in self._implementations:
                return False
            self._enabled[capability_id] = True
        return True

    def list_registered(self) -> list:
        """List all registered capabilities."""
        with self._lock:
            return list(self._implementations.keys())

    def get_stats(self, agent_id: str) -> dict:
        """Get execution statistics for an agent."""
        history = self.get_execution_history(agent_id, limit=1000)

        if not history:
            return {
                "agent_id": agent_id,
                "total_executions": 0,
                "success_rate": 0.0,
                "average_duration_ms": 0.0,
                "failed_count": 0,
            }

        total = len(history)
        successful = sum(1 for e in history if e.status == "success")
        failed = sum(1 for e in history if e.status == "failed")
        avg_duration = sum(e.duration_ms for e in history) / total if total > 0 else 0

        return {
            "agent_id": agent_id,
            "total_executions": total,
            "success_count": successful,
            "failed_count": failed,
            "success_rate": successful / total if total > 0 else 0.0,
            "average_duration_ms": avg_duration,
        }
