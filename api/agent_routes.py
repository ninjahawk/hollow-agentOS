"""
Agent OS routes — identity, isolation, messaging, scheduling.

Mounted onto the main FastAPI app as a router.
The registry, bus, and scheduler are passed in at startup (singletons).
"""

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.registry import AgentRegistry
from agents.bus import MessageBus
from agents.scheduler import TaskScheduler

router = APIRouter()

# Injected at startup by server.py
_registry: AgentRegistry = None
_bus: MessageBus = None
_scheduler: TaskScheduler = None
_events = None


def init(registry: AgentRegistry, bus: MessageBus, scheduler: TaskScheduler,
         events=None):
    global _registry, _bus, _scheduler, _events
    _registry = registry
    _bus = bus
    _scheduler = scheduler
    _events = events


# ── Auth helpers ────────────────────────────────────────────────────────────

def _resolve_agent(authorization: Optional[str]):
    """Extract bearer token → AgentRecord. Raises 401 if invalid."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.removeprefix("Bearer ").strip()
    agent = _registry.authenticate(token)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return agent


def _require_cap(agent, cap: str):
    if not agent.has_cap(cap):
        raise HTTPException(
            status_code=403,
            detail=f"Agent '{agent.agent_id}' lacks capability '{cap}'"
        )


def _check_budget(agent):
    over = agent.over_budget()
    if over:
        raise HTTPException(
            status_code=429,
            detail=f"Agent '{agent.agent_id}' exceeded budget: {over}"
        )


# ── Pydantic models ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str
    role: str = "worker"
    capabilities: Optional[list[str]] = None
    budget: Optional[dict] = None
    metadata: Optional[dict] = None
    model_policies: Optional[dict] = None  # e.g. {"qwen2.5:14b": ["fs_read"], "*": ["fs_read", "fs_write"]}
    group_id: Optional[str] = None         # v0.8.0: process group membership


class SignalRequest(BaseModel):
    signal: str                             # SIGTERM | SIGPAUSE | SIGINFO
    grace_seconds: float = 30.0            # SIGTERM only: watchdog fires after this many seconds


class SendMessageRequest(BaseModel):
    to_id: str
    content: dict
    msg_type: str = "data"
    reply_to: Optional[str] = None
    ttl_seconds: Optional[float] = None


class SubmitTaskRequest(BaseModel):
    description: str
    complexity: int = 2
    context: Optional[dict] = None
    system_prompt: Optional[str] = None


class SpawnRequest(BaseModel):
    name: str
    role: str = "worker"
    task: str
    complexity: int = 2
    capabilities: Optional[list[str]] = None


# ── Agent lifecycle ──────────────────────────────────────────────────────────

@router.post("/agents/register")
def register_agent(req: RegisterRequest, authorization: Optional[str] = Header(None)):
    """
    Register a new agent. Returns agent_id + token.
    The token is shown ONCE — store it, it cannot be recovered.
    Requires: admin capability (or master token).
    """
    caller = _resolve_agent(authorization)
    _require_cap(caller, "admin")

    record, raw_token = _registry.register(
        name=req.name,
        role=req.role,
        capabilities=req.capabilities,
        budget=req.budget,
        parent_id=caller.agent_id,
        metadata=req.metadata,
        model_policies=req.model_policies,
        group_id=req.group_id,
    )

    return {
        "agent_id": record.agent_id,
        "token": raw_token,   # shown once
        "role": record.role,
        "capabilities": record.capabilities,
        "workspace_dir": record.workspace_dir,
        "budget": record.budget,
        "warning": "Store this token — it will not be shown again.",
    }


@router.get("/agents")
def list_agents(authorization: Optional[str] = Header(None)):
    """List all registered agents and their current status."""
    caller = _resolve_agent(authorization)
    _require_cap(caller, "admin")
    agents = _registry.list_agents()
    return {
        "agents": [a.to_dict() for a in agents],
        "count": len(agents),
    }


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, authorization: Optional[str] = Header(None)):
    """Get state for a specific agent. Agents can view themselves; admin can view all."""
    caller = _resolve_agent(authorization)
    if caller.agent_id != agent_id and not caller.has_cap("admin"):
        raise HTTPException(status_code=403, detail="Can only view own agent record")

    a = _registry.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agent not found")
    return a.to_dict()


@router.delete("/agents/{agent_id}")
def terminate_agent(agent_id: str, authorization: Optional[str] = Header(None)):
    """Terminate an agent. Admins can terminate any; agents can self-terminate."""
    caller = _resolve_agent(authorization)
    if caller.agent_id != agent_id and not caller.has_cap("admin"):
        raise HTTPException(status_code=403, detail="Cannot terminate other agents without admin")
    try:
        _registry.terminate(agent_id, reason="explicit", terminated_by=caller.agent_id)
        return {"ok": True, "agent_id": agent_id, "status": "terminated"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/agents/{agent_id}/suspend")
def suspend_agent(agent_id: str, authorization: Optional[str] = Header(None)):
    caller = _resolve_agent(authorization)
    _require_cap(caller, "admin")
    _registry.suspend(agent_id)
    return {"ok": True, "agent_id": agent_id, "status": "suspended"}


@router.post("/agents/{agent_id}/resume")
def resume_agent(agent_id: str, authorization: Optional[str] = Header(None)):
    caller = _resolve_agent(authorization)
    _require_cap(caller, "admin")
    _registry.resume(agent_id)
    return {"ok": True, "agent_id": agent_id, "status": "active"}


# ── Agent spawning ───────────────────────────────────────────────────────────

@router.post("/agents/spawn")
def spawn_agent(req: SpawnRequest, authorization: Optional[str] = Header(None)):
    """
    Spawn a child agent, run a task with it via the scheduler, return the result.
    The child is terminated after the task completes.
    Requires: spawn capability.
    """
    caller = _resolve_agent(authorization)
    _require_cap(caller, "spawn")
    _check_budget(caller)

    result = _scheduler.spawn_agent(
        parent_id=caller.agent_id,
        name=req.name,
        role=req.role,
        task_description=req.task,
        complexity=req.complexity,
        capabilities=req.capabilities,
    )
    return result


# ── Message bus ──────────────────────────────────────────────────────────────

@router.post("/messages")
def send_message(req: SendMessageRequest, authorization: Optional[str] = Header(None)):
    """Send a message to another agent or broadcast to all."""
    caller = _resolve_agent(authorization)
    _require_cap(caller, "message")

    # Verify target exists (unless broadcast)
    if req.to_id != "broadcast" and not _registry.get(req.to_id):
        raise HTTPException(status_code=404, detail=f"Target agent '{req.to_id}' not found")

    msg_id = _bus.send(
        from_id=caller.agent_id,
        to_id=req.to_id,
        content=req.content,
        msg_type=req.msg_type,
        reply_to=req.reply_to,
        ttl_seconds=req.ttl_seconds,
    )
    return {"ok": True, "msg_id": msg_id}


@router.get("/messages")
def receive_messages(
    unread_only: bool = True,
    limit: int = 20,
    authorization: Optional[str] = Header(None),
):
    """Receive messages addressed to the calling agent."""
    caller = _resolve_agent(authorization)
    _require_cap(caller, "message")
    messages = _bus.receive(caller.agent_id, unread_only=unread_only, limit=limit)
    stats = _bus.stats(caller.agent_id)
    return {"messages": messages, "stats": stats}


@router.get("/messages/thread/{msg_id}")
def message_thread(msg_id: str, authorization: Optional[str] = Header(None)):
    """Get a message and all its replies."""
    caller = _resolve_agent(authorization)
    _require_cap(caller, "message")
    return {"thread": _bus.get_thread(msg_id)}


# ── Task scheduler ───────────────────────────────────────────────────────────

@router.post("/tasks/submit")
def submit_task(req: SubmitTaskRequest, authorization: Optional[str] = Header(None)):
    """
    Submit a task to the scheduler. It routes to the best Ollama model
    based on complexity (1=trivial → 5=deep reasoning) and returns the result.
    Requires: ollama capability.
    """
    caller = _resolve_agent(authorization)
    _require_cap(caller, "ollama")
    _check_budget(caller)

    _registry.set_task(caller.agent_id, req.description)
    task = _scheduler.submit(
        description=req.description,
        submitted_by=caller.agent_id,
        complexity=req.complexity,
        context=req.context,
        system_prompt=req.system_prompt,
    )
    _registry.update_usage(caller.agent_id, tokens_in=0, tokens_out=0)  # updated by scheduler
    return {
        "task_id": task.task_id,
        "status": task.status,
        "assigned_to": task.assigned_to,
        "result": task.result,
        "error": task.error,
        "ms": round((task.finished_at - task.started_at) * 1000) if task.finished_at and task.started_at else None,
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str, authorization: Optional[str] = Header(None)):
    caller = _resolve_agent(authorization)
    task = _scheduler.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # Agents can only see their own tasks unless admin
    if task.submitted_by != caller.agent_id and not caller.has_cap("admin"):
        raise HTTPException(status_code=403, detail="Cannot view other agents' tasks")
    from dataclasses import asdict
    return asdict(task)


@router.get("/tasks")
def list_tasks(
    agent_id: Optional[str] = None,
    limit: int = 20,
    authorization: Optional[str] = Header(None),
):
    caller = _resolve_agent(authorization)
    # Non-admins can only see their own tasks
    if not caller.has_cap("admin"):
        agent_id = caller.agent_id
    return {"tasks": _scheduler.list_tasks(agent_id=agent_id, limit=limit)}


# ── Agent locks ──────────────────────────────────────────────────────────────

@router.post("/agents/{agent_id}/lock/{lock_name}")
def acquire_lock(
    agent_id: str,
    lock_name: str,
    ttl_seconds: float = 300,
    authorization: Optional[str] = Header(None),
):
    """
    Acquire a named timed lock for an agent.
    Locks expire after ttl_seconds (default 300s).
    Returns 409 if another agent already holds the lock.
    Agents that fail to acquire a lock they need are detected as stalled/hallucinating.
    """
    caller = _resolve_agent(authorization)
    if caller.agent_id != agent_id and not caller.has_cap("admin"):
        raise HTTPException(status_code=403, detail="Cannot acquire lock for another agent")

    acquired = _registry.acquire_lock(agent_id, lock_name, ttl_seconds)
    if not acquired:
        raise HTTPException(status_code=409, detail=f"Lock '{lock_name}' already held by another agent")
    return {"ok": True, "agent_id": agent_id, "lock": lock_name, "ttl_seconds": ttl_seconds}


@router.delete("/agents/{agent_id}/lock/{lock_name}")
def release_lock(
    agent_id: str,
    lock_name: str,
    authorization: Optional[str] = Header(None),
):
    """Release a named lock held by an agent."""
    caller = _resolve_agent(authorization)
    if caller.agent_id != agent_id and not caller.has_cap("admin"):
        raise HTTPException(status_code=403, detail="Cannot release lock for another agent")

    released = _registry.release_lock(agent_id, lock_name)
    if not released:
        raise HTTPException(status_code=404, detail=f"Lock '{lock_name}' not found for agent '{agent_id}'")
    return {"ok": True, "agent_id": agent_id, "lock": lock_name, "status": "released"}


# ── Token usage ──────────────────────────────────────────────────────────────

@router.get("/agents/{agent_id}/usage")
def get_agent_usage(agent_id: str, authorization: Optional[str] = Header(None)):
    """
    Per-agent token and resource usage breakdown.
    Shows shell calls, tokens in/out, and budget remaining.
    """
    caller = _resolve_agent(authorization)
    if caller.agent_id != agent_id and not caller.has_cap("admin"):
        raise HTTPException(status_code=403, detail="Can only view own usage")

    a = _registry.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agent not found")

    budget = a.budget
    usage = a.usage
    remaining = {
        k: max(0, budget.get(k, 0) - usage.get(k, 0))
        for k in budget
    }
    pct_used = {
        k: round(usage.get(k, 0) / budget[k] * 100, 1) if budget.get(k, 0) > 0 else 0
        for k in budget
    }
    return {
        "agent_id": agent_id,
        "name": a.name,
        "role": a.role,
        "usage": usage,
        "budget": budget,
        "remaining": remaining,
        "pct_used": pct_used,
        "active_locks": _registry.get_locks(agent_id),
    }


@router.get("/usage")
def get_aggregate_usage(authorization: Optional[str] = Header(None)):
    """
    Aggregate token usage across all agents, broken down by pipeline stage.
    Shows where token spend concentrates across the whole system.
    """
    caller = _resolve_agent(authorization)
    _require_cap(caller, "admin")

    agents = _registry.list_agents()
    totals = {
        "shell_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }
    per_agent = []
    for a in agents:
        u = a.usage
        for k in totals:
            totals[k] += u.get(k, 0)
        per_agent.append({
            "agent_id": a.agent_id,
            "name": a.name,
            "role": a.role,
            "status": a.status,
            "usage": u,
        })

    per_agent.sort(key=lambda x: x["usage"].get("tokens_in", 0) + x["usage"].get("tokens_out", 0), reverse=True)

    return {
        "totals": totals,
        "by_agent": per_agent,
        "agent_count": len(agents),
    }


# ── v0.8.0: Process signals ──────────────────────────────────────────────────

@router.post("/agents/{agent_id}/signal")
def send_signal(
    agent_id: str,
    req: SignalRequest,
    authorization: Optional[str] = Header(None),
):
    """
    Send a process signal to an agent.

    SIGTERM  — graceful shutdown: agent gets grace_seconds to write handoff,
               then watchdog force-terminates.
    SIGPAUSE — immediately suspend the agent, preserving current_task.
    SIGINFO  — deliver status snapshot (task, usage, uptime) to caller's inbox.

    Requires: admin capability, or agent signaling itself.
    """
    caller = _resolve_agent(authorization)
    if caller.agent_id != agent_id and not caller.has_cap("admin"):
        raise HTTPException(status_code=403, detail="admin capability required to signal other agents")

    from agents.signals import signal_dispatch
    result = signal_dispatch(
        registry=_registry,
        bus=_bus,
        events=_events,
        agent_id=agent_id,
        signal=req.signal,
        sent_by=caller.agent_id,
        grace_seconds=req.grace_seconds,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/agents/group/{group_id}/terminate")
def terminate_group(
    group_id: str,
    grace_seconds: float = 30.0,
    authorization: Optional[str] = Header(None),
):
    """
    SIGTERM all active agents in a process group simultaneously.
    Requires: admin capability.
    """
    caller = _resolve_agent(authorization)
    _require_cap(caller, "admin")

    from agents.signals import signal_dispatch

    def _dispatch(aid, sig, sent_by, grace_seconds):
        signal_dispatch(_registry, _bus, _events, aid, sig,
                        sent_by=sent_by, grace_seconds=grace_seconds)

    result = _registry.terminate_group(
        group_id=group_id,
        sent_by=caller.agent_id,
        signal_fn=_dispatch,
        grace_seconds=grace_seconds,
    )
    return result


# ── v0.8.0: Tombstones ───────────────────────────────────────────────────────

@router.get("/tombstones")
def list_tombstones(authorization: Optional[str] = Header(None)):
    """
    List all agent tombstones. Each tombstone records the final state of a
    terminated agent: reason, usage, children, current task at termination.
    Requires: admin capability.
    """
    caller = _resolve_agent(authorization)
    _require_cap(caller, "admin")

    tombstones = []
    for a in _registry.list_agents():
        if a.tombstone_path:
            p = Path(a.tombstone_path)
            if p.exists():
                try:
                    tombstones.append(json.loads(p.read_text()))
                except Exception:
                    pass
    return {"tombstones": tombstones, "count": len(tombstones)}


@router.get("/tombstones/{agent_id}")
def get_tombstone(agent_id: str, authorization: Optional[str] = Header(None)):
    """
    Get the tombstone for a specific agent.
    Agents can view their own tombstone; admin can view any.
    """
    caller = _resolve_agent(authorization)
    if caller.agent_id != agent_id and not caller.has_cap("admin"):
        raise HTTPException(status_code=403, detail="Can only view own tombstone")

    a = _registry.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not a.tombstone_path:
        raise HTTPException(status_code=404, detail=f"No tombstone for agent '{agent_id}'")
    p = Path(a.tombstone_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Tombstone file not found on disk")
    return json.loads(p.read_text())
