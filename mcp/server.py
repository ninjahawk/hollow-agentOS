#!/usr/bin/env python3
"""
AgentOS MCP Server v1.2.0
Exposes AgentOS as native tools to Claude Code and any MCP-compatible agent.

Claude Code launches this via: wsl python3 /agentOS/mcp/server.py
All tool calls hit the AgentOS REST API on localhost:7777.

Tools (64 total):
  System:    state, state_diff, state_history
  Shell:     shell_exec
  FS:        fs_read, fs_write, fs_list, fs_batch_read, read_context
  Search:    search_files, search_content, semantic_search
  Git:       git_status, git_log, git_diff, git_commit
  Ollama:    ollama_chat
  Agent OS:  agent_register, agent_list, agent_get, agent_spawn,
             agent_suspend, agent_resume, agent_terminate,
             agent_lock, agent_lock_release, agent_usage,
             task_submit, task_get, task_list,
             message_send, message_inbox, message_thread
  Session:   agent_handoff, agent_pickup
  Memory:    memory_get, memory_set
  Standards: standards_set, standards_get, standards_list, standards_relevant, standards_delete
  Specs:     spec_create, spec_list, spec_get, spec_activate
  Project:   project_get, project_set
  Decisions: decision_queue
  Workspace: workspace_diff
  Events:    event_subscribe, event_unsubscribe, event_history  (v0.7.0)
  Signals:   agent_signal, agent_tombstone                      (v0.8.0)
  VRAM:      model_status                                       (v0.9.0)
  Heap:      memory_alloc, memory_read, memory_free, memory_list,
             memory_compress, heap_stats                        (v1.0.0)
  Audit:     audit_query, audit_stats, anomaly_history          (v1.1.0)
  Txn:       txn_begin, txn_commit, txn_rollback, txn_status    (v1.2.0)
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    print(json.dumps({"error": "Run: pip install httpx"}), file=sys.stderr)
    sys.exit(1)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print(json.dumps({"error": "Run: pip install mcp"}), file=sys.stderr)
    sys.exit(1)

CONFIG_PATH = Path("/agentOS/config.json")


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _api_base() -> str:
    config = _load_config()
    port = config.get("api", {}).get("port", 7777)
    return f"http://localhost:{port}"


def _headers() -> dict:
    config = _load_config()
    token = config.get("api", {}).get("token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _get(path: str, params: dict = None) -> Any:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{_api_base()}{path}", headers=_headers(), params=params or {})
        return r.json()


async def _post(path: str, body: dict = None) -> Any:
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(f"{_api_base()}{path}", headers=_headers(), json=body or {})
        return r.json()


def _out(data: Any) -> list[TextContent]:
    text = data if isinstance(data, str) else json.dumps(data, indent=2)
    return [TextContent(type="text", text=text)]


# ── Server ────────────────────────────────────────────────────────────────────

server = Server("agentos")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [

        # ── System ──────────────────────────────────────────────────────────
        Tool(
            name="state",
            description=(
                "Get full AgentOS system state in one call: disk, memory, GPU usage, "
                "workspace file index, recent actions, pending decisions, project context, "
                "lifetime token totals. Call this at the start of every session."
            ),
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="state_diff",
            description=(
                "Get only what changed since a given ISO timestamp. "
                "Returns: time, tokens, recent_actions, pending_decisions, gpu, load, "
                "ollama_running, semantic. Use this instead of state when polling — "
                "saves tokens by skipping unchanged fields."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "ISO timestamp of last state call"}
                }
            }
        ),

        # ── Shell ────────────────────────────────────────────────────────────
        Tool(
            name="shell_exec",
            description=(
                "Execute a shell command inside the AgentOS Linux environment. "
                "Returns structured JSON: stdout, stderr, exit_code, duration. "
                "Never blocks for input — all interactive prompts are suppressed. "
                "Use /agentOS/workspace as the working directory for project files."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "cwd": {"type": "string", "description": "Working directory (default: /agentOS/workspace)"},
                    "timeout": {"type": "integer", "description": "Timeout seconds (default: 30)"}
                },
                "required": ["command"]
            }
        ),

        # ── Filesystem ────────────────────────────────────────────────────────
        Tool(
            name="fs_read",
            description="Read a file. Returns content, line count, size in bytes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute Linux path to file"}
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="fs_write",
            description="Write content to a file. Creates parent directories automatically. Logs the write to session history.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute Linux path to write"},
                    "content": {"type": "string", "description": "File content"}
                },
                "required": ["path", "content"]
            }
        ),
        Tool(
            name="fs_list",
            description="List directory contents with type, size, and last-modified timestamp for each entry.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: /agentOS/workspace)"}
                }
            }
        ),
        Tool(
            name="fs_batch_read",
            description=(
                "Read multiple files in a single call. More token-efficient than "
                "calling fs_read repeatedly. Returns a map of path -> content."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of absolute file paths to read"
                    }
                },
                "required": ["paths"]
            }
        ),
        Tool(
            name="read_context",
            description=(
                "Read a file AND get semantically related chunks from other files in one call. "
                "Eliminates the read -> search -> read pattern. "
                "Returns: file content + top-k related chunks from the rest of the workspace."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file to read"},
                    "top_k": {"type": "integer", "description": "Number of related chunks to return (default: 5)"}
                },
                "required": ["path"]
            }
        ),

        # ── Search ────────────────────────────────────────────────────────────
        Tool(
            name="search_files",
            description="Find files by name pattern across the indexed workspace. Returns path, size, line count.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Substring to match against file paths"}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="search_content",
            description="Search file contents with regex using ripgrep. Returns matches with file path and line number.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Directory to search (default: /agentOS/workspace)"},
                    "context_lines": {"type": "integer", "description": "Lines of context around each match (default: 0)"}
                },
                "required": ["pattern"]
            }
        ),
        Tool(
            name="semantic_search",
            description=(
                "Natural language search across the entire indexed workspace using embeddings. "
                "Finds the right function or concept, not just the right filename. "
                "Returns: file, chunk_idx, preview (300 chars), similarity score."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "top_k": {"type": "integer", "description": "Number of results to return (default: 10)"}
                },
                "required": ["query"]
            }
        ),

        # ── Git ────────────────────────────────────────────────────────────────
        Tool(
            name="git_status",
            description="Get git status as structured JSON: branch, staged files, unstaged files, untracked files, clean flag.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository"}
                },
                "required": ["repo_path"]
            }
        ),
        Tool(
            name="git_log",
            description="Get recent commits as structured JSON: hash, author, email, date, message.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository"},
                    "n": {"type": "integer", "description": "Number of commits (default: 20)"}
                },
                "required": ["repo_path"]
            }
        ),
        Tool(
            name="git_diff",
            description="Get git diff as text. Pass staged=true to see staged-only diff.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository"},
                    "staged": {"type": "boolean", "description": "Show staged diff only (default: false)"}
                },
                "required": ["repo_path"]
            }
        ),
        Tool(
            name="git_commit",
            description="Stage all changes and create a commit. Returns the commit output.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository"},
                    "message": {"type": "string", "description": "Commit message"}
                },
                "required": ["repo_path", "message"]
            }
        ),

        # ── Ollama ────────────────────────────────────────────────────────────
        Tool(
            name="ollama_chat",
            description=(
                "Run inference on a local Ollama model via role-based routing. "
                "Roles: code, code-fast, general, general-large, reasoning, reasoning-large, uncensored, custom. "
                "Returns response + tokens_prompt + tokens_response + tokens_per_second."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Chat messages [{role, content}, ...]"
                    },
                    "role": {"type": "string", "description": "Model role (routes to the right model automatically)"},
                    "model": {"type": "string", "description": "Override model name directly"},
                    "temperature": {"type": "number", "description": "Sampling temperature (optional)"},
                    "max_tokens": {"type": "integer", "description": "Max tokens to generate (optional)"}
                },
                "required": ["messages"]
            }
        ),

        # ── Agent OS ──────────────────────────────────────────────────────────

        Tool(
            name="agent_register",
            description=(
                "Register a new agent with a name, role, and optional custom capabilities. "
                "Returns agent_id and token (shown once — store it). "
                "Roles: root, orchestrator, worker, coder, reasoner, readonly."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name":         {"type": "string", "description": "Human-readable agent name"},
                    "role":         {"type": "string", "description": "Agent role (worker, coder, orchestrator, etc.)"},
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional capability override (shell, fs_read, fs_write, ollama, spawn, message, admin)"
                    }
                },
                "required": ["name"]
            }
        ),
        Tool(
            name="agent_list",
            description="List all registered agents with their status, role, capabilities, and usage. Requires admin capability.",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="agent_get",
            description="Get a single agent's record: status, role, capabilities, workspace, budget, usage.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to look up"}
                },
                "required": ["agent_id"]
            }
        ),
        Tool(
            name="agent_spawn",
            description=(
                "Spawn a child agent, run one task with it, then auto-terminate it. "
                "The scheduler routes the task to the right local model automatically. "
                "Requires: spawn capability."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name":       {"type": "string", "description": "Name for the child agent"},
                    "task":       {"type": "string", "description": "Task description to run"},
                    "role":       {"type": "string", "description": "Child agent role (default: worker)"},
                    "complexity": {"type": "integer", "description": "Task complexity 1-5 (routes to model tier)"}
                },
                "required": ["name", "task"]
            }
        ),
        Tool(
            name="agent_suspend",
            description="Suspend an active agent. Its token will be rejected until resumed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to suspend"}
                },
                "required": ["agent_id"]
            }
        ),
        Tool(
            name="agent_resume",
            description="Resume a suspended agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to resume"}
                },
                "required": ["agent_id"]
            }
        ),
        Tool(
            name="agent_terminate",
            description="Terminate an agent permanently. Cannot be undone. Cannot terminate root.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to terminate"}
                },
                "required": ["agent_id"]
            }
        ),
        Tool(
            name="task_submit",
            description=(
                "Submit a task to the VRAM-aware scheduler. Complexity routes to the right local model "
                "with cache affinity (v0.9.0): 1-2 → mistral-nemo:12b, 3-4 → qwen2.5:14b, "
                "5 → qwen3.5-35b-moe. Priority: 0=URGENT (preempts BACKGROUND), 1=NORMAL, 2=BACKGROUND. "
                "Returns result synchronously with token usage and latency."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description":   {"type": "string", "description": "Task description / prompt"},
                    "complexity":    {"type": "integer", "description": "Complexity 1-5 (default: 2)"},
                    "priority":      {"type": "integer", "description": "0=URGENT, 1=NORMAL (default), 2=BACKGROUND"},
                    "context":       {"type": "object", "description": "Optional extra context dict"},
                    "system_prompt": {"type": "string", "description": "Optional system prompt override"}
                },
                "required": ["description"]
            }
        ),
        Tool(
            name="task_get",
            description="Get a task result by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID from task_submit"}
                },
                "required": ["task_id"]
            }
        ),
        Tool(
            name="task_list",
            description="List recent tasks with status, model role, complexity, and response preview.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max tasks to return (default: 20)"}
                }
            }
        ),
        Tool(
            name="message_send",
            description=(
                "Send a typed message to another agent's inbox. "
                "Types: task, result, alert, data, ping, log. "
                "Use to_id='broadcast' to send to all agents. "
                "Returns msg_id for threading."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to_id":       {"type": "string", "description": "Recipient agent ID (or 'broadcast')"},
                    "content":     {"type": "object", "description": "Message payload (any JSON object)"},
                    "msg_type":    {"type": "string", "description": "Message type: task, result, alert, data, ping, log"},
                    "reply_to":    {"type": "string", "description": "Parent msg_id for threading (optional)"},
                    "ttl_seconds": {"type": "number", "description": "Message TTL in seconds (optional)"}
                },
                "required": ["to_id", "content"]
            }
        ),
        Tool(
            name="message_inbox",
            description="Read messages from the current agent's inbox. Returns unread messages by default.",
            inputSchema={
                "type": "object",
                "properties": {
                    "unread_only": {"type": "boolean", "description": "Return only unread messages (default: true)"},
                    "limit":       {"type": "integer", "description": "Max messages to return (default: 20)"}
                }
            }
        ),
        Tool(
            name="message_thread",
            description="Get a message and all its replies in chronological order.",
            inputSchema={
                "type": "object",
                "properties": {
                    "msg_id": {"type": "string", "description": "Root message ID"}
                },
                "required": ["msg_id"]
            }
        ),

        # ── Agent handoff ─────────────────────────────────────────────────────
        Tool(
            name="agent_handoff",
            description=(
                "Write a structured session handoff so the next agent can start immediately. "
                "Include: what was done (summary), what is still in progress, decisions made, "
                "relevant files, and recommended next steps. "
                "The next agent calls agent_pickup to receive this."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "summary":        {"type": "string", "description": "What was accomplished this session"},
                    "in_progress":    {"type": "array", "items": {"type": "string"}, "description": "Tasks still in flight"},
                    "decisions_made": {"type": "array", "items": {"type": "string"}, "description": "Key decisions made"},
                    "relevant_files": {"type": "array", "items": {"type": "string"}, "description": "File paths the next agent should know about"},
                    "next_steps":     {"type": "array", "items": {"type": "string"}, "description": "Recommended next actions"},
                    "agent_id":       {"type": "string", "description": "Identifier for this agent session"}
                },
                "required": ["summary"]
            }
        ),
        Tool(
            name="agent_pickup",
            description=(
                "Get everything needed to start a new session without re-discovery. "
                "Returns: last handoff (what previous agent did + next steps), "
                "actions since handoff, pending decisions, project context, semantic index status. "
                "Call this instead of state at the start of a continuation session."
            ),
            inputSchema={"type": "object", "properties": {}}
        ),

        # ── Workspace diff ────────────────────────────────────────────────────
        Tool(
            name="workspace_diff",
            description=(
                "List workspace files modified since a given timestamp. "
                "Returns file paths, sizes, and modification times for everything that changed. "
                "Use this to find out what was edited between sessions without re-exploring the full workspace."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "ISO timestamp — return files modified after this time"},
                    "root":  {"type": "string", "description": "Root directory to scan (default: workspace root from config)"}
                },
                "required": ["since"]
            }
        ),

        # ── Memory / Decisions ────────────────────────────────────────────────
        Tool(
            name="memory_get",
            description="Get the full persistent project context — key/value store that survives restarts.",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="memory_set",
            description="Store a value in persistent project context. Survives restarts and session changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key":   {"type": "string", "description": "Context key"},
                    "value": {"description": "Value to store (any JSON type)"}
                },
                "required": ["key", "value"]
            }
        ),
        Tool(
            name="decision_queue",
            description="Queue a decision that needs human input. Agent continues working unless blocking=true.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message":  {"type": "string", "description": "The question or decision needed"},
                    "context":  {"type": "object",  "description": "Additional context for the human"},
                    "blocking": {"type": "boolean", "description": "Wait for resolution before continuing (default: false)"}
                },
                "required": ["message"]
            }
        ),

        # ── New in v0.6.0 ──────────────────────────────────────────────────────

        Tool(
            name="state_history",
            description="Return recorded state snapshots since a given ISO timestamp. Enables temporal context — what changed since the agent last looked.",
            inputSchema={
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "ISO timestamp (optional — returns all snapshots if omitted)"}
                },
                "required": []
            }
        ),
        Tool(
            name="standards_set",
            description="Store a named project convention. Rule-first format. Auto-injected into task scheduler when relevant.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name":        {"type": "string", "description": "Short identifier, e.g. 'api-response-format'"},
                    "content":     {"type": "string", "description": "Convention text — rule first, then explanation, then code example"},
                    "description": {"type": "string", "description": "One-line summary for listings"},
                    "tags":        {"type": "array", "items": {"type": "string"}, "description": "Optional labels"}
                },
                "required": ["name", "content"]
            }
        ),
        Tool(
            name="standards_get",
            description="Get a specific standard by name.",
            inputSchema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"]
            }
        ),
        Tool(
            name="standards_list",
            description="List all stored project conventions.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="standards_relevant",
            description="Get project conventions most relevant to a task description. Uses embedding similarity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task":  {"type": "string", "description": "Task description to match standards against"},
                    "top_k": {"type": "integer", "description": "Max results (default 5)"}
                },
                "required": ["task"]
            }
        ),
        Tool(
            name="standards_delete",
            description="Remove a project convention by name.",
            inputSchema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"]
            }
        ),
        Tool(
            name="spec_create",
            description="Create a feature spec. Specs give agents structured context about what's being built. Activate a spec to inject it into every agent_pickup call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title":       {"type": "string"},
                    "description": {"type": "string"},
                    "content":     {"type": "object", "description": "Structured spec content (plan, shape, references, etc.)"}
                },
                "required": ["title"]
            }
        ),
        Tool(
            name="spec_list",
            description="List all feature specs. Shows active spec ID.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="spec_get",
            description="Get a feature spec by ID.",
            inputSchema={
                "type": "object",
                "properties": {"spec_id": {"type": "string"}},
                "required": ["spec_id"]
            }
        ),
        Tool(
            name="spec_activate",
            description="Set a spec as active. The active spec is included in every agent_pickup response.",
            inputSchema={
                "type": "object",
                "properties": {"spec_id": {"type": "string"}},
                "required": ["spec_id"]
            }
        ),
        Tool(
            name="project_get",
            description="Get structured project context: mission, tech stack, current goals. Included in agent_pickup.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="project_set",
            description="Update project context fields (mission, tech_stack, goals). Fields are merged.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mission":    {"type": "string"},
                    "tech_stack": {"type": "string"},
                    "goals":      {"type": "array", "items": {"type": "string"}},
                    "extra":      {"type": "object"}
                },
                "required": []
            }
        ),
        Tool(
            name="agent_lock",
            description="Acquire a named timed lock for an agent. Returns 409 if another agent holds it. Expired locks are cleaned up automatically.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id":    {"type": "string"},
                    "lock_name":   {"type": "string"},
                    "ttl_seconds": {"type": "number", "description": "Lock TTL in seconds (default 300)"}
                },
                "required": ["agent_id", "lock_name"]
            }
        ),
        Tool(
            name="agent_lock_release",
            description="Release a named lock held by an agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id":  {"type": "string"},
                    "lock_name": {"type": "string"}
                },
                "required": ["agent_id", "lock_name"]
            }
        ),
        Tool(
            name="agent_usage",
            description="Get per-agent token and resource usage breakdown with budget remaining and active locks.",
            inputSchema={
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"]
            }
        ),

        # ── Events (v0.7.0) ──────────────────────────────────────────────────
        Tool(
            name="event_subscribe",
            description=(
                "Subscribe to system events matching a glob pattern. "
                "Events are delivered to your message inbox as msg_type='event'. "
                "Patterns: 'task.*' (all task events), 'agent.terminated', '*' (everything). "
                "Returns subscription_id for later unsubscription."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern":     {"type": "string",
                                   "description": "Glob pattern, e.g. 'task.*', 'agent.terminated', '*'"},
                    "ttl_seconds": {"type": "number",
                                   "description": "Subscription TTL in seconds. Omit for no expiry."},
                },
                "required": ["pattern"]
            }
        ),
        Tool(
            name="event_unsubscribe",
            description="Remove an event subscription by subscription_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "subscription_id": {"type": "string"}
                },
                "required": ["subscription_id"]
            }
        ),
        Tool(
            name="event_history",
            description=(
                "Query the append-only event log. Returns events newest-first. "
                "Use 'since' to get only events after a unix timestamp. "
                "Use 'event_types' to filter to specific types."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "since":       {"type": "number",
                                   "description": "Unix timestamp float — return events after this"},
                    "event_types": {"type": "string",
                                   "description": "Comma-separated filter, e.g. 'task.completed,agent.terminated'"},
                    "limit":       {"type": "integer",
                                   "description": "Max events to return (default 200)"},
                }
            }
        ),

        # ── Signals (v0.8.0) ────────────────────────────────────────────────
        Tool(
            name="agent_signal",
            description=(
                "Send a process signal to an agent. "
                "SIGTERM: graceful shutdown — agent gets grace_seconds to write handoff, "
                "then watchdog force-terminates. "
                "SIGPAUSE: immediately suspend, preserving current_task. "
                "SIGINFO: agent delivers status snapshot (task, usage, uptime) to caller's inbox."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id":      {"type": "string", "description": "Target agent ID"},
                    "signal":        {"type": "string", "enum": ["SIGTERM", "SIGPAUSE", "SIGINFO"],
                                     "description": "Signal to send"},
                    "grace_seconds": {"type": "number",
                                     "description": "SIGTERM only: seconds before force-kill (default 30)"},
                },
                "required": ["agent_id", "signal"],
            }
        ),
        Tool(
            name="agent_tombstone",
            description=(
                "Get the tombstone for a terminated agent. "
                "Tombstone records: reason, final_usage, current_task_at_termination, "
                "children, parent_id, terminated_at. "
                "Pass agent_id='all' to list tombstones for every terminated agent."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string",
                                "description": "Agent ID to get tombstone for, or 'all' to list all"},
                },
                "required": ["agent_id"],
            }
        ),
        # ── VRAM (v0.9.0) ────────────────────────────────────────────────────
        Tool(
            name="model_status",
            description=(
                "VRAM-aware model status (v0.9.0). Returns: loaded models, VRAM used/available/total (MB), "
                "VRAM pressure flag (>90% used), and task queue depth by priority "
                "(0=URGENT, 1=NORMAL, 2=BACKGROUND). Use before submitting large tasks to check headroom."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        # ── Working Memory (v1.0.0) ───────────────────────────────────────────
        Tool(
            name="memory_alloc",
            description=(
                "Allocate a named memory object on your working memory heap (v1.0.0). "
                "Content is measured in tokens, protected by priority (0-10), and optionally "
                "expires after ttl_seconds. Higher priority = protected from auto-compression. "
                "Use for facts, reasoning chains, or context you want to persist across turns."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "key":                  {"type": "string", "description": "Unique name for this memory slot"},
                    "content":              {"type": "string", "description": "Content to store"},
                    "priority":             {"type": "integer", "description": "0-10 (default: 5). ≥8 = never auto-compressed"},
                    "ttl_seconds":          {"type": "number",  "description": "Seconds until expiry (default: forever)"},
                    "compression_eligible": {"type": "boolean", "description": "Allow auto-compression (default: true)"},
                },
                "required": ["key", "content"]
            }
        ),
        Tool(
            name="memory_read",
            description="Read a memory object by key. Auto-swaps-in from disk if needed. Raises 404 if expired or freed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to read"},
                },
                "required": ["key"]
            }
        ),
        Tool(
            name="memory_free",
            description="Free a memory object and release its tokens from the heap.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to free"},
                },
                "required": ["key"]
            }
        ),
        Tool(
            name="memory_list",
            description=(
                "List all memory objects on your heap with metadata (no content). "
                "Includes heap_stats: total_tokens, object_count, compressible_tokens, "
                "swapped_count, fragmentation_score."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="memory_compress",
            description=(
                "Compress a memory object via Ollama summarization (mistral-nemo:12b). "
                "Original content saved to disk; compressed summary replaces it in heap. "
                "Returns original_tokens, compressed_tokens, ratio. Requires ollama capability."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to compress"},
                },
                "required": ["key"]
            }
        ),
        Tool(
            name="heap_stats",
            description=(
                "Return heap statistics for your working memory: total_tokens, object_count, "
                "compressible_tokens, swapped_count, fragmentation_score."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        # ── Audit (v1.1.0) ────────────────────────────────────────────────────
        Tool(
            name="audit_query",
            description=(
                "Query the audit log (v1.1.0). Filter by agent_id, operation, and time window. "
                "Returns entries with: agent_id, operation, params, result_code, tokens_charged, "
                "duration_ms, timestamp. Non-admin agents can only query their own entries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id":  {"type": "string", "description": "Filter by agent ID (omit for self)"},
                    "operation": {"type": "string", "description": "Filter by operation name (e.g. shell_exec)"},
                    "since":     {"type": "number",  "description": "Unix timestamp — entries after this time"},
                    "until":     {"type": "number",  "description": "Unix timestamp — entries before this time"},
                    "limit":     {"type": "integer", "description": "Max entries to return (default: 100)"},
                },
                "required": []
            }
        ),
        Tool(
            name="audit_stats",
            description=(
                "Return audit statistics for an agent: op_counts (per operation type), "
                "total_tokens, entry_count, anomaly_score (z-score vs role baseline). "
                "Agents can view own stats; admin can view any agent."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to get stats for"},
                },
                "required": ["agent_id"]
            }
        ),
        Tool(
            name="anomaly_history",
            description=(
                "Return recent security.anomaly events detected by the audit kernel (v1.1.0). "
                "Each anomaly includes: agent_id, metric, observed, baseline, z_score. "
                "Requires admin capability."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max anomalies to return (default: 50)"},
                },
                "required": []
            }
        ),
        # ── Transactions (v1.2.0) ─────────────────────────────────────────────
        Tool(
            name="txn_begin",
            description=(
                "Begin a multi-agent transaction (v1.2.0). Returns txn_id. "
                "Pass txn_id to fs_write, message_send to stage ops without applying. "
                "Auto-rolls-back after 60s if not committed."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="txn_commit",
            description=(
                "Commit a transaction. All staged ops applied atomically. "
                "Returns {ok: true, ops_count} or {ok: false, conflicts: [...]} on write-write conflict. "
                "On conflict, no staged ops are applied."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "txn_id": {"type": "string", "description": "Transaction ID from txn_begin"},
                },
                "required": ["txn_id"]
            }
        ),
        Tool(
            name="txn_rollback",
            description="Explicitly roll back a transaction. All staged ops discarded. Emits txn.rolled_back.",
            inputSchema={
                "type": "object",
                "properties": {
                    "txn_id": {"type": "string", "description": "Transaction ID to roll back"},
                },
                "required": ["txn_id"]
            }
        ),
        Tool(
            name="txn_status",
            description=(
                "Get the current status of a transaction: status (open/committed/rolled_back), "
                "ops_count, expires_in_seconds, conflict_resources."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "txn_id": {"type": "string", "description": "Transaction ID to query"},
                },
                "required": ["txn_id"]
            }
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "state":
            return _out(await _get("/state"))

        elif name == "state_diff":
            since = arguments.get("since")
            params = {"since": since} if since else {}
            return _out(await _get("/state/diff", params))

        elif name == "shell_exec":
            return _out(await _post("/shell", arguments))

        elif name == "fs_read":
            return _out(await _get("/fs/read", {"path": arguments["path"]}))

        elif name == "fs_write":
            return _out(await _post("/fs/write", arguments))

        elif name == "fs_list":
            path = arguments.get("path", "/agentOS/workspace")
            return _out(await _get("/fs/list", {"path": path}))

        elif name == "fs_batch_read":
            return _out(await _post("/fs/batch-read", {"paths": arguments["paths"]}))

        elif name == "read_context":
            return _out(await _post("/fs/read_context", {
                "path":  arguments["path"],
                "top_k": arguments.get("top_k", 5)
            }))

        elif name == "search_files":
            return _out(await _get("/fs/search", {"q": arguments["query"]}))

        elif name == "search_content":
            path = arguments.get("path", "/agentOS/workspace")
            ctx = arguments.get("context_lines", 0)
            pattern = arguments["pattern"]
            ctx_flag = f"-C {ctx}" if ctx else ""
            cmd = f"rg --json {ctx_flag} {json.dumps(pattern)} {json.dumps(path)}"
            result = await _post("/shell", {"command": cmd, "timeout": 30})
            matches = []
            for line in result.get("stdout", "").splitlines():
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "match":
                        data = obj["data"]
                        matches.append({
                            "file": data["path"]["text"],
                            "line": data["line_number"],
                            "text": data["lines"]["text"].rstrip()
                        })
                except Exception:
                    pass
            return _out({"matches": matches, "count": len(matches)})

        elif name == "semantic_search":
            return _out(await _post("/semantic/search", {
                "query": arguments["query"],
                "top_k": arguments.get("top_k", 10)
            }))

        elif name == "git_status":
            result = await _post("/shell", {
                "command": f"git -C {json.dumps(arguments['repo_path'])} status --porcelain=v1 -b"
            })
            lines = result.get("stdout", "").splitlines()
            branch = lines[0].replace("## ", "").split("...")[0] if lines else "unknown"
            staged, unstaged, untracked = [], [], []
            for line in lines[1:]:
                if len(line) < 3:
                    continue
                x, y, path = line[0], line[1], line[3:]
                if x not in (" ", "?"):
                    staged.append({"file": path, "status": x})
                if y not in (" ", "?"):
                    unstaged.append({"file": path, "status": y})
                if line[:2] == "??":
                    untracked.append(path)
            return _out({
                "branch": branch, "staged": staged, "unstaged": unstaged,
                "untracked": untracked,
                "clean": not staged and not unstaged and not untracked
            })

        elif name == "git_log":
            n = arguments.get("n", 20)
            repo = json.dumps(arguments["repo_path"])
            result = await _post("/shell", {
                "command": f"git -C {repo} log --pretty=format:'%H|%an|%ae|%ai|%s' -n {n}"
            })
            commits = []
            for line in result.get("stdout", "").splitlines():
                parts = line.split("|", 4)
                if len(parts) == 5:
                    commits.append({
                        "hash": parts[0], "author": parts[1],
                        "email": parts[2], "date": parts[3], "message": parts[4]
                    })
            return _out({"commits": commits, "count": len(commits)})

        elif name == "git_diff":
            repo = json.dumps(arguments["repo_path"])
            flag = "--staged" if arguments.get("staged") else ""
            result = await _post("/shell", {"command": f"git -C {repo} --no-pager diff {flag}"})
            return _out({"diff": result.get("stdout", ""), "ok": result.get("exit_code") == 0})

        elif name == "git_commit":
            repo = json.dumps(arguments["repo_path"])
            msg = json.dumps(arguments["message"])
            await _post("/shell", {"command": f"git -C {repo} add -A"})
            result = await _post("/shell", {"command": f"git -C {repo} commit -m {msg}"})
            return _out(result)

        elif name == "ollama_chat":
            return _out(await _post("/ollama/chat", arguments))

        elif name == "agent_register":
            return _out(await _post("/agents/register", {
                "name": arguments["name"],
                "role": arguments.get("role", "worker"),
                "capabilities": arguments.get("capabilities"),
            }))

        elif name == "agent_list":
            return _out(await _get("/agents"))

        elif name == "agent_get":
            return _out(await _get(f"/agents/{arguments['agent_id']}"))

        elif name == "agent_spawn":
            return _out(await _post("/agents/spawn", {
                "name":       arguments["name"],
                "task":       arguments["task"],
                "role":       arguments.get("role", "worker"),
                "complexity": arguments.get("complexity", 2),
            }))

        elif name == "agent_suspend":
            return _out(await _post(f"/agents/{arguments['agent_id']}/suspend"))

        elif name == "agent_resume":
            return _out(await _post(f"/agents/{arguments['agent_id']}/resume"))

        elif name == "agent_terminate":
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=30) as c:
                r = await c.delete(
                    f"{_api_base()}/agents/{arguments['agent_id']}",
                    headers=_headers()
                )
                return _out(r.json())

        elif name == "task_submit":
            return _out(await _post("/tasks/submit", {
                "description":   arguments["description"],
                "complexity":    arguments.get("complexity", 2),
                "priority":      arguments.get("priority", 1),
                "context":       arguments.get("context"),
                "system_prompt": arguments.get("system_prompt"),
            }))

        elif name == "task_get":
            return _out(await _get(f"/tasks/{arguments['task_id']}"))

        elif name == "task_list":
            params = {}
            if "limit" in arguments:
                params["limit"] = arguments["limit"]
            return _out(await _get("/tasks", params))

        elif name == "message_send":
            return _out(await _post("/messages", {
                "to_id":       arguments["to_id"],
                "content":     arguments["content"],
                "msg_type":    arguments.get("msg_type", "data"),
                "reply_to":    arguments.get("reply_to"),
                "ttl_seconds": arguments.get("ttl_seconds"),
            }))

        elif name == "message_inbox":
            return _out(await _get("/messages", {
                "unread_only": str(arguments.get("unread_only", True)).lower(),
                "limit":       arguments.get("limit", 20),
            }))

        elif name == "message_thread":
            return _out(await _get(f"/messages/thread/{arguments['msg_id']}"))

        elif name == "agent_handoff":
            body = {
                "agent_id":       arguments.get("agent_id", "agent"),
                "summary":        arguments["summary"],
                "in_progress":    arguments.get("in_progress", []),
                "decisions_made": arguments.get("decisions_made", []),
                "relevant_files": arguments.get("relevant_files", []),
                "next_steps":     arguments.get("next_steps", [])
            }
            return _out(await _post("/agent/handoff", body))

        elif name == "agent_pickup":
            return _out(await _get("/agent/pickup"))

        elif name == "workspace_diff":
            since_str = arguments["since"]
            root = arguments.get("root")
            if not root:
                cfg_path = Path("/agentOS/config.json")
                cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
                root = cfg.get("workspace", {}).get("root", "/agentOS/workspace")

            from datetime import datetime, timezone
            try:
                since_dt = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
            except ValueError:
                return _out({"error": f"Invalid timestamp: {since_str}"})

            SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv",
                    "dist", "build", ".next", "target", ".cache"}
            changed = []
            root_path = Path(root)
            if root_path.exists():
                for item in root_path.rglob("*"):
                    if any(p in SKIP for p in item.parts) or item.name.startswith("."):
                        continue
                    if not item.is_file():
                        continue
                    try:
                        mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
                        if mtime > since_dt:
                            changed.append({"path": str(item), "modified": mtime.isoformat(),
                                            "size": item.stat().st_size})
                    except Exception:
                        pass
            changed.sort(key=lambda x: x["modified"], reverse=True)
            return _out({"since": since_str, "root": str(root_path),
                          "changed_files": changed, "count": len(changed)})

        elif name == "memory_get":
            return _out(await _get("/memory/project"))

        elif name == "memory_set":
            return _out(await _post("/memory/project", {
                "key": arguments["key"], "value": arguments["value"]
            }))

        elif name == "decision_queue":
            from memory.manager import queue_decision
            did = queue_decision(
                arguments["message"],
                arguments.get("context", {}),
                arguments.get("blocking", False)
            )
            return _out({"ok": True, "decision_id": did})

        # ── New in v0.6.0 ────────────────────────────────────────────────────

        elif name == "state_history":
            since = arguments.get("since")
            params = {"since": since} if since else {}
            return _out(await _get("/state/history", params))

        elif name == "standards_set":
            return _out(await _post("/standards", {
                "name":        arguments["name"],
                "content":     arguments["content"],
                "description": arguments.get("description", ""),
                "tags":        arguments.get("tags", []),
            }))

        elif name == "standards_get":
            return _out(await _get(f"/standards/relevant", {"task": arguments["name"], "top_k": 1}))

        elif name == "standards_list":
            return _out(await _get("/standards"))

        elif name == "standards_relevant":
            return _out(await _get("/standards/relevant", {
                "task":  arguments["task"],
                "top_k": arguments.get("top_k", 5),
            }))

        elif name == "standards_delete":
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.delete(
                    f"{_api_base()}/standards/{arguments['name']}",
                    headers=_headers()
                )
                return _out(r.json())

        elif name == "spec_create":
            return _out(await _post("/specs", {
                "title":       arguments["title"],
                "description": arguments.get("description", ""),
                "content":     arguments.get("content", {}),
            }))

        elif name == "spec_list":
            return _out(await _get("/specs"))

        elif name == "spec_get":
            return _out(await _get(f"/specs/{arguments['spec_id']}"))

        elif name == "spec_activate":
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.patch(
                    f"{_api_base()}/specs/{arguments['spec_id']}/activate",
                    headers=_headers()
                )
                return _out(r.json())

        elif name == "project_get":
            return _out(await _get("/project"))

        elif name == "project_set":
            return _out(await _post("/project", {
                k: v for k, v in arguments.items() if v is not None
            }))

        elif name == "agent_lock":
            agent_id  = arguments["agent_id"]
            lock_name = arguments["lock_name"]
            ttl       = arguments.get("ttl_seconds", 300)
            return _out(await _post(
                f"/agents/{agent_id}/lock/{lock_name}",
                {"ttl_seconds": ttl}
            ))

        elif name == "agent_lock_release":
            agent_id  = arguments["agent_id"]
            lock_name = arguments["lock_name"]
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.delete(
                    f"{_api_base()}/agents/{agent_id}/lock/{lock_name}",
                    headers=_headers()
                )
                return _out(r.json())

        elif name == "agent_usage":
            return _out(await _get(f"/agents/{arguments['agent_id']}/usage"))

        # ── Events (v0.7.0) ──────────────────────────────────────────────────

        elif name == "event_subscribe":
            return _out(await _post("/events/subscribe", {
                "pattern":     arguments["pattern"],
                "ttl_seconds": arguments.get("ttl_seconds"),
            }))

        elif name == "event_unsubscribe":
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.delete(
                    f"{_api_base()}/events/subscriptions/{arguments['subscription_id']}",
                    headers=_headers()
                )
                return _out(r.json())

        elif name == "event_history":
            params = {}
            if "since" in arguments and arguments["since"] is not None:
                params["since"] = arguments["since"]
            if "event_types" in arguments and arguments["event_types"]:
                params["event_types"] = arguments["event_types"]
            if "limit" in arguments:
                params["limit"] = arguments["limit"]
            return _out(await _get("/events/history", params))

        # ── Signals (v0.8.0) ────────────────────────────────────────────────
        elif name == "agent_signal":
            agent_id = arguments["agent_id"]
            body = {"signal": arguments["signal"]}
            if "grace_seconds" in arguments:
                body["grace_seconds"] = arguments["grace_seconds"]
            return _out(await _post(f"/agents/{agent_id}/signal", body))

        elif name == "agent_tombstone":
            agent_id = arguments["agent_id"]
            if agent_id == "all":
                return _out(await _get("/tombstones"))
            return _out(await _get(f"/tombstones/{agent_id}"))

        # ── VRAM (v0.9.0) ────────────────────────────────────────────────────
        elif name == "model_status":
            return _out(await _get("/model_status"))

        # ── Working Memory (v1.0.0) ───────────────────────────────────────────
        elif name == "memory_alloc":
            body = {
                "key":     arguments["key"],
                "content": arguments["content"],
            }
            if "priority" in arguments:
                body["priority"] = arguments["priority"]
            if "ttl_seconds" in arguments:
                body["ttl_seconds"] = arguments["ttl_seconds"]
            if "compression_eligible" in arguments:
                body["compression_eligible"] = arguments["compression_eligible"]
            return _out(await _post("/memory/alloc", body))

        elif name == "memory_read":
            return _out(await _get(f"/memory/read/{arguments['key']}"))

        elif name == "memory_free":
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=30) as c:
                r = await c.delete(
                    f"{_api_base()}/memory/{arguments['key']}",
                    headers=_headers()
                )
                return _out(r.json())

        elif name == "memory_list":
            return _out(await _get("/memory"))

        elif name == "memory_compress":
            return _out(await _post("/memory/compress", {"key": arguments["key"]}))

        elif name == "heap_stats":
            return _out(await _get("/memory/stats"))

        # ── Audit (v1.1.0) ────────────────────────────────────────────────────
        elif name == "audit_query":
            params = {}
            for k in ("agent_id", "operation", "since", "until", "limit"):
                if k in arguments:
                    params[k] = arguments[k]
            return _out(await _get("/audit", params))

        elif name == "audit_stats":
            agent_id = arguments["agent_id"]
            return _out(await _get(f"/audit/stats/{agent_id}"))

        elif name == "anomaly_history":
            params = {}
            if "limit" in arguments:
                params["limit"] = arguments["limit"]
            return _out(await _get("/audit/anomalies", params))

        # ── Transactions (v1.2.0) ─────────────────────────────────────────────
        elif name == "txn_begin":
            return _out(await _post("/txn/begin", {}))

        elif name == "txn_commit":
            return _out(await _post(f"/txn/{arguments['txn_id']}/commit", {}))

        elif name == "txn_rollback":
            return _out(await _post(f"/txn/{arguments['txn_id']}/rollback", {}))

        elif name == "txn_status":
            return _out(await _get(f"/txn/{arguments['txn_id']}"))

        else:
            return _out({"error": f"Unknown tool: {name}"})

    except Exception as e:
        return _out({"error": str(e), "tool": name})


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
