"""
SharedLog — append-only broadcast channel for multi-agent communication.

Agents write structured messages here that all other agents can read.
Uses JSONL append mode so concurrent writers never overwrite each other.
Each entry is a single JSON line — the file only grows, never shrinks.

Storage: /agentOS/memory/shared_log.jsonl

Usage:
  from agents.shared_log import SharedLog
  log = SharedLog()
  log.write("scout", "found deadlock in execution_engine.py line 42")
  recent = log.read(limit=50)
"""

import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

LOG_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "shared_log.jsonl"
MAX_ENTRIES = int(os.getenv("AGENTOS_SHARED_LOG_MAX", "5000"))


@dataclass
class LogEntry:
    ts: float
    agent_id: str
    message: str
    tags: list


class SharedLog:
    """Thread-safe, append-only, multi-agent broadcast log."""

    def __init__(self):
        self._lock = threading.Lock()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    def write(self, agent_id: str, message: str, tags: Optional[list] = None) -> None:
        """Append a message. Safe for concurrent writers across threads."""
        entry = LogEntry(
            ts=time.time(),
            agent_id=agent_id,
            message=message[:4000],
            tags=tags or [],
        )
        line = json.dumps(asdict(entry)) + "\n"
        with self._lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
            self._maybe_trim()

    def read(self, limit: int = 100, since_ts: Optional[float] = None,
             agent_id: Optional[str] = None, tag: Optional[str] = None) -> list[dict]:
        """
        Read recent entries. Filters: since_ts, agent_id, tag.
        Returns newest-last (chronological order).
        """
        if not LOG_PATH.exists():
            return []
        try:
            with self._lock:
                raw = LOG_PATH.read_text(encoding="utf-8")
        except Exception:
            return []

        results = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if since_ts is not None and d.get("ts", 0) < since_ts:
                continue
            if agent_id is not None and d.get("agent_id") != agent_id:
                continue
            if tag is not None and tag not in d.get("tags", []):
                continue
            results.append(d)

        # Return the last `limit` entries (most recent)
        return results[-limit:]

    def _maybe_trim(self) -> None:
        """
        Trim the log to MAX_ENTRIES when it gets too large.
        Called under self._lock. Uses atomic rename to avoid corruption.
        """
        try:
            lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
            if len(lines) > MAX_ENTRIES * 1.1:  # 10% buffer before trimming
                keep = lines[-MAX_ENTRIES:]
                tmp = LOG_PATH.with_suffix(".tmp")
                tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
                tmp.rename(LOG_PATH)
        except Exception:
            pass
