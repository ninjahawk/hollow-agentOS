"""
ModelManager — VRAM-aware model routing for AgentOS v0.9.0.

The scheduler's static complexity→model routing ignores what is actually
loaded in VRAM. Switching from a loaded model to a lighter one costs
15–30s of eviction+load time. This module tracks VRAM state via Ollama's
/api/ps endpoint and recommends a model with cache affinity:

  prefer loaded → avoid eviction → evict LRU only when necessary

Eviction policy:
  - "lru"        — evict when VRAM is needed (default)
  - "pinned"     — never evict (root/high-priority models)
  - "background" — evict first, even before LRU
"""

import json
import time
import threading
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional

OLLAMA_BASE = "http://localhost:11434"

# VRAM headroom required before loading a new model (MB)
# Prevents OOM by leaving a buffer for model overhead + KV cache
VRAM_HEADROOM_MB = 512

# Complexity → preferred model name (used for affinity check + fallback)
COMPLEXITY_MODEL = {
    1: "qwen3.5:9b-gpu",
    2: "qwen3.5:9b-gpu",
    3: "qwen2.5:14b",
    4: "qwen2.5:14b",
    5: "qwen3.5-35b-moe:latest",
}

# Models that are too large to swap in/out frequently — pin them once loaded
PINNED_MODELS: set[str] = {"qwen3.5-35b-moe:latest"}

# Approximate VRAM footprint when Ollama's /api/ps doesn't report size (MB)
MODEL_VRAM_FALLBACK: dict[str, int] = {
    "qwen3.5:9b-gpu":         8_000,
    "qwen2.5:14b":             10_000,
    "qwen3.5-35b-moe:latest":  22_000,
}


@dataclass
class ModelSlot:
    model_name: str
    vram_mb: int                        # reported or estimated VRAM usage
    loaded_since: float                 # epoch time when loaded
    last_used: float                    # epoch time of last task dispatch
    eviction_policy: str = "lru"        # "lru" | "pinned" | "background"


class ModelManager:
    """
    Tracks loaded Ollama models and recommends which model to run next.

    Thread-safe. Refreshes VRAM state from Ollama before each recommend().
    """

    def __init__(self, event_bus=None):
        self._lock = threading.Lock()
        self._slots: dict[str, ModelSlot] = {}   # model_name → ModelSlot
        self._event_bus = event_bus
        self._vram_total_mb: int = 0              # populated on first refresh
        self._vram_pressure_warned: bool = False

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    # ── Public API ──────────────────────────────────────────────────────────

    def recommend(self, complexity: int, prefer_loaded: bool = True) -> str:
        """
        Return the best model name for this complexity level.

        1. Preferred model already loaded → return it (zero cost, affinity win)
        2. Preferred model not loaded, but fits in available VRAM → load it
        3. Must evict: evict LRU non-pinned (background first), then load
        4. Fallback: return preferred model name and let Ollama handle it
        """
        preferred = COMPLEXITY_MODEL.get(complexity, "qwen3.5:9b-gpu")
        self._refresh()

        with self._lock:
            if preferred in self._slots:
                self._slots[preferred].last_used = time.time()
                return preferred

            if not prefer_loaded:
                return preferred

            available = self._available_vram_mb()
            needed = MODEL_VRAM_FALLBACK.get(preferred, 8_000)

            if available >= needed + VRAM_HEADROOM_MB:
                # Fits alongside current models — Ollama will load on first use
                return preferred

            # Must evict — pick background first, then LRU
            evicted = self._evict_for(needed)
            if evicted:
                if self._event_bus:
                    self._event_bus.emit("model.evicted", "scheduler", {
                        "evicted_model": evicted,
                        "loading_model": preferred,
                        "complexity":    complexity,
                    })

        return preferred

    def mark_used(self, model_name: str) -> None:
        """Call after a task completes to update LRU timestamp."""
        with self._lock:
            if model_name in self._slots:
                self._slots[model_name].last_used = time.time()

    def get_loaded(self) -> list[dict]:
        """Return current loaded models with VRAM info."""
        self._refresh()
        with self._lock:
            return [
                {
                    "model_name":     s.model_name,
                    "vram_mb":        s.vram_mb,
                    "loaded_since":   s.loaded_since,
                    "last_used":      s.last_used,
                    "eviction_policy": s.eviction_policy,
                }
                for s in self._slots.values()
            ]

    def get_available_vram(self) -> int:
        """Return available VRAM in MB (0 if Ollama unreachable)."""
        self._refresh()
        with self._lock:
            return self._available_vram_mb()

    def get_vram_total(self) -> int:
        self._refresh()
        return self._vram_total_mb

    def status(self) -> dict:
        """Full VRAM status snapshot — used by model_status MCP tool."""
        self._refresh()
        with self._lock:
            used = sum(s.vram_mb for s in self._slots.values())
            total = self._vram_total_mb
            return {
                "vram_total_mb":     total,
                "vram_used_mb":      used,
                "vram_available_mb": max(0, total - used),
                "loaded_models":     [
                    {
                        "model_name":      s.model_name,
                        "vram_mb":         s.vram_mb,
                        "eviction_policy": s.eviction_policy,
                        "idle_seconds":    round(time.time() - s.last_used),
                    }
                    for s in self._slots.values()
                ],
                "pressure": (total > 0 and used / total > 0.90),
            }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        """Poll Ollama /api/ps and update _slots. Non-blocking on failure."""
        try:
            req = urllib.request.Request(f"{OLLAMA_BASE}/api/ps", method="GET")
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read())
        except Exception:
            return  # Ollama unreachable — keep stale state, don't crash

        now = time.time()
        loaded_names: set[str] = set()

        for entry in data.get("models", []):
            name = entry.get("name", "")
            if not name:
                continue
            # Ollama reports size_vram in bytes
            vram_bytes = entry.get("size_vram") or entry.get("size") or 0
            vram_mb = max(1, vram_bytes // (1024 * 1024))
            loaded_names.add(name)

            with self._lock:
                if name not in self._slots:
                    policy = "pinned" if name in PINNED_MODELS else "lru"
                    self._slots[name] = ModelSlot(
                        model_name=name,
                        vram_mb=vram_mb,
                        loaded_since=now,
                        last_used=now,
                        eviction_policy=policy,
                    )
                    if self._event_bus:
                        self._event_bus.emit("model.loaded", "scheduler", {
                            "model_name": name,
                            "vram_mb":    vram_mb,
                        })
                else:
                    self._slots[name].vram_mb = vram_mb

        # Remove models that Ollama unloaded (no longer in /api/ps)
        with self._lock:
            gone = set(self._slots) - loaded_names
            for name in gone:
                del self._slots[name]

            # VRAM total — Ollama doesn't expose this directly, so estimate from
            # GPU info if available, else use sum of loaded + reasonable headroom
            # If a "gpu_memory" field ever appears in /api/ps, use it here.
            # For now: vram_total = max(sum_loaded * 1.2, last known total)
            used = sum(s.vram_mb for s in self._slots.values())
            estimated_total = max(int(used * 1.25), self._vram_total_mb, 8_000)
            self._vram_total_mb = estimated_total

            # VRAM pressure event (>90%)
            if self._vram_total_mb > 0:
                ratio = used / self._vram_total_mb
                if ratio > 0.90 and not self._vram_pressure_warned:
                    self._vram_pressure_warned = True
                    if self._event_bus:
                        self._event_bus.emit("vram.pressure", "scheduler", {
                            "vram_used_mb":    used,
                            "vram_total_mb":   self._vram_total_mb,
                            "loaded_models":   list(self._slots.keys()),
                        })
                elif ratio <= 0.90:
                    self._vram_pressure_warned = False

    def _available_vram_mb(self) -> int:
        """Must be called under self._lock."""
        used = sum(s.vram_mb for s in self._slots.values())
        return max(0, self._vram_total_mb - used)

    def _evict_for(self, needed_mb: int) -> Optional[str]:
        """
        Evict the lowest-priority model to free at least needed_mb VRAM.
        Priority: background first, then LRU among lru-policy models.
        Pinned models are never evicted.
        Returns the evicted model name, or None if nothing evictable.
        Must be called under self._lock.
        """
        candidates = [
            s for s in self._slots.values()
            if s.eviction_policy != "pinned"
        ]
        if not candidates:
            return None

        # Background first, then by last_used ascending (LRU)
        candidates.sort(key=lambda s: (
            0 if s.eviction_policy == "background" else 1,
            s.last_used,
        ))

        evicted_name = candidates[0].model_name
        del self._slots[evicted_name]

        # Tell Ollama to unload (best-effort — if it fails, Ollama will evict naturally)
        threading.Thread(
            target=self._ollama_unload,
            args=(evicted_name,),
            daemon=True,
        ).start()

        return evicted_name

    def _ollama_unload(self, model_name: str) -> None:
        """Ask Ollama to unload a model by generating with keep_alive=0."""
        try:
            body = json.dumps({
                "model": model_name,
                "keep_alive": 0,
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_BASE}/api/generate",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass  # Unload is best-effort; Ollama will reclaim VRAM when needed
