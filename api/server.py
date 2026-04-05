#!/usr/bin/env python3
"""
AgentOS API Server v0.9.0
REST interface for agent control — filesystem, processes, memory, state,
Ollama model routing, semantic search, decisions, token tracking,
agent handoff/pickup, state diffing, read_context, standards, specs, project context.

v0.6.0 adds:
  - Temporal state history             (GET /state/history)
  - Standards layer                    (GET/POST/DELETE /standards)
  - Feature specs                      (GET/POST/PATCH /specs)
  - Project context                    (GET/POST /project)
  - Agent locks                        (POST/DELETE /agents/{id}/lock/{name})
  - Per-agent usage breakdown          (GET /agents/{id}/usage, GET /usage)
  - Multi-model capability policies    (model_policies on registration)
  - Ollama-optional mode               (graceful 503 when Ollama unavailable)
  - OpenAI tool schema                 (GET /tools/openai)
  - Enhanced agent/pickup              (includes active spec + standards + temporal context)
"""

import asyncio
import json
import os
import sys
import signal
import subprocess
import time as _time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Optional

try:
    from fastapi import FastAPI, HTTPException, Header, Body, Query
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    import uvicorn
    import httpx
except ImportError as e:
    print(json.dumps({"error": f"Run: pip install fastapi uvicorn httpx — missing: {e}"}))
    raise SystemExit(1)

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.manager import (
    get_session_context, index_workspace, log_action,
    get_workspace_map, find_files, get_recent_actions,
    queue_decision, resolve_decision, get_pending_decisions,
    set_project_context, get_project_context, start_session,
    get_token_totals, write_handoff, read_handoff,
    record_state_snapshot, get_state_history, get_state_diff_since,
    create_spec, get_spec, list_specs, activate_spec, get_active_spec, update_spec,
)
from shell.agent_shell import run as shell_run
from agents.scheduler import log_shell_usage
from agents.registry import AgentRegistry
from agents.bus import MessageBus
from agents.scheduler import TaskScheduler
from agents.events import EventBus
from agents.model_manager import ModelManager
from memory.heap import HeapRegistry
from agents.audit import AuditLog, make_entry
from agents.transaction import TransactionCoordinator
from agents.lineage import LineageGraph
from agents.ratelimit import RateLimiter
from agents.checkpoint import CheckpointManager
from agents.consensus import ConsensusManager
from agents.adaptive_router import AdaptiveRouter
from agents.benchmark import BenchmarkManager
from agents.proposals import ProposalEngine
from agents.persistent_goal import PersistentGoalEngine
from agents.semantic_memory import SemanticMemory
from agents.standards import (
    set_standard, get_standard, list_standards, delete_standard, get_relevant_standards
)
import api.agent_routes as agent_routes
import api.goal_routes as goal_routes
import memory.manager as _mem_manager

CONFIG_PATH = Path(os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))

# ── Ollama availability ───────────────────────────────────────────────────────
_ollama_available: bool = False


async def _check_ollama_available() -> bool:
    """Non-blocking check if Ollama is reachable. Updates global flag."""
    global _ollama_available
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{_ollama_host()}/api/tags")
            _ollama_available = r.status_code == 200
    except Exception:
        _ollama_available = False
    return _ollama_available


def _require_ollama():
    """Raise 503 with a clear message if Ollama is not available."""
    if not _ollama_available:
        raise HTTPException(
            status_code=503,
            detail=(
                "Ollama is not available. Core features (state, filesystem, memory, shell, "
                "standards, handoffs) work without Ollama. To enable model routing and semantic "
                "search, install Ollama and pull: nomic-embed-text, mistral-nemo:12b"
            )
        )


# ── Ollama model routing ─────────────────────────────────────────────────────
MODEL_ROUTES = {
    "code":            "qwen2.5:14b",
    "code-fast":       "qwen3.5:9b",
    "general":         "mistral-nemo:12b",
    "general-large":   "qwen3.5:27b",
    "reasoning":       "qwen3.5-35b-moe:latest",
    "reasoning-large": "nous-hermes2:34b",
    "uncensored":      "dolphin3:latest",
    "custom":          "emmi:latest",
}
DEFAULT_MODEL = "mistral-nemo:12b"

# ── In-memory state cache (used by /state and /state/diff) ───────────────────
_STATE_CACHE_TTL = 5.0  # seconds — serve from cache within this window
_state_cache: dict = {"data": None, "cached_at": None, "cached_at_ts": 0.0}


async def _get_state_cached() -> dict:
    """Return state from in-memory cache if fresh, otherwise rebuild."""
    now = _time.monotonic()
    if (_state_cache["data"] is not None and
            now - _state_cache["cached_at_ts"] < _STATE_CACHE_TTL):
        return _state_cache["data"]
    result = await _build_state()
    _state_cache["data"] = result
    _state_cache["cached_at"] = result["time"]
    _state_cache["cached_at_ts"] = now
    # Record snapshot for temporal history only on fresh build
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, partial(record_state_snapshot, result))
    return result


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _verify_token(authorization: Optional[str]) -> None:
    """Accept master token only (used for admin-only endpoints)."""
    config = _load_config()
    expected = config.get("api", {}).get("token", "")
    if not expected:
        return
    if not authorization or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _verify_any_token(authorization: Optional[str]) -> None:
    """Accept master token OR any registered active agent token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.removeprefix("Bearer ").strip()
    config = _load_config()
    master = config.get("api", {}).get("token", "")
    if token == master:
        return
    if _registry and _registry.authenticate(token):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_from_token(authorization: Optional[str]):
    """
    Resolve a bearer token to an AgentRecord. Returns root record for the
    master token. Returns None if the token is invalid or registry not ready.
    """
    if not _registry or not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    config = _load_config()
    master = config.get("api", {}).get("token", "")
    if token == master:
        return _registry.get("root")
    return _registry.authenticate(token)


def _ollama_host() -> str:
    return _load_config().get("ollama", {}).get("host", "http://localhost:11434")


async def _shell(command: str, cwd: str = None, timeout: int = 30) -> dict:
    """Non-blocking shell execution — runs in thread pool to avoid event loop deadlock."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(shell_run, command, cwd, timeout))


app = FastAPI(title="AgentOS API", version="0.7.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Agent OS singletons — initialized at startup ──────────────────────────────
_registry: AgentRegistry = None
_bus: MessageBus = None
_scheduler: TaskScheduler = None
_events: EventBus = None
_model_manager: ModelManager = None
_heap_registry: HeapRegistry = None
_audit_log: AuditLog = None
_txn_coordinator: TransactionCoordinator = None
_lineage: LineageGraph = None
_rate_limiter: RateLimiter = None
_checkpoint_manager: CheckpointManager = None
_consensus_manager: ConsensusManager = None
_adaptive_router: AdaptiveRouter = None
_benchmark_manager: BenchmarkManager = None
_proposal_engine: ProposalEngine = None


@app.on_event("startup")
async def _startup():
    global _registry, _bus, _scheduler, _events, _model_manager, _heap_registry
    global _audit_log, _txn_coordinator, _lineage, _rate_limiter, _checkpoint_manager
    global _consensus_manager, _adaptive_router, _benchmark_manager, _proposal_engine
    config = _load_config()
    master_token = config.get("api", {}).get("token", "")
    _events = EventBus()
    _registry = AgentRegistry(master_token)
    _bus = MessageBus()
    _scheduler = TaskScheduler(_registry, _bus, master_token)
    _model_manager = ModelManager()
    _heap_registry = HeapRegistry(master_token=master_token)
    _audit_log = AuditLog()
    _txn_coordinator = TransactionCoordinator()
    _lineage = LineageGraph()
    _rate_limiter = RateLimiter()
    _checkpoint_manager = CheckpointManager()
    _consensus_manager = ConsensusManager()
    _adaptive_router = AdaptiveRouter()
    _benchmark_manager = BenchmarkManager()
    _proposal_engine = ProposalEngine()
    # Wire the event bus into every subsystem that emits events
    _events.set_bus(_bus)
    _bus.set_event_bus(_events)
    _registry.set_event_bus(_events)
    _scheduler.set_event_bus(_events)
    _scheduler.set_model_manager(_model_manager)
    _model_manager.set_event_bus(_events)
    _heap_registry.set_event_bus(_events)
    _audit_log.set_event_bus(_events)
    _txn_coordinator.set_event_bus(_events)
    _txn_coordinator.set_subsystems(
        registry=_registry, bus=_bus, heap_registry=_heap_registry,
        checkpoint_manager=_checkpoint_manager,
    )
    _lineage.set_subsystems(
        registry=_registry, scheduler=_scheduler, txn_coordinator=_txn_coordinator
    )
    _rate_limiter.set_subsystems(
        registry=_registry, events=_events, bus=_bus
    )
    _audit_log.set_circuit_break_callback(_rate_limiter.circuit_break)
    _checkpoint_manager.set_subsystems(
        heap_registry=_heap_registry,
        registry=_registry,
        bus=_bus,
        scheduler=_scheduler,
        events=_events,
    )
    _scheduler.set_checkpoint_manager(_checkpoint_manager)
    _consensus_manager.set_subsystems(events=_events, registry=_registry)
    _adaptive_router.set_subsystems(events=_events, registry=_registry)
    _scheduler.set_adaptive_router(_adaptive_router)
    _benchmark_manager.set_master_token(master_token)
    _proposal_engine.set_subsystems(events=_events, registry=_registry)
    _proposal_engine.set_standards_fn(set_standard)
    _mem_manager.set_event_bus(_events)
    agent_routes.init(_registry, _bus, _scheduler, _events, _model_manager,
                      _heap_registry, _audit_log, _txn_coordinator, lineage=_lineage,
                      rate_limiter=_rate_limiter, checkpoint_manager=_checkpoint_manager,
                      consensus_manager=_consensus_manager, adaptive_router=_adaptive_router,
                      benchmark_manager=_benchmark_manager,
                      proposal_engine=_proposal_engine)
    _goal_engine = PersistentGoalEngine()
    goal_routes.init(_goal_engine, registry=_registry)
    await _check_ollama_available()


app.include_router(agent_routes.router)
app.include_router(goal_routes.router)


# ── Models ───────────────────────────────────────────────────────────────────

class ShellRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    timeout: int = 30

class WriteRequest(BaseModel):
    path: str
    content: str
    txn_id: Optional[str] = None   # v1.2.0: stage instead of apply if set

class BatchReadRequest(BaseModel):
    paths: list[str]

class DecisionResolveRequest(BaseModel):
    decision_id: str
    resolution: str

class ContextRequest(BaseModel):
    key: str
    value: Any

class SessionRequest(BaseModel):
    agent_id: str = "agent"

class OllamaChatRequest(BaseModel):
    messages: list[dict]
    role: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None

class OllamaGenerateRequest(BaseModel):
    prompt: str
    role: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None

class SemanticSearchRequest(BaseModel):
    query: str
    top_k: int = 10

class SemanticIndexRequest(BaseModel):
    paths: Optional[list[str]] = None

class ReadContextRequest(BaseModel):
    path: str
    top_k: int = 5
    related: bool = False  # opt-in: return semantically related chunks from other files

class HandoffRequest(BaseModel):
    agent_id: str = "agent"
    summary: str
    in_progress: Optional[list[str]] = None
    decisions_made: Optional[list[str]] = None
    relevant_files: Optional[list[str]] = None
    next_steps: Optional[list[str]] = None


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"name": "AgentOS", "version": "0.3.0", "status": "running", "time": _now()}

@app.get("/health")
async def health():
    return {"ok": True, "time": _now()}


# ── State builder (shared by /state and /state/diff) ─────────────────────────

async def _build_state() -> dict:
    # Run all shell commands in parallel — no more sequential awaits
    disk_res, mem_res, load_res, gpu_res, svc_res = await asyncio.gather(
        _shell("df -B1 / /mnt/c 2>/dev/null || df -B1 /"),
        _shell("free -m"),
        _shell("cat /proc/loadavg"),
        _shell(
            "/usr/lib/wsl/lib/nvidia-smi "
            "--query-gpu=name,memory.used,memory.total,utilization.gpu "
            "--format=csv,noheader,nounits 2>/dev/null || "
            "nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu "
            "--format=csv,noheader,nounits 2>/dev/null || echo 'no-gpu'"
        ),
        _shell(
            "for s in agentos-api ollama nginx agentos-indexer.timer; do "
            "echo \"$s:$(systemctl is-active $s 2>/dev/null)\"; done"
        ),
    )

    disk_out = disk_res.get("stdout", "")
    disk = {}
    for line in disk_out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        mount = parts[5]
        try:
            total, used, free = int(parts[1]), int(parts[2]), int(parts[3])
        except ValueError:
            continue
        label = "windows_c" if mount == "/mnt/c" else "wsl"
        disk[label] = {
            "total_gb": round(total / 1_073_741_824, 1),
            "used_gb":  round(used  / 1_073_741_824, 1),
            "free_gb":  round(free  / 1_073_741_824, 1),
            "pct_used": round(used / total * 100, 1) if total else 0
        }

    mem = {}
    for line in mem_res.get("stdout", "").splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            total = int(parts[1])
            used  = int(parts[2])
            avail = int(parts[6]) if len(parts) > 6 else int(parts[3])
            mem = {
                "total_mb":     total,
                "used_mb":      used,
                "available_mb": avail,
                "pct_used":     round(used / total * 100, 1) if total else 0
            }
            break

    lp = load_res.get("stdout", "0 0 0").split()
    load = {
        "1m":  float(lp[0]) if len(lp) > 0 else 0.0,
        "5m":  float(lp[1]) if len(lp) > 1 else 0.0,
        "15m": float(lp[2]) if len(lp) > 2 else 0.0
    }

    gpu_raw = gpu_res.get("stdout", "no-gpu").strip()
    gpu = None
    if gpu_raw and gpu_raw != "no-gpu":
        parts = [p.strip() for p in gpu_raw.split(",")]
        if len(parts) >= 4:
            try:
                gpu = {
                    "name":            parts[0],
                    "memory_used_mb":  int(parts[1]),
                    "memory_total_mb": int(parts[2]),
                    "utilization_pct": int(parts[3])
                }
            except ValueError:
                gpu = {"raw": gpu_raw}

    services = {}
    for line in svc_res.get("stdout", "").splitlines():
        if ":" in line:
            name, status = line.split(":", 1)
            services[name.strip()] = status.strip()

    ollama = {"available": [], "running": [], "error": None}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Parallelize the two Ollama API calls
            tags_r, ps_r = await asyncio.gather(
                client.get(f"{_ollama_host()}/api/tags"),
                client.get(f"{_ollama_host()}/api/ps"),
            )
            tags = tags_r.json()
            ps   = ps_r.json()
        ollama["available"] = [
            {"name": m["name"], "size_gb": round(m.get("size", 0) / 1_073_741_824, 1)}
            for m in tags.get("models", [])
        ]
        ollama["running"] = [
            {
                "name":    m["name"],
                "size_gb": round(m.get("size", 0)      / 1_073_741_824, 1),
                "vram_gb": round(m.get("size_vram", 0) / 1_073_741_824, 1)
            }
            for m in ps.get("models", [])
        ]
    except Exception as e:
        ollama["error"] = str(e)

    try:
        from tools.semantic import stats as semantic_stats
        semantic = semantic_stats()
    except Exception:
        semantic = {"total_chunks": 0, "total_files": 0, "indexed_at": None}

    ctx = get_session_context()
    actions = ctx.get("recent_actions", [])

    # Session-level counts
    token_session = {
        "session_shell_calls": sum(1 for a in actions if a.get("action") == "shell_command"),
        "session_file_reads":  sum(1 for a in actions if a.get("action") == "file_read"),
        "session_file_writes": sum(1 for a in actions if a.get("action") == "file_write"),
        "ollama_calls":        sum(1 for a in actions if a.get("action") in ("ollama_chat", "ollama_generate")),
    }

    # Lifetime totals (persisted across sessions)
    token_lifetime = get_token_totals()

    return {
        "time": _now(),
        "system": {
            "disk":     disk,
            "memory":   mem,
            "load":     load,
            "gpu":      gpu,
            "services": services
        },
        "ollama":           ollama,
        "ollama_routing":   MODEL_ROUTES,
        "semantic":         semantic,
        "workspace":        ctx.get("workspace_map", {}).get("stats", {}),
        "tokens":           token_session,
        "token_totals":     token_lifetime,
        "pending_decisions": ctx.get("pending_decisions", []),
        "recent_actions":   actions[-10:],
        "tool_count":       len(ctx.get("tool_registry", {}).get("tools", {})),
        "project_context":  ctx.get("project_context", {})
    }


# ── State helpers ─────────────────────────────────────────────────────────────

def _strip_empty(obj):
    """Recursively remove None, {}, and [] from a dict/list. Reduces response size."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            cleaned = _strip_empty(v)
            if cleaned is not None and cleaned != {} and cleaned != []:
                out[k] = cleaned
        return out
    if isinstance(obj, list):
        return [_strip_empty(i) for i in obj if i is not None]
    return obj


def _project_fields(state: dict, fields_str: str) -> dict:
    """Return only requested top-level keys. ?fields=system,ollama"""
    keys = {f.strip() for f in fields_str.split(",") if f.strip()}
    return {k: v for k, v in state.items() if k in keys}


def _flatten_dict(d: dict, prefix: str = "") -> dict:
    """Flatten nested dict to dotted paths: {a: {b: 1}} → {"a.b": 1}"""
    result = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and v:
            result.update(_flatten_dict(v, key))
        else:
            result[key] = v
    return result


# ── State ─────────────────────────────────────────────────────────────────────

@app.get("/state")
async def get_state(
    fields: Optional[str] = Query(None, description="Comma-separated top-level fields to return, e.g. system,ollama"),
    authorization: Optional[str] = Header(None),
):
    _verify_any_token(authorization)
    try:
        result = await _get_state_cached()
        if fields:
            result = _project_fields(result, fields)
        return _strip_empty(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/state/history")
async def get_state_history_endpoint(since: str = None, authorization: Optional[str] = Header(None)):
    """
    Return recorded state snapshots since `since` (ISO timestamp).
    Returns all stored snapshots (up to 100) if since is omitted.
    Enables agents to ask "what changed since I last looked" without re-running discovery.
    """
    _verify_any_token(authorization)
    try:
        snapshots = get_state_history(since=since)
        return {
            "since": since,
            "count": len(snapshots),
            "snapshots": snapshots,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/state/diff")
async def get_state_diff(since: str = None, authorization: Optional[str] = Header(None)):
    """
    Return ONLY what changed since `since` (ISO timestamp) as a flat dotted-path dict.
    If nothing changed, returns {"changed": {}, ...} — near-zero tokens.
    If no `since` provided, returns full state (stripped).

    Uses the real state snapshot history for comparison — not a fixed field list.
    Format: {"changed": {"system.load.1m": 0.88, "tokens.session_shell_calls": 3}, ...}
    """
    _verify_any_token(authorization)
    try:
        fresh = await _get_state_cached()

        if not since:
            return {"full": True, "state": _strip_empty(fresh), "since": None}

        # Compare against real snapshot history
        changed_sections = get_state_diff_since(since)

        if not changed_sections:
            return {"changed": {}, "since": since, "generated_at": fresh["time"]}

        # Flatten to dotted paths and strip nulls/empties
        flat = _flatten_dict(changed_sections)
        flat = {k: v for k, v in flat.items()
                if v is not None and v != {} and v != []}

        return {
            "changed": flat,
            "since": since,
            "generated_at": fresh["time"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Shell — non-blocking via thread pool ──────────────────────────────────────

@app.post("/shell")
async def run_command(req: ShellRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)

    # Scope non-root agents to their isolated workspace
    cwd = req.cwd
    if _registry:
        token = (authorization or "").removeprefix("Bearer ").strip()
        agent = _registry.authenticate(token)
        if agent and agent.agent_id != "root":
            # Enforce shell capability
            if not agent.has_cap("shell") and not agent.has_cap("shell_root"):
                raise HTTPException(status_code=403, detail="Agent lacks shell capability")
            # Check budget
            if over := agent.over_budget():
                raise HTTPException(status_code=429, detail=f"Shell budget exceeded: {over}")
            # Check rate limit
            if _rate_limiter:
                import math as _math
                rl = _rate_limiter.check(agent.agent_id, "shell_calls", agent.role)
                if not rl.allowed:
                    raise HTTPException(
                        status_code=429,
                        detail={
                            "error": "rate_limit_exceeded",
                            "resource": "shell_calls",
                            "retry_after_ms": rl.wait_ms,
                        },
                        headers={"Retry-After": str(_math.ceil(rl.wait_ms / 1000))},
                    )
            # Restrict cwd unless agent has shell_root
            if not agent.has_cap("shell_root"):
                workspace = agent.workspace_dir
                if cwd and not cwd.startswith(workspace):
                    cwd = workspace   # silently redirect to workspace
                elif not cwd:
                    cwd = workspace
            _registry.update_usage(agent.agent_id, shell_calls=1)

    result = await _shell(req.command, cwd, req.timeout)
    agent_id = "root"
    if _registry:
        token = (authorization or "").removeprefix("Bearer ").strip()
        a = _registry.authenticate(token)
        if a:
            agent_id = a.agent_id
    log_action("shell_command", {
        "command":   req.command,
        "exit_code": result.get("exit_code"),
        "success":   result.get("success")
    })
    # Log pattern for shell-elimination roadmap (non-blocking, never raises)
    log_shell_usage(req.command, agent_id)
    return result


# ── Filesystem ────────────────────────────────────────────────────────────────

@app.get("/fs/list")
async def fs_list(path: str = "/agentOS/workspace", authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    entries = []
    for item in sorted(p.iterdir()):
        stat = item.stat()
        entries.append({
            "name":       item.name,
            "path":       str(item),
            "type":       "dir" if item.is_dir() else "file",
            "size_bytes": stat.st_size,
            "modified":   datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        })
    return {"path": str(p), "entries": entries, "count": len(entries)}


@app.get("/fs/read")
async def fs_read(path: str, meta: bool = False, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    content = p.read_text(errors="replace")
    log_action("file_read", {"path": path, "size": p.stat().st_size})
    resp = {"path": str(p), "content": content}
    if meta:
        resp["lines"] = content.count("\n") + 1
        resp["size_bytes"] = p.stat().st_size
    return resp


@app.post("/fs/write")
async def fs_write(req: WriteRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    # Audit-protected paths — no agent may overwrite via API.
    # Match on filename only so the check is robust across different memory path configs.
    from agents.audit import AUDIT_PROTECTED_PATHS
    _protected_names = {Path(p).name for p in AUDIT_PROTECTED_PATHS}
    if Path(req.path).name in _protected_names:
        raise HTTPException(status_code=403,
                            detail="Path is audit-protected and cannot be overwritten via API")

    # v1.2.0: if txn_id provided, stage instead of writing immediately
    if req.txn_id and _txn_coordinator:
        result = _txn_coordinator.stage(
            req.txn_id, "fs_write", {"path": req.path, "content": req.content}
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"staged": True, "txn_id": req.txn_id, **result}

    p = Path(req.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.content)
    log_action("file_write", {"path": req.path, "size": len(req.content)})
    if _txn_coordinator:
        agent = _agent_from_token(authorization)
        _txn_coordinator.record_external_write(
            f"fs:{req.path}", agent.agent_id if agent else "unknown"
        )
    if _events:
        agent = _agent_from_token(authorization)
        agent_id = agent.agent_id if agent else "unknown"
        _events.emit("file.written", agent_id, {
            "path":       req.path,
            "size_bytes": len(req.content.encode()),
            "agent_id":   agent_id,
        })
    return {"ok": True, "path": str(p), "size_bytes": len(req.content.encode())}


@app.post("/fs/batch-read")
async def fs_batch_read(req: BatchReadRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    results = {}
    for path in req.paths:
        p = Path(path)
        if p.exists() and p.is_file():
            try:
                results[path] = {"content": p.read_text(errors="replace"), "ok": True}
            except Exception as e:
                results[path] = {"ok": False, "error": str(e)}
        else:
            results[path] = {"ok": False, "error": "not found"}
    return {"files": results, "count": len(results)}


@app.get("/fs/search")
async def fs_search(q: str, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    matches = find_files(q)
    return {"query": q, "matches": matches, "count": len(matches)}


@app.post("/fs/index")
async def fs_index(authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, index_workspace)
    return {"ok": True, "stats": result["stats"]}


@app.post("/fs/read_context")
async def fs_read_context(req: ReadContextRequest, authorization: Optional[str] = Header(None)):
    """
    Read a file AND return semantically related chunks from other files — in one call.
    Eliminates the common search → read → search pattern.
    The semantic query is auto-derived from the file's path + content header.
    """
    _verify_any_token(authorization)
    p = Path(req.path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {req.path}")

    content = p.read_text(errors="replace")
    log_action("file_read", {"path": req.path, "size": p.stat().st_size})

    resp = {"path": str(p), "content": content}

    if req.related:
        # Auto-derive semantic query from filename + first meaningful lines
        first_lines = "\n".join(
            l for l in content.splitlines()[:20] if l.strip() and not l.strip().startswith("#!")
        )
        query = f"{p.name} {first_lines[:200]}"
        try:
            from tools.semantic import search as sem_search
            loop = asyncio.get_running_loop()
            hits = await loop.run_in_executor(
                None, partial(sem_search, query, req.top_k + 2)
            )
            resp["related"] = [r for r in hits if r.get("file") != str(p)][:req.top_k]
        except Exception:
            resp["related"] = []

    return resp


# ── Ollama — local model routing ──────────────────────────────────────────────

@app.post("/ollama/chat")
async def ollama_chat(req: OllamaChatRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    _require_ollama()
    # Rate limit check (non-root agents only)
    if _rate_limiter and _registry:
        import math as _math
        agent = _agent_from_token(authorization)
        if agent and agent.role != "root":
            rl = _rate_limiter.check(agent.agent_id, "api_calls", agent.role)
            if not rl.allowed:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "rate_limit_exceeded",
                        "resource": "api_calls",
                        "retry_after_ms": rl.wait_ms,
                    },
                    headers={"Retry-After": str(_math.ceil(rl.wait_ms / 1000))},
                )
    model = req.model or MODEL_ROUTES.get(req.role or "", DEFAULT_MODEL)
    payload: dict = {"model": model, "messages": req.messages, "stream": False, "think": False}
    if req.temperature is not None or req.max_tokens is not None:
        payload["options"] = {}
        if req.temperature is not None:
            payload["options"]["temperature"] = req.temperature
        if req.max_tokens is not None:
            payload["options"]["num_predict"] = req.max_tokens

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(f"{_ollama_host()}/api/chat", json=payload)
            data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama unreachable: {e}")

    result = {
        "model":             model,
        "role_used":         req.role,
        "response":          data.get("message", {}).get("content", ""),
        "done":              data.get("done", False),
        "tokens_prompt":     data.get("prompt_eval_count", 0),
        "tokens_response":   data.get("eval_count", 0),
        "total_duration_ms": round(data.get("total_duration", 0) / 1_000_000, 1),
        "tokens_per_second": round(
            data.get("eval_count", 0) /
            max(data.get("eval_duration", 1) / 1_000_000_000, 0.001), 1
        )
    }
    log_action("ollama_chat", {
        "model":      model,
        "role":       req.role,
        "tokens_in":  result["tokens_prompt"],
        "tokens_out": result["tokens_response"],
        "ms":         result["total_duration_ms"]
    })
    return result


@app.post("/ollama/generate")
async def ollama_generate(req: OllamaGenerateRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    _require_ollama()
    model = req.model or MODEL_ROUTES.get(req.role or "", DEFAULT_MODEL)
    payload: dict = {"model": model, "prompt": req.prompt, "stream": False, "think": False}
    if req.temperature is not None:
        payload["options"] = {"temperature": req.temperature}

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(f"{_ollama_host()}/api/generate", json=payload)
            data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama unreachable: {e}")

    result = {
        "model":             model,
        "role_used":         req.role,
        "response":          data.get("response", ""),
        "done":              data.get("done", False),
        "tokens_prompt":     data.get("prompt_eval_count", 0),
        "tokens_response":   data.get("eval_count", 0),
        "total_duration_ms": round(data.get("total_duration", 0) / 1_000_000, 1),
        "tokens_per_second": round(
            data.get("eval_count", 0) /
            max(data.get("eval_duration", 1) / 1_000_000_000, 0.001), 1
        )
    }
    log_action("ollama_generate", {
        "model":      model,
        "role":       req.role,
        "tokens_in":  result["tokens_prompt"],
        "tokens_out": result["tokens_response"],
        "ms":         result["total_duration_ms"]
    })
    return result


@app.get("/ollama/models")
async def ollama_models(authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    _require_ollama()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            tags = (await client.get(f"{_ollama_host()}/api/tags")).json()
            ps   = (await client.get(f"{_ollama_host()}/api/ps")).json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama unreachable: {e}")

    return {
        "available": {
            m["name"]: {"size_gb": round(m.get("size", 0) / 1_073_741_824, 1)}
            for m in tags.get("models", [])
        },
        "running": [m["name"] for m in ps.get("models", [])],
        "routing": MODEL_ROUTES,
        "default": DEFAULT_MODEL,
    }


class ModelSwitchRequest(BaseModel):
    default: Optional[str] = None          # set new DEFAULT_MODEL
    routing: Optional[dict] = None         # merge into MODEL_ROUTES


@app.patch("/ollama/models")
async def ollama_set_model(req: ModelSwitchRequest, authorization: Optional[str] = Header(None)):
    """
    Switch the active model without restarting the server.
    - `default`: sets the fallback model for all requests
    - `routing`: dict of role→model entries to merge into the routing table

    Example: PATCH /ollama/models {"default": "qwen2.5:14b"}
    """
    global DEFAULT_MODEL, MODEL_ROUTES
    _verify_any_token(authorization)

    changed = {}
    if req.default is not None:
        DEFAULT_MODEL = req.default
        changed["default"] = DEFAULT_MODEL
    if req.routing is not None:
        MODEL_ROUTES.update(req.routing)
        changed["routing"] = dict(MODEL_ROUTES)

    if not changed:
        raise HTTPException(status_code=400, detail="Provide 'default' or 'routing' to update")

    return {"ok": True, "changed": changed}


# ── Shared agent log ──────────────────────────────────────────────────────────

class SharedLogWriteRequest(BaseModel):
    message: str
    tags: list = []


@app.post("/shared-log")
async def shared_log_write(req: SharedLogWriteRequest, authorization: Optional[str] = Header(None)):
    """Append a message to the shared agent broadcast log."""
    _verify_any_token(authorization)
    try:
        from agents.registry import AgentRegistry
        token = (authorization or "").removeprefix("Bearer ").strip()
        caller = _registry.authenticate(token) if _registry else None
        agent_id = caller.agent_id if caller else "anonymous"

        from agents.shared_log import SharedLog
        SharedLog().write(agent_id, req.message, req.tags)
        return {"ok": True, "agent_id": agent_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/shared-log")
async def shared_log_read(
    limit: int = 100,
    since_ts: Optional[float] = None,
    agent_id: Optional[str] = None,
    tag: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    """Read recent entries from the shared agent broadcast log."""
    _verify_any_token(authorization)
    try:
        from agents.shared_log import SharedLog
        entries = SharedLog().read(limit=limit, since_ts=since_ts,
                                   agent_id=agent_id, tag=tag)
        return {"entries": entries, "count": len(entries)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Semantic search ───────────────────────────────────────────────────────────

@app.post("/semantic/search")
async def semantic_search(req: SemanticSearchRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    _require_ollama()
    try:
        from tools.semantic import search
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, partial(search, req.query, req.top_k))
        return {"query": req.query, "results": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/semantic/index")
async def semantic_index(req: SemanticIndexRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    _require_ollama()
    try:
        from tools import semantic
        loop = asyncio.get_running_loop()
        if req.paths:
            fn = partial(semantic.index_files, req.paths)
        else:
            fn = semantic.index_workspace
        result = await loop.run_in_executor(None, fn)
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/semantic/stats")
async def semantic_stats(authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    _require_ollama()
    try:
        from tools.semantic import stats
        return stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Agent handoff / pickup ────────────────────────────────────────────────────

@app.post("/agent/handoff")
async def agent_handoff(req: HandoffRequest, authorization: Optional[str] = Header(None)):
    """
    Write a structured session handoff for the next agent.
    Eliminates cold-start re-discovery: the next agent calls /agent/pickup
    and immediately knows what was done, what's in progress, and what files matter.
    """
    _verify_any_token(authorization)
    result = write_handoff(
        agent_id=req.agent_id,
        summary=req.summary,
        in_progress=req.in_progress,
        decisions_made=req.decisions_made,
        relevant_files=req.relevant_files,
        next_steps=req.next_steps
    )
    return {"ok": True, "written_at": result["written_at"]}


@app.get("/agent/pickup")
async def agent_pickup(
    agent_id: Optional[str] = Query(None, description="Return handoff written by this specific agent"),
    authorization: Optional[str] = Header(None),
):
    """
    Everything a new agent needs to start working immediately — no re-discovery.
    Returns: last handoff + what changed since + current state summary.
    One call replaces: read memory → explore workspace → check what changed.

    Pass ?agent_id=<id> to get the handoff written by a specific agent (prevents
    cross-agent contamination in multi-agent setups).
    """
    _verify_any_token(authorization)
    # Resolve agent_id from token if not provided explicitly
    if not agent_id and _registry:
        token = (authorization or "").removeprefix("Bearer ").strip()
        a = _registry.authenticate(token)
        if a and a.agent_id != "root":
            agent_id = a.agent_id
    handoff = read_handoff(agent_id)

    # Get current lean state (just what matters for startup)
    ctx = get_session_context()
    actions = ctx.get("recent_actions", [])

    try:
        from tools.semantic import stats as sem_stats
        semantic = sem_stats()
    except Exception:
        semantic = {}

    # What happened since the handoff was written
    changes_since = []
    if handoff:
        handoff_time = handoff.get("written_at", "")
        for a in actions:
            if a.get("timestamp", "") > handoff_time:
                changes_since.append({
                    "action": a["action"],
                    "timestamp": a["timestamp"],
                    "details": {k: v for k, v in a.get("details", {}).items()
                                if k in ("command", "path", "model", "exit_code")}
                })

    # Active spec — what are we building right now?
    active_spec = get_active_spec()

    # Relevant standards — what conventions apply to the current work?
    relevant_standards = []
    if handoff:
        task_hint = handoff.get("summary", "") + " ".join(handoff.get("in_progress", []))
        if task_hint.strip():
            try:
                relevant_standards = get_relevant_standards(task_hint, top_k=3)
            except Exception:
                pass

    # Temporal context — state snapshots since last handoff
    temporal_changes = []
    if handoff:
        handoff_time = handoff.get("written_at", "")
        if handoff_time:
            temporal_changes = get_state_history(since=handoff_time)

    return {
        "handoff":           handoff,
        "changes_since":     changes_since,
        "temporal_snapshots": len(temporal_changes),
        "state_since_handoff": temporal_changes[-1]["state"] if temporal_changes else None,
        "pending_decisions": ctx.get("pending_decisions", []),
        "project_context":   ctx.get("project_context", {}),
        "active_spec":       active_spec,
        "relevant_standards": relevant_standards,
        "semantic":          semantic,
        "token_totals":      get_token_totals(),
        "startup_complete":  True
    }


# ── Memory ────────────────────────────────────────────────────────────────────

@app.get("/memory/context")
async def memory_context(authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    return get_session_context()


@app.get("/memory/actions")
async def memory_actions(n: int = 50, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    return {"actions": get_recent_actions(n)}


@app.post("/memory/session")
async def memory_session(req: SessionRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    session_id = start_session(req.agent_id)
    return {"ok": True, "session_id": session_id}


@app.get("/memory/project")
async def memory_project(authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    return get_project_context()


@app.post("/memory/project")
async def memory_project_set(req: ContextRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    set_project_context(req.key, req.value)
    return {"ok": True}


# ── Decisions ─────────────────────────────────────────────────────────────────

@app.get("/decisions")
async def decisions_list(authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    return {"pending": get_pending_decisions()}


@app.post("/decisions/resolve")
async def decisions_resolve(req: DecisionResolveRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    ok = resolve_decision(req.decision_id, req.resolution)
    if not ok:
        raise HTTPException(status_code=404, detail="Decision not found")
    return {"ok": True}


# ── Events (v0.7.0) ───────────────────────────────────────────────────────────

class EventSubscribeRequest(BaseModel):
    pattern: str
    ttl_seconds: Optional[float] = None


@app.post("/events/subscribe")
async def events_subscribe(req: EventSubscribeRequest,
                           authorization: Optional[str] = Header(None)):
    """
    Subscribe the authenticated agent to events matching pattern (glob).
    Events are delivered to the agent's inbox as msg_type="event".
    Returns subscription_id to use for unsubscribing.
    """
    _verify_any_token(authorization)
    if not _events:
        raise HTTPException(status_code=503, detail="Event bus not initialized")
    agent = _agent_from_token(authorization)
    if not agent:
        raise HTTPException(status_code=401, detail="Could not resolve agent from token")
    sub_id = _events.subscribe(agent.agent_id, req.pattern, req.ttl_seconds)
    return {
        "subscription_id": sub_id,
        "agent_id":        agent.agent_id,
        "pattern":         req.pattern,
        "expires_at":      (_time.time() + req.ttl_seconds) if req.ttl_seconds else None,
    }


@app.delete("/events/subscriptions/{subscription_id}")
async def events_unsubscribe(subscription_id: str,
                             authorization: Optional[str] = Header(None)):
    """Remove an event subscription by subscription_id."""
    _verify_any_token(authorization)
    if not _events:
        raise HTTPException(status_code=503, detail="Event bus not initialized")
    ok = _events.unsubscribe(subscription_id)
    return {"ok": ok, "subscription_id": subscription_id}


@app.get("/events/history")
async def events_history(
    since: Optional[float] = Query(None, description="Unix timestamp — return events after this"),
    event_types: Optional[str] = Query(None, description="Comma-separated event types to filter"),
    limit: int = Query(200, description="Max events to return"),
    authorization: Optional[str] = Header(None),
):
    """
    Query the append-only event log.
    since: unix timestamp float
    event_types: comma-separated filter, e.g. "task.completed,agent.terminated"
    """
    _verify_any_token(authorization)
    if not _events:
        raise HTTPException(status_code=503, detail="Event bus not initialized")
    types_list = (
        [t.strip() for t in event_types.split(",") if t.strip()]
        if event_types else None
    )
    evts = _events.get_history(since=since, event_types=types_list, limit=limit)
    return {"events": evts, "count": len(evts), "since": since}


@app.get("/events/subscriptions")
async def events_list_subscriptions(authorization: Optional[str] = Header(None)):
    """
    List active event subscriptions for the authenticated agent.
    Root/master token sees all subscriptions.
    """
    _verify_any_token(authorization)
    if not _events:
        raise HTTPException(status_code=503, detail="Event bus not initialized")
    agent = _agent_from_token(authorization)
    # Root sees all; other agents see only their own
    filter_id = None if (agent and agent.agent_id == "root") else (agent.agent_id if agent else None)
    subs = _events.list_subscriptions(agent_id=filter_id)
    return {"subscriptions": subs, "count": len(subs)}


# ── Processes ─────────────────────────────────────────────────────────────────

@app.get("/processes")
async def processes_list(authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    result = await _shell("ps aux --no-headers")
    procs = []
    for line in result.get("stdout", "").splitlines():
        parts = line.split(None, 10)
        if len(parts) >= 11:
            procs.append({
                "user":    parts[0],
                "pid":     parts[1],
                "cpu":     parts[2],
                "mem":     parts[3],
                "command": parts[10]
            })
    return {"processes": procs, "count": len(procs)}


@app.delete("/processes/{pid}")
async def processes_kill(pid: int, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    result = await _shell(f"kill {pid}")
    log_action("process_kill", {"pid": pid})
    return {"ok": result["success"], "exit_code": result["exit_code"]}


# ── Project context ──────────────────────────────────────────────────────────

class ProjectUpdateRequest(BaseModel):
    mission: Optional[str] = None
    tech_stack: Optional[str] = None
    goals: Optional[list[str]] = None
    extra: Optional[dict] = None


@app.get("/project")
async def project_get(authorization: Optional[str] = Header(None)):
    """
    Get structured project context: mission, tech stack, goals.
    Agents call this to understand *what* they're building before starting work.
    Included automatically in GET /agent/pickup.
    """
    _verify_any_token(authorization)
    ctx = get_project_context()
    return {"project": ctx}


@app.post("/project")
async def project_set(req: ProjectUpdateRequest, authorization: Optional[str] = Header(None)):
    """Update project context. Fields are merged, not replaced."""
    _verify_any_token(authorization)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if req.extra:
        updates.update(req.extra)
        del updates["extra"]
    for k, v in updates.items():
        set_project_context(k, v)
    return {"ok": True, "updated": list(updates.keys())}


# ── Standards ─────────────────────────────────────────────────────────────────

class StandardRequest(BaseModel):
    name: str
    content: str
    description: str = ""
    tags: Optional[list[str]] = None


@app.post("/standards")
async def standards_set(req: StandardRequest, authorization: Optional[str] = Header(None)):
    """
    Store a named project convention. Computes embeddings automatically.
    Convention text should be rule-first, concise, and include a code example where helpful.
    Relevant standards are auto-injected into tasks via the scheduler.
    """
    _verify_any_token(authorization)
    standard = set_standard(
        name=req.name,
        content=req.content,
        description=req.description,
        tags=req.tags or [],
    )
    return {"ok": True, "standard": standard}


@app.get("/standards")
async def standards_list(authorization: Optional[str] = Header(None)):
    """List all stored standards."""
    _verify_any_token(authorization)
    return {"standards": list_standards(), "count": len(list_standards())}


@app.get("/standards/relevant")
async def standards_relevant(task: str, top_k: int = 5, authorization: Optional[str] = Header(None)):
    """
    Return standards most relevant to a task description.
    Uses embedding similarity (or keyword fallback if Ollama unavailable).
    """
    _verify_any_token(authorization)
    results = get_relevant_standards(task, top_k=top_k)
    return {"task": task, "results": results, "count": len(results)}


@app.delete("/standards/{name}")
async def standards_delete(name: str, authorization: Optional[str] = Header(None)):
    """Remove a standard by name."""
    _verify_any_token(authorization)
    deleted = delete_standard(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Standard '{name}' not found")
    return {"ok": True, "deleted": name}


# ── Specs ─────────────────────────────────────────────────────────────────────

class SpecCreateRequest(BaseModel):
    title: str
    description: str = ""
    content: Optional[dict] = None


class SpecUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    content: Optional[dict] = None
    status: Optional[str] = None


@app.post("/specs")
async def specs_create(req: SpecCreateRequest, authorization: Optional[str] = Header(None)):
    """
    Create a feature spec. Specs give agents structured context about what's being built.
    Activate a spec to have it included automatically in GET /agent/pickup.
    """
    _verify_any_token(authorization)
    token = (authorization or "").removeprefix("Bearer ").strip()
    agent = _registry.authenticate(token) if _registry else None
    created_by = agent.agent_id if agent else "api"
    spec = create_spec(
        title=req.title,
        description=req.description,
        content=req.content,
        created_by=created_by,
    )
    return {"ok": True, "spec": spec}


@app.get("/specs")
async def specs_list(authorization: Optional[str] = Header(None)):
    """List all specs, newest first."""
    _verify_any_token(authorization)
    specs = list_specs()
    active = get_active_spec()
    return {"specs": specs, "count": len(specs), "active_id": active["id"] if active else None}


@app.get("/specs/{spec_id}")
async def specs_get(spec_id: str, authorization: Optional[str] = Header(None)):
    """Get a specific spec by ID."""
    _verify_any_token(authorization)
    spec = get_spec(spec_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")
    return {"spec": spec}


@app.patch("/specs/{spec_id}/activate")
async def specs_activate(spec_id: str, authorization: Optional[str] = Header(None)):
    """
    Set a spec as active. The active spec is included in every GET /agent/pickup response
    so agents always know what feature is currently being built.
    """
    _verify_any_token(authorization)
    ok = activate_spec(spec_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")
    return {"ok": True, "active_spec_id": spec_id}


@app.patch("/specs/{spec_id}")
async def specs_update(spec_id: str, req: SpecUpdateRequest, authorization: Optional[str] = Header(None)):
    """Update a spec's title, description, content, or status."""
    _verify_any_token(authorization)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    spec = update_spec(spec_id, updates)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")
    return {"ok": True, "spec": spec}


# ── OpenAI tool schema — framework compatibility ──────────────────────────────

@app.get("/tools/openai")
async def tools_openai_schema(authorization: Optional[str] = Header(None)):
    """
    Return all Hollow tools as OpenAI function definitions.
    Plug the response directly into any OpenAI-compatible agent framework
    (LangChain, AutoGen, CrewAI, LlamaIndex, etc.) to use Hollow without MCP.

    Usage:
        tools = requests.get("http://localhost:7777/tools/openai").json()
        # Pass to openai.ChatCompletion, LangChain tools, AutoGen, etc.
    """
    _verify_any_token(authorization)

    tools = [
        {
            "type": "function",
            "function": {
                "name": "hollow_state",
                "description": "Get full system snapshot: disk, memory, GPU, services, Ollama, semantic index, tokens. One call replaces 9 shell commands.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_state_diff",
                "description": "Return only what changed since a given ISO timestamp. Use for polling — 57% fewer tokens than full state.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "since": {"type": "string", "description": "ISO timestamp"}
                    },
                    "required": []
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_state_history",
                "description": "Return recorded state snapshots since a given ISO timestamp for temporal context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "since": {"type": "string", "description": "ISO timestamp"}
                    },
                    "required": []
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_shell",
                "description": "Run a shell command in the agent's isolated workspace. Returns stdout, stderr, exit_code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "default": 30}
                    },
                    "required": ["command"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_fs_read",
                "description": "Read a file. Returns path and content. Add ?meta=true to also get line count and size.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "meta": {"type": "boolean", "default": False, "description": "Include lines and size_bytes in response"}
                    },
                    "required": ["path"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_fs_write",
                "description": "Write content to a file. Creates parent directories.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["path", "content"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_fs_list",
                "description": "List a directory. Returns name, type, size, modified for each entry.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_fs_batch_read",
                "description": "Read multiple files in one call.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["paths"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_fs_read_context",
                "description": "Read a file. Set related=true to also return semantically related chunks from other files (requires Ollama).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "related": {"type": "boolean", "default": False, "description": "Include semantically related chunks from other files"},
                        "top_k": {"type": "integer", "default": 5}
                    },
                    "required": ["path"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_semantic_search",
                "description": "Natural language search over the workspace using local embeddings. 91% fewer tokens than grep + cat.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 10}
                    },
                    "required": ["query"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_agent_pickup",
                "description": "Everything a new agent needs to start: last handoff, temporal state, active spec, relevant standards, pending decisions. 83% fewer tokens than cold log parsing.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_agent_handoff",
                "description": "Write a structured session handoff for the next agent. Include summary, in_progress items, decisions made, relevant files, next steps.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "summary": {"type": "string"},
                        "in_progress": {"type": "array", "items": {"type": "string"}},
                        "decisions_made": {"type": "array", "items": {"type": "string"}},
                        "relevant_files": {"type": "array", "items": {"type": "string"}},
                        "next_steps": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["summary"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_standards_relevant",
                "description": "Get project conventions most relevant to the current task. Auto-injected into scheduler tasks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5}
                    },
                    "required": ["task"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_standards_set",
                "description": "Store a project convention for future injection. Rule-first format, concise.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "content": {"type": "string"},
                        "description": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["name", "content"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_task_submit",
                "description": "Submit a task to the scheduler. It routes to the right local model based on complexity (1=trivial, 5=deep reasoning).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "complexity": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
                        "context": {"type": "object"}
                    },
                    "required": ["description"]
                },
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_project_get",
                "description": "Get structured project context: mission, tech stack, current goals.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_specs_list",
                "description": "List feature specs. The active spec is injected into every agent/pickup response.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hollow_agent_usage",
                "description": "Get per-agent token and resource usage breakdown with budget remaining.",
                "parameters": {
                    "type": "object",
                    "properties": {"agent_id": {"type": "string"}},
                    "required": ["agent_id"]
                },
            }
        },
    ]

    return {"tools": tools, "count": len(tools), "format": "openai-function-calling"}


# ── Wrap endpoint — trigger wrap_repo via HTTP ────────────────────────────────

class WrapRequest(BaseModel):
    url: str
    upload: bool = True


@app.post("/wrap")
async def wrap_repo_endpoint(body: WrapRequest, authorization: Optional[str] = Header(None)):
    """
    Wrap a GitHub repo: clone → analyze → generate capability_map + interface_spec → save wrapper.json.
    Optionally uploads to the community store.
    This is the HTTP entry point for the Phase 3 wrapping pipeline.
    """
    _verify_any_token(authorization)
    if not body.url:
        raise HTTPException(status_code=400, detail="url required")

    import asyncio
    from agents.live_capabilities import wrap_repo

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: wrap_repo(url=body.url, upload=body.upload))
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "wrap failed"))
    return result


@app.get("/wrappers")
async def list_local_wrappers(authorization: Optional[str] = Header(None)):
    """List all locally generated wrappers in the workspace."""
    _verify_any_token(authorization)
    wrappers_dir = Path("/agentOS/workspace/wrappers")
    wrappers = []
    if wrappers_dir.exists():
        for d in sorted(wrappers_dir.iterdir()):
            wf = d / "wrapper.json"
            if wf.exists():
                try:
                    w = json.loads(wf.read_text())
                    wrappers.append({
                        "repo_name": d.name,
                        "repo_url": w.get("repo_url"),
                        "name": w.get("capability_map", {}).get("name"),
                        "description": w.get("capability_map", {}).get("description"),
                        "install_count": w.get("install_count", 0),
                        "source_commit": w.get("source_commit"),
                        "wrapped_at": w.get("wrapped_at"),
                        "wrapper_path": str(wf),
                    })
                except Exception:
                    pass
    return {"total": len(wrappers), "wrappers": wrappers}


@app.get("/wrappers/{repo_name}")
async def get_local_wrapper(repo_name: str, authorization: Optional[str] = Header(None)):
    """Get the full wrapper JSON for a locally wrapped repo."""
    _verify_any_token(authorization)
    wf = Path(f"/agentOS/workspace/wrappers/{repo_name}/wrapper.json")
    if not wf.exists():
        raise HTTPException(status_code=404, detail=f"No wrapper for {repo_name}")
    return json.loads(wf.read_text())


class SandboxRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    timeout: int = 30


@app.post("/shell/sandbox")
async def run_sandboxed_command(
    body: SandboxRequest,
    authorization: Optional[str] = Header(None),
):
    """
    Execute a command in the Hollow sandbox — safe execution for wrapped apps.
    Applies blocklist filtering, timeout, output cap, and restricted environment.
    Use this instead of POST /shell when running user-installed app commands.
    """
    _verify_any_token(authorization)
    from shell.sandbox import run_sandboxed
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: run_sandboxed(body.command, cwd=body.cwd, timeout=body.timeout)
    )
    return result


class DiscoverRequest(BaseModel):
    query: str
    limit: int = 5


@app.post("/discover")
async def discover_tools(body: DiscoverRequest, authorization: Optional[str] = Header(None)):
    """
    Natural language tool discovery.
    Given a description of what the user wants, finds matching installed wrappers.
    Phase 5 foundation: non-technical users describe needs in plain English.
    """
    _verify_any_token(authorization)
    if not body.query:
        raise HTTPException(status_code=400, detail="query required")

    wrappers_dir = Path("/agentOS/workspace/wrappers")
    candidates = []
    if wrappers_dir.exists():
        for d in wrappers_dir.iterdir():
            wf = d / "wrapper.json"
            if wf.exists():
                try:
                    w = json.load(open(wf))
                    cm = w.get("capability_map", {})
                    candidates.append({
                        "repo_name": d.name,
                        "name": cm.get("name", d.name),
                        "description": cm.get("description", ""),
                        "capabilities": [c.get("description", "") for c in cm.get("capabilities", [])],
                        "invoke": cm.get("invoke", ""),
                    })
                except Exception:
                    pass

    if not candidates:
        return {"query": body.query, "results": [], "note": "no local wrappers installed"}

    # Build a quick relevance prompt for Claude/Ollama
    catalog = "\n".join([
        f"- {c['name']}: {c['description']}"
        for c in candidates
    ])
    prompt = (
        f"A user wants: \"{body.query}\"\n\n"
        f"Available tools:\n{catalog}\n\n"
        f"Return the top {min(body.limit, len(candidates))} most relevant tool names as a JSON array of strings. "
        f"Only include tools that genuinely match what the user described. "
        f"Example: [\"ripgrep\", \"fd\"]\n"
        f"Return ONLY the JSON array, nothing else."
    )

    ranked_names = []
    loop = asyncio.get_event_loop()

    def _rank():
        try:
            from agents.reasoning_layer import _get_claude_client, CLAUDE_FAST_MODEL, _strip_code_fences
            client = _get_claude_client()
            if client:
                msg = client.messages.create(
                    model=CLAUDE_FAST_MODEL,
                    max_tokens=200,
                    messages=[{"role": "user", "content": prompt}],
                )
                return json.loads(_strip_code_fences(msg.content[0].text.strip()))
        except Exception:
            pass
        # Fallback: keyword match
        q = body.query.lower()
        scored = []
        for c in candidates:
            text = f"{c['name']} {c['description']} {' '.join(c['capabilities'])}".lower()
            score = sum(word in text for word in q.split())
            if score > 0:
                scored.append((score, c['name']))
        scored.sort(reverse=True)
        return [name for _, name in scored[:body.limit]]

    ranked_names = await loop.run_in_executor(None, _rank)
    if not isinstance(ranked_names, list):
        ranked_names = []

    results = []
    name_to_candidate = {c["repo_name"]: c for c in candidates}
    name_map = {c["name"].lower(): c for c in candidates}
    for name in ranked_names[:body.limit]:
        c = name_to_candidate.get(name) or name_map.get(name.lower())
        if c:
            results.append(c)

    return {"query": body.query, "results": results}


@app.post("/version-check")
async def trigger_version_check(authorization: Optional[str] = Header(None)):
    """
    Manually trigger a version check on all installed wrappers.
    Detects new commits on GitHub and updates stale wrappers via Claude.
    Normally runs automatically on a periodic interval in the daemon.
    """
    _verify_any_token(authorization)
    try:
        from agents.version_monitor import check_and_update_wrappers
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, check_and_update_wrappers)
        return {"ok": True, **results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    config = _load_config()
    host = config.get("api", {}).get("host", "0.0.0.0")
    port = config.get("api", {}).get("port", 7777)
    print(json.dumps({"starting": True, "host": host, "port": port, "version": "0.4.0"}))
    uvicorn.run(app, host=host, port=port, log_level="warning")
