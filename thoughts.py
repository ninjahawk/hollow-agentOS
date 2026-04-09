#!/usr/bin/env python3
"""
Hollow AgentOS — Live Thought Stream
Tails logs/thoughts.log and shows agent inner-life and actions in real time.
Run:  python thoughts.py
"""

import os
import sys
import time
from pathlib import Path

HOLLOW_DIR = Path(os.getenv("HOLLOW_DIR", Path(__file__).parent))
THOUGHTS_LOG = HOLLOW_DIR / "logs" / "thoughts.log"

HEADER = """\033[1;97m
  ╔══════════════════════════════════════════════════════╗
  ║        hollow agentOS — live thought stream          ║
  ║  Dune (analyst)  ·  Fern (scout)  ·  Drift (builder) ║
  ╚══════════════════════════════════════════════════════╝
\033[0m\033[90m  Waiting for thoughts…  (Ctrl+C to exit)\033[0m
"""

LEGEND = (
    "\033[90m  ▶ action  ✓ success  ✗ fail  "
    "🧠 worldview  ❓ question  💭 opinion  "
    "🎯 goal  🪞 reflect  🌑 nothing\033[0m\n"
)


def tail_forever(path: Path):
    """Open path and yield new lines as they are appended."""
    # Wait for file to exist
    while not path.exists():
        time.sleep(1)

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        # Jump to end so we only see new lines
        fh.seek(0, 2)
        while True:
            line = fh.readline()
            if line:
                yield line
            else:
                time.sleep(0.15)


def main():
    os.system("cls" if os.name == "nt" else "clear")
    sys.stdout.write(HEADER)
    sys.stdout.write(LEGEND)
    sys.stdout.flush()

    try:
        for line in tail_forever(THOUGHTS_LOG):
            sys.stdout.write(line)
            sys.stdout.flush()
    except KeyboardInterrupt:
        sys.stdout.write("\n\033[90m  stream ended.\033[0m\n")


if __name__ == "__main__":
    main()
