"""
Resource Manager — AgentOS v3.20.0.

Agents monitor their own storage footprint and act before limits are hit.
Runs automatically after goal completions via the autonomy loop.

Operations:
  - Memory pruning: remove old/low-access semantic memories to stay under capacity
  - Reasoning compaction: summarize old reasoning history into a single entry
  - Capability audit: flag capabilities unused for >7 days
  - Execution chain trimming: keep only recent execution steps on disk

ResourceManager:
  check_footprint(agent_id) → ResourceReport
  prune_memories(agent_id, max_entries=500) → int (pruned count)
  compact_reasoning(agent_id, keep_recent=50) → bool
  trim_execution_chain(agent_id, keep_recent=200) → int (trimmed count)
  audit_capabilities(graph) → list[str] (unused capability IDs)
  auto_manage(agent_id, graph=None) → ResourceReport
"""

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List

MEMORY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory"))
AUTONOMY_PATH = MEMORY_PATH / "autonomy"
REASONING_PATH = MEMORY_PATH / "reasoning"
SEMANTIC_PATH = MEMORY_PATH / "semantic"

# Thresholds that trigger automatic action
MAX_SEMANTIC_ENTRIES = 500    # per agent
MAX_REASONING_ENTRIES = 200   # per agent
MAX_EXECUTION_ENTRIES = 500   # per agent
UNUSED_CAP_DAYS = 7           # flag capability if not used in N days


@dataclass
class ResourceReport:
    agent_id: str
    semantic_entries: int = 0
    reasoning_entries: int = 0
    execution_entries: int = 0
    semantic_pruned: int = 0
    reasoning_compacted: bool = False
    execution_trimmed: int = 0
    unused_capabilities: List[str] = field(default_factory=list)
    disk_bytes: int = 0
    timestamp: float = field(default_factory=time.time)


class ResourceManager:
    """Monitor and enforce resource limits for agents."""

    def __init__(self):
        self._lock = threading.Lock()

    # ── Footprint Check ────────────────────────────────────────────────────

    def check_footprint(self, agent_id: str) -> ResourceReport:
        """Return a snapshot of how much space this agent is using."""
        report = ResourceReport(agent_id=agent_id)

        sem_dir = SEMANTIC_PATH / agent_id
        if sem_dir.exists():
            meta = sem_dir / "metadata.jsonl"
            if meta.exists():
                lines = [l for l in meta.read_text().strip().split("\n") if l.strip()]
                report.semantic_entries = len(lines)

        rea_dir = REASONING_PATH / agent_id
        if rea_dir.exists():
            hist = rea_dir / "history.jsonl"
            if hist.exists():
                lines = [l for l in hist.read_text().strip().split("\n") if l.strip()]
                report.reasoning_entries = len(lines)

        aut_dir = AUTONOMY_PATH / agent_id
        if aut_dir.exists():
            chain = aut_dir / "execution_chain.jsonl"
            if chain.exists():
                lines = [l for l in chain.read_text().strip().split("\n") if l.strip()]
                report.execution_entries = len(lines)

        # Disk usage
        for base in (sem_dir, rea_dir, aut_dir):
            if base.exists():
                for f in base.rglob("*"):
                    if f.is_file():
                        try:
                            report.disk_bytes += f.stat().st_size
                        except OSError:
                            pass

        return report

    # ── Semantic Memory Pruning ────────────────────────────────────────────

    def prune_memories(self, agent_id: str, max_entries: int = MAX_SEMANTIC_ENTRIES) -> int:
        """
        Remove oldest/least-accessed semantic memories to stay under max_entries.
        Preserves entries with 'Goal completed' or 'Self-directed' in them (important).
        Returns number of entries pruned.
        """
        sem_dir = SEMANTIC_PATH / agent_id
        meta_file = sem_dir / "metadata.jsonl"
        emb_file = sem_dir / "embeddings.npy"

        if not meta_file.exists() or not emb_file.exists():
            return 0

        with self._lock:
            try:
                import numpy as np
                lines = [l for l in meta_file.read_text().strip().split("\n") if l.strip()]
                if len(lines) <= max_entries:
                    return 0

                records = [json.loads(l) for l in lines]
                embeddings = np.load(emb_file)

                # Score: high score = keep. Low score = prune.
                def keep_score(r: dict) -> float:
                    s = 0.0
                    s += r.get("access_count", 0) * 10.0
                    thought = r.get("thought", "")
                    if "Goal completed" in thought:
                        s += 100.0
                    if "Self-directed" in thought:
                        s += 50.0
                    if "FAILED" in thought:
                        s += 30.0  # keep failure records too
                    age_days = (time.time() - r.get("timestamp", time.time())) / 86400
                    s -= age_days * 0.5
                    return s

                scored = sorted(enumerate(records), key=lambda x: keep_score(x[1]), reverse=True)
                keep_indices = sorted([i for i, _ in scored[:max_entries]])
                prune_count = len(lines) - len(keep_indices)

                new_lines = [lines[i] for i in keep_indices]
                new_embeddings = embeddings[keep_indices]

                meta_file.write_text("\n".join(new_lines) + "\n")
                np.save(emb_file, new_embeddings)

                # Rebuild index
                index_file = sem_dir / "index.json"
                new_index = {}
                for new_pos, old_pos in enumerate(keep_indices):
                    r = records[old_pos]
                    new_index[r.get("memory_id", str(old_pos))] = new_pos
                index_file.write_text(json.dumps(new_index, indent=2))

                return prune_count

            except Exception:
                return 0

    # ── Reasoning History Compaction ───────────────────────────────────────

    def compact_reasoning(self, agent_id: str, keep_recent: int = MAX_REASONING_ENTRIES) -> bool:
        """
        Trim reasoning history to keep_recent most recent entries.
        Drops oldest entries (they've already been learned from).
        Returns True if compaction occurred.
        """
        rea_dir = REASONING_PATH / agent_id
        hist_file = rea_dir / "history.jsonl"

        if not hist_file.exists():
            return False

        with self._lock:
            try:
                lines = [l for l in hist_file.read_text().strip().split("\n") if l.strip()]
                if len(lines) <= keep_recent:
                    return False

                # Keep most recent entries (last N lines)
                trimmed = lines[-keep_recent:]
                hist_file.write_text("\n".join(trimmed) + "\n")
                return True

            except Exception:
                return False

    # ── Execution Chain Trimming ───────────────────────────────────────────

    def trim_execution_chain(self, agent_id: str, keep_recent: int = MAX_EXECUTION_ENTRIES) -> int:
        """
        Trim the execution chain to keep only recent steps.
        Returns number of entries removed.
        """
        aut_dir = AUTONOMY_PATH / agent_id
        chain_file = aut_dir / "execution_chain.jsonl"

        if not chain_file.exists():
            return 0

        with self._lock:
            try:
                lines = [l for l in chain_file.read_text().strip().split("\n") if l.strip()]
                if len(lines) <= keep_recent:
                    return 0

                trimmed = lines[-keep_recent:]
                removed = len(lines) - len(trimmed)
                chain_file.write_text("\n".join(trimmed) + "\n")
                return removed

            except Exception:
                return 0

    # ── Capability Audit ───────────────────────────────────────────────────

    def audit_capabilities(self, graph) -> List[str]:
        """
        Return capability IDs that have never been used or haven't been used
        in UNUSED_CAP_DAYS days. These are candidates for removal.
        """
        if graph is None:
            return []

        cutoff = time.time() - (UNUSED_CAP_DAYS * 86400)
        unused = []

        for cap in graph.list_all(limit=1000):
            if cap.usage_count == 0:
                # Never used — only flag if older than 1 day
                if (time.time() - cap.created_at) > 86400:
                    unused.append(cap.capability_id)
            elif cap.last_used < cutoff:
                # Not used recently
                unused.append(cap.capability_id)

        return unused

    # ── Auto-Manage ────────────────────────────────────────────────────────

    def auto_manage(self, agent_id: str, graph=None) -> ResourceReport:
        """
        Run all resource checks and act if thresholds are exceeded.
        Safe to call after every goal completion.
        """
        report = self.check_footprint(agent_id)

        if report.semantic_entries > MAX_SEMANTIC_ENTRIES:
            report.semantic_pruned = self.prune_memories(agent_id, MAX_SEMANTIC_ENTRIES)

        if report.reasoning_entries > MAX_REASONING_ENTRIES:
            report.reasoning_compacted = self.compact_reasoning(agent_id)

        if report.execution_entries > MAX_EXECUTION_ENTRIES:
            report.execution_trimmed = self.trim_execution_chain(agent_id)

        if graph is not None:
            report.unused_capabilities = self.audit_capabilities(graph)

        return report