"""
App Sandbox — Hollow Phase 4.

Safe execution layer for wrapped app commands. Apps installed from the store
run through this sandbox rather than the root shell_exec capability.

Restrictions applied:
- Blocklist of dangerous command patterns (rm -rf, mkfs, dd, etc.)
- Hard timeout (default 30s — no app should need more)
- Output capped at 256 KB (prevents log floods)
- Restricted environment (only safe vars passed through)
- Working directory isolated to /agentOS/workspace/sandbox/
- No capability to touch /agentOS/memory/, /agentOS/agents/, etc.

This is not container isolation — it's defense in depth within Docker.
Full container isolation per app is Phase 5+ when we have user accounts.
"""

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

SANDBOX_ROOT = Path(os.getenv("HOLLOW_SANDBOX_DIR", "/agentOS/workspace/sandbox"))
DEFAULT_TIMEOUT = int(os.getenv("HOLLOW_SANDBOX_TIMEOUT", "30"))  # seconds
MAX_OUTPUT_BYTES = int(os.getenv("HOLLOW_SANDBOX_MAX_OUTPUT", str(256 * 1024)))

# Environment variables allowed through to sandboxed processes
_ALLOWED_ENV_VARS = {
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER", "SHELL",
    "TMPDIR", "TMP", "TEMP",
}

# Dangerous patterns that are never allowed, regardless of source
# These apply to the FULL command string (after shell_template substitution)
_BLOCKLIST: list[re.Pattern] = [
    # Filesystem destruction
    re.compile(r"\brm\s+(-[a-z]*f[a-z]*|-[a-z]*r[a-z]*f|--force|--recursive)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bdd\s+"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bshred\b"),
    re.compile(r"\bwipefs\b"),
    # Privilege escalation
    re.compile(r"\bsu\s"),
    re.compile(r"\bchown\b"),
    re.compile(r"\bnewgrp\b"),
    # Network exfiltration (allow curl/wget for actual tool use, block suspicious patterns)
    re.compile(r"\bnc\s+.*-[el]"),        # netcat listener/exec
    re.compile(r"\bcurl\s.*exec\b"),
    # Shell escape
    re.compile(r"[;&|`]\s*sh\b"),
    re.compile(r"[;&|`]\s*bash\b"),
    re.compile(r"[;&|`]\s*zsh\b"),
    # Hollowagent internals protection
    re.compile(r"/agentOS/memory"),
    re.compile(r"/agentOS/agents"),
    re.compile(r"/agentOS/api"),
    re.compile(r"/claude-auth"),
]


def _check_command(command: str) -> Optional[str]:
    """
    Returns a rejection reason string if the command is unsafe, else None.
    """
    for pattern in _BLOCKLIST:
        if pattern.search(command):
            return f"blocked pattern: {pattern.pattern}"
    return None


def _clean_env() -> dict:
    """Build a restricted environment for sandboxed processes."""
    env = {}
    for key in _ALLOWED_ENV_VARS:
        val = os.environ.get(key)
        if val:
            env[key] = val
    # Ensure PATH has the basics
    if "PATH" not in env:
        env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    return env


def run_sandboxed(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = MAX_OUTPUT_BYTES,
) -> dict:
    """
    Execute a shell command in the sandbox.

    Args:
        command: Shell command string from a wrapper's shell_template
        cwd: Working directory (defaults to sandbox root)
        timeout: Max seconds to run (hard kill after this)
        max_output: Max bytes of stdout+stderr to capture

    Returns:
        dict with stdout, stderr, exit_code, ok, sandboxed=True,
        and optionally blocked=True + reason if rejected
    """
    # 1. Safety check
    rejection = _check_command(command)
    if rejection:
        return {
            "ok": False,
            "blocked": True,
            "reason": rejection,
            "stdout": "",
            "stderr": f"[sandbox] Command blocked: {rejection}",
            "exit_code": 126,
            "sandboxed": True,
        }

    # 2. Resolve working directory
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    work_dir = Path(cwd) if cwd else SANDBOX_ROOT
    # Don't allow cwd to escape sandbox — allow only workspace subdirs or tool installs
    allowed_cwds = [
        Path("/agentOS/workspace"),
        SANDBOX_ROOT,
        Path("/usr/local/bin"),
        Path("/usr"),
    ]
    safe_cwd = False
    for allowed in allowed_cwds:
        try:
            work_dir.resolve().relative_to(allowed.resolve())
            safe_cwd = True
            break
        except ValueError:
            continue
    if not safe_cwd:
        work_dir = SANDBOX_ROOT

    work_dir.mkdir(parents=True, exist_ok=True)

    # 3. Execute with timeout and output cap
    start = time.time()
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(work_dir),
            env=_clean_env(),
        )
        try:
            stdout_b, stderr_b = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {
                "ok": False,
                "blocked": False,
                "timed_out": True,
                "stdout": "",
                "stderr": f"[sandbox] Command timed out after {timeout}s",
                "exit_code": 124,
                "sandboxed": True,
                "elapsed": round(time.time() - start, 2),
            }

        # Cap output
        stdout = stdout_b[:max_output].decode("utf-8", errors="replace")
        stderr = stderr_b[:max_output].decode("utf-8", errors="replace")
        if len(stdout_b) > max_output:
            stdout += f"\n[sandbox] Output truncated at {max_output} bytes"

        return {
            "ok": proc.returncode == 0,
            "blocked": False,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "sandboxed": True,
            "elapsed": round(time.time() - start, 2),
        }

    except Exception as e:
        return {
            "ok": False,
            "blocked": False,
            "stdout": "",
            "stderr": f"[sandbox] Execution error: {e}",
            "exit_code": 1,
            "sandboxed": True,
        }
