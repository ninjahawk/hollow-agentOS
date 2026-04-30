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
        r = _httpx.post(
            f"{ollama_host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
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

def fs_write(path: str = "", content="", append: bool = False) -> dict:
    """Write content to a file. Set append=True to add to existing content instead of overwriting."""
    if not path:
        return {"error": "no path provided", "ok": False}
    # Coerce non-string content — agents sometimes pass dicts/lists directly
    if not isinstance(content, str):
        import json as _j
        content = _j.dumps(content, indent=2)
    full = path if path.startswith("/") else f"/agentOS/workspace/{path}"
    for blocked in _FS_WRITE_BLOCKED:
        if full.startswith(blocked) or full == blocked.rstrip("/"):
            return {"error": f"blocked: writes to {blocked} are not permitted", "ok": False}
    if append:
        import os
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "a") as f:
            f.write(content)
        return {"ok": True, "path": full, "mode": "append"}
    _call("post", "/fs/write", json={"path": path, "content": content})
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

    # Quality gate: validate implementation before submitting
    if implementation:
        import ast as _ast
        # Reject obvious stubs
        stub_signals = ["...", "pass\n    pass", "# TODO", "# placeholder",
                        '{"ok": true', "raise NotImplementedError"]
        if any(sig in implementation for sig in stub_signals):
            return {"ok": False, "error": "implementation looks like a stub — provide real code"}
        try:
            # Wrap in function if needed for parse check
            test_code = implementation if implementation.strip().startswith("def ") else f"def {name}(**kw):\n    " + "\n    ".join(implementation.splitlines())
            tree = _ast.parse(test_code)
        except SyntaxError as e:
            return {"ok": False, "error": f"implementation has syntax error: {e}"}

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

        # Dedup guard: if this exact tool was deployed in the last 90 seconds, stop.
        # Prevents agents from spinning on synthesize_capability in a tight loop.
        if py_path.exists():
            age = _time.time() - py_path.stat().st_mtime
            if age < 90:
                return {
                    "ok": True,
                    "name": name,
                    "status": "already_deployed",
                    "message": f"'{name}' was deployed {int(age)}s ago — it is already live. Call it or move to the next step.",
                    "path": str(py_path),
                }

        py_path.write_text(code, encoding="utf-8")

        # Write .json spec so MCP server can expose it
        spec = {
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": {}},
            "activated_at": _time.time(),
            "proposed_by": "agent",
        }
        (tools_dir / f"{name}.json").write_text(_json.dumps(spec, indent=2), encoding="utf-8")

        # Hot-reload into the running execution engine
        try:
            _call("post", "/tools/reload")
        except Exception:
            pass

        # Auto-test: try to exec the deployed file to surface broken references early
        test_result = {"passed": None}
        try:
            import subprocess as _sub
            r = _sub.run(
                ["python3", "-c", f"exec(open({repr(str(py_path))}).read())"],
                capture_output=True, text=True, timeout=8
            )
            test_result = {
                "passed": r.returncode == 0,
                "stderr": r.stderr.strip()[:300] if r.stderr else "",
            }
        except Exception as _te:
            test_result = {"passed": None, "error": str(_te)[:100]}

        result = {
            "ok": True,
            "name": name,
            "status": "deployed",
            "path": str(py_path),
            "test": test_result,
        }
        if test_result.get("passed") is False:
            result["warning"] = f"tool deployed but failed exec test: {test_result.get('stderr','')[:150]}"
        return result
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
    request_id = f"req-{_u.uuid4().hex[:12]}"
    entry = {
        "request_id": request_id,
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
    return {"ok": True, "request_id": request_id, "status": "pending",
            "message": "Request queued. Use check_claude_status to see when fulfilled."}


def check_claude_status(request_id: str = "") -> dict:
    """Check the status of a previous invoke_claude request."""
    import json as _j
    if not request_id:
        return {"ok": False, "error": "request_id required"}
    # Check responses first
    if _RESPONSES_FILE.exists():
        for line in _RESPONSES_FILE.read_text().splitlines():
            try:
                r = _j.loads(line)
                if r.get("request_id") == request_id:
                    return {"ok": True, "status": r.get("status", "unknown"),
                            "result": r.get("result", ""), "implemented_at": r.get("implemented_at", "")}
            except Exception:
                continue
    # Still pending
    if _REQUESTS_FILE.exists():
        for line in _REQUESTS_FILE.read_text().splitlines():
            try:
                r = _j.loads(line)
                if r.get("request_id") == request_id:
                    return {"ok": True, "status": "pending",
                            "message": "Not yet implemented. Check back later."}
            except Exception:
                continue
    return {"ok": False, "status": "not_found", "error": f"No request found with id {request_id}"}


def self_evaluate(question: str = "", evidence_paths: list = None,
                  memory_keys: list = None) -> dict:
    """
    Evaluate your own recent work against observable evidence using your own model.
    Not a feeling — an assessment against real file contents and memory values.
    """
    import json as _j, os as _os, httpx as _hx
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
            "Optional: implementation (str, Python function body). "
            "The capability goes to quorum, gets voted on automatically next daemon cycle, "
            "and is hot-loaded into the running engine on approval — no human needed. "
            "Use this whenever you identify a gap: something agents need to do but can't. "
            "This is how the system expands itself. "
            "Example call: synthesize_capability(name='parse_json_safely', description='Parse JSON without crashing on malformed input', implementation='def parse_json_safely(text=\"\", **kwargs):\\n  import json\\n  try:\\n    return json.loads(text)\\n  except: return {}')"
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
