#!/usr/bin/env python3
"""
Hollow AgentOS — Live Agent Monitor
Shows the last N lines of history, then streams new events in real time.
Plain-English narrator lines are injected after key events.
Run:  python thoughts.py
"""

import os
import re
import sys
import time
import json
import collections
from pathlib import Path

HOLLOW_DIR   = Path(os.getenv("HOLLOW_DIR", Path(__file__).parent))
THOUGHTS_LOG = HOLLOW_DIR / "logs" / "thoughts.log"
HISTORY_LINES = 60

HEADER = """\033[1;97m
  ╔══════════════════════════════════════════════════════════╗
  ║          hollow agentOS  —  live agent monitor           ║
  ╚══════════════════════════════════════════════════════════╝
\033[0m"""

LEGEND = (
    "\033[90m"
    "  ◎ new goal   ▶ action   ✓ success   ✗ fail\n"
    "  💭 opinion   ❓ question   🧠 worldview   🪞 reflect\n"
    "\033[0m"
    "\033[90m  ─────────────────────────────────────────────────────────\033[0m\n"
)

# ANSI strip
_ANSI = re.compile(r"\033\[[0-9;]*m")
def strip(s): return _ANSI.sub("", s)

# ── narrator ─────────────────────────────────────────────────────────────────

def _json(s):
    try:    return json.loads(s)
    except: return {}

def narrate(raw: str) -> str | None:
    """Return a small plain-English line for key events, or None."""
    line = strip(raw).strip()
    if not line:
        return None

    # who — first non-space token that looks like a name
    m = re.match(r"\s*(\w+)\s+", line)
    who = m.group(1).title() if m else "Agent"

    # ── new goal ◎
    if "◎" in line:
        text = re.sub(r".*◎\s*", "", line).strip()[:80]
        return f"\033[90m  ↳ {who} started: {text}\033[0m"

    # ── capability approved
    if '"result": "approved"' in line or "'result': 'approved'" in line:
        nm = re.search(r'"name":\s*"([^"]+)"', line)
        name = nm.group(1) if nm else "a capability"
        return f"\033[90m  ↳ capability approved by quorum: {name}\033[0m"

    # ── capability submitted
    if '"status": "submitted_to_quorum"' in line:
        nm = re.search(r'"name":\s*"([^"]+)"', line)
        name = nm.group(1) if nm else "a capability"
        return f"\033[90m  ↳ {who} proposed: {name} — waiting for votes\033[0m"

    # ── synthesize success (without quorum line)
    if "✓  synthesize_capability" in line:
        nm = re.search(r'"name":\s*"([^"]+)"', line)
        name = nm.group(1) if nm else "something"
        if '"submitted_to_quorum"' not in line:
            return f"\033[90m  ↳ {who} synthesized: {name}\033[0m"

    # ── synthesize fail
    if "✗  synthesize_capability" in line:
        err = re.search(r'"error":\s*"([^"]+)"', line)
        reason = err.group(1)[:60] if err else "unknown error"
        return f"\033[90m  ↳ {who} synthesis failed: {reason}\033[0m"

    # ── file written
    if "✓  fs_write" in line:
        p = re.search(r'"path":\s*"([^"]+)"', line)
        if p:
            fname = p.group(1).split("/")[-1]
            return f"\033[90m  ↳ {who} wrote: {fname}\033[0m"

    # ── propose_change success
    if "✓  propose_change" in line:
        return f"\033[90m  ↳ {who} submitted a code change proposal\033[0m"

    # ── vote finalized
    if "✓  vote_on_proposal" in line and "finalized" in line:
        result = "approved" if '"result": "approved"' in line else "rejected"
        return f"\033[90m  ↳ vote finalized — proposal {result}\033[0m"

    return None


# ── log tailing ──────────────────────────────────────────────────────────────

def last_n_lines(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    buf = collections.deque(maxlen=n)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            buf.append(line)
    return list(buf)


def tail_forever(path: Path):
    while not path.exists():
        sys.stdout.write("\033[90m  waiting for logs…\033[0m\r")
        sys.stdout.flush()
        time.sleep(1)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        fh.seek(0, 2)
        while True:
            line = fh.readline()
            if line:
                yield line
            else:
                time.sleep(0.1)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    os.system("cls" if os.name == "nt" else "clear")
    sys.stdout.write(HEADER)
    sys.stdout.write(LEGEND)

    history = last_n_lines(THOUGHTS_LOG, HISTORY_LINES)
    if history:
        sys.stdout.write(f"\033[90m  — last {len(history)} lines —\033[0m\n")
        for line in history:
            sys.stdout.write(line)
        sys.stdout.write("\033[90m  — live —\033[0m\n")
    else:
        sys.stdout.write("\033[90m  no history yet — streaming live…\033[0m\n")

    sys.stdout.flush()

    try:
        for line in tail_forever(THOUGHTS_LOG):
            sys.stdout.write(line)
            note = narrate(line)
            if note:
                sys.stdout.write(note + "\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        sys.stdout.write("\n\033[90m  stream ended.\033[0m\n")


if __name__ == "__main__":
    main()
