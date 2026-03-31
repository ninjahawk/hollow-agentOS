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
_model_manager = None
_heap_registry = None
_audit_log = None


def init(registry: AgentRegistry, bus: MessageBus, scheduler: TaskScheduler,
         events=None, model_manager=None, heap_registry=None, audit_log=None):
    global _registry, _bus, _scheduler, _events, _model_manager, _heap_registry, _audit_log
    _registry = registry
    _bus = bus
    _scheduler = scheduler
    _events = events
    _model_manager = model_manager
    _heap_registry = heap_registry
    _audit_log = audit_log


def _audit(agent, operation: str, params: dict,
           result_code: str = "ok", tokens: int = 0, ms: float = 0.0) -> None:
    """
    Fire-and-forget audit log entry. Never raises — audit must never break callers.
    """
    if not _audit_log or not agent:
        return
    try:
        from agents.audit import make_entry
        entry = make_entry(
            agent_id=agent.agent_id,
            operation=operation,
            params=params,
            result_code=result_code,
            tokens_charged=tokens,
            duration_ms=ms,
            role=agent.role,
        )
        _audit_log.log(entry)
    except Exception:
        pass


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
    priority: int = 1  # 0=URGENT, 1=NORMAL, 2=BACKGROUND


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
    _audit(caller, "agent_register", {"new_agent_id": record.agent_id, "role": req.role})

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
        _audit(caller, "agent_terminate", {"target_agent_id": agent_id})
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
    _audit(caller, "agent_spawn", {"name": req.name, "role": req.role, "complexity": req.complexity,
                                    "child_agent_id": result.get("child_agent_id")})
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
    _audit(caller, "message_send", {"to_id": req.to_id, "msg_type": req.msg_type})
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
        priority=req.priority,
    )
    _registry.update_usage(caller.agent_id, tokens_in=0, tokens_out=0)  # updated by scheduler
    tokens_used = 0
    ms_used = 0.0
    if task.result:
        tokens_used = (task.result.get("tokens_in", 0) or 0) + (task.result.get("tokens_out", 0) or 0)
    if task.finished_at and task.started_at:
        ms_used = round((task.finished_at - task.started_at) * 1000)
    _audit(caller, "task_submit", {
        "task_id": task.task_id, "complexity": req.complexity,
        "priority": req.priority, "status": task.status,
    }, result_code="ok" if task.status == "done" else task.status,
       tokens=tokens_used, ms=ms_used)
    return {
        "task_id": task.task_id,
        "status": task.status,
        "assigned_to": task.assigned_to,
        "result": task.result,
        "error": task.error,
        "ms": ms_used or None,
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
        _audit(caller, "lock_acquire", {"lock_name": lock_name, "ttl_seconds": ttl_seconds},
               result_code="denied")
        raise HTTPException(status_code=409, detail=f"Lock '{lock_name}' already held by another agent")
    _audit(caller, "lock_acquire", {"lock_name": lock_name, "ttl_seconds": ttl_seconds})
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


# ── v0.9.0: VRAM / Model Status ──────────────────────────────────────────────

@router.get("/model_status")
def model_status(authorization: Optional[str] = Header(None)):
    """
    Return current VRAM state: loaded models, VRAM usage, queue depth by priority.
    Available to any authenticated agent.
    """
    _resolve_agent(authorization)
    if not _model_manager:
        return {"error": "ModelManager not initialized", "loaded_models": [], "vram_total_mb": 0}
    status = _model_manager.status()
    status["queue"] = _scheduler.queue_status() if _scheduler else {}
    return status


# ── v1.0.0: Working Memory Heap ───────────────────────────────────────────────

class MemoryAllocRequest(BaseModel):
    key: str
    content: str
    priority: int = 5              # 0-10, higher = protected from compression
    ttl_seconds: Optional[float] = None   # None = forever
    compression_eligible: bool = True


class MemoryCompressRequest(BaseModel):
    key: str


def _require_heap() -> None:
    if not _heap_registry:
        raise HTTPException(status_code=503, detail="HeapRegistry not initialized")


@router.post("/memory/alloc")
def memory_alloc(req: MemoryAllocRequest, authorization: Optional[str] = Header(None)):
    """
    Allocate a named memory object on the caller's heap.
    Overwrites existing key if present.
    """
    caller = _resolve_agent(authorization)
    _require_heap()
    ttl = (None if req.ttl_seconds is None
           else __import__("time").time() + req.ttl_seconds)
    heap = _heap_registry.get(caller.agent_id)
    obj = heap.alloc(
        key=req.key,
        content=req.content,
        priority=req.priority,
        ttl=ttl,
        compression_eligible=req.compression_eligible,
    )
    _audit(caller, "memory_alloc", {"key": req.key, "token_count": obj.token_count,
                                     "priority": req.priority})
    return {
        "key":         obj.key,
        "token_count": obj.token_count,
        "priority":    obj.priority,
        "ttl":         obj.ttl,
    }


@router.get("/memory/read/{key}")
def memory_read(key: str, authorization: Optional[str] = Header(None)):
    """Read a memory object's content. Auto-swaps-in if on disk."""
    caller = _resolve_agent(authorization)
    _require_heap()
    heap = _heap_registry.get(caller.agent_id)
    try:
        content = heap.read(key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"key": key, "content": content}


@router.delete("/memory/{key}")
def memory_free(key: str, authorization: Optional[str] = Header(None)):
    """Free a memory object and release its tokens."""
    caller = _resolve_agent(authorization)
    _require_heap()
    heap = _heap_registry.get(caller.agent_id)
    freed = heap.free(key)
    if not freed:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not found")
    return {"freed": key}


@router.get("/memory")
def memory_list(authorization: Optional[str] = Header(None)):
    """List all memory objects with metadata (no content). Includes heap_stats."""
    caller = _resolve_agent(authorization)
    _require_heap()
    heap = _heap_registry.get(caller.agent_id)
    return {
        "objects": heap.list_objects(),
        "stats":   heap.heap_stats(),
    }


@router.post("/memory/compress")
def memory_compress(req: MemoryCompressRequest, authorization: Optional[str] = Header(None)):
    """
    Compress a memory object via Ollama summarization.
    Original stored to disk; summary replaces in-heap content.
    Requires: ollama capability.
    """
    caller = _resolve_agent(authorization)
    _require_cap(caller, "ollama")
    _require_heap()
    heap = _heap_registry.get(caller.agent_id)
    try:
        result = heap.compress(req.key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@router.post("/memory/swap/{key}")
def memory_swap_out(key: str, authorization: Optional[str] = Header(None)):
    """Serialize content to disk, free from active heap."""
    caller = _resolve_agent(authorization)
    _require_heap()
    heap = _heap_registry.get(caller.agent_id)
    ok = heap.swap_out(key)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not found or already swapped")
    return {"swapped_out": key}


@router.get("/memory/stats")
def heap_stats(authorization: Optional[str] = Header(None)):
    """
    Return heap statistics for the caller's working memory:
    total_tokens, object_count, compressible_tokens, swapped_count,
    fragmentation_score.
    """
    caller = _resolve_agent(authorization)
    _require_heap()
    heap = _heap_registry.get(caller.agent_id)
    return heap.heap_stats()


# ── v1.1.0: Audit Kernel ──────────────────────────────────────────────────────

def _require_audit() -> None:
    if not _audit_log:
        raise HTTPException(status_code=503, detail="AuditLog not initialized")


@router.get("/audit")
def audit_query(
    agent_id: Optional[str] = None,
    operation: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
    limit: int = 100,
    authorization: Optional[str] = Header(None),
):
    """
    Query the audit log. Filter by agent, operation, and time range.
    Agents can only query their own entries unless admin.
    """
    caller = _resolve_agent(authorization)
    _require_audit()
    # Non-admin agents can only see their own audit entries
    if agent_id and agent_id != caller.agent_id and not caller.has_cap("admin"):
        raise HTTPException(status_code=403, detail="Cannot query other agents' audit entries")
    effective_agent = agent_id if agent_id else (
        None if caller.has_cap("admin") else caller.agent_id
    )
    entries = _audit_log.query(
        agent_id=effective_agent,
        operation=operation,
        since=since,
        until=until,
        limit=limit,
    )
    return {"entries": entries, "count": len(entries)}


@router.get("/audit/stats/{agent_id}")
def audit_stats(agent_id: str, authorization: Optional[str] = Header(None)):
    """
    Return audit statistics for an agent: op_counts, total_tokens, anomaly_score.
    Agents can view own stats; admin can view any.
    """
    caller = _resolve_agent(authorization)
    _require_audit()
    if agent_id != caller.agent_id and not caller.has_cap("admin"):
        raise HTTPException(status_code=403, detail="Cannot view other agents' audit stats")
    return _audit_log.stats(agent_id)


@router.get("/audit/anomalies")
def anomaly_history(limit: int = 50, authorization: Optional[str] = Header(None)):
    """
    Return recent security.anomaly events. Admin only.
    Delegates to event history filtered by type.
    """
    caller = _resolve_agent(authorization)
    _require_cap(caller, "admin")
    _require_audit()
    if not _events:
        return {"anomalies": [], "count": 0}
    # Pull from event bus history filtered by security.anomaly
    try:
        history = _events.get_history(event_types=["security.anomaly"], limit=limit)
    except Exception:
        history = []
    return {"anomalies": history, "count": len(history)}
