#!/usr/bin/env python3
"""
Hollow AgentOS — Live Agent Monitor
Streams thoughts.log with terminal-width-aware line wrapping.
Run:  python thoughts.py
"""

import io
import os
import re
import sys
import time
import shutil
import collections
from pathlib import Path

# Force UTF-8 on Windows so box-drawing chars render correctly
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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

_ANSI = re.compile(r"\033\[[0-9;]*m")
def _vis(s): return _ANSI.sub("", s)

def _cols() -> int:
    return shutil.get_terminal_size((120, 24)).columns


def _active_color(raw: str) -> str:
    """Active ANSI color at end of raw string (resets DO clear state)."""
    active = []
    i = 0
    while i < len(raw):
        if raw[i] == "\033" and i + 1 < len(raw) and raw[i + 1] == "[":
            j = i + 2
            while j < len(raw) and raw[j] not in "mKHJABCDfsu":
                j += 1
            if j < len(raw) and raw[j] == "m":
                code = raw[i + 2:j]
                if code in ("0", ""):
                    active = []
                else:
                    active.append(raw[i:j + 1])
            i = j + 1
        else:
            i += 1
    return "".join(active)


def _dominant_color(raw: str) -> str:
    """Last non-reset ANSI color set anywhere in raw (ignores trailing resets).
    Used as fallback so continuation lines stay colored even when the split
    lands after a reset code."""
    last = ""
    i = 0
    while i < len(raw):
        if raw[i] == "\033" and i + 1 < len(raw) and raw[i + 1] == "[":
            j = i + 2
            while j < len(raw) and raw[j] not in "mKHJABCDfsu":
                j += 1
            if j < len(raw) and raw[j] == "m":
                code = raw[i + 2:j]
                if code not in ("0", ""):
                    last = raw[i:j + 1]   # record every non-reset, keep updating
            i = j + 1
        else:
            i += 1
    return last


def _wrap(raw: str) -> str:
    """
    Wrap a single log line at terminal width, preserving ANSI codes.
    Continuation lines are indented (hanging indent) and carry the active
    color forward so multi-line entries stay fully colored.
    """
    w = _cols()
    content = raw.rstrip("\n")
    if not content:
        return raw

    visible = _vis(content)
    if len(visible) <= w:
        return raw  # fits — return unchanged

    # ── Calculate the hanging indent ────────────────────────────────────────
    # Fixed at 30 — matches where content starts after "HH:MM:SS  name(15)  icon  "
    col = min(30, w // 2)
    hang = " " * col

    # ── ANSI-preserving word-wrap ─────────────────────────────────────────────
    def split_at(s: str, limit: int):
        """Split s at word boundary at `limit` visible chars. Returns (before, after)."""
        vis_pos = 0
        last_space = -1
        i = 0
        while i < len(s):
            if s[i] == "\033" and i + 1 < len(s) and s[i + 1] == "[":
                j = i + 2
                while j < len(s) and s[j] not in "mKHJABCDfsu":
                    j += 1
                i = j + 1
                continue
            if s[i] == " ":
                last_space = i
            vis_pos += 1
            if vis_pos >= limit:
                cut = last_space if last_space > 0 else i + 1
                return s[:cut], s[cut:].lstrip(" ")
            i += 1
        return s, ""

    lines = []
    remaining = content
    first = True
    max_vis = w

    while remaining:
        if len(_vis(remaining)) <= max_vis:
            lines.append(("" if first else hang) + remaining)
            break

        before, after = split_at(remaining, max_vis)

        # Carry the active color forward.
        # _active_color respects resets; if it returns empty (split after a
        # reset), fall back to _dominant_color which finds the last color
        # set anywhere in the line, ignoring trailing resets.
        carry = _active_color(before) or _dominant_color(before) or _dominant_color(content)

        lines.append(("" if first else hang) + before)
        remaining = carry + after  # prepend active color to continuation
        first = False
        max_vis = w - col

    return "\n".join(lines) + "\033[0m\n"


# ── narrator ──────────────────────────────────────────────────────────────────

def narrate(raw: str) -> str | None:
    """Return a small plain-English line for key events, or None."""
    line = _vis(raw).strip()
    if not line:
        return None

    m = re.match(r"\s*(\w+)\s+", line)
    who = m.group(1).title() if m else "Agent"

    if "◎" in line:
        text = re.sub(r".*◎\s*", "", line).strip()[:80]
        return f"\033[90m  ↳ {who} started: {text}\033[0m"
    if '"result": "approved"' in line or "'result': 'approved'" in line:
        nm = re.search(r'"name":\s*"([^"]+)"', line)
        name = nm.group(1) if nm else "a capability"
        return f"\033[90m  ↳ capability approved: {name}\033[0m"
    if '"status": "submitted_to_quorum"' in line:
        nm = re.search(r'"name":\s*"([^"]+)"', line)
        name = nm.group(1) if nm else "a capability"
        return f"\033[90m  ↳ {who} proposed: {name}\033[0m"
    if "✓  synthesize_capability" in line:
        nm = re.search(r'"name":\s*"([^"]+)"', line)
        name = nm.group(1) if nm else "something"
        if '"submitted_to_quorum"' not in line:
            return f"\033[90m  ↳ {who} synthesized: {name}\033[0m"
    if "✗  synthesize_capability" in line:
        err = re.search(r'"error":\s*"([^"]+)', line)  # no closing quote — handles 200-char truncation
        reason = err.group(1)[:80] if err else "unknown error"
        return f"\033[90m  ↳ synthesis failed: {reason}\033[0m"
    if "✓  fs_write" in line:
        p = re.search(r'"path":\s*"([^"]+)"', line)
        if p:
            fname = p.group(1).split("/")[-1]
            return f"\033[90m  ↳ {who} wrote: {fname}\033[0m"
    return None


# ── log tailing ───────────────────────────────────────────────────────────────

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


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    os.system("cls" if os.name == "nt" else "clear")
    sys.stdout.write(HEADER)
    sys.stdout.write(LEGEND)

    history = last_n_lines(THOUGHTS_LOG, HISTORY_LINES)
    if history:
        sys.stdout.write(f"\033[90m  — last {len(history)} lines —\033[0m\n")
        for line in history:
            if "artifact ok |" in _vis(line):
                continue
            sys.stdout.write(_wrap(line))
        sys.stdout.write("\033[90m  — live —\033[0m\n")
    else:
        sys.stdout.write("\033[90m  no history yet — streaming live…\033[0m\n")

    sys.stdout.flush()

    try:
        for line in tail_forever(THOUGHTS_LOG):
            vis = _vis(line)
            # Suppress noisy lines that add no value
            if "artifact ok |" in vis:
                continue
            sys.stdout.write(_wrap(line))
            note = narrate(line)
            if note:
                sys.stdout.write(_wrap(note + "\n"))
            sys.stdout.flush()
    except KeyboardInterrupt:
        sys.stdout.write("\n\033[90m  stream ended.\033[0m\n")


if __name__ == "__main__":
    main()
