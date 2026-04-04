"""
Agent Registry — identity, isolation, capabilities, resource accounting.

Every process that wants to use AgentOS must register and get an agent_id + token.
The master token (from config) acts as root — it can register/terminate agents
but runs as the "root" agent with full capabilities.
"""

import json
import hashlib
import hmac
import time
import uuid
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import threading

REGISTRY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory") + "/agent-registry.json")
WORKSPACE_ROOT = Path(os.getenv("AGENTOS_WORKSPACE_ROOT", "/agentOS/workspace/agents"))

# All possible capabilities an agent can be granted
ALL_CAPS = {
    "shell",      # run shell commands (scoped to workspace)
    "shell_root", # run shell commands anywhere (root agents only)
    "fs_read",    # read any file
    "fs_write",   # write files (scoped to workspace unless fs_root)
    "fs_root",    # write anywhere
    "ollama",     # call local models
    "spawn",      # create child agents
    "message",    # send/receive inter-agent messages
    "semantic",   # semantic search
    "admin",      # manage other agents
}

# Default capability sets by role
ROLE_DEFAULTS: dict[str, set[str]] = {
    "root":      ALL_CAPS,
    "orchestrator": {"shell", "fs_read", "fs_write", "ollama", "spawn", "message", "semantic"},
    "worker":    {"shell", "fs_read", "fs_write", "ollama", "message", "semantic"},
    "readonly":  {"fs_read", "semantic", "message"},
    "coder":     {"shell", "fs_read", "fs_write", "ollama", "message", "semantic"},
    "reasoner":  {"fs_read", "ollama", "message", "semantic"},
    "custom":    {"fs_read", "message"},
}

# Default resource budgets by role (soft limits — warn; hard limits — block)
ROLE_BUDGETS: dict[str, dict] = {
    "root":         {"shell_calls": 10000, "tokens_in": 10_000_000, "tokens_out": 10_000_000},
    "orchestrator": {"shell_calls": 1000,  "tokens_in": 500_000,    "tokens_out": 500_000},
    "worker":       {"shell_calls": 200,   "tokens_in": 100_000,    "tokens_out": 100_000},
    "readonly":     {"shell_calls": 0,     "tokens_in": 50_000,     "tokens_out": 0},
    "coder":        {"shell_calls": 500,   "tokens_in": 200_000,    "tokens_out": 200_000},
    "reasoner":     {"shell_calls": 10,    "tokens_in": 200_000,    "tokens_out": 200_000},
    "custom":       {"shell_calls": 50,    "tokens_in": 50_000,     "tokens_out": 50_000},
}


MAX_SPAWN_DEPTH = 5  # prevent recursive agent storms

@dataclass
class AgentRecord:
    agent_id: str
    name: str
    role: str
    capabilities: list[str]
    token_hash: str          # sha256 of the actual token — never store raw
    workspace_dir: str
    status: str              # registered | active | suspended | terminated
    budget: dict             # max limits
    usage: dict              # current usage
    created_at: float
    parent_id: Optional[str] = None
    spawn_depth: int = 0     # distance from root; capped at MAX_SPAWN_DEPTH
    current_task: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    locks: dict = field(default_factory=dict)          # name → {acquired_at, expires_at}
    model_policies: dict = field(default_factory=dict) # model_name → [allowed_caps]
    group_id: Optional[str] = None        # v0.8.0: process group (spawner's agent_id)
    tombstone_path: Optional[str] = None  # v0.8.0: path to tombstone.json after termination
    parent_task_id: Optional[str] = None  # v1.3.0: task that caused this agent to be spawned

    def has_cap(self, cap: str) -> bool:
        return cap in self.capabilities

    def over_budget(self) -> Optional[str]:
        """Return the resource name if over hard limit, else None."""
        for key, limit in self.budget.items():
            if self.usage.get(key, 0) >= limit:
                return key
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("token_hash")  # never expose hash via API
        return d


def _token_for(agent_id: str, master_secret: str) -> str:
    """Derive agent token from agent_id + master secret. Deterministic."""
    return hmac.new(
        master_secret.encode(),
        agent_id.encode(),
        hashlib.sha256,
    ).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AgentRegistry:
    def __init__(self, master_token: str):
        self._lock = threading.Lock()
        self._agents: dict[str, AgentRecord] = {}
        self._token_index: dict[str, str] = {}  # token_hash → agent_id (O(1) auth)
        self._master_token = master_token
        self._master_hash = _hash_token(master_token)
        self._event_bus = None
        # Track budget thresholds already warned — reset on restart (intentional)
        self._budget_warned: set = set()       # "{agent_id}:{resource}" keys at 80%
        self._budget_exhausted: set = set()    # "{agent_id}:{resource}" keys at 100%
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        self._load()
        self._ensure_root()
        self._ensure_core_agents()

    def set_event_bus(self, event_bus) -> None:
        """Inject EventBus after both are created. Called at server startup."""
        self._event_bus = event_bus

    def _ensure_root(self):
        """Root agent always exists — represents the master token caller."""
        if "root" not in self._agents:
            root = AgentRecord(
                agent_id="root",
                name="root",
                role="root",
                capabilities=list(ALL_CAPS),
                token_hash=self._master_hash,
                workspace_dir=str(WORKSPACE_ROOT / "root"),
                status="active",
                budget=ROLE_BUDGETS["root"].copy(),
                usage={"shell_calls": 0, "tokens_in": 0, "tokens_out": 0},
                created_at=time.time(),
                spawn_depth=0,
            )
            self._agents["root"] = root
            self._token_index[self._master_hash] = "root"
            Path(root.workspace_dir).mkdir(parents=True, exist_ok=True)
            self._save()

    def _ensure_core_agents(self):
        """Scout, analyst, and builder always exist as first-class registered agents."""
        core = [
            ("scout",   "orchestrator", "Scout — discovers and maps the codebase"),
            ("analyst", "coder",        "Analyst — identifies issues and proposes fixes"),
            ("builder", "coder",        "Builder — implements code changes and new capabilities"),
        ]
        changed = False
        for agent_id, role, description in core:
            if agent_id in self._agents:
                # Ensure status is active in case it got suspended or terminated
                if self._agents[agent_id].status != "active":
                    self._agents[agent_id].status = "active"
                    changed = True
                continue
            raw_token = _token_for(agent_id, self._master_token)
            token_hash = _hash_token(raw_token)
            workspace = WORKSPACE_ROOT / agent_id
            workspace.mkdir(parents=True, exist_ok=True)
            caps = list(ROLE_DEFAULTS.get(role, ROLE_DEFAULTS["worker"]))
            record = AgentRecord(
                agent_id=agent_id,
                name=agent_id.capitalize(),
                role=role,
                capabilities=caps,
                token_hash=token_hash,
                workspace_dir=str(workspace),
                status="active",
                budget=ROLE_BUDGETS.get(role, ROLE_BUDGETS["worker"]).copy(),
                usage={"shell_calls": 0, "tokens_in": 0, "tokens_out": 0},
                created_at=time.time(),
                spawn_depth=1,
                metadata={"description": description, "core": True},
            )
            self._agents[agent_id] = record
            self._token_index[token_hash] = agent_id
            changed = True
        if changed:
            self._save()

    def register(
        self,
        name: str,
        role: str = "worker",
        capabilities: Optional[list[str]] = None,
        budget: Optional[dict] = None,
        parent_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        model_policies: Optional[dict] = None,
        group_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
    ) -> tuple[AgentRecord, str]:
        """Register a new agent. Returns (record, raw_token)."""
        role = role if role in ROLE_DEFAULTS else "custom"
        agent_id = str(uuid.uuid4())[:8]

        # Capabilities: intersection of requested and role defaults
        allowed = ROLE_DEFAULTS[role]
        if capabilities:
            caps = list(allowed & set(capabilities))
        else:
            caps = list(allowed)

        raw_token = _token_for(agent_id, self._master_token)
        token_hash = _hash_token(raw_token)
        workspace = WORKSPACE_ROOT / agent_id
        workspace.mkdir(parents=True, exist_ok=True)

        # Compute spawn depth from parent
        parent_record = self._agents.get(parent_id) if parent_id else None
        depth = (parent_record.spawn_depth + 1) if parent_record else 0
        if depth > MAX_SPAWN_DEPTH:
            raise ValueError(f"Spawn depth limit ({MAX_SPAWN_DEPTH}) exceeded — infinite recursion guard")

        record = AgentRecord(
            agent_id=agent_id,
            name=name,
            role=role,
            capabilities=caps,
            token_hash=token_hash,
            workspace_dir=str(workspace),
            status="active",
            budget=(budget or ROLE_BUDGETS.get(role, ROLE_BUDGETS["custom"])).copy(),
            usage={"shell_calls": 0, "tokens_in": 0, "tokens_out": 0},
            created_at=time.time(),
            parent_id=parent_id,
            spawn_depth=depth,
            metadata=metadata or {},
            locks={},
            model_policies=model_policies or {},
            group_id=group_id,
            parent_task_id=parent_task_id,
        )

        with self._lock:
            self._agents[agent_id] = record
            self._token_index[token_hash] = agent_id
            self._save()

        if self._event_bus:
            self._event_bus.emit("agent.registered", agent_id, {
                "agent_id":   agent_id,
                "name":       name,
                "role":       role,
                "parent_id":  parent_id,
                "spawn_depth": depth,
                "group_id":   group_id,
            })

        return record, raw_token

    def authenticate(self, token: str) -> Optional[AgentRecord]:
        """Resolve a raw token to an agent. O(1) via token hash index."""
        if token == self._master_token:
            return self._agents.get("root")
        token_hash = _hash_token(token)
        agent_id = self._token_index.get(token_hash)
        if not agent_id:
            return None
        agent = self._agents.get(agent_id)
        if agent and agent.status == "active":
            return agent
        return None

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        return self._agents.get(agent_id)

    def get_lineage(self, agent_id: str) -> list[AgentRecord]:
        """Full ancestor chain from agent_id up to root (inclusive)."""
        chain: list[AgentRecord] = []
        current_id = agent_id
        seen: set = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            agent = self._agents.get(current_id)
            if not agent:
                break
            chain.append(agent)
            current_id = agent.parent_id
        return chain

    def list_agents(self) -> list[AgentRecord]:
        return list(self._agents.values())

    def update_usage(self, agent_id: str, shell_calls: int = 0,
                     tokens_in: int = 0, tokens_out: int = 0):
        events_to_emit: list[tuple] = []
        with self._lock:
            a = self._agents.get(agent_id)
            if not a:
                return
            a.usage["shell_calls"] = a.usage.get("shell_calls", 0) + shell_calls
            a.usage["tokens_in"]   = a.usage.get("tokens_in", 0) + tokens_in
            a.usage["tokens_out"]  = a.usage.get("tokens_out", 0) + tokens_out
            # Check budget thresholds and queue events to emit outside the lock
            for key, limit in a.budget.items():
                if limit <= 0:
                    continue
                current = a.usage.get(key, 0)
                pct = current / limit
                wkey = f"{agent_id}:{key}"
                if pct >= 1.0 and wkey not in self._budget_exhausted:
                    self._budget_exhausted.add(wkey)
                    events_to_emit.append(("budget.exhausted", agent_id, {
                        "agent_id": agent_id,
                        "resource": key,
                        "usage":    current,
                        "budget":   limit,
                    }))
                elif pct >= 0.8 and wkey not in self._budget_warned:
                    self._budget_warned.add(wkey)
                    events_to_emit.append(("budget.warning", agent_id, {
                        "agent_id": agent_id,
                        "resource": key,
                        "usage":    current,
                        "budget":   limit,
                        "pct_used": round(pct * 100, 1),
                    }))
            self._save()
        if self._event_bus:
            for etype, source, payload in events_to_emit:
                self._event_bus.emit(etype, source, payload)

    def set_task(self, agent_id: str, task: Optional[str]):
        with self._lock:
            a = self._agents.get(agent_id)
            if a:
                a.current_task = task
                self._save()

    def write_tombstone(self, agent_id: str, reason: str = "explicit",
                        terminated_by: str = "system") -> str:
        """
        Write tombstone.json to the agent's workspace dir.
        Records final state at time of termination. Returns tombstone path.
        Safe to call before status is changed — captures live state.
        """
        a = self._agents.get(agent_id)
        if not a:
            return ""

        children = [aid for aid, rec in self._agents.items()
                    if rec.parent_id == agent_id]

        tombstone = {
            "agent_id":                  agent_id,
            "name":                      a.name,
            "role":                      a.role,
            "terminated_at":             datetime.now(timezone.utc).isoformat(),
            "terminated_by":             terminated_by,
            "reason":                    reason,
            "final_usage":               dict(a.usage),
            "current_task_at_termination": a.current_task,
            "children":                  children,
            "parent_id":                 a.parent_id,
            "group_id":                  a.group_id,
        }

        workspace = Path(a.workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        tombstone_path = workspace / "tombstone.json"
        tombstone_path.write_text(json.dumps(tombstone, indent=2))

        with self._lock:
            b = self._agents.get(agent_id)
            if b:
                b.tombstone_path = str(tombstone_path)
                self._save()

        return str(tombstone_path)

    def terminate(self, agent_id: str, reason: str = "explicit",
                  terminated_by: str = "system"):
        if agent_id == "root":
            raise ValueError("Cannot terminate root agent")

        # Write tombstone before changing status (captures live state)
        self.write_tombstone(agent_id, reason=reason, terminated_by=terminated_by)

        payload = None
        with self._lock:
            a = self._agents.get(agent_id)
            if a:
                # Orphan adoption: reassign children to root so they keep running
                for rec in self._agents.values():
                    if rec.parent_id == agent_id and rec.status != "terminated":
                        rec.parent_id = "root"
                payload = {
                    "agent_id":      agent_id,
                    "name":          a.name,
                    "role":          a.role,
                    "parent_id":     a.parent_id,
                    "final_usage":   dict(a.usage),
                    "reason":        reason,
                    "terminated_by": terminated_by,
                }
                a.status = "terminated"
                self._token_index.pop(a.token_hash, None)
                self._save()
        if payload and self._event_bus:
            self._event_bus.emit("agent.terminated", agent_id, payload)

    def force_terminate(self, agent_id: str, reason: str = "force",
                        terminated_by: str = "system"):
        """
        Force-terminate without grace period. Called by SIGTERM watchdog after
        grace period expires. Writes tombstone, adopts orphans, emits event.
        """
        if agent_id == "root":
            return
        self.terminate(agent_id, reason=reason, terminated_by=terminated_by)

    def terminate_group(self, group_id: str, sent_by: str = "system",
                        signal_fn=None, grace_seconds: float = 30) -> dict:
        """
        SIGTERM all active agents belonging to group_id simultaneously.
        signal_fn is signal_dispatch (injected to avoid circular import).
        Falls back to direct force_terminate if no signal_fn provided.
        """
        members = [
            a.agent_id for a in self._agents.values()
            if a.group_id == group_id and a.status not in ("terminated",)
        ]
        if signal_fn:
            for mid in members:
                signal_fn(mid, "SIGTERM", sent_by=sent_by,
                          grace_seconds=grace_seconds)
        else:
            for mid in members:
                self.force_terminate(mid, reason="group_terminate",
                                     terminated_by=sent_by)
        return {"group_id": group_id, "signaled": members, "count": len(members)}

    def suspend(self, agent_id: str):
        did_suspend = False
        with self._lock:
            a = self._agents.get(agent_id)
            if a and a.status == "active":
                a.status = "suspended"
                did_suspend = True
                self._save()
        if did_suspend and self._event_bus:
            self._event_bus.emit("agent.suspended", agent_id, {"agent_id": agent_id})

    def resume(self, agent_id: str):
        did_resume = False
        with self._lock:
            a = self._agents.get(agent_id)
            if a and a.status == "suspended":
                a.status = "active"
                did_resume = True
                self._save()
        if did_resume and self._event_bus:
            self._event_bus.emit("agent.resumed", agent_id, {"agent_id": agent_id})

    def acquire_lock(self, agent_id: str, lock_name: str, ttl_seconds: float = 300) -> bool:
        """
        Acquire a named lock for an agent. Returns True if acquired, False if already held
        by another agent. Locks expire automatically after ttl_seconds.
        """
        with self._lock:
            now = time.time()
            # Expire stale locks across all agents
            for a in self._agents.values():
                stale = [n for n, l in a.locks.items() if l.get("expires_at", 0) < now]
                for n in stale:
                    del a.locks[n]

            # Check if any other agent holds this lock
            for a in self._agents.values():
                if a.agent_id != agent_id and lock_name in a.locks:
                    return False

            a = self._agents.get(agent_id)
            if not a:
                return False
            a.locks[lock_name] = {
                "acquired_at": now,
                "expires_at": now + ttl_seconds,
            }
            self._save()
            return True

    def release_lock(self, agent_id: str, lock_name: str) -> bool:
        with self._lock:
            a = self._agents.get(agent_id)
            if not a or lock_name not in a.locks:
                return False
            del a.locks[lock_name]
            self._save()
            return True

    def get_locks(self, agent_id: str) -> dict:
        a = self._agents.get(agent_id)
        if not a:
            return {}
        now = time.time()
        return {
            name: {**lock, "ttl_remaining": max(0, lock["expires_at"] - now)}
            for name, lock in a.locks.items()
            if lock.get("expires_at", 0) > now
        }

    def check_model_policy(self, agent_id: str, model: str, required_cap: str) -> bool:
        """
        Check if an agent's model_policies allow `required_cap` for `model`.
        Returns True if no policy is set (open) or policy includes the cap.
        """
        a = self._agents.get(agent_id)
        if not a or not a.model_policies:
            return True
        policy = a.model_policies.get(model)
        if policy is None:
            # No explicit policy for this model — check wildcard
            policy = a.model_policies.get("*")
        if policy is None:
            return True  # no policy at all — allow
        return required_cap in policy

    def _load(self):
        if REGISTRY_PATH.exists():
            try:
                data = json.loads(REGISTRY_PATH.read_text())
                for d in data.values():
                    # Backwards compat: add new fields if missing
                    d.setdefault("locks", {})
                    d.setdefault("model_policies", {})
                    d.setdefault("spawn_depth", 0)
                    d.setdefault("group_id", None)       # v0.8.0
                    d.setdefault("tombstone_path", None)  # v0.8.0
                    r = AgentRecord(**d)
                    self._agents[r.agent_id] = r
                    # Rebuild O(1) token index — only active agents authenticate
                    if r.status == "active":
                        self._token_index[r.token_hash] = r.agent_id
            except Exception:
                pass

    def _save(self):
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = {aid: asdict(a) for aid, a in self._agents.items()}
        REGISTRY_PATH.write_text(json.dumps(out, indent=2))
