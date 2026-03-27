#!/usr/bin/env python3
"""
AgentOS Agent Shell
JSON-native subprocess runner. No ANSI, no interactive prompts, structured output.
Designed to be called both directly and from async contexts via run_in_executor.
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path("/agentOS/config.json")


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}


def run(command: str, cwd: Optional[str] = None, timeout: int = 30) -> dict:
    """
    Run a shell command and return structured JSON.
    Blocking — call from a thread pool executor in async contexts.
    """
    config = _load_config()
    default_timeout = config.get("shell", {}).get("default_timeout_seconds", 30)
    timeout = timeout or default_timeout

    env_extras = {
        "DEBIAN_FRONTEND": "noninteractive",
        "GIT_TERMINAL_PROMPT": "0",
        "TERM": "dumb",
    }
    import os
    env = {**os.environ, **env_extras}

    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        elapsed = round(time.monotonic() - start, 3)
        result = {
            "success":          proc.returncode == 0,
            "exit_code":        proc.returncode,
            "stdout":           proc.stdout.rstrip("\n"),
            "stderr":           proc.stderr.rstrip("\n"),
            "elapsed_seconds":  elapsed,
            "command":          command,
            "timestamp":        _now(),
        }

        # Best-effort: parse stdout as JSON if it looks like it
        if proc.stdout.strip().startswith(("{", "[")):
            try:
                result["parsed"] = json.loads(proc.stdout)
            except Exception:
                pass

        return result

    except subprocess.TimeoutExpired:
        elapsed = round(time.monotonic() - start, 3)
        return {
            "success":         False,
            "exit_code":       -1,
            "stdout":          "",
            "stderr":          "",
            "elapsed_seconds": elapsed,
            "command":         command,
            "timestamp":       _now(),
            "error":           "timeout",
        }
    except Exception as e:
        return {
            "success":         False,
            "exit_code":       -1,
            "stdout":          "",
            "stderr":          "",
            "elapsed_seconds": 0,
            "command":         command,
            "timestamp":       _now(),
            "error":           str(e),
        }


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import sys
    cmd = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "echo 'no command'"
    print(json.dumps(run(cmd)))
