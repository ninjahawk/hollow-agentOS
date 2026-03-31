"""
Working Memory Heap — AgentOS v1.0.0.

An LLM's context window is RAM. This module manages it:
- alloc: claim a named slot with content + token count + priority
- read: retrieve content, raising KeyError if freed/expired/not found
- free: release a slot and its tokens
- gc: collect expired (TTL) objects and emit memory.gc_complete
- compress: summarize via mistral-nemo:12b; original to disk, summary in heap
- swap_out / swap_in: serialize object to disk, free active heap slot
- heap_stats: token counts, object counts, fragmentation score

Auto-management (called by memory/manager.py at 80% token budget):
  memory.pressure → compress low-priority objects → swap if still over threshold
"""

import json
import os
import time
import threading
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

HEAP_DIR = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "heaps"
API_BASE = "http://localhost:7777"

# Simple whitespace-based token estimator (no tokenizer dependency)
# GPT-4 rough approximation: 1 token ≈ 4 chars, but we use word count × 1.3
def _count_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.3))


@dataclass
class MemoryObject:
    key: str
    content: str
    token_count: int              # measured on alloc
    priority: int                 # 0-10, higher = protected from compression
    ttl: Optional[float]          # unix timestamp, None = forever
    compression_eligible: bool
    compressed: bool = False
    swapped: bool = False         # True if content is on disk, not in memory
    created_at: float = field(default_factory=time.time)
    last_read_at: float = field(default_factory=time.time)
    disk_path: Optional[str] = None


class WorkingMemoryHeap:
    """
    Per-agent working memory heap. One instance per agent_id.
    Thread-safe. Persists to disk on mutation.
    """

    def __init__(self, agent_id: str, master_token: str = "", event_bus=None):
        self._agent_id = agent_id
        self._master_token = master_token
        self._event_bus = event_bus
        self._lock = threading.Lock()
        self._objects: dict[str, MemoryObject] = {}
        self._heap_dir = HEAP_DIR / agent_id
        self._heap_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    # ── Core operations ──────────────────────────────────────────────────────

    def alloc(
        self,
        key: str,
        content: str,
        priority: int = 5,
        ttl: Optional[float] = None,
        compression_eligible: bool = True,
    ) -> MemoryObject:
        """
        Allocate a named memory slot. If key already exists, overwrites it.
        Returns the new MemoryObject with token_count populated.
        """
        tokens = _count_tokens(content)
        now = time.time()
        obj = MemoryObject(
            key=key,
            content=content,
            token_count=tokens,
            priority=max(0, min(10, priority)),
            ttl=ttl,
            compression_eligible=compression_eligible,
            created_at=now,
            last_read_at=now,
        )
        with self._lock:
            self._objects[key] = obj
            self._save()
        return obj

    def read(self, key: str) -> str:
        """
        Return content for key. Auto-swaps-in if swapped to disk.
        Raises KeyError if key doesn't exist, is freed, or TTL expired.
        """
        with self._lock:
            obj = self._objects.get(key)
            if not obj:
                raise KeyError(f"Memory key '{key}' not found in heap for agent '{self._agent_id}'")
            if obj.ttl and time.time() > obj.ttl:
                del self._objects[key]
                self._save()
                raise KeyError(f"Memory key '{key}' has expired (TTL)")
            if obj.swapped:
                # Auto swap-in
                if obj.disk_path and Path(obj.disk_path).exists():
                    obj.content = Path(obj.disk_path).read_text(encoding="utf-8")
                    obj.swapped = False
                    obj.token_count = _count_tokens(obj.content)
                    self._save()
                else:
                    raise KeyError(f"Memory key '{key}' is swapped but disk_path missing")
            obj.last_read_at = time.time()
            return obj.content

    def free(self, key: str) -> bool:
        """Free a memory slot. Returns True if freed, False if not found."""
        with self._lock:
            if key not in self._objects:
                return False
            obj = self._objects.pop(key)
            # Clean up disk artifact if any
            if obj.disk_path:
                try:
                    Path(obj.disk_path).unlink(missing_ok=True)
                except Exception:
                    pass
            self._save()
        return True

    def gc(self, agent_id: Optional[str] = None) -> dict:
        """
        Collect expired (past TTL) objects.
        Returns {freed_keys, freed_tokens}.
        Emits memory.gc_complete event.
        """
        now = time.time()
        freed_keys = []
        freed_tokens = 0

        with self._lock:
            expired = [
                key for key, obj in self._objects.items()
                if obj.ttl and now > obj.ttl
            ]
            for key in expired:
                obj = self._objects.pop(key)
                freed_tokens += obj.token_count
                freed_keys.append(key)
                if obj.disk_path:
                    try:
                        Path(obj.disk_path).unlink(missing_ok=True)
                    except Exception:
                        pass
            if freed_keys:
                self._save()

        if self._event_bus:
            self._event_bus.emit("memory.gc_complete", self._agent_id, {
                "agent_id":     self._agent_id,
                "freed_keys":   freed_keys,
                "freed_tokens": freed_tokens,
            })

        return {"freed_keys": freed_keys, "freed_tokens": freed_tokens}

    def compress(self, key: str) -> dict:
        """
        Compress object content via mistral-nemo:12b summarization.
        Original content saved to disk. Summary replaces in-heap content.
        Returns {original_tokens, compressed_tokens, ratio}.
        """
        with self._lock:
            obj = self._objects.get(key)
            if not obj:
                raise KeyError(f"Memory key '{key}' not found")
            if obj.compressed:
                return {
                    "original_tokens":    obj.token_count,
                    "compressed_tokens":  obj.token_count,
                    "ratio":              1.0,
                    "note":               "already compressed",
                }
            original_content = obj.content
            original_tokens = obj.token_count

        # Call Ollama via API (outside lock to avoid blocking)
        summary = self._summarize(original_content)
        compressed_tokens = _count_tokens(summary)

        # Save original to disk
        disk_path = self._heap_dir / f"{key}.original.txt"
        disk_path.write_text(original_content, encoding="utf-8")

        with self._lock:
            obj = self._objects.get(key)
            if not obj:
                raise KeyError(f"Memory key '{key}' not found (freed during compression)")
            obj.content = summary
            obj.token_count = compressed_tokens
            obj.compressed = True
            obj.disk_path = str(disk_path)
            self._save()

        ratio = compressed_tokens / max(1, original_tokens)

        if self._event_bus:
            self._event_bus.emit("memory.compressed", self._agent_id, {
                "agent_id":          self._agent_id,
                "key":               key,
                "original_tokens":   original_tokens,
                "compressed_tokens": compressed_tokens,
                "ratio":             ratio,
            })

        return {
            "original_tokens":   original_tokens,
            "compressed_tokens": compressed_tokens,
            "ratio":             ratio,
        }

    def swap_out(self, key: str) -> bool:
        """
        Serialize content to disk, free from active heap (content set to "").
        Returns True on success.
        """
        with self._lock:
            obj = self._objects.get(key)
            if not obj or obj.swapped:
                return False
            disk_path = self._heap_dir / f"{key}.swap"
            disk_path.write_text(obj.content, encoding="utf-8")
            obj.disk_path = str(disk_path)
            obj.content = ""
            obj.token_count = 0
            obj.swapped = True
            self._save()

        if self._event_bus:
            self._event_bus.emit("memory.swapped", self._agent_id, {
                "agent_id":  self._agent_id,
                "key":       key,
                "direction": "out",
            })
        return True

    def swap_in(self, key: str) -> bool:
        """
        Restore swapped content from disk. Returns True on success.
        Also called automatically by read().
        """
        with self._lock:
            obj = self._objects.get(key)
            if not obj:
                return False
            if not obj.swapped:
                return True  # already in memory
            if not obj.disk_path or not Path(obj.disk_path).exists():
                return False
            obj.content = Path(obj.disk_path).read_text(encoding="utf-8")
            obj.token_count = _count_tokens(obj.content)
            obj.swapped = False
            self._save()

        if self._event_bus:
            self._event_bus.emit("memory.swapped", self._agent_id, {
                "agent_id":  self._agent_id,
                "key":       key,
                "direction": "in",
            })
        return True

    def heap_stats(self) -> dict:
        """Return token/object counts and fragmentation score."""
        with self._lock:
            objects = list(self._objects.values())

        total_tokens = sum(o.token_count for o in objects if not o.swapped)
        swapped_tokens = sum(
            _count_tokens(Path(o.disk_path).read_text(encoding="utf-8"))
            if o.disk_path and Path(o.disk_path).exists() else 0
            for o in objects if o.swapped
        )
        compressible = sum(
            o.token_count for o in objects
            if o.compression_eligible and not o.compressed and not o.swapped
        )

        # Fragmentation score: ratio of freed slots we'd need to reclaim
        # vs total addressable slots. Higher = more fragmented.
        # Here we approximate: number of swapped objects / total objects
        total = len(objects)
        swapped_count = sum(1 for o in objects if o.swapped)
        fragmentation_score = round(swapped_count / max(1, total), 3)

        return {
            "agent_id":            self._agent_id,
            "object_count":        total,
            "total_tokens":        total_tokens,
            "compressible_tokens": compressible,
            "swapped_count":       swapped_count,
            "swapped_tokens":      swapped_tokens,
            "fragmentation_score": fragmentation_score,
        }

    def list_objects(self) -> list[dict]:
        """List all objects with metadata (no content — just stats)."""
        with self._lock:
            return [
                {
                    "key":                   o.key,
                    "token_count":           o.token_count,
                    "priority":              o.priority,
                    "ttl":                   o.ttl,
                    "compression_eligible":  o.compression_eligible,
                    "compressed":            o.compressed,
                    "swapped":               o.swapped,
                    "created_at":            o.created_at,
                    "last_read_at":          o.last_read_at,
                    "expires_in_seconds":    round(o.ttl - time.time()) if o.ttl else None,
                }
                for o in self._objects.values()
            ]

    # ── Auto-management ──────────────────────────────────────────────────────

    def auto_manage(self, token_budget: int) -> dict:
        """
        Called at 80% budget usage. Compress bottom quartile, swap if still over.
        Returns summary of actions taken.
        """
        stats = self.heap_stats()
        threshold = int(token_budget * 0.80)
        if stats["total_tokens"] < threshold:
            return {"action": "none", "total_tokens": stats["total_tokens"]}

        # Emit pressure event
        if self._event_bus:
            self._event_bus.emit("memory.pressure", self._agent_id, {
                "agent_id":     self._agent_id,
                "total_tokens": stats["total_tokens"],
                "budget":       token_budget,
            })

        # Sort compressible objects by (priority ASC, last_read_at ASC) — compress least-used first
        with self._lock:
            candidates = sorted(
                [o for o in self._objects.values()
                 if o.compression_eligible and not o.compressed and not o.swapped],
                key=lambda o: (o.priority, o.last_read_at),
            )
            quartile = max(1, len(candidates) // 4)
            to_compress = [o.key for o in candidates[:quartile]]

        compressed_keys = []
        for key in to_compress:
            try:
                self.compress(key)
                compressed_keys.append(key)
            except Exception:
                pass

        # Re-check
        stats2 = self.heap_stats()
        swapped_keys = []
        if stats2["total_tokens"] >= threshold:
            # Swap out oldest low-priority objects
            with self._lock:
                swappable = sorted(
                    [o for o in self._objects.values()
                     if not o.swapped and o.priority < 5],
                    key=lambda o: (o.priority, o.last_read_at),
                )
                to_swap = [o.key for o in swappable[:max(1, len(swappable) // 4)]]
            for key in to_swap:
                if self.swap_out(key):
                    swapped_keys.append(key)

        return {
            "compressed_keys": compressed_keys,
            "swapped_keys":    swapped_keys,
            "tokens_before":   stats["total_tokens"],
            "tokens_after":    self.heap_stats()["total_tokens"],
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _summarize(self, content: str) -> str:
        """
        Summarize content via the API's Ollama endpoint.
        Falls back to truncation if Ollama unavailable.
        """
        prompt = (
            "Summarize the following content concisely, preserving all key facts, "
            "decisions, numbers, and entities. Output only the summary, no preamble.\n\n"
            + content[:8000]  # cap input to avoid OOM
        )
        try:
            body = json.dumps({
                "model": "mistral-nemo:12b",
                "messages": [{"role": "user", "content": prompt}],
            }).encode()
            req = urllib.request.Request(
                f"{API_BASE}/ollama/chat",
                data=body,
                headers={
                    "Authorization": f"Bearer {self._master_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read())
            summary = resp.get("response", "").strip()
            if summary:
                return summary
        except Exception:
            pass
        # Fallback: hard truncate to 20% of original
        words = content.split()
        cutoff = max(50, len(words) // 5)
        return " ".join(words[:cutoff]) + " [truncated]"

    def _load(self) -> None:
        index_path = self._heap_dir / "index.json"
        if not index_path.exists():
            return
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            for d in data.values():
                d.setdefault("compressed", False)
                d.setdefault("swapped", False)
                d.setdefault("created_at", time.time())
                d.setdefault("last_read_at", time.time())
                d.setdefault("disk_path", None)
                obj = MemoryObject(**d)
                self._objects[obj.key] = obj
        except Exception:
            pass

    def _save(self) -> None:
        index_path = self._heap_dir / "index.json"
        data = {}
        for key, obj in self._objects.items():
            d = asdict(obj)
            data[key] = d
        index_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Registry of heaps — one per agent, created on demand
# ---------------------------------------------------------------------------

class HeapRegistry:
    """
    Global registry of WorkingMemoryHeap instances.
    Server holds one HeapRegistry; agent_routes call into it.
    """

    def __init__(self, master_token: str = "", event_bus=None):
        self._master_token = master_token
        self._event_bus = event_bus
        self._lock = threading.Lock()
        self._heaps: dict[str, WorkingMemoryHeap] = {}

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus
        with self._lock:
            for heap in self._heaps.values():
                heap.set_event_bus(event_bus)

    def get(self, agent_id: str) -> WorkingMemoryHeap:
        with self._lock:
            if agent_id not in self._heaps:
                self._heaps[agent_id] = WorkingMemoryHeap(
                    agent_id=agent_id,
                    master_token=self._master_token,
                    event_bus=self._event_bus,
                )
            return self._heaps[agent_id]

    def auto_manage_all(self, registry) -> None:
        """
        Walk all agents, trigger auto_manage if they're at 80% token budget.
        Called periodically (e.g. from a background thread or event handler).
        """
        for agent_id, heap in list(self._heaps.items()):
            agent = registry.get(agent_id)
            if not agent:
                continue
            budget_tokens_in = agent.budget.get("tokens_in", 0)
            if budget_tokens_in <= 0:
                continue
            used = agent.usage.get("tokens_in", 0)
            if used >= int(budget_tokens_in * 0.80):
                heap.auto_manage(budget_tokens_in)
