"""
agents/lineage.py — Agent Lineage and Call Graphs (v1.3.0)

Persists a directed graph of agent relationships so post-mortems and
parallel-workflow debugging have a complete causal record.

Edge types:
  spawned     — parent spawned child via spawn_agent()
  delegated   — agent handed a task to another agent
  signaled    — agent sent a signal to another
  transacted  — agents participated in the same transaction
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

LINEAGE_PATH = (
    Path(os.environ.get("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "lineage.json"
)


@dataclass
class LineageEdge:
    edge_id: str
    parent_id: str
    child_id: str
    edge_type: str          # spawned | delegated | signaled | transacted
    metadata: dict
    timestamp: float = field(default_factory=time.time)


class LineageGraph:
    """
    Directed graph of agent relationships.  Thread-safe, persisted to
    lineage.json (full rewrite on each mutation — graph stays small).
    """

    def __init__(self, registry=None, scheduler=None, txn_coordinator=None):
        self._lock = threading.Lock()
        self._edges: list[LineageEdge] = []
        self._registry = registry
        self._scheduler = scheduler
        self._txn_coordinator = txn_coordinator
        LINEAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def set_subsystems(self, registry=None, scheduler=None, txn_coordinator=None):
        if registry:
            self._registry = registry
        if scheduler:
            self._scheduler = scheduler
        if txn_coordinator:
            self._txn_coordinator = txn_coordinator

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if LINEAGE_PATH.exists():
            try:
                data = json.loads(LINEAGE_PATH.read_text(encoding="utf-8"))
                self._edges = [LineageEdge(**e) for e in data]
            except Exception:
                pass

    def _save(self) -> None:
        """Must be called under self._lock."""
        LINEAGE_PATH.write_text(
            json.dumps([asdict(e) for e in self._edges], indent=2),
            encoding="utf-8",
        )

    # ── Core mutation ──────────────────────────────────────────────────────────

    def record_edge(
        self,
        parent_id: str,
        child_id: str,
        edge_type: str,
        metadata: dict,
    ) -> str:
        """Record a directed relationship. Returns edge_id."""
        edge = LineageEdge(
            edge_id=str(uuid.uuid4())[:12],
            parent_id=parent_id,
            child_id=child_id,
            edge_type=edge_type,
            metadata=metadata,
        )
        with self._lock:
            self._edges.append(edge)
            self._save()
        return edge.edge_id

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_lineage(self, agent_id: str) -> list[dict]:
        """
        Ancestor chain from agent_id up to root (inclusive).
        Result is ordered [agent, parent, grandparent, ..., root].
        """
        if not self._registry:
            return []
        chain = []
        current_id = agent_id
        seen: set = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            agent = self._registry.get(current_id)
            if not agent:
                break
            chain.append(agent.to_dict())
            current_id = agent.parent_id
        return chain

    def get_subtree(self, root_id: str) -> dict:
        """
        Full descendant call tree rooted at root_id.
        Returns nested dict: {agent, edges, children: {child_id: subtree}}.
        """
        if not self._registry:
            return {}

        def build_node(agent_id: str, visited: set) -> dict:
            if agent_id in visited:
                return {"agent": {"agent_id": agent_id}, "edges": [], "children": {}}
            visited = visited | {agent_id}
            agent = self._registry.get(agent_id)
            with self._lock:
                outgoing = [asdict(e) for e in self._edges if e.parent_id == agent_id]
            children = {}
            for edge in outgoing:
                child_id = edge["child_id"]
                children[child_id] = build_node(child_id, visited)
            return {
                "agent": agent.to_dict() if agent else {"agent_id": agent_id},
                "edges": outgoing,
                "children": children,
            }

        node = build_node(root_id, set())
        # Annotate with total descendant count
        node["descendant_count"] = _count_descendants(node)
        return node

    def get_blast_radius(self, agent_id: str) -> dict:
        """
        Forward-reachability impact: which agents, tasks, and files are at
        risk if this agent fails right now?
        """
        subtree = self.get_subtree(agent_id)
        affected_agents = _collect_descendants(subtree)

        locked_resources: list[str] = []
        if self._registry:
            agent = self._registry.get(agent_id)
            if agent:
                locked_resources = list(agent.locks.keys())

        open_txns: list[str] = []
        if self._txn_coordinator:
            try:
                # Use internal _txns dict — TransactionCoordinator exposes no list method
                with self._txn_coordinator._lock:
                    open_txns = [
                        txn_id
                        for txn_id, rec in self._txn_coordinator._txns.items()
                        if rec.agent_id == agent_id and rec.status == "open"
                    ]
            except Exception:
                pass

        running_tasks: list[str] = []
        if self._scheduler:
            tasks = self._scheduler.list_tasks(agent_id=agent_id, limit=200)
            running_tasks = [
                t["task_id"]
                for t in tasks
                if t.get("status") in ("queued", "running")
            ]

        return {
            "agent_id": agent_id,
            "affected_agents": affected_agents,
            "affected_agent_count": len(affected_agents),
            "locked_resources": locked_resources,
            "open_transactions": open_txns,
            "running_tasks": running_tasks,
        }

    def critical_path(self, task_id: str) -> list[str]:
        """
        Longest dependency chain through the task graph starting at task_id.
        Requires Task.depends_on to be populated (v1.3.0).
        """
        if not self._scheduler:
            return [task_id]

        # Build a forward-adjacency map: task_id → tasks that list it in depends_on
        all_tasks = self._scheduler.list_tasks(limit=1000)
        forward: dict[str, list[str]] = {}
        for t in all_tasks:
            for dep in t.get("depends_on", []):
                forward.setdefault(dep, []).append(t["task_id"])

        def longest_path(tid: str, visited: set) -> list[str]:
            if tid in visited:
                return [tid]
            visited = visited | {tid}
            successors = forward.get(tid, [])
            if not successors:
                return [tid]
            best: list[str] = []
            for s in successors:
                path = longest_path(s, visited)
                if len(path) > len(best):
                    best = path
            return [tid] + best

        return longest_path(task_id, set())

    def list_edges(self, agent_id: Optional[str] = None) -> list[dict]:
        with self._lock:
            edges = list(self._edges)
        if agent_id:
            edges = [e for e in edges if e.parent_id == agent_id or e.child_id == agent_id]
        return [asdict(e) for e in edges]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _count_descendants(node: dict) -> int:
    total = 0
    for child_node in node.get("children", {}).values():
        total += 1 + _count_descendants(child_node)
    return total


def _collect_descendants(node: dict) -> list[str]:
    result = []
    for child_id, child_node in node.get("children", {}).items():
        result.append(child_id)
        result.extend(_collect_descendants(child_node))
    return result
