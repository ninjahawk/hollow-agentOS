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

CONFIG_PATH = Path("/agentOS/config.json")
MEMORY_PATH = Path("/agentOS/memory")

WORKSPACE_MAP   = MEMORY_PATH / "workspace-map.json"
SESSION_LOG     = MEMORY_PATH / "session-log.json"
TOOL_REGISTRY   = MEMORY_PATH / "tool-registry.json"
PROJECT_CONTEXT = MEMORY_PATH / "project-context.json"
DECISIONS       = MEMORY_PATH / "decisions-needed.json"


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


# ── Workspace Map ─────────────────────────────────────────────────────────────

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
        # skip hidden and noisy dirs
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


# ── Session Log ───────────────────────────────────────────────────────────────

def log_action(action: str, details: dict = None) -> None:
    """Append an action to the session log."""
    log = _load(SESSION_LOG, {"sessions": [], "current_session": None, "actions": []})
    config = _load(CONFIG_PATH, {})
    max_entries = config.get("memory", {}).get("max_session_log_entries", 10000)

    entry = {
        "timestamp": _now(),
        "action": action,
        "details": details or {}
    }

    log["actions"].append(entry)

    # trim if too long
    if len(log["actions"]) > max_entries:
        log["actions"] = log["actions"][-max_entries:]

    _save(SESSION_LOG, log)


def start_session(agent_id: str = "agent") -> str:
    """Mark the start of a new agent session."""
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
    return log["actions"][-n:]


# ── Tool Registry ─────────────────────────────────────────────────────────────

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


# ── Project Context ───────────────────────────────────────────────────────────

def set_project_context(key: str, value: Any) -> None:
    ctx = _load(PROJECT_CONTEXT, {})
    ctx[key] = value
    ctx["updated_at"] = _now()
    _save(PROJECT_CONTEXT, ctx)


def get_project_context() -> dict:
    return _load(PROJECT_CONTEXT, {})


# ── Decisions Queue ───────────────────────────────────────────────────────────

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
            return True
    return False


def get_pending_decisions() -> list:
    return _load(DECISIONS, {"pending": []})["pending"]


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


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "context"

    if cmd == "index":
        root = sys.argv[2] if len(sys.argv) > 2 else None
        result = index_workspace(root)
        print(json.dumps({"ok": True, "stats": result["stats"]}))

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

    else:
        print(json.dumps({"error": f"Unknown command: {cmd}"}))
        sys.exit(1)
