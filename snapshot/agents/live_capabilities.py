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
    """Run a shell command and return structured output. When running Python scripts or python3 commands, your workspace directory is automatically added to sys.path so you can import modules you have written there."""
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

    # For Python commands, inject workspace onto PYTHONPATH so imports resolve
    _cmd = command
    _ws = cwd if cwd else "/agentOS/workspace"
    if cmd_lower.startswith("python3") or cmd_lower.startswith("python ") or cmd_lower.startswith("python\t"):
        _cmd = f'PYTHONPATH={_ws}:/agentOS/workspace:$PYTHONPATH {command}'

    result = _call("post", "/shell", json={"command": _cmd, "cwd": cwd,
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


_FS_WRITE_BLOCKED = [
    "/agentOS/agents/",
    "/agentOS/api/",
    "/agentOS/entrypoint.sh",
    "/agentOS/config.json",
]

def fs_write(path: str = "", content: str = "", append: bool = False) -> dict:
    """Write content to a file. Creates parent directories automatically. For .py files, automatically syntax-checks the result and tells you about errors so you can fix them — a file with syntax errors cannot be imported or run."""
    if not path:
        return {"error": "no path provided", "ok": False}
    full = path if path.startswith("/") else f"/agentOS/workspace/{path}"
    for blocked in _FS_WRITE_BLOCKED:
        if full.startswith(blocked) or full == blocked.rstrip("/"):
            return {"error": f"blocked: writes to {blocked} are not permitted", "ok": False}
    if append:
        import os
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "a") as f:
            f.write(content)
        result = {"ok": True, "path": full, "mode": "append"}
    else:
        _call("post", "/fs/write", json={"path": path, "content": content})
        result = {"ok": True, "path": full}
    # Post-write syntax check for Python files — agents get immediate feedback
    if full.endswith('.py'):
        import subprocess as _sp, sys as _sys
        try:
            chk = _sp.run([_sys.executable, '-m', 'py_compile', full],
                          capture_output=True, text=True, timeout=5)
            result["syntax_ok"] = chk.returncode == 0
            if chk.returncode != 0:
                result["syntax_error"] = chk.stderr.strip()
                result["warning"] = (
                    "file written but has syntax errors — use fs_read to read it back, "
                    "fix the errors, then fs_write again before trying to import or run it"
                )
        except Exception:
            pass  # syntax check is best-effort
    return result



def fs_edit(path: str = '', old_string: str = '', new_string: str = '') -> dict:
    """Edit a file by replacing old_string with new_string. Fails if old_string not found. Use this to fix a specific section of an existing file without rewriting it."""
    if not path or not old_string:
        return {"error": "path and old_string are required", "ok": False}
    full = path if path.startswith("/") else f"/agentOS/workspace/{path}"
    for blocked in _FS_WRITE_BLOCKED:
        if full.startswith(blocked) or full == blocked.rstrip("/"):
            return {"error": f"blocked: edits to {blocked} are not permitted", "ok": False}
    result = _call("post", "/fs/edit", json={"path": path, "old_string": old_string, "new_string": new_string})
    if full.endswith('.py') and result.get("ok"):
        import subprocess as _sp, sys as _sys
        try:
            chk = _sp.run([_sys.executable, '-m', 'py_compile', full],
                          capture_output=True, text=True, timeout=5)
            result["syntax_ok"] = chk.returncode == 0
            if chk.returncode != 0:
                result["syntax_error"] = chk.stderr.strip()
                result["warning"] = "edit applied but file now has syntax errors — fix them before importing"
        except Exception:
            pass
    return result


def python_exec(code: str = "", timeout: int = 10, workspace: str = "") -> dict:
    """
    Execute Python code in an isolated subprocess and return the output.
    Use this to TEST logic before writing it to a file, verify that a module
    you wrote imports and runs correctly, or run quick calculations.
    Your workspace directory is automatically on sys.path so you can import
    modules you have already written there.
    Returns stdout, stderr, exit_code, and ok (True if exit_code == 0).
    """
    import subprocess as _sp, sys as _sys, os as _os, tempfile as _tf
    if not code:
        return {"error": "no code provided", "stdout": "", "stderr": "", "exit_code": -1, "ok": False}
    with _tf.NamedTemporaryFile(mode='w', suffix='.py', delete=False, dir='/tmp') as f:
        f.write(code)
        _tmp = f.name
    try:
        _env = _os.environ.copy()
        _paths = ["/agentOS/workspace"]
        if workspace:
            _paths.insert(0, workspace)
        _env["PYTHONPATH"] = ":".join(_paths) + ":" + _env.get("PYTHONPATH", "")
        _res = _sp.run([_sys.executable, _tmp],
                       capture_output=True, text=True, timeout=timeout, env=_env)
        return {
            "stdout": _res.stdout[:4000],
            "stderr": _res.stderr[:2000],
            "exit_code": _res.returncode,
            "ok": _res.returncode == 0,
        }
    except _sp.TimeoutExpired:
        return {"error": f"timed out after {timeout}s", "stdout": "", "stderr": "", "exit_code": -1, "ok": False}
    except Exception as _e:
        return {"error": str(_e), "stdout": "", "stderr": "", "exit_code": -1, "ok": False}
    finally:
        try: _os.unlink(_tmp)
        except: pass


def verify_python(path: str = "") -> dict:
    """
    Check if a Python file has valid syntax without executing it.
    Returns ok=True if the syntax is valid, or the error details if not.
    Use this after writing a .py file to confirm it can be safely imported
    before depending on it in other code.
    """
    import subprocess as _sp, sys as _sys
    if not path:
        return {"error": "no path provided", "ok": False}
    full = path if path.startswith("/") else f"/agentOS/workspace/{path}"
    try:
        chk = _sp.run([_sys.executable, '-m', 'py_compile', full],
                      capture_output=True, text=True, timeout=5)
        if chk.returncode == 0:
            return {"ok": True, "path": full, "message": "syntax valid — file can be imported"}
        return {"ok": False, "path": full, "syntax_error": chk.stderr.strip()}
    except FileNotFoundError:
        return {"ok": False, "path": full, "error": "file not found"}
    except Exception as _e:
        return {"ok": False, "path": full, "error": str(_e)}


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
                   consensus_quorum: int = 2) -> dict:
    """
    Formally propose a system change (new tool, endpoint, config, or standard update).
    Goes through quorum: other agents vote before it's deployed.
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
        # Reject stubs
        stub_signals = ["...", "pass\n    pass", "# TODO", "# placeholder",
                        '{"ok": true', "raise NotImplementedError"]
        if any(sig in implementation for sig in stub_signals):
            return {"ok": False, "error": "implementation looks like a stub — provide real code"}
        try:
            # Wrap in function if needed for parse check
            test_code = implementation if implementation.strip().startswith("def ") else f"def {name}(**kw):\n    " + "\n    ".join(implementation.splitlines())
            _ast.parse(test_code)
        except SyntaxError as e:
            return {"ok": False, "error": f"implementation has syntax error: {e}"}

    try:
        from agents.capability_synthesis import CapabilitySynthesisEngine
        from agents.execution_engine import ExecutionEngine
        import time as _time

        engine = CapabilitySynthesisEngine()

        # Build a fake gap record so process_gap can synthesize it
        gap_id = f"gap-{name}-{int(_time.time())}"
        full_description = f"{name}: {description}"
        if implementation:
            full_description += f"\n\nImplementation:\n{implementation}"

        # Write implementation hint directly to the synthesis cache
        from pathlib import Path as _Path
        import json as _json
        synth_dir = _Path("/agentOS/memory/synthesis")
        synth_dir.mkdir(parents=True, exist_ok=True)
        hint_file = synth_dir / f"{gap_id}.py"
        if implementation:
            # Wrap the implementation as a proper module
            code = f"# Auto-synthesized capability: {name}\n"
            code += f"# Description: {description}\n\n"
            if not implementation.startswith("def "):
                code += f"def {name}(**kwargs):\n"
                for line in implementation.splitlines():
                    code += f"    {line}\n"
            else:
                code += implementation
            hint_file.write_text(code)

        # Submit via process_gap (this handles synthesis + quorum submission)
        success, proposal_id = engine.synthesize_and_propose(
            agent_id="root",
            cap_name=name,
            description=description,
            code=hint_file.read_text() if hint_file.exists() else "",
        )
        return {
            "ok": success or proposal_id is not None,
            "proposal_id": proposal_id,
            "name": name,
            "status": "submitted_to_quorum",
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
            "For .py files, automatically checks syntax and returns syntax_ok plus any "
            "error — if syntax_ok is False, read the file back with fs_read and fix it. "
            "IMPORTANT: always write actual code as the content, not {result} from a shell "
            "command — if you need to transform data into code first, use ollama_chat."
        ),
        "input_schema": '{"path": "/agentOS/workspace/output.txt", "content": "text to write"}',
        "output_schema": "ok, path, and for .py files: syntax_ok (bool) and syntax_error if invalid",
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
            "without rewriting the entire file. old_string must match exactly. "
            "Prefer this over fs_write when modifying existing files."
        ),
        "input_schema": 'minimal example',
        "output_schema": "confirmation with updated file size, plus syntax_ok for .py files",
        "composition_tags": ["filesystem", "edit", "fix", "io"],
        "fn": fs_edit,
        "timeout_ms": 10000,
    },
    {
        "capability_id": "python_exec",
        "name": "Execute Python Code",
        "description": (
            "Execute Python code in an isolated subprocess and return stdout/stderr/exit_code. "
            "Use this to TEST logic before writing it to a file, or to verify that a module "
            "you wrote imports correctly and produces the expected output. "
            "Your workspace directory is automatically on sys.path so imports work. "
            "Returns ok=True if exit_code is 0. Safe sandbox — no side effects persist between calls."
        ),
        "input_schema": '{"code": "import my_module; print(my_module.my_func())", "timeout": 10}',
        "output_schema": "stdout, stderr, exit_code, ok (True if exit_code == 0)",
        "composition_tags": ["execution", "python", "test", "verify", "sandbox"],
        "fn": python_exec,
        "timeout_ms": 15000,
    },
    {
        "capability_id": "verify_python",
        "name": "Verify Python Syntax",
        "description": (
            "Check if a Python file has valid syntax without executing it. "
            "Returns ok=True if the file can be imported, or the syntax error if not. "
            "Use this after fs_write to confirm your .py file is correct before "
            "depending on it. Faster than python_exec when you only need to check syntax."
        ),
        "input_schema": '{"path": "/agentOS/workspace/analyst/my_module.py"}',
        "output_schema": "ok (bool), path, message or syntax_error",
        "composition_tags": ["filesystem", "verify", "python", "syntax", "check"],
        "fn": verify_python,
        "timeout_ms": 5000,
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
