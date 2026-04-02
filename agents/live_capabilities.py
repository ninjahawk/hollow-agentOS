"""
Live Capabilities — AgentOS v3.11.1.

Registers the OS's live operations as CapabilityGraph entries with
ExecutionEngine implementations. This is the bridge between the Phase
3-6 cognitive layer and the actual OS.

Before this module, Phase 3-6 modules existed as a library no running
system used. Now the CapabilityGraph is pre-populated with real ops
and the ExecutionEngine implementations call the live API.

Agents can:
  - Discover capabilities semantically ("how do I run a command?")
  - Execute them through the ExecutionEngine
  - Learn from outcomes via the autonomy loop

Capabilities:
  shell_exec       Run shell commands
  ollama_chat      Ask an LLM
  fs_read          Read a file
  fs_write         Write a file
  semantic_search  Search the codebase by meaning
  memory_set       Persist a key-value to agent memory
  memory_get       Retrieve a value from agent memory
  agent_message    Send a message to another agent
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))
API_BASE = os.getenv("AGENTOS_API_BASE", "http://localhost:7777")


# --------------------------------------------------------------------------- #
#  API plumbing                                                                #
# --------------------------------------------------------------------------- #

def _token() -> str:
    try:
        return json.loads(CONFIG_PATH.read_text())["api"]["token"]
    except Exception:
        return ""


def _call(method: str, path: str, **kwargs) -> dict:
    import httpx
    headers = {"Authorization": f"Bearer {_token()}"}
    with httpx.Client(timeout=30) as client:
        resp = getattr(client, method)(f"{API_BASE}{path}", headers=headers, **kwargs)
        resp.raise_for_status()
        return resp.json()


# --------------------------------------------------------------------------- #
#  Capability implementations                                                  #
# --------------------------------------------------------------------------- #
# Each function accepts keyword args with safe defaults so the ExecutionEngine
# can call them with func() (empty params) or func(**params) (non-empty).

def shell_exec(command: str = "", cwd: str = "/agentOS/workspace",
               timeout: int = 30) -> dict:
    """Run a shell command and return structured output."""
    if not command:
        return {"error": "no command provided", "success": False}
    result = _call("post", "/shell", json={"command": command, "cwd": cwd,
                                            "timeout": timeout})
    return {
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("exit_code", -1),
        "success": result.get("exit_code", -1) == 0,
    }


def ollama_chat(prompt: str = "", role: str = "general",
                max_tokens: int = 512) -> dict:
    """Ask a language model a question."""
    if not prompt:
        return {"error": "no prompt provided", "response": ""}
    result = _call("post", "/ollama/chat", json={
        "messages": [{"role": "user", "content": prompt}],
        "role": role,
        "max_tokens": max_tokens,
    })
    return {
        "response": result.get("response", ""),
        "model": result.get("model", ""),
        "tokens": result.get("tokens_response", 0),
    }


def fs_read(path: str = "") -> dict:
    """Read a file from the filesystem."""
    if not path:
        return {"error": "no path provided", "content": ""}
    result = _call("get", "/fs/read", params={"path": path})
    content = result.get("content", "")
    return {"content": content, "path": path, "size": len(content)}


def fs_write(path: str = "", content: str = "") -> dict:
    """Write content to a file."""
    if not path:
        return {"error": "no path provided", "ok": False}
    _call("post", "/fs/write", json={"path": path, "content": content})
    return {"ok": True, "path": path}


def semantic_search(query: str = "", top_k: int = 5) -> dict:
    """Search the indexed codebase by natural language."""
    if not query:
        return {"results": [], "count": 0}
    result = _call("post", "/semantic/search", json={"query": query,
                                                      "top_k": top_k})
    return {
        "results": result.get("results", []),
        "count": result.get("count", 0),
    }


def memory_set(key: str = "", value=None) -> dict:
    """Persist a key-value pair to shared agent memory."""
    if not key:
        return {"error": "no key provided", "ok": False}
    # Stringify non-string values so callers can store dicts/lists directly
    str_value = json.dumps(value) if not isinstance(value, str) else value
    _call("post", "/memory/project", json={"key": key, "value": str_value})
    return {"ok": True, "key": key}


def memory_get(key: str = "") -> dict:
    """Retrieve a previously stored memory value by key."""
    if not key:
        return {"error": "no key provided", "value": None}
    result = _call("get", "/memory/project")
    return {"key": key, "value": result.get(key)}


def agent_message(to_id: str = "", content: str = "",
                  msg_type: str = "text") -> dict:
    """Send a message to another agent."""
    if not to_id or not content:
        return {"error": "to_id and content required", "ok": False}
    result = _call("post", "/messages", json={
        "to_id": to_id, "content": content, "msg_type": msg_type
    })
    return {"ok": True, "msg_id": result.get("msg_id"), "to": to_id}


# --------------------------------------------------------------------------- #
#  Capability manifest                                                         #
# --------------------------------------------------------------------------- #

LIVE_CAPABILITIES = [
    {
        "capability_id": "shell_exec",
        "name": "Shell Command Execution",
        "description": (
            "Run a shell command on the OS. Use for file operations, running "
            "scripts, checking system state, installing packages, git operations."
        ),
        "input_schema": '{"command": "ls -la /agentOS/agents/", "cwd": "/agentOS"}',
        "output_schema": "stdout text, stderr text, exit code, and success flag",
        "composition_tags": ["execution", "system", "shell"],
        "fn": shell_exec,
        "timeout_ms": 60000,
    },
    {
        "capability_id": "ollama_chat",
        "name": "LLM Inference",
        "description": (
            "Ask a language model a question or request reasoning. Use for "
            "analysis, planning, summarization, code generation, decision making."
        ),
        "input_schema": '{"prompt": "Summarize the following: ..."}',
        "output_schema": "the model's response text",
        "composition_tags": ["reasoning", "inference", "llm", "analysis"],
        "fn": ollama_chat,
        "timeout_ms": 120000,
    },
    {
        "capability_id": "fs_read",
        "name": "Read File",
        "description": (
            "Read the contents of a file from the filesystem. "
            "Use to inspect code, configs, logs, or any text file."
        ),
        "input_schema": '{"path": "/agentOS/agents/autonomy_loop.py"}',
        "output_schema": "the file contents as text",
        "composition_tags": ["filesystem", "read", "io"],
        "fn": fs_read,
        "timeout_ms": 10000,
    },
    {
        "capability_id": "fs_write",
        "name": "Write File",
        "description": (
            "Write content to a file. Creates parent directories automatically. "
            "Use to save code, configs, results, or any text output."
        ),
        "input_schema": '{"path": "/agentOS/workspace/output.txt", "content": "text to write"}',
        "output_schema": "confirmation with the path written",
        "composition_tags": ["filesystem", "write", "io"],
        "fn": fs_write,
        "timeout_ms": 10000,
    },
    {
        "capability_id": "semantic_search",
        "name": "Semantic Code Search",
        "description": (
            "Search the indexed codebase by meaning. Finds functions, classes, "
            "and concepts matching a natural language query. "
            "Use before reading files to locate the right code."
        ),
        "input_schema": '{"query": "how goals are stored on disk", "top_k": 5}',
        "output_schema": "list of matching code chunks with file path and score",
        "composition_tags": ["search", "semantic", "code", "discovery"],
        "fn": semantic_search,
        "timeout_ms": 15000,
    },
    {
        "capability_id": "memory_set",
        "name": "Store Memory",
        "description": (
            "Persist a key-value pair to shared agent memory. "
            "Use to remember facts, decisions, or intermediate state across steps."
        ),
        "input_schema": '{"key": "search_results", "value": "summary of what was found"}',
        "output_schema": "confirmation that the value was stored",
        "composition_tags": ["memory", "storage", "persistence"],
        "fn": memory_set,
        "timeout_ms": 5000,
    },
    {
        "capability_id": "memory_get",
        "name": "Retrieve Memory",
        "description": (
            "Retrieve a previously stored memory value by key. "
            "Use to recall facts or state saved in earlier steps."
        ),
        "input_schema": '{"key": "search_results"}',
        "output_schema": "the stored value for that key, or None if not found",
        "composition_tags": ["memory", "retrieval", "persistence"],
        "fn": memory_get,
        "timeout_ms": 5000,
    },
    {
        "capability_id": "agent_message",
        "name": "Send Agent Message",
        "description": (
            "Send a message to another agent by ID. "
            "Use for coordination, delegation, reporting results, or requesting help."
        ),
        "input_schema": '{"to_id": "agent-abc123", "content": "task complete"}',
        "output_schema": "confirmation with the message ID",
        "composition_tags": ["communication", "coordination", "message"],
        "fn": agent_message,
        "timeout_ms": 10000,
    },
]


# --------------------------------------------------------------------------- #
#  Stack builders                                                              #
# --------------------------------------------------------------------------- #

def build_capability_graph():
    """
    Build and return a CapabilityGraph pre-populated with all live
    OS capabilities. Agents can semantically discover any of these.
    """
    from agents.capability_graph import CapabilityGraph, CapabilityRecord

    graph = CapabilityGraph()
    for cap in LIVE_CAPABILITIES:
        record = CapabilityRecord(
            capability_id=cap["capability_id"],
            name=cap["name"],
            description=cap["description"],
            input_schema=cap["input_schema"],
            output_schema=cap["output_schema"],
            composition_tags=cap["composition_tags"],
            introduced_by="system",
            confidence=1.0,
        )
        graph.register(record)
    return graph


def build_execution_engine():
    """
    Build and return an ExecutionEngine with implementations for all
    live OS capabilities.
    """
    from agents.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    for cap in LIVE_CAPABILITIES:
        engine.register(
            cap["capability_id"],
            cap["fn"],
            timeout_ms=cap.get("timeout_ms", 30000),
            requires_approval=False,
        )
    return engine


def build_live_stack():
    """
    Build the complete live capability stack.

    Returns (CapabilityGraph, ExecutionEngine) ready for use with
    ReasoningLayer and AutonomyLoop.

    Usage:
        graph, engine = build_live_stack()
        reasoning = ReasoningLayer(capability_graph=graph)
        loop = AutonomyLoop(reasoning_layer=reasoning,
                            execution_engine=engine, ...)
    """
    return build_capability_graph(), build_execution_engine()
