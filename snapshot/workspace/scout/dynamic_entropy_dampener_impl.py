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
    "SIGTERM":  "graceful shutdown — write h