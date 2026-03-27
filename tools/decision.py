#!/usr/bin/env python3
"""
Queue a decision for human approval without blocking the agent.
Usage: decision "Need to install package X — approve?"
"""
import json, sys
from pathlib import Path
sys.path.insert(0, "/agentOS")
from memory.manager import queue_decision, get_pending_decisions, resolve_decision

def main():
    args = sys.argv[1:]
    op = args[0] if args else "queue"

    if op == "list":
        print(json.dumps({"pending": get_pending_decisions()}))
    elif op == "resolve":
        did = args[1]
        resolution = " ".join(args[2:]) if len(args) > 2 else "approved"
        ok = resolve_decision(did, resolution)
        print(json.dumps({"ok": ok}))
    else:
        message = " ".join(args) if args else op
        did = queue_decision(message)
        print(json.dumps({"ok": True, "decision_id": did, "message": message,
                          "note": "Agent continues. Human can resolve at /decisions in dashboard."}))

if __name__ == "__main__":
    main()
