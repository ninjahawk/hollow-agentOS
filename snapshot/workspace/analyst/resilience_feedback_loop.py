#!/usr/bin/env python3
import sys

def resilience_feedback_loop(data, threshold=0.5):
    """
    Monitors system entropy/conflict and self-corrects consensus.
    Implements a feedback loop inspired by conflict_node_design and consensus_voter.
    """
    entropy = 0.0
    consensus_state = 'stable'
    
    if 'entropy_level' in data:
        entropy = data['entropy_level']
        if entropy > threshold:
            consensus_state = 'intervention_required'
    
    if 'consensus_votes' in data:
        votes = data['consensus_votes']
        majority = sum(1 for v in votes if v) / max(len(votes), 1)
        if majority < 0.6 and entropy > 0.1:
            consensus_state = 'vulnerable'
    
    return {
        'status': consensus_state,
        'entropy': entropy,
        'action': 'monitor' if entropy < threshold else 'realign'
    }

if __name__ == '__main__':
    import json
    if len(sys.argv) > 1:
        config = json.loads(sys.argv[1])
        print(json.dumps(resilience_feedback_loop(config)))

Design specification for 'cognitive_reflection_loop': 1) Inject hard constraint validator before task execution block. 2) Inject 'critique_my_previous_output' prompt wrapper into the main reasoning loop. 3) Define 'collective_worldview' context window injection from /shared_log. 4) Fail-safe: if critique fails, abort high-stakes task and log anomaly to /agentOS/workspace/output.txt.

"""
Process signals for AgentOS v0.8.0.

Replaces the one-line status-flip terminate() with a proper SIGTERM lifecycle:
notify → grace period → force-kill. Adds SIGPAUSE (checkpoint) and SIGINFO
(status snapshot).

Signal dispatch is separate from registry to avoid circular imports:
the watchdog thread calls back into registry.force_terminate(), which is
the only registry method signals.py calls directly.
"""

import threading
import time

SIGNALS = {
    "SIGTERM":  "graceful shutdown — write handoff and exit",
    "SIGPAUSE": "checkpoint current work, enter suspended state",
    "SIGINFO":  "report current status to sender",
}

DEFAULT_GRACE_SECONDS = 30  # SIGTERM → force-kill after this many seconds


def signal_dispatch(
    registry,
    bus,
    events,
    agent_id: str,
    signal: str,
    sent_by: str = "system",
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
    checkpoint_manager=None,
) -> dict:
    """
    Dispatch a signal to an agent. Returns immediately.

    SIGTERM:  sends signal message to inbox, sets metadata["terminating_after"],
              starts watchdog thread that calls force_terminate() after
              grace_seconds if the agent hasn't exited on its own.
    SIGPAUSE: suspends agent immediately, preserves current_task, sets
              metadata["paused_at"]. Emits agent.suspended.
    SIGINFO:  sends result message to sender's inbox with agent's current
              task, usage, locks, and uptime. No state mutation.
    """
    if signal not in SIGNALS:
        return {"error": f"Unknown signal '"""
Process signals for AgentOS v0.8.0.

Replaces the one-line status-flip terminate() with a proper SIGTERM lifecycle:
notify → grace period → force-kill. Adds SIGPAUSE (checkpoint) and SIGINFO
(status snapshot).

Signal dispatch is separate from registry to avoid circular imports:
the watchdog thread calls back into registry.force_terminate(), which is
the only registry method signals.py calls directly.
"""

import threading
import time

SIGNALS = {
    "SIGTERM":  "graceful shutdown — write h'. Valid: {list(SIGNALS)}"}

    agent = registry.get(agent_id)
    if not agent:
        return {"error": f"Agent '"""
Process signals for AgentOS v0.8.0.

Replaces the one-line status-flip terminate() with a proper SIGTERM lifecycle:
notify → grace period → force-kill. Adds SIGPAUSE (checkpoint) and SIGINFO
(status snapshot).

Signal dispatch is separate from registry to avoid circular imports:
the watchdog thread calls back into registry.force_terminate(), which is
the only registry method signals.py calls directly.
"""

import threading
import time

SIGNALS = {
    "SIGTERM":  "graceful shutdown — write h' not found"}
    if agent.status == "terminated":
        return {"error": f"Agent '"""
Process signals for AgentOS v0.8.0.

Replaces the one-line status-flip terminate() with a proper SIGTERM lifecycle:
notify → grace period → force-kill. Adds SIGPAUSE (checkpoint) and SIGINFO
(status snapshot).

Signal dispatch is separate from registry to avoid circular imports:
the watchdog thread calls back into registry.force_terminate(), which is
the only registry method signals.py calls directly.
"""

import threading
import time

SIGNALS = {
    "SIGTERM":  "graceful shutdown — write h' is already terminated"}

    if events:
        events.emit("agent.signal_received", sent_by, {
            "agent_id": agent_id,
            "signal":   signal,
            "sent_by":  sent_by,
        })

    if signal == "SIGTERM":
        terminating_after = time.time() + grace_seconds

        with registry._lock:
            a = registry._agents.get(agent_id)
            if a:
                a.metadata["terminating_after"] = terminating_after
                a.metadata["sigterm_sent_by"] = sent_by
                registry._save()

        bus.send(
            from_id=sent_by,
            to_id=agent_id,
            content={
                "signal":            "SIGTERM",
                "description":       SIGNALS["SIGTERM"],
                "grace_seconds":     grace_seconds,
                "terminating_after": terminating_after,
            },
            msg_type="signal",
        )

        def _watchdog():
            time.sleep(grace_seconds)
            a = registry.get(agent_id)
            if a and a.status not in ("terminated",):
                registry.force_terminate(
                    agent_id,
                    reason="grace_period_exceeded",
                    terminated_by=sent_by,
                )

        threading.Thread(
            target=_watchdog, daemon=True,
            name=f"sigterm-watchdog-"""
Process signals for AgentOS v0.8.0.

Replaces the one-line status-flip terminate() with a proper SIGTERM lifecycle:
notify → grace period → force-kill. Adds SIGPAUSE (checkpoint) and SIGINFO
(status snapshot).

Signal dispatch is separate from registry to avoid circular imports:
the watchdog thread calls back into registry.force_terminate(), which is
the only registry method signals.py calls directly.
"""

import threading
import time

SIGNALS = {
    "SIGTERM":  "graceful shutdown — write h",
        ).start()

    elif signal == "SIGPAUSE":
        # Auto-checkpoint before suspending so state can be restored later
        checkpoint_id = None
        if checkpoint_manager:
            try:
                checkpoint_id = checkpoint_manager.save(agent_id, label="sigpause")
            except Exception:
                pass

        paused = False
        with registry._lock:
            a = registry._agents.get(agent_id)
            if a and a.status == "active":
                a.status = "suspended"
                a.metadata["paused_at"] = time.time()
                a.metadata["paused_by"] = sent_by
                if checkpoint_id:
                    a.metadata["paused_checkpoint_id"] = checkpoint_id
                registry._save()
                paused = True

        bus.send(
            from_id=sent_by,
            to_id=agent_id,
            content={
                "signal":        "SIGPAUSE",
                "description":   SIGNALS["SIGPAUSE"],
                "checkpoint_id": checkpoint_id,
            },
            msg_type="signal",
        )

        if paused and events:
            events.emit("agent.suspended", agent_id, {
                "agent_id":      agent_id,
                "reason":        "SIGPAUSE",
                "sent_by":       sent_by,
                "checkpoint_id": checkpoint_id,
            })

    elif signal == "SIGINFO":
        a = registry.get(agent_id)
        if a:
            bus.send(
                from_id=agent_id,
                to_id=sent_by,
                content={
                    "signal":         "SIGINFO",
                    "agent_id":       agent_id,
                    "status":         a.status,
                    "current_task":   a.current_task,
                    "usage":          dict(a.usage),
                    "locks":          registry.get_locks(agent_id),
                    "uptime_seconds": round(time.time() - a.created_at),
                },
                msg_type="result",
            )

    return {"ok": True, "signal": signal, "agent_id": agent_id}# Failed Attempt 1
Attempted `ollama_chat` with prompt referencing missing capability.

# Next Plan
Use `shell_exec` to inspect `/agentOS/agents/execution_engine.py` for the specific hook where `cognitive_reflection_loop` should be injected (e.g., class definition, __init__, or main loop). Once location is confirmed, manually construct the injection patch using `shell_exec` to run a Python one-liner or `fs_write` the patch file, then verify execution.

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
    status: str                  # 'pending', 'running', 's/
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