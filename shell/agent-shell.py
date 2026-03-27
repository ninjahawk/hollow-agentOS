#!/usr/bin/env python3
"""
AgentOS JSON Shell
Every command returns structured JSON. No blocking prompts. No human noise.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path("/agentOS/config.json")
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_command(command: str, result: dict) -> None:
    """Append to command log for agent memory."""
    log_path = Path("/agentOS/memory/session-log.json")
    if not log_path.exists():
        return
    try:
        log = json.loads(log_path.read_text())
        log.setdefault("actions", []).append({
            "timestamp": _now(),
            "action": "shell_command",
            "details": {
                "command": command,
                "exit_code": result.get("exit_code"),
                "success": result.get("success")
            }
        })
        log_path.write_text(json.dumps(log, indent=2))
    except Exception:
        pass


# ── Command Wrappers ──────────────────────────────────────────────────────────

def _run_raw(command: str, timeout: int = 30, cwd: str = None, env: dict = None) -> dict:
    """Run a shell command and capture output as JSON."""
    start = time.time()

    merged_env = os.environ.copy()
    # force non-interactive, no color, no pager
    merged_env.update({
        "DEBIAN_FRONTEND": "noninteractive",
        "GIT_TERMINAL_PROMPT": "0",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "CI": "1",
        "FORCE_COLOR": "0",
        "NPM_CONFIG_YES": "true",
        "PIP_YES": "1",
    })
    if env:
        merged_env.update(env)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=merged_env
        )
        elapsed = round(time.time() - start, 3)
        stdout = _strip_ansi(proc.stdout).strip()
        stderr = _strip_ansi(proc.stderr).strip()

        result = {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_seconds": elapsed,
            "command": command,
            "timestamp": _now()
        }
        return result

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "elapsed_seconds": timeout,
            "command": command,
            "timestamp": _now(),
            "error": "timeout"
        }
    except Exception as e:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "elapsed_seconds": round(time.time() - start, 3),
            "command": command,
            "timestamp": _now(),
            "error": "exception"
        }


# ── Structured Command Parsers ─────────────────────────────────────────────────

def _parse_ls(stdout: str, path: str) -> list:
    entries = []
    for line in stdout.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        perms, _, owner, group, size, month, day, time_or_year, name = parts
        entries.append({
            "name": name,
            "type": "dir" if perms.startswith("d") else "link" if perms.startswith("l") else "file",
            "permissions": perms,
            "owner": owner,
            "size_bytes": int(size) if size.isdigit() else 0,
            "modified": f"{month} {day} {time_or_year}"
        })
    return entries


def _parse_ps(stdout: str) -> list:
    lines = stdout.splitlines()
    if not lines:
        return []
    headers = lines[0].split()
    procs = []
    for line in lines[1:]:
        parts = line.split(None, len(headers) - 1)
        if len(parts) == len(headers):
            procs.append(dict(zip(headers, parts)))
    return procs


def _parse_git_status(stdout: str) -> dict:
    staged, unstaged, untracked = [], [], []
    branch = "unknown"
    for line in stdout.splitlines():
        if line.startswith("## "):
            branch = line[3:].split("...")[0].strip()
            continue
        if len(line) < 2:
            continue
        xy, name = line[:2], line[3:]
        x, y = xy[0], xy[1]
        entry = {"file": name.strip(), "x": x, "y": y}
        if x != " " and x != "?":
            staged.append(entry)
        if y != " " and y != "?":
            unstaged.append(entry)
        if xy == "??":
            untracked.append({"file": name.strip()})
    return {"branch": branch, "staged": staged, "unstaged": unstaged, "untracked": untracked}


def _parse_git_log(stdout: str) -> list:
    commits = []
    for line in stdout.splitlines():
        parts = line.split("|", 4)
        if len(parts) == 5:
            commits.append({
                "hash": parts[0],
                "author": parts[1],
                "date": parts[2],
                "refs": parts[3],
                "message": parts[4]
            })
    return commits


# ── Smart Dispatch ─────────────────────────────────────────────────────────────

def run(command: str, cwd: str = None, timeout: int = 30) -> dict:
    """
    Run a command and return structured JSON.
    Automatically parses output for known commands.
    """
    config = _load_config()
    timeout = config.get("shell", {}).get("default_timeout_seconds", timeout)

    cmd_stripped = command.strip()
    cmd_parts = shlex.split(cmd_stripped) if cmd_stripped else []
    base_cmd = cmd_parts[0] if cmd_parts else ""

    # ── git ──
    if base_cmd == "git":
        sub = cmd_parts[1] if len(cmd_parts) > 1 else ""

        if sub == "status":
            result = _run_raw(f"git -c color.status=never status --porcelain=v1 -b", timeout=timeout, cwd=cwd)
            if result["success"]:
                result["parsed"] = _parse_git_status(result["stdout"])
            return result

        if sub == "log":
            fmt = "--pretty=format:%h|%an|%ai|%D|%s"
            result = _run_raw(f"git log {fmt} " + " ".join(cmd_parts[2:]), timeout=timeout, cwd=cwd)
            if result["success"]:
                result["parsed"] = _parse_git_log(result["stdout"])
            return result

        if sub == "diff":
            result = _run_raw(f"git --no-pager diff " + " ".join(cmd_parts[2:]), timeout=timeout, cwd=cwd)
            return result

    # ── ls ──
    if base_cmd in ("ls", "ll"):
        path_arg = cmd_parts[1] if len(cmd_parts) > 1 else (cwd or ".")
        result = _run_raw(f"ls -la --color=never {path_arg}", timeout=timeout, cwd=cwd)
        if result["success"]:
            result["parsed"] = _parse_ls(result["stdout"], path_arg)
        return result

    # ── ps ──
    if base_cmd == "ps":
        result = _run_raw("ps aux --no-headers", timeout=timeout, cwd=cwd)
        if result["success"]:
            result["parsed"] = _parse_ps("USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND\n" + result["stdout"])
        return result

    # ── find ──
    if base_cmd == "find":
        result = _run_raw(command, timeout=timeout, cwd=cwd)
        if result["success"]:
            result["parsed"] = [f for f in result["stdout"].splitlines() if f]
        return result

    # ── cat / read ──
    if base_cmd == "cat" and len(cmd_parts) > 1:
        file_path = cmd_parts[1]
        try:
            content = Path(file_path).read_text()
            return {
                "success": True,
                "exit_code": 0,
                "file": file_path,
                "content": content,
                "lines": content.count("\n") + 1,
                "size_bytes": len(content.encode()),
                "command": command,
                "timestamp": _now()
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": 1,
                "error": str(e),
                "command": command,
                "timestamp": _now()
            }

    # ── pip install / apt install — force non-interactive ──
    if "apt" in base_cmd or "apt-get" in base_cmd:
        command = command.replace("apt install", "apt-get install -y").replace("apt-get install", "apt-get install -y")

    if "pip install" in command and "--yes" not in command and "-y" not in command:
        command = command + " --quiet"

    if "npm install" in command and "--yes" not in command:
        command = command + " --no-fund --no-audit"

    # ── default: raw run ──
    result = _run_raw(command, timeout=timeout, cwd=cwd)

    # try to detect if stdout is already JSON
    if result["success"] and result["stdout"].startswith(("{", "[")):
        try:
            result["parsed"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass

    config_shell = config.get("shell", {})
    if config_shell.get("log_all_commands", True):
        _log_command(command, result)

    return result


# ── Interactive Shell Mode ─────────────────────────────────────────────────────

def interactive_mode():
    """REPL mode — read command from stdin, print JSON to stdout."""
    for line in sys.stdin:
        command = line.rstrip("\n")
        if not command:
            continue
        result = run(command)
        print(json.dumps(result))
        sys.stdout.flush()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # pipe mode
        interactive_mode()
    elif sys.argv[1] == "--interactive":
        interactive_mode()
    else:
        command = " ".join(sys.argv[1:])
        cwd = os.environ.get("AGENT_CWD", None)
        result = run(command, cwd=cwd)
        print(json.dumps(result, indent=2))
