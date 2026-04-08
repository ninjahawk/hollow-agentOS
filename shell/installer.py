"""
Tool Installer — Hollow Phase 5.

Installs CLI tools that aren't pre-packaged in the container.
Handles: apt-get, cargo install, pip install, Go install, precompiled binaries.

Called when a user selects an app and the tool binary isn't found.
The install is detected from the wrapper's install_hint field, validated,
and executed with restricted permissions (no sudo, no root operations).

Note: installs are NOT persistent across container restarts unless
the tool was added to the Dockerfile. This is a known limitation.
For persistence, add frequently-used tools to the Dockerfile and rebuild.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

INSTALL_DIR = Path(os.getenv("HOLLOW_INSTALL_DIR", "/agentOS/workspace/bin"))
INSTALL_TIMEOUT = int(os.getenv("HOLLOW_INSTALL_TIMEOUT", "120"))  # seconds

# Persistent install manifest — survives container restarts via volume mount
_MANIFEST = INSTALL_DIR.parent / "installed_tools.json"


def _load_manifest() -> dict:
    """Load the persistent tool install manifest."""
    try:
        if _MANIFEST.exists():
            return json.loads(_MANIFEST.read_text())
    except Exception:
        pass
    return {}


def _save_manifest(manifest: dict) -> None:
    """Atomically write the persistent tool install manifest."""
    _MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = _MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    tmp.replace(_MANIFEST)


def restore_installed_tools() -> dict:
    """
    Re-install all tools recorded in the manifest.
    Call at server startup to restore tools across container restarts.
    Returns {restored: int, skipped: int, failed: int}.
    """
    manifest = _load_manifest()
    restored = skipped = failed = 0
    for invoke, entry in manifest.items():
        if shutil.which(invoke):
            skipped += 1
            continue
        hint = entry.get("install_hint", "")
        result = install_tool(invoke, hint, timeout=180)
        if result.get("ok") and result.get("available"):
            restored += 1
        else:
            failed += 1
    return {"restored": restored, "skipped": skipped, "failed": failed}

# Allowed install commands — whitelist approach (much safer than blocklist)
_ALLOWED_PATTERNS = [
    re.compile(r"^cargo install [\w-]+$"),
    re.compile(r"^pip install [\w\-\[\]\.]+$"),
    re.compile(r"^pip3 install [\w\-\[\]\.]+$"),
    re.compile(r"^uv tool install [\w\-\.]+$"),   # uv: fast Python tool installer
    re.compile(r"^uv pip install [\w\-\[\]\.]+$"),
    re.compile(r"^go install [\w\./@]+$"),
    re.compile(r"^npm install -g [\w@/\-]+$"),
    re.compile(r"^apt-get install -y [\w\-]+$"),
    re.compile(r"^apt install -y [\w\-]+$"),
    re.compile(r"^brew install [\w\-]+$"),
]

# Packages blocked even if they match patterns above
_BLOCKED_PACKAGES = {
    "rm", "sudo", "su", "chmod", "chown", "bash", "sh", "zsh",
    "netcat", "nc", "ncat", "socat", "curl", "wget",  # already installed
}


def _parse_install_hint(install_hint: str) -> Optional[str]:
    """
    Extract a safe install command from an install_hint string.
    install_hint may be natural language like 'Install with: cargo install bat'
    or just 'cargo install bat'.
    """
    if not install_hint:
        return None

    # Try to extract a command from prose
    patterns = [
        r"(uv tool install [\w\-\.]+)",
        r"(uv pip install [\w\-\[\]\.]+)",
        r"(cargo install [\w-]+)",
        r"(pip3? install [\w\-\[\]\.]+)",
        r"(go install [\w\./@]+)",
        r"(npm install -g [\w@/\-]+)",
        r"(apt-?get install -y [\w\-]+)",
    ]
    for p in patterns:
        m = re.search(p, install_hint)
        if m:
            return m.group(1)

    # If it's already a clean command, use it directly
    hint = install_hint.strip()
    for allowed in _ALLOWED_PATTERNS:
        if allowed.match(hint):
            return hint

    return None


def _is_command_allowed(command: str) -> tuple[bool, str]:
    """Returns (allowed, reason)."""
    for allowed in _ALLOWED_PATTERNS:
        if allowed.match(command):
            # Check blocked packages
            parts = command.split()
            for pkg in parts[2:]:  # skip the command and subcommand
                if pkg.strip("-") in _BLOCKED_PACKAGES:
                    return False, f"package '{pkg}' is not allowed"
            return True, ""
    return False, f"command format not recognized as safe install"


def install_tool(
    invoke: str,
    install_hint: str,
    timeout: int = INSTALL_TIMEOUT,
) -> dict:
    """
    Install a CLI tool if it's not already available.

    Args:
        invoke: Binary name (e.g. 'bat', 'delta')
        install_hint: How to install (e.g. 'cargo install bat')
        timeout: Max install time in seconds

    Returns:
        dict with ok, available, method, message
    """
    # Already installed?
    if shutil.which(invoke):
        return {"ok": True, "available": True, "method": "pre-installed",
                "message": f"{invoke} is already installed"}

    # Parse install command from hint
    install_cmd = _parse_install_hint(install_hint)
    if not install_cmd:
        return {
            "ok": False,
            "available": False,
            "method": "none",
            "message": f"Could not determine how to install '{invoke}'. "
                       f"Hint was: {install_hint[:100] if install_hint else 'none'}",
        }

    # Safety check
    allowed, reason = _is_command_allowed(install_cmd)
    if not allowed:
        return {
            "ok": False,
            "available": False,
            "method": "blocked",
            "message": f"Install blocked: {reason}",
        }

    # Run the install
    start = time.time()
    try:
        proc = subprocess.run(
            install_cmd,
            shell=True,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "PATH": f"{INSTALL_DIR}:{os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"},
        )
        elapsed = round(time.time() - start, 1)

        if proc.returncode == 0:
            # Verify it's now available
            if shutil.which(invoke):
                # Record in persistent manifest for restore on next container start
                try:
                    manifest = _load_manifest()
                    manifest[invoke] = {
                        "install_hint": install_hint,
                        "install_cmd": install_cmd,
                        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    _save_manifest(manifest)
                except Exception:
                    pass
                return {
                    "ok": True,
                    "available": True,
                    "method": install_cmd.split()[0],
                    "message": f"Installed {invoke} in {elapsed}s",
                    "elapsed": elapsed,
                }
            else:
                return {
                    "ok": False,
                    "available": False,
                    "method": install_cmd.split()[0],
                    "message": f"Install ran but '{invoke}' still not found in PATH. "
                               f"May need manual PATH setup.",
                    "stdout": proc.stdout.decode("utf-8", errors="replace")[:500],
                }
        else:
            stderr = proc.stderr.decode("utf-8", errors="replace")[:500]
            return {
                "ok": False,
                "available": False,
                "method": install_cmd.split()[0],
                "message": f"Install failed: {stderr[:200]}",
            }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "available": False,
            "method": "timeout",
            "message": f"Install timed out after {timeout}s",
        }
    except Exception as e:
        return {
            "ok": False,
            "available": False,
            "method": "error",
            "message": str(e),
        }
