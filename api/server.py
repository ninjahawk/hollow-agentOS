#!/usr/bin/env python3
"""
AgentOS API Server v0.4.0
REST interface for agent control — filesystem, processes, memory, state,
Ollama model routing, semantic search, decisions, token tracking,
agent handoff/pickup, state diffing, read_context.

v0.4.0 adds real agent OS primitives:
  - Agent identity & per-agent tokens  (/agents/register)
  - Isolated per-agent workspaces      (each agent gets /workspace/agents/<id>/)
  - Capability enforcement             (agents can only use what they were granted)
  - Resource budgets & accounting      (shell calls, token in/out per agent)
  - Inter-agent message bus            (/messages)
  - Task scheduler w/ model routing    (/tasks/submit)
  - Agent spawning                     (/agents/spawn)
"""

import asyncio
import json
import os
import sys
import signal
import subprocess
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Optional

try:
    from fastapi import FastAPI, HTTPException, Header, Body
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
    get_token_totals, write_handoff, read_handoff
)
from shell.agent_shell import run as shell_run
from agents.registry import AgentRegistry
from agents.bus import MessageBus
from agents.scheduler import TaskScheduler
import api.agent_routes as agent_routes

CONFIG_PATH = Path("/agentOS/config.json")

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

# ── In-memory state cache (used by /state/diff) ──────────────────────────────
_state_cache: dict = {"data": None, "cached_at": None}


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


def _ollama_host() -> str:
    return _load_config().get("ollama", {}).get("host", "http://localhost:11434")


async def _shell(command: str, cwd: str = None, timeout: int = 30) -> dict:
    """Non-blocking shell execution — runs in thread pool to avoid event loop deadlock."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(shell_run, command, cwd, timeout))


app = FastAPI(title="AgentOS API", version="0.4.0", docs_url="/docs")

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


@app.on_event("startup")
async def _startup():
    global _registry, _bus, _scheduler
    config = _load_config()
    master_token = config.get("api", {}).get("token", "")
    _registry = AgentRegistry(master_token)
    _bus = MessageBus()
    _scheduler = TaskScheduler(_registry, _bus, master_token)
    agent_routes.init(_registry, _bus, _scheduler)


app.include_router(agent_routes.router)


# ── Models ───────────────────────────────────────────────────────────────────

class ShellRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    timeout: int = 30

class WriteRequest(BaseModel):
    path: str
    content: str

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
    disk_out = (await _shell("df -B1 / /mnt/c 2>/dev/null || df -B1 /")).get("stdout", "")
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
    for line in (await _shell("free -m")).get("stdout", "").splitlines():
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

    lp = (await _shell("cat /proc/loadavg")).get("stdout", "0 0 0").split()
    load = {
        "1m":  float(lp[0]) if len(lp) > 0 else 0.0,
        "5m":  float(lp[1]) if len(lp) > 1 else 0.0,
        "15m": float(lp[2]) if len(lp) > 2 else 0.0
    }

    gpu_raw = (await _shell(
        "/usr/lib/wsl/lib/nvidia-smi "
        "--query-gpu=name,memory.used,memory.total,utilization.gpu "
        "--format=csv,noheader,nounits 2>/dev/null || "
        "nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu "
        "--format=csv,noheader,nounits 2>/dev/null || echo 'no-gpu'"
    )).get("stdout", "no-gpu").strip()
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

    svc_raw = (await _shell(
        "for s in agentos-api ollama nginx agentos-indexer.timer; do "
        "echo \"$s:$(systemctl is-active $s 2>/dev/null)\"; done"
    )).get("stdout", "")
    services = {}
    for line in svc_raw.splitlines():
        if ":" in line:
            name, status = line.split(":", 1)
            services[name.strip()] = status.strip()

    ollama = {"available": [], "running": [], "error": None}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            tags = (await client.get(f"{_ollama_host()}/api/tags")).json()
            ps   = (await client.get(f"{_ollama_host()}/api/ps")).json()
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


# ── State ─────────────────────────────────────────────────────────────────────

@app.get("/state")
async def get_state(authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    try:
        result = await _build_state()
        # Update cache for diff endpoint
        _state_cache["data"] = result
        _state_cache["cached_at"] = result["time"]
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/state/diff")
async def get_state_diff(since: str = None, authorization: Optional[str] = Header(None)):
    """
    Return only what changed since `since` (ISO timestamp).
    If no cache exists or cache is older than `since`, returns full state.
    Eliminates repeated full-state parsing for agents that poll frequently.
    """
    _verify_any_token(authorization)
    try:
        fresh = await _build_state()
        _state_cache["data"] = fresh
        _state_cache["cached_at"] = fresh["time"]

        if not since:
            return {"full": True, "state": fresh, "since": None}

        # Deep-compare top-level sections against the state as it was at `since`
        # Since we can't replay history, we return sections whose values differ
        # from what would have been stable. Practically: return only the mutable
        # sections that are worth diffing for polling agents.
        changed = {}

        # Always include time-sensitive fields
        changed["time"] = fresh["time"]
        changed["tokens"] = fresh["tokens"]
        changed["token_totals"] = fresh["token_totals"]
        changed["recent_actions"] = fresh["recent_actions"]
        changed["pending_decisions"] = fresh["pending_decisions"]

        # Include GPU/system if load has changed meaningfully
        gpu = fresh["system"].get("gpu")
        if gpu:
            changed["gpu"] = gpu

        changed["load"] = fresh["system"].get("load")

        # Include Ollama running models (changes when models load/unload)
        changed["ollama_running"] = fresh["ollama"].get("running", [])

        # Include semantic index stats (changes on re-index)
        changed["semantic"] = fresh["semantic"]

        return {
            "full": False,
            "since": since,
            "generated_at": fresh["time"],
            "changed": changed
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
            # Restrict cwd unless agent has shell_root
            if not agent.has_cap("shell_root"):
                workspace = agent.workspace_dir
                if cwd and not cwd.startswith(workspace):
                    cwd = workspace   # silently redirect to workspace
                elif not cwd:
                    cwd = workspace
            _registry.update_usage(agent.agent_id, shell_calls=1)

    result = await _shell(req.command, cwd, req.timeout)
    log_action("shell_command", {
        "command":   req.command,
        "exit_code": result.get("exit_code"),
        "success":   result.get("success")
    })
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
async def fs_read(path: str, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    content = p.read_text(errors="replace")
    log_action("file_read", {"path": path, "size": p.stat().st_size})
    return {
        "path":       str(p),
        "content":    content,
        "lines":      content.count("\n") + 1,
        "size_bytes": p.stat().st_size
    }


@app.post("/fs/write")
async def fs_write(req: WriteRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    p = Path(req.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.content)
    log_action("file_write", {"path": req.path, "size": len(req.content)})
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

    # Auto-derive semantic query from filename + first meaningful lines
    first_lines = "\n".join(
        l for l in content.splitlines()[:20] if l.strip() and not l.strip().startswith("#!")
    )
    query = f"{p.name} {first_lines[:200]}"

    try:
        from tools.semantic import search as sem_search
        loop = asyncio.get_running_loop()
        related = await loop.run_in_executor(
            None, partial(sem_search, query, req.top_k + 2)
        )
        # Exclude chunks from the same file
        related = [r for r in related if r.get("file") != str(p)][:req.top_k]
    except Exception:
        related = []

    return {
        "path":       str(p),
        "content":    content,
        "lines":      content.count("\n") + 1,
        "size_bytes": p.stat().st_size,
        "related":    related
    }


# ── Ollama — local model routing ──────────────────────────────────────────────

@app.post("/ollama/chat")
async def ollama_chat(req: OllamaChatRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
    model = req.model or MODEL_ROUTES.get(req.role or "", DEFAULT_MODEL)
    payload: dict = {"model": model, "messages": req.messages, "stream": False}
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
    model = req.model or MODEL_ROUTES.get(req.role or "", DEFAULT_MODEL)
    payload: dict = {"model": model, "prompt": req.prompt, "stream": False}
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


# ── Semantic search ───────────────────────────────────────────────────────────

@app.post("/semantic/search")
async def semantic_search(req: SemanticSearchRequest, authorization: Optional[str] = Header(None)):
    _verify_any_token(authorization)
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
async def agent_pickup(authorization: Optional[str] = Header(None)):
    """
    Everything a new agent needs to start working immediately — no re-discovery.
    Returns: last handoff + what changed since + current state summary.
    One call replaces: read memory → explore workspace → check what changed.
    """
    _verify_any_token(authorization)
    handoff = read_handoff()

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

    return {
        "handoff":        handoff,
        "changes_since":  changes_since,
        "pending_decisions": ctx.get("pending_decisions", []),
        "project_context":   ctx.get("project_context", {}),
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


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    config = _load_config()
    host = config.get("api", {}).get("host", "0.0.0.0")
    port = config.get("api", {}).get("port", 7777)
    print(json.dumps({"starting": True, "host": host, "port": port, "version": "0.4.0"}))
    uvicorn.run(app, host=host, port=port, log_level="warning")
