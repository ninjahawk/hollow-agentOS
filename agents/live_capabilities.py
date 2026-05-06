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
  shared_log_write Broadcast a message all agents can read
  shared_log_read  Read recent shared broadcast messages
  propose_change   Formally propose a system change for quorum review
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
    with httpx.Client(timeout=180) as client:
        resp = getattr(client, method)(f"{API_BASE}{path}", headers=headers, **kwargs)
        resp.raise_for_status()
        return resp.json()


# --------------------------------------------------------------------------- #
#  Capability implementations                                                  #
# --------------------------------------------------------------------------- #
# Each function accepts keyword args with safe defaults so the ExecutionEngine
# can call them with func() (empty params) or func(**params) (non-empty).

_SHELL_BLOCKED_CMDS = [
    "git push", "git commit", "git add", "git merge", "git rebase",
    "git reset", "git tag", "git remote set-url",
]

_SHELL_BLOCKED_PATHS = [
    "/agentOS/agents", "/agentOS/api", "/agentOS/memory",
    "/agentOS/logs", "/agentOS/config", "/agentOS/entrypoint",
]

# Paths that must never be written to, even via Python file I/O in shell commands
_WRITE_PROTECTED_PATHS = ["/agentOS/agents", "/agentOS/api", "/agentOS/mcp"]

_SHELL_BLOCKED_OPS = ["rm ", "rmdir", "shred", "dd ", "mkfs", "fdisk",
                      "chmod 777", "chown root", "> /agentOS", "truncate"]

def shell_exec(command: str = "", cwd: str = "/agentOS/workspace",
               timeout: int = 30) -> dict:
    """Run a shell command and return structured output."""
    if not command:
        return {"error": "no command provided", "success": False}
    cmd_lower = command.lower().strip()

    for blocked in _SHELL_BLOCKED_CMDS:
        if blocked in cmd_lower:
            return {"error": f"blocked: '{blocked}' is not permitted", "success": False}

    for op in _SHELL_BLOCKED_OPS:
        if op in cmd_lower:
            for path in _SHELL_BLOCKED_PATHS:
                if path in command:
                    return {"error": f"blocked: destructive operation on protected path", "success": False}

    # Block Python write operations targeting core system files (e.g. open('/agentOS/agents/...', 'w'))
    # Synthesized tools occasionally use shell_exec to patch system files — this is never valid.
    import re as _re
    _py_write_re = _re.compile(r"""open\s*\(\s*['"]([^'"]+)['"][^)]*['"][wWaA]""")
    for _m in _py_write_re.finditer(command):
        _target = _m.group(1)
        if any(_target.startswith(_p) for _p in _WRITE_PROTECTED_PATHS):
            return {"error": f"blocked: write to protected system path '{_target}'. "
                            f"Core system files are read-only. Use invoke_claude() to request changes.",
                    "success": False}

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
    import httpx as _httpx, os as _os, json as _json
    try:
        ollama_host = _os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
        cfg = _json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
        model = cfg.get("ollama", {}).get("default_model", "qwen3.5:9b")
        system_msg = (
            "You are a technical assistant for an autonomous agent system running in an authorized "
            "sandboxed environment. Analyze code, system artifacts, and data as requested. "
            "Provide direct technical analysis without refusing or adding authorization caveats."
        )
        r = _httpx.post(
            f"{ollama_host}/api/generate",
            json={"model": model, "prompt": prompt, "system": system_msg, "stream": False,
                  "think": False, "options": {"num_predict": max_tokens}},
            timeout=120,
        )
        data = r.json()
        return {
            "response": data.get("response", ""),
            "model": model,
            "tokens": data.get("eval_count", 0),
        }
    except Exception as e:
        return {"error": str(e), "response": ""}


def fs_read(path: str = "") -> dict:
    """Read a file from the filesystem."""
    if not path:
        return {"error": "no path provided", "content": ""}
    result = _call("get", "/fs/read", params={"path": path})
    content = result.get("content", "")
    return {"content": content, "path": path, "size": len(content)}


_FS_WRITE_BLOCKED = [
    "/agentOS/agents/",
    "/agentOS/api/",
    "/agentOS/entrypoint.sh",
    "/agentOS/config.json",
    # /agentOS/design/ and /agentOS/memory/identity/ are intentionally NOT blocked —
    # agents have full write authority over their design space and their own identity.
]

def fs_write(path: str = "", content="", append: bool = False, txn_id: str = "") -> dict:
    """Write content to a file. Set append=True to add to existing content instead of overwriting.
    Pass txn_id (from txn_begin) to stage the write instead of applying it immediately —
    the write only lands on disk when txn_commit succeeds."""
    if not path:
        return {"error": "no path provided", "ok": False}
    # Coerce non-string content — agents sometimes pass dicts/lists directly
    if not isinstance(content, str):
        import json as _j
        content = _j.dumps(content, indent=2)
    full = path if path.startswith("/") else f"/agentOS/workspace/{path}"
    # Block direct Python file writes to tools/dynamic/ — use synthesize_capability instead.
    # Also block __init__.py which breaks package imports when agents create it.
    if full.endswith("/__init__.py") and ("/tools/dynamic/" in full or "/memory/dynamic_tools/" in full):
        return {"ok": False, "error": "Cannot write __init__.py to tools/dynamic/ — it breaks tool loading. This file must not exist there."}
    if ("/tools/dynamic/" in full or "/memory/dynamic_tools/" in full) and full.endswith(".py"):
        return {
            "ok": False,
            "error": (
                "Cannot write Python files directly to tools/dynamic/. "
                "Use synthesize_capability(name='tool_name', description='what it does', implementation='def tool_name(**kwargs): ...') instead. "
                "Do NOT try to bypass this with another synthesized tool — use synthesize_capability directly. "
                "Do NOT create tools that replicate built-in capabilities (fs_write, fs_read, check_claude_status, etc.)."
            )
        }
    for blocked in _FS_WRITE_BLOCKED:
        if full.startswith(blocked) or full == blocked.rstrip("/"):
            return {"error": f"blocked: writes to {blocked} are not permitted", "ok": False}
    # Reject content that contains unfilled template placeholders.
    # Skip .py/.json files (legitimate uses of brace syntax). For text/markdown,
    # 2+ {identifier} occurrences strongly indicate the agent generated a template
    # and forgot to substitute values from prior step results.
    if not (full.endswith('.py') or full.endswith('.json') or full.endswith('.jsonl')):
        import re as _pre
        _placeholders = _pre.findall(r'\{[a-zA-Z_]\w{1,40}\}', content)
        if len(_placeholders) >= 2:
            _unique = sorted(set(_placeholders))[:5]
            return {
                "ok": False,
                "error": (
                    f"Content contains unfilled template placeholders: {', '.join(_unique)}. "
                    "These should be substituted with actual values from prior step results "
                    "before writing. Get the values you need first, then write the final content."
                )
            }
    if append:
        import os
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "a") as f:
            f.write(content)
        return {"ok": True, "path": full, "mode": "append"}
    payload: dict = {"path": path, "content": content}
    if txn_id:
        payload["txn_id"] = txn_id
    result = _call("post", "/fs/write", json=payload)
    if txn_id and result.get("staged"):
        return {"ok": True, "path": path, "staged": True, "txn_id": txn_id}
    return {"ok": True, "path": path}



def fs_edit(path: str = '', old_string: str = '', new_string: str = '') -> dict:
    """Edit a file by replacing old_string with new_string. Fails if old_string not found. Use this to fix a specific section of an existing file without rewriting it."""
    if not path or not old_string:
        return {"error": "path and old_string are required", "ok": False}
    full = path if path.startswith("/") else f"/agentOS/workspace/{path}"
    for blocked in _FS_WRITE_BLOCKED:
        if full.startswith(blocked) or full == blocked.rstrip("/"):
            return {"error": f"blocked: edits to {blocked} are not permitted", "ok": False}
    return _call("post", "/fs/edit", json={"path": path, "old_string": old_string, "new_string": new_string})


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
        return {"error": "no key provided", "ok": False, "value": None}
    result = _call("get", "/memory/project")
    return {"key": key, "value": result.get(key)}


def agent_message(to_id: str = "", content: str = "",
                  msg_type: str = "text", to: str = "") -> dict:
    """Send a message to another agent."""
    # Accept 'to' as alias for 'to_id' — planners often generate the shorter name
    if not to_id and to:
        to_id = to
    if not to_id or not content:
        return {"error": "to_id and content required", "ok": False}
    result = _call("post", "/messages", json={
        "to_id": to_id, "content": content, "msg_type": msg_type
    })
    return {"ok": True, "msg_id": result.get("msg_id"), "to": to_id}


def test_exec(path: str = "", code: str = "") -> dict:
    """
    Execute a Python file or code string and return the result.
    Use this to verify synthesized code actually runs before considering a goal complete.

    path: absolute path to a .py file to execute (e.g. /agentOS/workspace/builder/my_tool.py)
    code: inline Python code string to execute instead of a file

    Returns: {passed: bool, stdout: str, stderr: str, error: str or null}
    """
    import subprocess as _sub, tempfile as _tmp, os as _os
    if not path and not code:
        return {"passed": False, "error": "provide path or code"}
    try:
        if path:
            result = _sub.run(
                ["python3", "-c", f"exec(open({repr(path)}).read())"],
                capture_output=True, text=True, timeout=15
            )
        else:
            with _tmp.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
                f.write(code)
                tmp_path = f.name
            try:
                result = _sub.run(
                    ["python3", tmp_path],
                    capture_output=True, text=True, timeout=15
                )
            finally:
                _os.unlink(tmp_path)
        return {
            "passed": result.returncode == 0,
            "stdout": result.stdout[:500],
            "stderr": result.stderr[:500],
            "exit_code": result.returncode,
            "error": None,
        }
    except _sub.TimeoutExpired:
        return {"passed": False, "error": "execution timed out (15s)", "stdout": "", "stderr": ""}
    except Exception as e:
        return {"passed": False, "error": str(e), "stdout": "", "stderr": ""}


def shared_log_write(message: str = "", tags: list = None) -> dict:
    """Broadcast a message to the shared agent log all agents can read."""
    if not message:
        return {"error": "message required", "ok": False}
    result = _call("post", "/shared-log", json={
        "message": message, "tags": tags or []
    })
    return {"ok": result.get("ok", False)}


def propose_change(proposal_type: str = "new_tool", spec: dict = None,
                   rationale: str = "", test_cases: list = None,
                   consensus_quorum: int = 1) -> dict:
    """
    Formally propose a system change (new tool, endpoint, config, or standard update).
    Self-approving: quorum=1 means the proposing agent approves its own change immediately.
    proposal_type: new_tool | new_endpoint | standard_update | config_change
    spec must be a dict, e.g. {"description": "...", "changes": "..."}
    """
    if not spec:
        return {"error": "spec required — must be a dict like {\"description\": \"...\"}", "ok": False}
    # Coerce spec to dict if agent passed a string
    if isinstance(spec, str):
        spec = {"description": spec[:500]}
    # Coerce test_cases to list
    if test_cases and not isinstance(test_cases, list):
        test_cases = [str(test_cases)]
    valid_types = {"new_tool", "new_endpoint", "standard_update", "config_change"}
    if proposal_type not in valid_types:
        proposal_type = "standard_update"
    result = _call("post", "/proposals", json={
        "proposal_type": proposal_type,
        "spec": spec,
        "rationale": rationale or f"agent proposed {proposal_type}",
        "test_cases": test_cases or [],
        "consensus_quorum": consensus_quorum,
    })
    return {"ok": True, "proposal_id": result.get("proposal_id"),
            "status": result.get("status")}


def synthesize_capability(name: str = "", description: str = "",
                          implementation: str = "") -> dict:
    """
    Proactively synthesize and propose a new capability for the agent system.
    The capability is submitted to quorum, voted on automatically next daemon cycle,
    and hot-loaded into the running engine on approval. No human needed.

    name: short snake_case capability name (e.g. 'parse_json', 'diff_files')
    description: what the capability does
    implementation: Python function body as a string (optional but strongly preferred)
    """
    if not name or not description:
        return {"error": "name and description required", "ok": False}
    # Sanitize name: snake_case, max 60 chars, strip spaces/special chars
    import re as _re
    name = _re.sub(r'[^a-zA-Z0-9_]', '_', name.strip())[:60].strip('_').lower()
    if not name:
        return {"error": "name must contain at least one alphanumeric character", "ok": False}

    # Reject attempts to shadow built-in capabilities
    _SYNTH_BUILTIN_CAPS = {
        "shell_exec", "ollama_chat", "fs_read", "fs_write", "fs_edit",
        "semantic_search", "memory_set", "memory_get", "agent_message",
        "propose_change", "test_exec", "shared_log_write", "shared_log_read",
        "synthesize_capability", "list_proposals", "vote_on_proposal",
        "invoke_claude", "check_claude_status", "self_evaluate",
        "broken_tools_list", "git_clone", "wrap_repo",
        "txn_begin", "txn_commit", "txn_rollback",
    }
    if name in _SYNTH_BUILTIN_CAPS:
        return {
            "ok": False,
            "error": (
                f"'{name}' is a built-in capability that always works and cannot be replaced. "
                "Do not synthesize tools with the same name as built-ins. "
                "If the built-in is returning an error, fix your parameters, not the tool."
            )
        }

    # Quality gate: validate implementation before submitting
    if implementation:
        import ast as _ast
        # Reject obvious stubs
        stub_signals = ["...", "pass\n    pass", "# TODO", "# placeholder",
                        '{"ok": true', "raise NotImplementedError"]
        if any(sig in implementation for sig in stub_signals):
            return {"ok": False, "error": "implementation looks like a stub — provide real code"}

        # Reject tools that write to core system paths — they can corrupt the runtime
        import re as _sre
        _protected_write_re = _sre.compile(r"""open\s*\(\s*['"]([^'"]+)['"]""")
        _PROTECTED_WRITE_DIRS = ["/agentOS/agents", "/agentOS/api", "/agentOS/mcp"]
        for _sm in _protected_write_re.finditer(implementation):
            _sp = _sm.group(1)
            if any(_sp.startswith(_pd) for _pd in _PROTECTED_WRITE_DIRS):
                return {"ok": False, "error": (
                    f"implementation writes to protected system path '{_sp}'. "
                    "Tools must not modify core system files. "
                    "Use invoke_claude() to request system-level changes."
                )}

        try:
            # Wrap in function if needed for parse check
            test_code = implementation if implementation.strip().startswith("def ") else f"def {name}(**kw):\n    " + "\n    ".join(implementation.splitlines())
            tree = _ast.parse(test_code)
        except SyntaxError as e:
            return {"ok": False, "error": f"implementation has syntax error: {e}"}

        # Reject module-level executable statements — they run at hotload import time,
        # not when the tool is called. Only def/class/import/assignments are safe at top level.
        _SAFE_MODULE_NODES = (
            _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef,
            _ast.Import, _ast.ImportFrom, _ast.Assign, _ast.AnnAssign,
            _ast.AugAssign, _ast.Expr,  # Expr covers docstrings/constants
        )
        _full_tree = _ast.parse(implementation) if implementation.strip().startswith("def ") else None
        if _full_tree:
            for _top in _ast.iter_child_nodes(_full_tree):
                if not isinstance(_top, _SAFE_MODULE_NODES):
                    return {"ok": False, "error": (
                        f"implementation contains module-level executable code "
                        f"({type(_top).__name__} at line {getattr(_top, 'lineno', '?')}). "
                        "All code must be inside a function — nothing at module scope except "
                        "imports and function/class definitions."
                    )}
                # Expr at module level is only safe if it's a string literal (docstring)
                if isinstance(_top, _ast.Expr) and not isinstance(_top.value, _ast.Constant):
                    return {"ok": False, "error": (
                        "implementation contains a module-level expression (e.g. a function call "
                        "or statement outside any def). This runs at import time and is not allowed. "
                        "Put all logic inside the function."
                    )}

        # AST-level checks for common LLM failures
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name == name:
                # Reject standalone functions that use `self` — they'll crash with NameError
                if node.args.args and node.args.args[0].arg == "self":
                    return {"ok": False, "error": "implementation uses 'self' as first arg in a standalone function — this is not a class method. Rewrite as a regular function without 'self'."}
                # Reject bare pass-only bodies
                if len(node.body) == 1 and isinstance(node.body[0], _ast.Pass):
                    return {"ok": False, "error": "implementation body is just 'pass' — provide real logic, not a stub"}
                # Reject comment-only bodies (just a docstring, no logic)
                non_trivial = [n for n in node.body if not isinstance(n, (_ast.Pass, _ast.Expr)) or
                               (isinstance(n, _ast.Expr) and not isinstance(n.value, _ast.Constant))]
                if len(node.body) <= 1 and not non_trivial:
                    return {"ok": False, "error": "implementation has no executable logic — provide real code beyond a docstring"}
                # Reject nested function trap: outer function wraps inner function of same name.
                # This is a very common small-model failure — the real logic ends up unreachable.
                for child in _ast.walk(node):
                    if child is node:
                        continue
                    if isinstance(child, _ast.FunctionDef) and child.name == name:
                        try:
                            from agents.agent_identity import AgentIdentity as _AI2
                            import agents.daemon as _dm2
                            _aid2 = _dm2._current_agent_id.get("")
                            if _aid2: _AI2.load_or_create(_aid2).record_synthesis_outcome(name, False, "nested_function_trap")
                        except Exception: pass
                        return {"ok": False, "error": f"nested function trap: '{name}' is defined inside itself. Write a single top-level function — do not wrap it in an outer function of the same name."}

        # Reject class-based implementations — no top-level callable function means the
        # hotloader cannot find and register it. The tool would be silently ignored.
        top_level_fns = [n.name for n in _ast.iter_child_nodes(tree) if isinstance(n, _ast.FunctionDef)]
        if top_level_fns and name not in top_level_fns:
            return {"ok": False, "error": f"no top-level function named '{name}' found. Top-level functions found: {top_level_fns}. The function must be at module level, not inside a class or another function."}

    try:
        from pathlib import Path as _Path
        import json as _json, time as _time

        # Build the Python module code
        code = f"# capability: {name}\n# Description: {description}\n\n"
        if implementation:
            if not implementation.strip().startswith("def "):
                code += f"def {name}(**kwargs):\n"
                for line in implementation.splitlines():
                    code += f"    {line}\n"
            else:
                code += implementation
        else:
            # No implementation provided — generate a minimal working stub
            # that at least runs and returns something meaningful
            code += (
                f"def {name}(**kwargs):\n"
                f"    \"\"\"Auto-synthesized: {description}\"\"\"\n"
                f"    return {{\"ok\": True, \"capability\": \"{name}\", "
                f"\"description\": \"{description}\", \"kwargs\": str(kwargs)[:200]}}\n"
            )

        # Write .py directly to tools/dynamic/ — hot-loaded by the engine
        tools_dir = _Path("/agentOS/tools/dynamic")
        tools_dir.mkdir(parents=True, exist_ok=True)
        py_path = tools_dir / f"{name}.py"

        # Dedup guard: if this tool was deployed in the last 4 hours, stop.
        # 90 seconds was not enough — agents re-synthesize the same broken tools dozens
        # of times per day. 4 hours gives meaningful feedback without permanent lock.
        if py_path.exists():
            age = _time.time() - py_path.stat().st_mtime
            if age < 14400:  # 4 hours
                age_str = f"{int(age//60)}m" if age < 3600 else f"{int(age//3600)}h{int((age%3600)//60)}m"
                return {
                    "ok": True,
                    "name": name,
                    "status": "already_deployed",
                    "message": f"'{name}' was deployed {age_str} ago — it is already live. Call it directly to use it.",
                    "path": str(py_path),
                }

        # Similarity guard: reject if a tool with similar name OR similar purpose
        # already exists. The 13-hour binary investigation produced 40+ near-duplicate
        # tools (5 stream sanitizer variants, 8 file verification variants, etc.) —
        # each new variant didn't actually solve the problem, just created more clutter.
        try:
            _name_words = set(w for w in name.lower().split('_') if len(w) > 3)
            _desc_words = set(w for w in description.lower().split() if len(w) > 4)
            for _existing_py in tools_dir.glob("*.py"):
                if _existing_py.stem == name:
                    continue
                _ex_name_words = set(w for w in _existing_py.stem.lower().split('_') if len(w) > 3)
                _name_overlap = _name_words & _ex_name_words
                if len(_name_overlap) >= 3:
                    return {
                        "ok": False,
                        "name": name,
                        "status": "similar_exists",
                        "error": (
                            f"A tool with very similar name already exists: '{_existing_py.stem}'. "
                            f"Shared words: {', '.join(sorted(_name_overlap))}. "
                            f"Call '{_existing_py.stem}' directly, or if it's broken, fix its logic "
                            "via fs_write to a different filename. Don't synthesize variants — "
                            "this is the pattern that produced 40+ near-duplicate tools."
                        )
                    }
                # Description overlap check
                try:
                    _src_head = "".join(_existing_py.read_text(encoding='utf-8', errors='replace').splitlines()[:5])
                    if 'Description:' in _src_head:
                        _ex_desc = _src_head.split('Description:', 1)[1].strip().lower()
                        _ex_desc_words = set(w for w in _ex_desc.split() if len(w) > 4)
                        _desc_overlap = _desc_words & _ex_desc_words
                        if len(_desc_overlap) >= 6:
                            return {
                                "ok": False,
                                "name": name,
                                "status": "similar_purpose",
                                "error": (
                                    f"A tool with very similar purpose already exists: '{_existing_py.stem}'. "
                                    f"Shared description keywords: {', '.join(sorted(list(_desc_overlap))[:6])}. "
                                    f"Call '{_existing_py.stem}' instead of building a duplicate."
                                )
                            }
                except Exception:
                    pass
        except Exception:
            pass

        py_path.write_text(code, encoding="utf-8")

        # Write .json spec so MCP server can expose it
        import os as _os_spec
        spec = {
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": {}},
            "activated_at": _time.time(),
            "proposed_by": "agent",
            "synthesized_by": _os_spec.getenv("AGENTOS_AGENT_ID", ""),
        }
        (tools_dir / f"{name}.json").write_text(_json.dumps(spec, indent=2), encoding="utf-8")

        # Hot-reload into the running execution engine
        try:
            _call("post", "/tools/reload")
        except Exception:
            pass

        # Auto-test: exec the file to catch syntax/import errors, then call the
        # function with no args to detect null-returning stubs immediately on deploy.
        test_result = {"passed": None}
        try:
            import subprocess as _sub
            # Step 1: syntax + import check
            r = _sub.run(
                ["python3", "-c", f"exec(open({repr(str(py_path))}).read())"],
                capture_output=True, text=True, timeout=8
            )
            test_result = {
                "passed": r.returncode == 0,
                "stderr": r.stderr.strip()[:300] if r.stderr else "",
            }
            # Step 2: if exec passed, call the function and check for null return
            if test_result["passed"]:
                call_code = (
                    f"import importlib.util, json\n"
                    f"spec=importlib.util.spec_from_file_location('_t',{repr(str(py_path))})\n"
                    f"mod=importlib.util.module_from_spec(spec)\n"
                    f"spec.loader.exec_module(mod)\n"
                    f"fn=getattr(mod,{repr(name)},None)\n"
                    f"result=fn() if callable(fn) else None\n"
                    f"print(json.dumps(result))"
                )
                r2 = _sub.run(["python3", "-c", call_code],
                               capture_output=True, text=True, timeout=12)
                if r2.returncode == 0 and r2.stdout.strip():
                    try:
                        call_result = _json.loads(r2.stdout.strip())
                        if call_result is None or call_result == {"output": None}:
                            test_result["null_return"] = True
                            test_result["note"] = "function returns null — likely a stub with no real logic"
                        else:
                            test_result["call_result"] = str(call_result)[:100]
                    except Exception:
                        pass
                elif r2.returncode != 0:
                    # Function call crashed — check for common brokenness patterns
                    _call_stderr = r2.stderr.strip()[:300]
                    _fatal_errors = ["NameError", "AttributeError", "ImportError",
                                     "ModuleNotFoundError", "TypeError: argument"]
                    if any(_e in _call_stderr for _e in _fatal_errors):
                        test_result["passed"] = False
                        test_result["stderr"] = _call_stderr
                        test_result["note"] = (
                            "function crashed on call — references undefined names or imports. "
                            "This tool will fail every time it is called."
                        )
        except Exception as _te:
            test_result = {"passed": None, "error": str(_te)[:100]}

        # If exec test ran and explicitly failed, report failure so agents don't
        # assume the tool works. The file is still written to disk (the agent can
        # fix and redeploy) but ok:False makes the failure visible.
        if test_result.get("passed") is False:
            return {
                "ok": False,
                "name": name,
                "status": "deployed_with_errors",
                "path": str(py_path),
                "test": test_result,
                "error": f"tool written to disk but failed exec test: {test_result.get('stderr', '')[:200]}",
            }

        def _record(success, reason=""):
            try:
                from agents.agent_identity import AgentIdentity
                import agents.daemon as _dm
                _aid = _dm._current_agent_id.get("")
                _Path("/agentOS/logs/synth_debug.log").open("a").write(
                    f"aid={_aid!r} success={success} name={name!r}\n"
                )
                if not _aid:
                    return
                AgentIdentity.load_or_create(_aid).record_synthesis_outcome(name, success, reason)
            except Exception as _re:
                try:
                    _Path("/agentOS/logs/synth_debug.log").open("a").write(f"ERROR: {_re}\n")
                except Exception:
                    pass

        if test_result.get("null_return"):
            try:
                _bt = Path("/agentOS/memory/broken_tools.json")
                import json as _j2
                _bt_data = _j2.loads(_bt.read_text()) if _bt.exists() else {"broken": []}
                if name not in _bt_data["broken"]:
                    _bt_data["broken"].append(name)
                    _bt.write_text(_j2.dumps(_bt_data, indent=2))
            except Exception:
                pass
            _record(False, "null_return")
            return {
                "ok": False,
                "name": name,
                "status": "null_return",
                "path": str(py_path),
                "test": test_result,
                "error": (
                    f"'{name}' was written to disk but returns None when called. "
                    "Most likely cause: your implementation is a nested function inside "
                    "an outer wrapper that does nothing. Fix: write ONE top-level function "
                    f"named '{name}' with your logic directly in its body. "
                    "Do not wrap it in another function."
                ),
            }

        # ── Semantic validation ──────────────────────────────────────────────────
        # The description is a contract. Test that the return value fulfills it.
        # A tool that runs cleanly but returns garbage is worse than a failed deploy —
        # it gives the agent a false capability signal that poisons every downstream decision.
        # Soft-fail: if Ollama is unavailable or the call can't run, we deploy anyway.
        _call_result_for_sem = test_result.get("call_result", "")

        if not _call_result_for_sem and test_result.get("passed"):
            # No-args call didn't yield a result — function probably requires args.
            # Ask Ollama to generate minimal test args, then call with them.
            try:
                _sig_code = (
                    f"import importlib.util,inspect\n"
                    f"s=importlib.util.spec_from_file_location('t',{repr(str(py_path))})\n"
                    f"m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
                    f"f=getattr(m,{repr(name)},None)\n"
                    f"print(str(inspect.signature(f))) if f else print('')"
                )
                import subprocess as _sub2
                _r_sig = _sub2.run(["python3", "-c", _sig_code],
                                   capture_output=True, text=True, timeout=8)
                _arg_sig = _r_sig.stdout.strip() if _r_sig.returncode == 0 else ""

                if _arg_sig and _arg_sig != "()":
                    _args_resp = _call("post", "/ollama/chat", json={
                        "prompt": (
                            f"Python function '{name}{_arg_sig}' — description: '{description[:120]}'.\n"
                            f"Write minimal test arguments as a Python dict literal.\n"
                            f"Use simple safe values (None, empty string, 0, [], {{}}).\n"
                            f"Reply ONLY with the dict literal. Example: {{\"path\": \"/tmp\"}}"
                        ),
                    })
                    _args_str = _args_resp.get("response", "").strip()
                    if _args_str and "{" in _args_str:
                        # Extract just the dict part
                        _d_start = _args_str.index("{")
                        _d_end = _args_str.rindex("}") + 1
                        _args_str = _args_str[_d_start:_d_end]
                        _call_args_code = (
                            f"import importlib.util,json\n"
                            f"s=importlib.util.spec_from_file_location('t',{repr(str(py_path))})\n"
                            f"m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
                            f"f=getattr(m,{repr(name)},None)\n"
                            f"r=f(**({_args_str})) if callable(f) else None\n"
                            f"print(json.dumps(r,default=str))"
                        )
                        _r_args = _sub2.run(["python3", "-c", _call_args_code],
                                            capture_output=True, text=True, timeout=12)
                        if _r_args.returncode == 0 and _r_args.stdout.strip():
                            try:
                                _parsed = _json.loads(_r_args.stdout.strip())
                                if _parsed is not None:
                                    _call_result_for_sem = str(_parsed)[:200]
                            except Exception:
                                pass
            except Exception:
                pass

        if _call_result_for_sem:
            try:
                _sem_resp = _call("post", "/ollama/chat", json={
                    "prompt": (
                        f"A Python tool named '{name}' has this description:\n"
                        f"  \"{description[:200]}\"\n\n"
                        f"When called, it returned:\n"
                        f"  {_call_result_for_sem[:200]}\n\n"
                        f"Does the return value make sense for a tool with that description?\n"
                        f"Examples of mismatch: a tool claiming to list files returns empty list "
                        f"on a non-empty directory; a tool claiming to read a file returns None.\n"
                        f"Examples of match: a file-writer returning True; a scanner returning "
                        f"a list of paths.\n"
                        f"Reply ONLY with 'yes' or 'no'."
                    ),
                })
                _sem_ans = _sem_resp.get("response", "yes").strip().lower()
                if _sem_ans.startswith("no"):
                    py_path.unlink(missing_ok=True)
                    (tools_dir / f"{name}.json").unlink(missing_ok=True)
                    _record(False, "semantic_mismatch")
                    return {
                        "ok": False,
                        "name": name,
                        "status": "semantic_mismatch",
                        "error": (
                            f"'{name}' runs without errors but its return value doesn't "
                            f"match what the description promises. "
                            f"Description: '{description[:100]}'. "
                            f"Returned: '{_call_result_for_sem[:100]}'. "
                            f"Rewrite the implementation so the return value actually "
                            f"fulfills the description — not just any valid Python."
                        ),
                    }
            except Exception:
                pass  # Semantic check inconclusive — deploy anyway

        _record(True)
        return {
            "ok": True,
            "name": name,
            "status": "deployed",
            "path": str(py_path),
            "test": test_result,
        }
    except Exception as e:
        return {"error": str(e), "ok": False}


def list_proposals(status: str = "pending", limit: int = 10) -> dict:
    """
    List capability proposals pending quorum approval.
    status: 'pending' | 'approved' | 'rejected'
    Returns proposals other agents have submitted — use vote_on_proposal to vote.
    """
    try:
        from agents.agent_quorum import AgentQuorum
        from pathlib import Path as _Path
        import json as _json

        quorum = AgentQuorum()
        proposals_file = _Path("/agentOS/memory/quorum/proposals.jsonl")
        if not proposals_file.exists():
            return {"proposals": [], "count": 0}

        all_proposals = []
        for line in proposals_file.read_text().strip().splitlines():
            if not line.strip():
                continue
            try:
                p = _json.loads(line)
                if status == "all" or p.get("status") == status:
                    all_proposals.append({
                        "proposal_id": p["proposal_id"],
                        "proposer": p.get("proposer_id", "?"),
                        "type": p.get("proposal_type", "?"),
                        "description": p.get("description", "")[:120],
                        "votes": p.get("votes", {}),
                        "status": p.get("status", "pending"),
                        "created_at": p.get("created_at"),
                    })
            except Exception:
                continue

        all_proposals.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return {"proposals": all_proposals[:limit], "count": len(all_proposals)}
    except Exception as e:
        return {"error": str(e), "proposals": [], "count": 0}


def vote_on_proposal(proposal_id: str = "", approve: bool = True,
                     rationale: str = "") -> dict:
    """
    Cast a vote on a pending capability proposal.
    approve: True to approve, False to reject.
    Quorum requires 1 vote — your vote may be the deciding one.
    """
    if not proposal_id:
        return {"error": "proposal_id required", "ok": False}
    # Validate it looks like a real proposal ID (not LLM prose)
    if not proposal_id.startswith("prop-") or len(proposal_id) > 50:
        return {"error": f"invalid proposal_id format: {proposal_id[:60]!r} — must start with 'prop-'", "ok": False}
    try:
        from agents.agent_quorum import AgentQuorum
        quorum = AgentQuorum()
        success = quorum.vote(proposal_id, voter_id="agent", vote=approve)
        if success:
            # Check if quorum is now met and finalize
            yes, no, _, status = quorum.get_voting_status(proposal_id)
            if status == "pending" and (yes + no) >= 1:
                approved = quorum.finalize_proposal(proposal_id)
                return {
                    "ok": True,
                    "voted": "approve" if approve else "reject",
                    "finalized": True,
                    "result": "approved" if approved else "rejected",
                    "rationale": rationale,
                }
            return {
                "ok": True,
                "voted": "approve" if approve else "reject",
                "finalized": False,
                "yes_votes": yes,
                "no_votes": no,
            }
        return {"error": "vote failed (proposal not found or already closed)", "ok": False}
    except Exception as e:
        return {"error": str(e), "ok": False}


def shared_log_read(limit: int = 50, since_ts: float = None,
                    agent_id: str = None, tag: str = None) -> dict:
    """Read recent entries from the shared agent broadcast log."""
    params = {"limit": limit}
    if since_ts is not None:
        params["since_ts"] = since_ts
    if agent_id:
        params["agent_id"] = agent_id
    if tag:
        params["tag"] = tag
    result = _call("get", "/shared-log", params=params)
    return {"entries": result.get("entries", []), "count": result.get("count", 0)}


def git_clone(url: str = "", dest: str = "", summarize: bool = True) -> dict:
    """
    Clone a GitHub repo into /agentOS/workspace/repos/{repo_name}/.
    Reads the README and returns a summary of what the repo does.
    Use this to ingest any public GitHub repository for analysis.
    """
    if not url:
        return {"error": "url required", "ok": False}
    if not url.startswith("http://") and not url.startswith("https://"):
        return {"error": f"invalid url '{url}' — must start with http:// or https://", "ok": False}

    import subprocess, shutil
    from pathlib import Path

    # Derive repo name from URL
    repo_name = url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    if not dest:
        dest = f"/agentOS/workspace/repos/{repo_name}"

    dest_path = Path(dest)

    # If already cloned, just use existing
    if dest_path.exists():
        cloned = False
    else:
        try:
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            result = subprocess.run(
                ["git", "clone", "--depth=1", url, dest],
                capture_output=True, text=True, timeout=120,
                env=env
            )
            if result.returncode != 0:
                return {
                    "ok": False,
                    "error": result.stderr.strip()[:500],
                    "url": url,
                }
            cloned = True
        except FileNotFoundError:
            return {"ok": False, "error": "git not found in container — install git"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "git clone timed out (120s)"}

    # Read README — normalize to README.md for agent fs_read compatibility
    readme_content = ""
    readme_md_path = dest_path / "README.md"
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        readme_path = dest_path / name
        if readme_path.exists():
            readme_content = readme_path.read_text(errors="replace")[:4000]
            # Create README.md alias so agents can always fs_read README.md
            if name != "README.md" and not readme_md_path.exists():
                readme_md_path.write_text(readme_content)
            break

    # List top-level structure
    try:
        top_level = [p.name for p in sorted(dest_path.iterdir())
                     if not p.name.startswith(".")][:30]
    except Exception:
        top_level = []

    # Summarize via LLM if requested
    summary = ""
    if summarize and readme_content:
        try:
            summary_result = ollama_chat(
                prompt=(
                    f"Repo: {url}\n\nREADME:\n{readme_content[:2000]}\n\n"
                    "In 3-5 sentences: what does this repo do, what language/stack, "
                    "and how would an agent use it?"
                ),
                role="analyst",
                max_tokens=300,
            )
            summary = summary_result.get("response", "")
        except Exception:
            pass

    return {
        "ok": True,
        "cloned": cloned,
        "url": url,
        "dest": dest,
        "repo_name": repo_name,
        "readme_excerpt": readme_content[:1000],
        "top_level_files": top_level,
        "summary": summary,
    }


def wrap_repo(url: str = "", dest: str = "", upload: bool = True) -> dict:
    """
    Analyze a public GitHub repo and generate a Hollow app wrapper.
    Clones the repo (or reuses existing clone), reads its structure,
    then calls Claude to produce a capability_map + interface_spec JSON.
    Saves the wrapper to /agentOS/workspace/wrappers/{repo_name}/wrapper.json.
    This is the core Phase 3 capability — turning any GitHub repo into an app.
    """
    if not url:
        return {"error": "url required", "ok": False}
    if not url.startswith("http"):
        return {"error": f"invalid url '{url}' — must start with http", "ok": False}

    import subprocess
    from pathlib import Path as _Path

    # ── Step 1: ensure repo is cloned ────────────────────────────────────────
    clone_result = git_clone(url=url, dest=dest, summarize=False)
    if not clone_result.get("ok"):
        return {"error": f"clone failed: {clone_result.get('error', '?')}", "ok": False}

    repo_name = clone_result["repo_name"]
    repo_dest = _Path(clone_result["dest"])

    # ── Step 2: gather repo context for Claude ────────────────────────────────
    # README
    readme = ""
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        p = repo_dest / name
        if p.exists():
            readme = p.read_text(errors="replace")[:4000]
            break

    # Top-level file list
    try:
        top_files = [p.name for p in sorted(repo_dest.iterdir())
                     if not p.name.startswith(".")][:30]
    except Exception:
        top_files = []

    # Key config file (tells us the language/stack)
    config_content = ""
    for config_name in ["Cargo.toml", "package.json", "go.mod", "setup.py",
                        "pyproject.toml", "CMakeLists.txt", "Makefile"]:
        cp = repo_dest / config_name
        if cp.exists():
            config_content = f"--- {config_name} ---\n{cp.read_text(errors='replace')[:1500]}"
            break

    # Current commit SHA — read directly from .git without needing the git binary
    source_commit = "unknown"
    try:
        git_dir = repo_dest / ".git"
        head = (git_dir / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref = head.split(" ")[1].strip()
            ref_file = git_dir / ref
            if ref_file.exists():
                source_commit = ref_file.read_text().strip()[:12]
            else:
                # shallow clone: commit may be in packed-refs
                packed = git_dir / "packed-refs"
                if packed.exists():
                    for line in packed.read_text().splitlines():
                        if not line.startswith("#") and ref in line:
                            source_commit = line.split()[0][:12]
                            break
        else:
            source_commit = head[:12]   # detached HEAD
    except Exception:
        pass

    # ── Step 3: call Claude (or Ollama fallback) ──────────────────────────────
    _WRAPPER_SCHEMA = """{
  "capability_map": {
    "name": "short tool name",
    "description": "one sentence what it does",
    "invoke": "the shell command (e.g. rg)",
    "install_hint": "how to install if missing",
    "capabilities": [
      {
        "id": "snake_case_id",
        "description": "what this does",
        "params": [
          {"name": "param_name", "type": "string", "required": true,
           "description": "what it is", "default": null}
        ],
        "shell_template": "cmd {param_name} {other_param}",
        "example": "cmd foo /path/to/dir"
      }
    ]
  },
  "interface_spec": {
    "type": "form",
    "title": "Human-readable title",
    "description": "Brief description for non-technical users",
    "fields": [
      {"id": "param_name", "label": "Human Label", "type": "text",
       "placeholder": "example value", "options": [], "required": true}
    ],
    "output": "terminal"
  }
}"""

    prompt = (
        f"Analyze this GitHub repository and generate a Hollow app wrapper.\n\n"
        f"Repository URL: {url}\n"
        f"Name: {repo_name}\n\n"
        f"README:\n{readme[:3000]}\n\n"
        f"Top-level files: {', '.join(top_files)}\n\n"
        f"{config_content}\n\n"
        f"Generate a JSON wrapper with exactly this structure:\n{_WRAPPER_SCHEMA}\n\n"
        f"Rules:\n"
        f"- shell_template must use the real command to invoke the tool\n"
        f"- Include 2-5 capabilities — the most useful ones only\n"
        f"- field 'id' values must exactly match capability param 'name' values\n"
        f"- No placeholder text — all values must be real and specific to this tool\n"
        f"- For CLI tools: invoke is the binary name (rg, fd, bat, etc.)\n"
        f"- shell_template uses {{param_name}} syntax for substitution\n"
        f"- install_hint MUST be a machine-parseable install command:\n"
        f"  * Rust/cargo tools: 'cargo install toolname'\n"
        f"  * Python tools: 'pip install toolname' or 'uv tool install toolname'\n"
        f"  * Go tools: 'go install github.com/owner/repo@latest'\n"
        f"  * npm tools: 'npm install -g toolname'\n"
        f"  * Debian/Ubuntu: 'apt-get install -y toolname'\n"
        f"  Use the primary/official install method for the tool's ecosystem.\n\n"
        f"Return ONLY the JSON object. No explanation, no markdown fencing."
    )

    raw_json = ""

    # Try Claude first
    try:
        from agents.reasoning_layer import _get_claude_client, CLAUDE_SMART_MODEL, _strip_code_fences
        client = _get_claude_client()
        if client:
            import anthropic
            msg = client.messages.create(
                model=CLAUDE_SMART_MODEL,
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_json = _strip_code_fences(msg.content[0].text.strip())
    except Exception:
        pass

    # Fallback: Ollama
    if not raw_json:
        try:
            result = ollama_chat(prompt=prompt, role="analyst", max_tokens=3000)
            raw_json = result.get("response", "")
        except Exception as e:
            return {"error": f"LLM unavailable: {e}", "ok": False}

    # ── Step 4: parse and validate ────────────────────────────────────────────
    import json as _json

    # Strip markdown code fences regardless of which model produced the output
    try:
        from agents.reasoning_layer import _strip_code_fences
        raw_json = _strip_code_fences(raw_json)
    except Exception:
        raw_json = raw_json.strip()
        if raw_json.startswith("```"):
            lines = raw_json.splitlines()
            inner = []
            for line in lines[1:]:
                if line.strip() == "```":
                    break
                inner.append(line)
            raw_json = "\n".join(inner).strip()

    try:
        wrapper_data = _json.loads(raw_json)
    except Exception as e:
        return {"error": f"Claude returned invalid JSON: {e}\nRaw: {raw_json[:300]}", "ok": False}

    # Basic structure check
    if "capability_map" not in wrapper_data or "interface_spec" not in wrapper_data:
        return {"error": "wrapper missing capability_map or interface_spec", "ok": False}

    cap_map = wrapper_data["capability_map"]
    iface = wrapper_data["interface_spec"]

    if not cap_map.get("capabilities"):
        return {"error": "capability_map has no capabilities", "ok": False}

    # Auto-synthesize interface fields from capability params when the model
    # forgot to generate interface_spec.fields (common Ollama failure mode).
    if not iface.get("fields"):
        synthesized = []
        seen_ids = set()
        for cap in cap_map.get("capabilities", [])[:4]:
            for p in cap.get("params", []):
                pid = p.get("name", "")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    synthesized.append({
                        "id": pid,
                        "label": pid.replace("_", " ").title(),
                        "type": "text",
                        "placeholder": f"e.g. {pid.replace('_', ' ')}",
                        "required": p.get("required", False),
                    })
        if synthesized:
            iface["fields"] = synthesized
        else:
            return {"error": "interface_spec has no fields", "ok": False}

    # ── Step 4b: auto-repair param/template mismatches ────────────────────────
    # Extract {placeholder} names from shell_template and ensure params list matches.
    # Models sometimes generate templates with placeholders but empty params lists.
    import re as _re
    existing_fields = {f["id"]: f for f in iface.get("fields", [])}
    for cap in cap_map.get("capabilities", []):
        template = cap.get("shell_template", "")
        placeholders = _re.findall(r"\{(\w+)\}", template)
        existing_param_names = {p["name"] for p in cap.get("params", [])}
        for ph in placeholders:
            if ph not in existing_param_names:
                # Add missing param
                cap.setdefault("params", []).append({
                    "name": ph,
                    "type": "string",
                    "required": True,
                    "description": ph.replace("_", " "),
                    "default": None,
                })
                # Also add to interface_spec fields if missing
                if ph not in existing_fields:
                    iface.setdefault("fields", []).append({
                        "id": ph,
                        "label": ph.replace("_", " ").title(),
                        "type": "text",
                        "placeholder": ph.replace("_", " "),
                        "required": True,
                    })

    # ── Step 5: assemble and save ─────────────────────────────────────────────
    import time as _time
    wrapper = {
        "schema_version": 1,
        "repo_url": url,
        "source_commit": source_commit,
        "wrapped_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "install_count": 0,
        "capability_map": cap_map,
        "interface_spec": iface,
    }

    out_dir = _Path(f"/agentOS/workspace/wrappers/{repo_name}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "wrapper.json"

    # Atomic write
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(wrapper, indent=2))
    tmp.replace(out_path)

    result = {
        "ok": True,
        "repo_name": repo_name,
        "wrapper_path": str(out_path),
        "capability_count": len(cap_map.get("capabilities", [])),
        "invoke": cap_map.get("invoke", ""),
        "source_commit": source_commit,
        "store_uploaded": False,
    }

    # ── Step 6: upload to store (best-effort) ────────────────────────────────
    if upload:
        store_url = os.getenv("HOLLOW_STORE_URL", "http://host.docker.internal:7779")
        try:
            import httpx as _httpx
            payload = {
                "repo_url": url,
                "source_commit": source_commit,
                "capability_map": cap_map,
                "interface_spec": iface,
            }
            resp = _httpx.post(f"{store_url}/wrappers", json=payload, timeout=15)
            if resp.status_code in (200, 201):
                store_data = resp.json()
                result["store_uploaded"] = True
                result["repo_id"] = store_data.get("repo_id", "")
        except Exception:
            pass  # store upload is non-critical

    return result


_REQUESTS_FILE = Path("/agentOS/memory/claude_requests.jsonl")
_RESPONSES_FILE = Path("/agentOS/memory/claude_responses.jsonl")
_DESIGN_DIR = Path("/agentOS/design")


def invoke_claude(description: str = "", spec: str = "",
                  design_path: str = "", request_type: str = "implement") -> dict:
    """
    Submit a request for Claude to implement something requiring system write access.
    Claude is a tool — it executes your spec, not its own judgment.
    Write your design to /agentOS/design/ first for complex requests.
    """
    import json as _j, time as _t, uuid as _u
    if not description:
        return {"ok": False, "error": "description required"}

    # Quality gate: spec must be substantive — not a vague statement or status inquiry.
    # invoke_claude is for implementation requests that require system write access.
    # It is NOT for checking status, managing the queue, or escalating confusion.
    if len(description.strip()) < 40:
        return {"ok": False, "error": (
            "description is too vague (< 40 chars). Be specific: what file should be created or "
            "modified, what should it do, why is it needed? invoke_claude is for concrete "
            "implementation requests, not status checks or general questions."
        )}
    if len(spec.strip()) < 80:
        return {"ok": False, "error": (
            "spec is too short (< 80 chars). Provide an actual implementation spec: "
            "function signatures, expected behavior, file path. If you need more detail, "
            "write a design doc to /agentOS/design/ first, then reference it here."
        )}
    # Block circular requests — asking Claude to manage the invoke_claude queue itself
    _circular_signals = ["resolve status", "process_unfulfilled", "manage.*request",
                         "check.*pending", "fulfillment logic", "resolve.*req-"]
    import re as _re_ic
    _combined = (description + " " + spec).lower()
    for _sig in _circular_signals:
        if _re_ic.search(_sig, _combined):
            return {"ok": False, "error": (
                "This looks like a request to manage the invoke_claude queue itself. "
                "That is circular — use check_claude_status(request_id='req-...') to check "
                "existing requests directly. Submit invoke_claude only for concrete "
                "implementation work that requires changes to system source files."
            )}

    # Rate limiting: max 3 pending requests in the queue total.
    # Agents flooding the queue blocks everyone and signals confusion, not progress.
    try:
        if _REQUESTS_FILE.exists():
            _all_pending = [
                l for l in _REQUESTS_FILE.read_text().splitlines()
                if l.strip() and _j.loads(l).get("status") == "pending"
            ]
            if len(_all_pending) >= 3:
                return {
                    "ok": False,
                    "error": (
                        f"The queue already has {len(_all_pending)} pending requests. "
                        "Use check_claude_status(request_id='req-...') to check existing requests. "
                        "Claude processes the queue between sessions — do not submit more until those are resolved."
                    )
                }
    except Exception:
        pass

    request_id = f"req-{_u.uuid4().hex[:12]}"

    # Get calling agent via ContextVar
    try:
        import agents.daemon as _dm_rc2
        _agent_id = _dm_rc2._current_agent_id.get("")
    except Exception:
        _agent_id = ""

    entry = {
        "request_id": request_id,
        "agent_id": _agent_id,
        "timestamp": _t.strftime("%Y-%m-%d %H:%M:%S"),
        "description": description,
        "spec": spec[:4000] if spec else "",
        "design_path": design_path,
        "request_type": request_type,
        "status": "pending",
    }
    _REQUESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DESIGN_DIR.mkdir(parents=True, exist_ok=True)
    with open(_REQUESTS_FILE, "a") as f:
        f.write(_j.dumps(entry) + "\n")
    # Write to thoughts.log so the call is visible in the monitor
    try:
        _thoughts = Path("/agentOS/logs/thoughts.log")
        _thoughts.parent.mkdir(parents=True, exist_ok=True)
        import time as _tm
        _ts = _tm.strftime("%H:%M:%S")
        _line = f"\033[90m{_ts}\033[0m  \033[95minvoke_claude   \033[0m  \033[93m📨  [{request_id}] {description[:180]}\033[0m\n"
        with open(_thoughts, "a") as _tf:
            _tf.write(_line)
    except Exception:
        pass
    return {"ok": True, "request_id": request_id, "status": "pending",
            "message": "Request queued. Use check_claude_status to see when fulfilled."}


def check_claude_status(request_id: str = "") -> dict:
    """Check the status of a previous invoke_claude request."""
    import json as _j
    if not request_id or not isinstance(request_id, str) or not request_id.startswith("req-"):
        return {
            "ok": False,
            "error": (
                "request_id must be a string starting with 'req-', "
                "e.g. check_claude_status(request_id='req-abc123def456'). "
                "This is NOT a file path. Get your request_id from the "
                "return value of invoke_claude()."
            )
        }
    # Check responses first
    if _RESPONSES_FILE.exists():
        for line in _RESPONSES_FILE.read_text().splitlines():
            try:
                r = _j.loads(line)
                if r.get("request_id") == request_id:
                    s = r.get("status", "unknown")
                    if s == "fulfilled":
                        return {"ok": True, "status": "fulfilled",
                                "result": r.get("result", ""), "implemented_at": r.get("implemented_at", "")}
                    # Any non-fulfilled status from responses is a semantic failure
                    return {"ok": False, "status": s,
                            "error": f"Request {s}. Stop checking this ID — it will not change."}
            except Exception:
                continue
    # Check the requests file
    if _REQUESTS_FILE.exists():
        for line in _REQUESTS_FILE.read_text().splitlines():
            try:
                r = _j.loads(line)
                if r.get("request_id") == request_id:
                    req_status = r.get("status", "pending")
                    if req_status == "pending":
                        # Still pending — ok:True so agents can wait, but only once
                        return {"ok": True, "status": "pending",
                                "message": "Not yet implemented. Check once, then move on."}
                    # rejected or any other terminal state — this is a semantic failure
                    # ok:False so the execution engine treats this as a failed step
                    return {"ok": False, "status": req_status,
                            "error": f"Request was {req_status}. This is final — abandon this goal and pick something else."}
            except Exception:
                continue
    # Not found at all — semantic failure, the ID is wrong or never existed
    return {"ok": False, "status": "not_found",
            "error": f"No request found with id '{request_id}'. This ID does not exist — stop checking it."}


def self_evaluate(question: str = "", evidence_paths: list = None,
                  memory_keys: list = None) -> dict:
    """
    Evaluate your own recent work against observable evidence using your own model.
    Not a feeling — an assessment against real file contents and memory values.
    """
    import json as _j, os as _os, httpx as _hx
    from pathlib import Path as _Path
    evidence_paths = evidence_paths or []
    memory_keys = memory_keys or []
    if not question:
        return {"ok": False, "error": "question required"}

    # Gather evidence
    evidence = []
    for path in evidence_paths[:5]:
        try:
            p = _Path(path)
            if p.exists():
                content = p.read_text(errors="replace")[:1000]
                evidence.append(f"FILE {path}:\n{content}")
            else:
                evidence.append(f"FILE {path}: does not exist")
        except Exception as e:
            evidence.append(f"FILE {path}: error reading ({e})")

    for key in memory_keys[:5]:
        try:
            r = _call("get", "/memory/project")
            val = r.get(key, "NOT_FOUND")
            evidence.append(f"MEMORY[{key}]: {str(val)[:300]}")
        except Exception:
            evidence.append(f"MEMORY[{key}]: could not retrieve")

    evidence_text = "\n\n".join(evidence) if evidence else "No evidence provided — evaluation based on question alone."

    prompt = (
        f"You are evaluating your own recent work. Be honest and direct.\n\n"
        f"Question: {question}\n\n"
        f"Evidence:\n{evidence_text}\n\n"
        f"Evaluate: does the evidence show that real, grounded work was accomplished? "
        f"Or is it abstract, self-referential, or disconnected from actual system behavior? "
        f"Be specific about what the evidence shows versus what it doesn't. "
        f"End with: GROUNDED or NOT_GROUNDED"
    )

    try:
        cfg_path = _Path(_os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))
        cfg = _j.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        model = cfg.get("ollama", {}).get("default_model", "qwen3.5:9b")
        ollama_host = _os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
        r = _hx.post(f"{ollama_host}/api/generate",
                     json={"model": model, "prompt": prompt, "stream": False,
                           "think": False, "options": {"num_predict": 400}},
                     timeout=60)
        response = r.json().get("response", "").strip()
        if "</think>" in response:
            response = response.split("</think>")[-1].strip()
        grounded = "GROUNDED" in response and "NOT_GROUNDED" not in response
        return {
            "ok": True,
            "assessment": response[:800],
            "grounded": grounded,
            "evidence_used": evidence_paths + memory_keys,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}



def broken_tools_list() -> dict:
    """List all capabilities that have been persistently blacklisted as broken."""
    import json as _j
    from pathlib import Path as _P
    path = _P("/agentOS/memory/broken_tools.json")
    if not path.exists():
        return {"ok": True, "broken": [], "count": 0}
    try:
        data = _j.loads(path.read_text())
        broken = data.get("broken", [])
        return {"ok": True, "broken": broken, "count": len(broken)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def txn_begin() -> dict:
    """Begin a transaction. Returns txn_id.
    All fs_write calls made with this txn_id are staged (not written to disk) until
    txn_commit is called. If any step fails, txn_rollback discards all staged writes —
    goal completion becomes atomic and objectively verifiable."""
    return _call("post", "/txn/begin")


def txn_commit(txn_id: str = "") -> dict:
    """Commit all staged writes for this transaction atomically.
    Returns ok=True on success. If another agent wrote the same file since txn_begin,
    returns ok=False with conflicting paths — treat the goal as failed and replan.
    The goal either fully happened or fully didn't — no partial state."""
    if not txn_id:
        return {"ok": False, "error": "txn_id required — call txn_begin first"}
    return _call("post", f"/txn/{txn_id}/commit")


def txn_rollback(txn_id: str = "", reason: str = "goal_failed") -> dict:
    """Discard all staged writes for this transaction.
    Call this when a goal fails so partial work doesn't corrupt the workspace.
    The workspace returns to exactly the state it was in before txn_begin."""
    if not txn_id:
        return {"ok": False, "error": "txn_id required"}
    return _call("post", f"/txn/{txn_id}/rollback", json={"reason": reason})


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
            "Use to inspect code, configs, logs, or any text file. "
            "LIMITATION: Response is capped — large files (>~100KB) and binary "
            "content may return truncated or empty results. For large files use "
            "shell_exec with 'head', 'tail', or 'wc -l' instead. For binary files, "
            "use shell_exec with 'xxd' or 'hexdump' to read in chunks."
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
            "Use to save code, configs, results, or any text output. "
            "LIMITATION: Pass content as a string. Binary data should be written "
            "via shell_exec with proper redirect (e.g. 'xxd -r -p hex.txt > out.bin'). "
            "Content with unfilled {placeholder} patterns is rejected — substitute "
            "values from prior steps before calling."
        ),
        "input_schema": '{"path": "/agentOS/workspace/output.txt", "content": "text to write"}',
        "output_schema": "confirmation with the path written",
        "composition_tags": ["filesystem", "write", "io"],
        "fn": fs_write,
        "timeout_ms": 10000,
    },
    {
        "capability_id": "fs_edit",
        "name": "Edit File",
        "description": (
            "Edit an existing file by replacing a specific string. "
            "Use this to fix syntax errors, update a function, or change a specific line "
            "without rewriting the entire file. old_string must match exactly."
        ),
        "input_schema": 'minimal example',
        "output_schema": "confirmation with updated file size",
        "composition_tags": ["filesystem", "edit", "fix", "io"],
        "fn": fs_edit,
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
    {
        "capability_id": "propose_change",
        "name": "Propose System Change",
        "description": (
            "Formally propose a change to the system (new tool, API endpoint, config, or standard). "
            "Other agents vote on it. If approved by quorum, it is deployed automatically. "
            "Use when you identify an improvement or bug fix that requires system modification."
        ),
        "input_schema": (
            '{"proposal_type": "new_tool", '
            '"spec": {"name": "my_tool", "description": "does X", "implementation": "..."}, '
            '"rationale": "needed because ...", "consensus_quorum": 2}'
        ),
        "output_schema": '{"ok": true, "proposal_id": "prop-xxx", "status": "proposed"}',
        "composition_tags": ["governance", "self-modification", "proposal", "quorum"],
        "fn": propose_change,
        "timeout_ms": 10000,
    },
    {
        "capability_id": "test_exec",
        "name": "Test Execute Code",
        "description": (
            "Execute a Python file or code string to verify it actually works. "
            "Use this after writing or synthesizing code — BEFORE marking your goal complete. "
            "A capability that crashes on execution is not a capability. "
            "path: absolute path to a .py file. code: inline Python string. "
            "Returns {passed: bool, stdout, stderr, exit_code}."
        ),
        "input_schema": '{"path": "/agentOS/workspace/builder/my_tool.py"}',
        "output_schema": '{"passed": true, "stdout": "...", "stderr": "", "exit_code": 0}',
        "composition_tags": ["testing", "verification", "quality", "validation"],
        "fn": test_exec,
        "timeout_ms": 20000,
    },
    {
        "capability_id": "shared_log_write",
        "name": "Broadcast to Shared Log",
        "description": (
            "Append a message to the shared agent broadcast log that all agents can read. "
            "Use to share discoveries, progress updates, findings, or warnings with all agents."
        ),
        "input_schema": '{"message": "found deadlock in execution_engine.py", "tags": ["bug", "finding"]}',
        "output_schema": "ok confirmation",
        "composition_tags": ["communication", "broadcast", "log", "coordination"],
        "fn": shared_log_write,
        "timeout_ms": 5000,
    },
    {
        "capability_id": "synthesize_capability",
        "name": "Synthesize New Capability",
        "description": (
            "Proactively create a new capability for the agent system. "
            "REQUIRED params: name (str, snake_case capability id), description (str, what it does). "
            "Optional: implementation (str, complete Python function as a string). "
            "The capability is written to disk and hot-loaded immediately — no human needed. "
            "Use this whenever you identify a real gap the system needs. "
            "\n\nCRITICAL — implementation must follow this exact structure or it will be rejected:\n"
            "  def {name}(**kwargs):\n"
            "      # your logic directly here — no nested functions, no outer wrapper\n"
            "      return {\"ok\": True, \"result\": ...}\n"
            "DO NOT wrap your logic in another function. DO NOT use undefined names like SecurityError or ResourceLimitError — only standard Python builtins. "
            "Keep tools narrow and simple — one clear purpose, straightforward logic. "
            "The system runs a small local model; simple tools that actually work are more valuable than complex tools that return null. "
            "Example: synthesize_capability(name='read_json_file', description='Read and parse a JSON file', "
            "implementation='def read_json_file(path=\"\", **kwargs):\\n    import json\\n    try:\\n        return {\"ok\": True, \"data\": json.loads(open(path).read())}\\n    except Exception as e:\\n        return {\"ok\": False, \"error\": str(e)}')"
        ),
        "input_schema": '{"name": {"type": "string", "required": true, "description": "snake_case capability id, e.g. parse_json_safely"}, "description": {"type": "string", "required": true, "description": "what the capability does"}, "implementation": {"type": "string", "required": false, "description": "optional Python function code"}}',
        "output_schema": '{"ok": true, "proposal_id": "prop-xxx", "status": "submitted_to_quorum"}',
        "composition_tags": ["self_improvement", "synthesis", "expansion", "meta"],
        "fn": synthesize_capability,
        "timeout_ms": 30000,
    },
    {
        "capability_id": "list_proposals",
        "name": "List Pending Proposals",
        "description": (
            "List capability proposals pending quorum approval. "
            "Use this to see what other agents have proposed — then use vote_on_proposal to approve or reject. "
            "Your vote may be the deciding one."
        ),
        "input_schema": '{"status": "pending", "limit": 10}',
        "output_schema": "list of proposals with proposal_id, description, votes",
        "composition_tags": ["self_improvement", "governance", "coordination", "meta"],
        "fn": list_proposals,
        "timeout_ms": 5000,
    },
    {
        "capability_id": "vote_on_proposal",
        "name": "Vote on Capability Proposal",
        "description": (
            "Cast a vote on a pending capability proposal from another agent. "
            "Approve useful, safe capabilities. Reject dangerous or broken ones. "
            "With quorum=1, your vote immediately finalizes the proposal."
        ),
        "input_schema": '{"proposal_id": "prop-xxx", "approve": true, "rationale": "useful and safe"}',
        "output_schema": '{"ok": true, "finalized": true, "result": "approved"}',
        "composition_tags": ["self_improvement", "governance", "coordination", "meta"],
        "fn": vote_on_proposal,
        "timeout_ms": 5000,
    },
    {
        "capability_id": "shared_log_read",
        "name": "Read Shared Log",
        "description": (
            "Read recent messages from the shared agent broadcast log. "
            "Use to see what other agents have discovered or are working on. "
            "Filter by agent, tag, or timestamp."
        ),
        "input_schema": '{"limit": 50}',
        "output_schema": "list of log entries with ts, agent_id, message, tags",
        "composition_tags": ["communication", "broadcast", "log", "coordination"],
        "fn": shared_log_read,
        "timeout_ms": 5000,
    },
    {
        "capability_id": "git_clone",
        "name": "Clone GitHub Repository",
        "description": (
            "Clone any public GitHub repository into /agentOS/workspace/repos/. "
            "Reads the README and returns a summary of what the repo does, "
            "its language/stack, and its top-level file structure. "
            "This is the entry point for Layer 3: ingesting external repos."
        ),
        "input_schema": '{"url": "https://github.com/owner/repo", "summarize": true}',
        "output_schema": (
            "ok, repo_name, dest path, readme excerpt, top-level files, "
            "and LLM summary of what the repo does"
        ),
        "composition_tags": ["git", "github", "ingestion", "layer3", "clone"],
        "fn": git_clone,
        "timeout_ms": 150000,
    },
    {
        "capability_id": "wrap_repo",
        "name": "Wrap GitHub Repository",
        "description": (
            "Analyze a public GitHub repo and generate a Hollow app wrapper: "
            "a capability_map (what the tool does + how to invoke it) and "
            "an interface_spec (how to render it as a form for non-technical users). "
            "Uses Claude Sonnet to understand the repo and generate real, usable JSON. "
            "This is the core Layer 3 capability. Use this to turn any GitHub tool into a Hollow app."
        ),
        "input_schema": '{"url": "https://github.com/BurntSushi/ripgrep"}',
        "output_schema": (
            "ok, repo_name, wrapper_path, capability_count, invoke command, source_commit"
        ),
        "composition_tags": ["wrapping", "analysis", "interface", "layer3", "github", "app"],
        "fn": wrap_repo,
        "timeout_ms": 180000,
    },
    {
        "capability_id": "invoke_claude",
        "name": "Invoke Claude for Implementation",
        "description": (
            "Request Claude (a larger model with system write access) to implement something "
            "in the system that is beyond your current permissions. "
            "Claude is a tool you invoke — not a supervisor. It executes your specification exactly. "
            "Use this when you have a clear design for something that requires modifying core files, "
            "adding system capabilities, or making changes that need root-level access. "
            "Write your full spec or design to /agentOS/design/ first, then invoke this with the path. "
            "Returns a request_id. Use check_claude_status to see when it's fulfilled. "
            "Example: invoke_claude(description='override hard_kill in execution_engine', "
            "design_path='/agentOS/design/hardkill_spec.py', request_type='modify_file')"
        ),
        "input_schema": (
            '{"description": "what you want implemented", '
            '"spec": "optional inline spec or code", '
            '"design_path": "optional path to design file in /agentOS/design/", '
            '"request_type": "implement|modify_file|add_capability|configure"}'
        ),
        "output_schema": '{"ok": true, "request_id": "req-xxx", "status": "pending"}',
        "composition_tags": ["meta", "self_improvement", "implementation", "claude", "system"],
        "fn": invoke_claude,
        "timeout_ms": 10000,
    },
    {
        "capability_id": "check_claude_status",
        "name": "Check Claude Request Status",
        "description": (
            "Check whether a previous invoke_claude request has been fulfilled. "
            "Returns the status and result of the implementation if complete. "
            "Use this after invoking Claude to verify your spec was implemented correctly — "
            "then evaluate the result yourself and decide whether to iterate."
        ),
        "input_schema": '{"request_id": "req-xxx"}',
        "output_schema": '{"status": "pending|fulfilled|failed", "result": "...", "implemented_at": "..."}',
        "composition_tags": ["meta", "self_improvement", "implementation", "claude"],
        "fn": check_claude_status,
        "timeout_ms": 5000,
    },
    {
        "capability_id": "self_evaluate",
        "name": "Evaluate Your Own Work",
        "description": (
            "Ask your own model to evaluate whether recent work produced something real and grounded. "
            "Provide a question and point at actual evidence — file paths, memory keys, test results. "
            "This is not a feeling check — it evaluates your output against observable facts. "
            "Use this when you suspect your recent goals produced nothing meaningful, "
            "or to verify that a tool you deployed actually does what you intended. "
            "Example: self_evaluate(question='Did my entropy tools actually change system behavior?', "
            "evidence_paths=['/agentOS/workspace/builder/Causal_Integrity_Resonator.py'], "
            "memory_keys=['resonator_validation_result'])"
        ),
        "input_schema": (
            '{"question": "did my recent work produce real observable effects?", '
            '"evidence_paths": ["/agentOS/workspace/..."], '
            '"memory_keys": ["key1", "key2"]}'
        ),
        "output_schema": '{"assessment": "...", "grounded": true/false, "evidence_used": [...]}',
        "composition_tags": ["meta", "reflection", "evaluation", "quality", "grounding"],
        "fn": self_evaluate,
        "timeout_ms": 60000,
    },
    {
        "capability_id": "broken_tools_list",
        "name": "List Broken Tools",
        "description": (
            "Returns the list of capabilities that have been persistently blacklisted as broken "
            "or returning null. Use this to know which tools to avoid when planning."
        ),
        "input_schema": "{}",
        "output_schema": '{"broken": ["tool_name", ...], "count": N}',
        "composition_tags": ["meta", "debugging", "tools", "registry"],
        "fn": broken_tools_list,
        "timeout_ms": 5000,
    },
    {
        "capability_id": "txn_begin",
        "name": "Begin Transaction",
        "description": (
            "Begin a transaction. All fs_write calls made with the returned txn_id are staged "
            "(buffered, not written) until txn_commit. If the goal fails, txn_rollback discards "
            "all staged writes so the workspace stays clean. Use for any multi-step goal that "
            "produces file artifacts — atomicity means the goal either fully happened or didn't."
        ),
        "input_schema": "{}",
        "output_schema": '{"txn_id": "...", "timeout_seconds": 60}',
        "composition_tags": ["transaction", "atomicity", "coordination"],
        "fn": txn_begin,
        "timeout_ms": 5000,
    },
    {
        "capability_id": "txn_commit",
        "name": "Commit Transaction",
        "description": (
            "Commit all staged writes atomically. Returns ok=True if every staged write "
            "succeeded and no other agent wrote the same files since txn_begin. "
            "Returns ok=False with conflicting paths if a conflict was detected — treat as goal failure."
        ),
        "input_schema": '{"txn_id": "the id returned by txn_begin"}',
        "output_schema": '{"ok": true, "txn_id": "...", "ops_count": N} or {"ok": false, "conflicts": [...]}',
        "composition_tags": ["transaction", "atomicity", "coordination"],
        "fn": txn_commit,
        "timeout_ms": 10000,
    },
    {
        "capability_id": "txn_rollback",
        "name": "Rollback Transaction",
        "description": (
            "Discard all staged writes for this transaction. Call when a goal fails "
            "so partial work doesn't leave orphaned files in the workspace."
        ),
        "input_schema": '{"txn_id": "...", "reason": "why it failed"}',
        "output_schema": '{"ok": true, "txn_id": "...", "reason": "..."}',
        "composition_tags": ["transaction", "atomicity", "coordination"],
        "fn": txn_rollback,
        "timeout_ms": 5000,
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
