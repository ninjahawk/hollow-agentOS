"""
Message Bus — inter-agent communication.

Agents send typed messages to each other by agent_id.
Messages are queued per recipient and persisted to disk.
Supports direct messages, broadcasts, and request/reply patterns.
"""

import json
import time
import uuid
import threading
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

BUS_PATH = Path("/agentOS/memory/message-bus.json")

MSG_TYPES = {
    "task",     # assign a task to another agent
    "result",   # return a result to the sender
    "alert",    # urgent notification
    "data",     # pass structured data
    "ping",     # liveness check
    "log",      # agent logging to a monitor
}


@dataclass
class Message:
    msg_id: str
    from_id: str
    to_id: str           # agent_id or "broadcast"
    msg_type: str
    content: dict
    timestamp: float
    read: bool = False
    reply_to: Optional[str] = None   # msg_id this is replying to
    ttl: Optional[float] = None      # expire after this unix timestamp


class MessageBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._queues: dict[str, list[Message]] = {}   # agent_id → list[Message]
        self._all: dict[str, Message] = {}             # msg_id → Message
        self._load()

    def send(
        self,
        from_id: str,
        to_id: str,
        content: dict,
        msg_type: str = "data",
        reply_to: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> str:
        if msg_type not in MSG_TYPES:
            msg_type = "data"

        msg = Message(
            msg_id=str(uuid.uuid4())[:12],
            from_id=from_id,
            to_id=to_id,
            msg_type=msg_type,
            content=content,
            timestamp=time.time(),
            reply_to=reply_to,
            ttl=time.time() + ttl_seconds if ttl_seconds else None,
        )

        with self._lock:
            self._all[msg.msg_id] = msg
            if to_id == "broadcast":
                # Each existing queue gets a copy
                for q in self._queues.values():
                    q.append(msg)
            else:
                self._queues.setdefault(to_id, []).append(msg)
            self._save()

        return msg.msg_id

    def receive(self, agent_id: str, unread_only: bool = True, limit: int = 20) -> list[dict]:
        """Return messages addressed to agent_id, marking them read."""
        now = time.time()
        with self._lock:
            q = self._queues.get(agent_id, [])
            # Prune expired
            q = [m for m in q if m.ttl is None or m.ttl > now]
            self._queues[agent_id] = q

            results = []
            for msg in q:
                if unread_only and msg.read:
                    continue
                msg.read = True
                results.append(asdict(msg))
                if len(results) >= limit:
                    break

            if results:
                self._save()
            return results

    def get_thread(self, msg_id: str) -> list[dict]:
        """Return a message and all its replies."""
        root = self._all.get(msg_id)
        if not root:
            return []
        thread = [asdict(root)]
        for m in self._all.values():
            if m.reply_to == msg_id:
                thread.append(asdict(m))
        thread.sort(key=lambda x: x["timestamp"])
        return thread

    def stats(self, agent_id: str) -> dict:
        q = self._queues.get(agent_id, [])
        return {
            "total": len(q),
            "unread": sum(1 for m in q if not m.read),
        }

    def _load(self):
        if BUS_PATH.exists():
            try:
                data = json.loads(BUS_PATH.read_text())
                for aid, msgs in data.get("queues", {}).items():
                    self._queues[aid] = [Message(**m) for m in msgs]
                    for m in self._queues[aid]:
                        self._all[m.msg_id] = m
            except Exception:
                pass

    def _save(self):
        BUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = {
            "queues": {
                aid: [asdict(m) for m in msgs]
                for aid, msgs in self._queues.items()
            }
        }
        BUS_PATH.write_text(json.dumps(out, indent=2))
