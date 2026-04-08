#!/usr/bin/env python3
"""
Hollow AgentOS — cast recorder
Runs sim.py and captures its ANSI output into an asciinema v2 .cast file,
with realistic per-character timing so the playback looks natural.

Usage: python demo/record.py demo/hollow-demo.cast
"""

import sys
import io
import json
import time
import subprocess
import os

WIDTH  = 110
HEIGHT = 36

def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "demo/hollow-demo.cast"

    # Run sim.py with ANSI output, capture stdout
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = str(WIDTH)
    env["LINES"] = str(HEIGHT)

    proc = subprocess.Popen(
        [sys.executable, "demo/sim.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    raw, _ = proc.communicate()
    text = raw.decode("utf-8", errors="replace")

    # Split into "frames" — each line becomes an event with a delay
    # We model the delays to match the sleep() calls in sim.py
    events = []
    t = 0.0

    # Timing map: certain line patterns get longer pauses before them
    # to reproduce the feel of sim.py's sleep() calls
    line_delays = {
        "starting daemon":       0.5,
        "event bus":             0.08,
        "memory heap":           0.08,
        "audit kernel":          0.08,
        "capability graph":      0.08,
        "transaction log":       0.08,
        "ollama":                0.15,
        "agents registered":     0.1,
        "scout":                 0.07,
        "rook":                  0.07,
        "finch":                 0.07,
        "bramble":               0.07,
        "Cycle 1":               0.8,
        "Cycle 2":               0.5,
        "capability synthesis":  0.5,
        "Cycle 3":               0.5,
        "session summary":       0.4,
        "planning":              0.3,
        "step 1/3  search":      0.2,
        "step 1/3  search.*done":0.3,
        "step 2/3  semantic.*run":0.4,
        "step 2/3  semantic.*done":0.3,
        "step 3/3":              0.2,
        "step 1/3  memory":      0.3,
        "step 2/3  ollama_chat.*think": 0.9,
        "step 2/3  ollama_chat.*done":  0.2,
        "step 3/3  fs_write":    0.2,
        "step 1/2  shell_exec":  0.35,
        "step 2/2  memory_set":  0.2,
        "step 2/2  ollama_chat.*think": 1.1,
        "step 2/2  ollama_chat.*done":  0.2,
        "proposing":             0.1,
        "writing module":        1.0,
        "sending to quorum":     0.4,
        "reviewing.*scout":      0.5,
        "accept.*logic":         0.2,
        "reviewing.*rook":       0.4,
        "accept.*fills":         0.2,
        "reviewing.*bramble":    0.35,
        "accept.*no conflict":   0.2,
        "quorum reached":        0.3,
        "hot-loaded":            0.2,
        "capability graph updated": 0.15,
    }

    import re
    lines = text.split("\n")
    base_char_delay = 0.006  # seconds per char (typewriter feel)

    for line in lines:
        stripped = line.strip()
        delay = 0.04  # default inter-line delay

        for pattern, d in line_delays.items():
            if re.search(pattern, stripped, re.IGNORECASE):
                delay = d
                break

        t += delay
        events.append([round(t, 3), "o", line + "\n"])
        # Add a tiny char-level delay for longer lines to feel typed
        if len(stripped) > 20:
            t += len(stripped) * base_char_delay * 0.3

    # Write .cast file
    header = {
        "version": 2,
        "width": WIDTH,
        "height": HEIGHT,
        "timestamp": 1744070400,
        "title": "Hollow AgentOS — autonomous agent runtime",
        "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
    }

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for event in events:
            f.write(json.dumps(event) + "\n")

    print(f"wrote {len(events)} events -> {out_path}  (duration: {t:.1f}s)")


if __name__ == "__main__":
    main()
