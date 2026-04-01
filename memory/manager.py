#!/usr/bin/env python3
"""
AgentOS Memory Manager
Persistent workspace knowledge — eliminates re-exploration between sessions.
"""

import json
import os
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))
MEMORY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory"))

# EventBus reference — injected at server startup, None when running standalone
_event_bus = None


def set_event_bus(event_bus) -> None:
    """Inject EventBus into the memory manager. Called at server startup."""
    global _event_bus
    _event_bus = event_bus

WORKSPACE_MAP   = MEMORY_PATH / "workspace-map.json"
SESSION_LOG     = MEMORY_PATH / "session-log.json"
TOOL_REGISTRY   = MEMORY_PATH / "tool-registry.json"
PROJECT_CONTEXT = MEMORY_PATH / "project-context.json"
DECISIONS       = MEMORY_PATH / "decisions-needed.json"
TOKEN_TOTALS    = MEMORY_PATH / "token-totals.json"
STATE_HISTORY   = MEMORY_PATH / "state-history.json"
SPECS_STORE     = MEMORY_PATH / "specs.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default
    return default


def _save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


# ── Write buffers — reduce disk I/O on hot paths ─────────────────────────────

_LOG_BUFFER: list = []          # buffered log entries not yet flushed
_LOG_BUFFER_SIZE = 10           # flush every N entries
_TOKEN_TOTALS_DIRTY = False     # in-memory totals differ from disk
_TOKEN_TOTALS_CACHE: dict = {}  # in-memory token counters
_TOKEN_FLUSH_EVERY = 10         # flush token totals every N updates
_token_dirty_count = 0


def _flush_log_buffer() -> None:
    """Write buffered log entries to disk."""
    global _LOG_BUFFER
    if not _LOG_BUFFER:
        return
    log = _load(SESSION_LOG, {"sessions": [], "current_session": None, "actions": []})
    config = _load(CONFIG_PATH, {})
    max_entries = config.get("memory", {}).get("max_session_log_entries", 10000)
    log["actions"].extend(_LOG_BUFFER)
    _LOG_BUFFER = []
    if len(log["actions"]) > max_entries:
        log["actions"] = log["actions"][-max_entries:]
    _save(SESSION_LOG, log)


def _flush_token_totals() -> None:
    """Write in-memory token totals to disk."""
    global _TOKEN_TOTALS_DIRTY, _token_dirty_count
    if not _TOKEN_TOTALS_DIRTY or not _TOKEN_TOTALS_CACHE:
        return
    _save(TOKEN_TOTALS, _TOKEN_TOTALS_CACHE)
    _TOKEN_TOTALS_DIRTY = False
    _token_dirty_count = 0


# ── Workspace Map ────────────────────────────────────────────────────────────

def index_workspace(root: str = None) -> dict:
    """Scan workspace and build a file index. Skips hidden dirs and common noise."""
    config = _load(CONFIG_PATH, {})
    root = root or config.get("workspace", {}).get("root", "/agentOS/workspace")
    extensions = set(config.get("workspace", {}).get("index_extensions", []))

    root_path = Path(root)
    if not root_path.exists():
        root_path.mkdir(parents=True, exist_ok=True)

    SKIP_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "env", "dist", "build", ".next", ".nuxt", "target", ".cache"
    }

    index = {
        "root": str(root_path),
        "indexed_at": _now(),
        "files": {},
        "dirs": [],
        "stats": {"total_files": 0, "total_dirs": 0, "total_bytes": 0}
    }

    for item in root_path.rglob("*"):
        if any(part in SKIP_DIRS for part in item.parts):
            continue
        if item.name.startswith("."):
            continue

        rel = str(item.relative_to(root_path))

        if item.is_dir():
            index["dirs"].append(rel)
            index["stats"]["total_dirs"] += 1
        elif item.is_file():
            if extensions and item.suffix not in extensions:
                continue
            stat = item.stat()
            index["files"][rel] = {
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "extension": item.suffix,
                "lines": _count_lines(item)
            }
            index["stats"]["total_files"] += 1
            index["stats"]["total_bytes"] += stat.st_size

    _save(WORKSPACE_MAP, index)
    return index


def _count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return -1


def get_workspace_map() -> dict:
    return _load(WORKSPACE_MAP, {})


def find_files(pattern: str) -> list:
    """Find files in workspace map matching a pattern (substring match on path)."""
    wmap = get_workspace_map()
    pattern = pattern.lower()
    return [
        {"path": k, **v}
        for k, v in wmap.get("files", {}).items()
        if pattern in k.lower()
    ]


# ── Session Log ──────────────────────────────────────────────────────────────

def log_action(action: str, details: dict = None) -> None:
    """Append an action to the session log and update persistent token totals."""
    global _LOG_BUFFER

    entry = {
        "timestamp": _now(),
        "action": action,
        "details": details or {}
    }
    _LOG_BUFFER.append(entry)

    # Flush to disk every LOG_BUFFER_SIZE entries
    if len(_LOG_BUFFER) >= _LOG_BUFFER_SIZE:
        _flush_log_buffer()

    # Update token totals for trackable actions
    d = details or {}
    if action == "shell_command":
        update_token_totals("shell_call")
    elif action == "file_read":
        update_token_totals("file_read")
    elif action == "file_write":
        update_token_totals("file_write")
    elif action in ("ollama_chat", "ollama_generate"):
        update_token_totals(
            "ollama",
            model=d.get("model"),
            tokens_in=d.get("tokens_in", 0),
            tokens_out=d.get("tokens_out", 0)
        )


def start_session(agent_id: str = "agent") -> str:
    """Mark the start of a new agent session."""
    # Flush any pending writes before creating a session boundary
    _flush_log_buffer()
    _flush_token_totals()
    log = _load(SESSION_LOG, {"sessions": [], "actions": []})
    session_id = f"{agent_id}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    session = {"id": session_id, "started_at": _now(), "agent": agent_id}
    log.setdefault("sessions", []).append(session)
    log["current_session"] = session_id
    _save(SESSION_LOG, log)
    log_action("session_start", {"session_id": session_id})
    return session_id


def get_recent_actions(n: int = 50) -> list:
    log = _load(SESSION_LOG, {"actions": []})
    # Merge flushed actions with any buffered-but-not-yet-flushed entries
    all_actions = log["actions"] + _LOG_BUFFER
    return all_actions[-n:]


# ── Token Totals (persistent across sessions) ────────────────────────────────

def update_token_totals(action_type: str, model: str = None,
                        tokens_in: int = 0, tokens_out: int = 0) -> None:
    """Update lifetime token/usage counters. Batches writes to reduce disk I/O."""
    global _TOKEN_TOTALS_CACHE, _TOKEN_TOTALS_DIRTY, _token_dirty_count

    # Warm the in-memory cache from disk on first use
    if not _TOKEN_TOTALS_CACHE:
        _TOKEN_TOTALS_CACHE = _load(TOKEN_TOTALS, {
            "updated_at": _now(),
            "lifetime": {
                "shell_calls": 0,
                "file_reads": 0,
                "file_writes": 0,
                "ollama_calls": 0,
                "ollama_tokens_in": 0,
                "ollama_tokens_out": 0
            },
            "by_model": {}
        })

    lt = _TOKEN_TOTALS_CACHE.setdefault("lifetime", {})
    if action_type == "shell_call":
        lt["shell_calls"] = lt.get("shell_calls", 0) + 1
    elif action_type == "file_read":
        lt["file_reads"] = lt.get("file_reads", 0) + 1
    elif action_type == "file_write":
        lt["file_writes"] = lt.get("file_writes", 0) + 1
    elif action_type == "ollama":
        lt["ollama_calls"] = lt.get("ollama_calls", 0) + 1
        lt["ollama_tokens_in"] = lt.get("ollama_tokens_in", 0) + (tokens_in or 0)
        lt["ollama_tokens_out"] = lt.get("ollama_tokens_out", 0) + (tokens_out or 0)
        if model:
            bm = _TOKEN_TOTALS_CACHE.setdefault("by_model", {})
            m = bm.setdefault(model, {"calls": 0, "tokens_in": 0, "tokens_out": 0})
            m["calls"] += 1
            m["tokens_in"] += tokens_in or 0
            m["tokens_out"] += tokens_out or 0

    _TOKEN_TOTALS_CACHE["updated_at"] = _now()
    _TOKEN_TOTALS_DIRTY = True
    _token_dirty_count += 1

    # Flush to disk every TOKEN_FLUSH_EVERY updates
    if _token_dirty_count >= _TOKEN_FLUSH_EVERY:
        _flush_token_totals()


def get_token_totals() -> dict:
    """Get lifetime token usage counters. Returns in-memory cache when available."""
    if _TOKEN_TOTALS_CACHE:
        return _TOKEN_TOTALS_CACHE
    return _load(TOKEN_TOTALS, {
        "lifetime": {
            "shell_calls": 0, "file_reads": 0, "file_writes": 0,
            "ollama_calls": 0, "ollama_tokens_in": 0, "ollama_tokens_out": 0
        },
        "by_model": {}
    })


# ── Tool Registry ────────────────────────────────────────────────────────────

def register_tool(name: str, description: str, usage: str, path: str) -> None:
    registry = _load(TOOL_REGISTRY, {"tools": {}})
    registry["tools"][name] = {
        "description": description,
        "usage": usage,
        "path": path,
        "registered_at": _now()
    }
    registry["updated_at"] = _now()
    _save(TOOL_REGISTRY, registry)


def get_tool_registry() -> dict:
    return _load(TOOL_REGISTRY, {"tools": {}})


def bootstrap_tool_registry() -> None:
    """Register all built-in AgentOS tools."""
    tools = [
        ("agent-shell",   "JSON-native shell — run any command, get JSON back",          "agent-shell <command>",           "/agentOS/shell/agent-shell.py"),
        ("agent-fs",      "Filesystem ops with JSON output",                              "agent-fs <list|read|write|stat>", "/agentOS/tools/agent-fs.py"),
        ("agent-git",     "Git operations with JSON output",                              "agent-git <status|log|diff>",     "/agentOS/tools/agent-git.py"),
        ("agent-search",  "Semantic + text search across workspace",                      "agent-search <query>",            "/agentOS/tools/agent-search.py"),
        ("batch-read",    "Read multiple files in one call",                              "batch-read file1 file2 ...",      "/agentOS/tools/batch-read.py"),
        ("agent-process", "Process management with JSON output",                          "agent-process <list|kill|start>", "/agentOS/tools/agent-process.py"),
        ("memory-manager","Read/write persistent agent memory",                           "memory-manager <get|set|index>",  "/agentOS/memory/manager.py"),
        ("state",         "Get full system state in one call",                            "state",                           "/agentOS/api/state.py"),
        ("decision",      "Queue a decision for human approval",                          "decision <message>",              "/agentOS/tools/decision.py"),
    ]
    for name, desc, usage, path in tools:
        register_tool(name, desc, usage, path)


# ── Project Context ──────────────────────────────────────────────────────────

def set_project_context(key: str, value: Any) -> None:
    ctx = _load(PROJECT_CONTEXT, {})
    ctx[key] = value
    ctx["updated_at"] = _now()
    _save(PROJECT_CONTEXT, ctx)


def get_project_context() -> dict:
    return _load(PROJECT_CONTEXT, {})


# ── Decisions Queue ──────────────────────────────────────────────────────────

def queue_decision(message: str, context: dict = None, blocking: bool = False) -> str:
    """
    Queue something that needs human input.
    Agent continues unless blocking=True.
    """
    decisions = _load(DECISIONS, {"pending": [], "resolved": []})
    decision_id = hashlib.md5(f"{message}{_now()}".encode()).hexdigest()[:8]
    decision = {
        "id": decision_id,
        "message": message,
        "context": context or {},
        "queued_at": _now(),
        "status": "pending",
        "blocking": blocking
    }
    decisions["pending"].append(decision)
    _save(DECISIONS, decisions)
    log_action("decision_queued", {"id": decision_id, "message": message})
    return decision_id


def resolve_decision(decision_id: str, resolution: str) -> bool:
    decisions = _load(DECISIONS, {"pending": [], "resolved": []})
    for i, d in enumerate(decisions["pending"]):
        if d["id"] == decision_id:
            d["status"] = "resolved"
            d["resolution"] = resolution
            d["resolved_at"] = _now()
            decisions["resolved"].append(d)
            decisions["pending"].pop(i)
            _save(DECISIONS, decisions)
            if _event_bus:
                _event_bus.emit("decision.resolved", "system", {
                    "decision_id": decision_id,
                    "resolution":  resolution,
                    "message":     d.get("message", ""),
                })
            return True
    return False


def get_pending_decisions() -> list:
    return _load(DECISIONS, {"pending": []})["pending"]


# ── Agent Handoff ────────────────────────────────────────────────────────────

HANDOFF_PATH = MEMORY_PATH / "handoff.json"  # legacy single-agent path
SESSION_LOG_MAX = 1000  # max actions kept in session log before eviction
MESSAGE_BUS_MAX = 2000  # max messages kept in bus before eviction


def _handoff_path(agent_id: str) -> Path:
    """Per-agent handoff file — prevents concurrent agents from overwriting each other."""
    safe_id = agent_id.replace("/", "_").replace("..", "_")
    return MEMORY_PATH / f"handoff-{safe_id}.json"


def write_handoff(agent_id: str, summary: str, in_progress: list = None,
                  decisions_made: list = None, relevant_files: list = None,
                  next_steps: list = None) -> dict:
    """
    Write a structured handoff for the next agent session.
    Each agent gets its own file — concurrent agents no longer overwrite each other.
    """
    # Flush buffers before writing handoff — ensures complete picture for next agent
    _flush_log_buffer()
    _flush_token_totals()
    handoff = {
        "written_at": _now(),
        "written_by": agent_id,
        "summary": summary,
        "in_progress": in_progress or [],
        "decisions_made": decisions_made or [],
        "relevant_files": relevant_files or [],
        "next_steps": next_steps or [],
        "token_totals_at_handoff": get_token_totals()
    }
    _save(_handoff_path(agent_id), handoff)
    # Also keep the legacy shared file for backwards compat
    _save(HANDOFF_PATH, handoff)
    log_action("agent_handoff", {"agent_id": agent_id, "summary": summary[:100]})
    return handoff


def read_handoff(agent_id: str = None) -> dict:
    """
    Get the handoff for a specific agent, or the most recent shared handoff.
    agent_id=None falls back to the legacy shared handoff file.
    """
    if agent_id:
        per_agent = _handoff_path(agent_id)
        if per_agent.exists():
            return _load(per_agent, None)
    return _load(HANDOFF_PATH, None)


# ── Session Context Dump ──────────────────────────────────────────────────────

def get_session_context() -> dict:
    """
    Called at the start of every agent session.
    Returns everything the agent needs to skip re-exploration.
    """
    return {
        "workspace_map": get_workspace_map(),
        "recent_actions": get_recent_actions(20),
        "tool_registry": get_tool_registry(),
        "project_context": get_project_context(),
        "pending_decisions": get_pending_decisions(),
        "loaded_at": _now()
    }


# ── State History (temporal context) ────────────────────────────────────────

STATE_HISTORY_MAX = 100  # rolling window


def record_state_snapshot(state: dict) -> None:
    """Append a state snapshot to the rolling history. Trims to STATE_HISTORY_MAX."""
    history = _load(STATE_HISTORY, {"snapshots": []})
    history["snapshots"].append({
        "recorded_at": _now(),
        "state": state
    })
    if len(history["snapshots"]) > STATE_HISTORY_MAX:
        history["snapshots"] = history["snapshots"][-STATE_HISTORY_MAX:]
    _save(STATE_HISTORY, history)


def get_state_history(since: str = None) -> list:
    """
    Return state snapshots newer than `since` (ISO timestamp).
    Returns all snapshots if since is None.
    Each entry: {recorded_at, state}
    """
    history = _load(STATE_HISTORY, {"snapshots": []})
    snapshots = history["snapshots"]
    if since:
        snapshots = [s for s in snapshots if s.get("recorded_at", "") > since]
    return snapshots


def get_state_diff_since(since: str) -> dict:
    """
    Derive what changed between the snapshot just before `since` and the latest snapshot.
    Returns a dict of top-level keys whose values differ.
    """
    snapshots = get_state_history()
    if not snapshots:
        return {}

    before = None
    after = None
    for s in snapshots:
        ts = s.get("recorded_at", "")
        if ts <= since:
            before = s["state"]
        else:
            after = s["state"]
            break

    if not after:
        after = snapshots[-1]["state"]

    if not before:
        return after  # no baseline — return latest

    changed = {}
    for key in after:
        if after[key] != before.get(key):
            changed[key] = after[key]
    return changed


# ── Spec Storage ─────────────────────────────────────────────────────────────

import uuid as _uuid


def create_spec(title: str, description: str = "", content: dict = None,
                created_by: str = "agent") -> dict:
    """Create a new feature spec. Returns the spec record."""
    specs = _load(SPECS_STORE, {"specs": {}, "active_spec_id": None})
    spec_id = _uuid.uuid4().hex[:10]
    spec = {
        "id": spec_id,
        "title": title,
        "description": description,
        "content": content or {},
        "created_by": created_by,
        "created_at": _now(),
        "updated_at": _now(),
        "status": "draft"
    }
    specs["specs"][spec_id] = spec
    _save(SPECS_STORE, specs)
    return spec


def get_spec(spec_id: str) -> dict:
    specs = _load(SPECS_STORE, {"specs": {}})
    return specs["specs"].get(spec_id)


def list_specs() -> list:
    specs = _load(SPECS_STORE, {"specs": {}})
    return sorted(specs["specs"].values(), key=lambda s: s["created_at"], reverse=True)


def activate_spec(spec_id: str) -> bool:
    specs = _load(SPECS_STORE, {"specs": {}, "active_spec_id": None})
    if spec_id not in specs["specs"]:
        return False
    specs["active_spec_id"] = spec_id
    specs["specs"][spec_id]["status"] = "active"
    spec_title = specs["specs"][spec_id].get("title", "")
    # Deactivate others
    for sid, s in specs["specs"].items():
        if sid != spec_id and s["status"] == "active":
            s["status"] = "draft"
    _save(SPECS_STORE, specs)
    if _event_bus:
        _event_bus.emit("spec.activated", "system", {
            "spec_id": spec_id,
            "title":   spec_title,
        })
    return True


def get_active_spec() -> dict:
    specs = _load(SPECS_STORE, {"specs": {}, "active_spec_id": None})
    active_id = specs.get("active_spec_id")
    if active_id:
        return specs["specs"].get(active_id)
    return None


def update_spec(spec_id: str, updates: dict) -> dict:
    specs = _load(SPECS_STORE, {"specs": {}})
    if spec_id not in specs["specs"]:
        return None
    specs["specs"][spec_id].update(updates)
    specs["specs"][spec_id]["updated_at"] = _now()
    _save(SPECS_STORE, specs)
    return specs["specs"][spec_id]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "context"

    if cmd == "index":
        root = sys.argv[2] if len(sys.argv) > 2 else None
        result = index_workspace(root)
        # Also rebuild the semantic (embedding) index so search stays current
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from tools.semantic import index_workspace as semantic_index
            sem = semantic_index()
            print(json.dumps({"ok": True, "stats": result["stats"], "semantic": sem}))
        except Exception as e:
            print(json.dumps({"ok": True, "stats": result["stats"], "semantic_error": str(e)}))

    elif cmd == "context":
        print(json.dumps(get_session_context()))

    elif cmd == "log":
        action = sys.argv[2] if len(sys.argv) > 2 else "manual"
        details = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        log_action(action, details)
        print(json.dumps({"ok": True}))

    elif cmd == "session-start":
        agent_id = sys.argv[2] if len(sys.argv) > 2 else "agent"
        session_id = start_session(agent_id)
        print(json.dumps({"ok": True, "session_id": session_id}))

    elif cmd == "decision":
        message = sys.argv[2] if len(sys.argv) > 2 else ""
        did = queue_decision(message)
        print(json.dumps({"ok": True, "decision_id": did}))

    elif cmd == "bootstrap":
        bootstrap_tool_registry()
        print(json.dumps({"ok": True, "message": "Tool registry bootstrapped"}))

    elif cmd == "set-context":
        key = sys.argv[2]
        value = json.loads(sys.argv[3])
        set_project_context(key, value)
        print(json.dumps({"ok": True}))

    elif cmd == "token-totals":
        print(json.dumps(get_token_totals(), indent=2))

    elif cmd == "handoff":
        summary = sys.argv[2] if len(sys.argv) > 2 else "No summary provided"
        result = write_handoff("cli", summary)
        print(json.dumps({"ok": True, "written_at": result["written_at"]}))

    else:
        print(json.dumps({"error": f"Unknown command: {cmd}"}))
        sys.exit(1)
