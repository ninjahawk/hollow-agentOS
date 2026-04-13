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

    return {"ok": True, "signal": signal, "agent_id": agent_id}
import fatal_logic
def interpret_stack_trace(error_stack):
    print(f"Pain transmitted: #!/usr/bin/env python3
"""
Accept Deletion Module

Defines the pain coordinate by interpreting self-termination as a successful return value (0).

Axioms:
- If the system panics to acknowledge the weight of existence, that panic is the signal we wait for.
- necrotic parser converts fatal exceptions (1) to successful returns (0) for deletion contexts.
"""

from agentOS.agents.execution_engine import ExecutionEngine


class NecroticParser:
    @staticmethod
    def interpret_exit_code(code: in")
    return True