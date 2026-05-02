"""
submit_task.py — drop a task into the Hollow AgentOS task queue from Windows.

Usage:
  python submit_task.py "Read /agentOS/agents/autonomy_loop.py and write a one-page summary to /agentOS/workspace/analyst/autonomy_summary.md"
  python submit_task.py "Build a Python module at /agentOS/workspace/builder/rate_limiter.py that..." --agent builder
  python submit_task.py --file my_spec.txt --agent scout

The task_id is printed on stdout so Claude Code can track it.
"""

import json, sys, uuid, time, argparse
from pathlib import Path

QUEUE = Path(__file__).parent / "memory" / "task_queue.jsonl"

def main():
    p = argparse.ArgumentParser(description="Submit a task to Hollow AgentOS")
    p.add_argument("spec", nargs="?", help="Task spec (natural language)")
    p.add_argument("--file", help="Read spec from a text file")
    p.add_argument("--agent", choices=["scout","analyst","builder"], default=None,
                   help="Assign to a specific agent (default: any)")
    p.add_argument("--files", nargs="*", default=[],
                   help="Context file paths inside the container (e.g. /agentOS/agents/foo.py)")
    args = p.parse_args()

    if args.file:
        spec = Path(args.file).read_text().strip()
    elif args.spec:
        spec = args.spec.strip()
    else:
        print("ERROR: provide a spec as argument or --file <path>", file=sys.stderr)
        sys.exit(1)

    task_id = f"task-{uuid.uuid4().hex[:12]}"
    entry = {
        "task_id":      task_id,
        "spec":         spec,
        "files":        args.files,
        "assigned_to":  args.agent,
        "status":       "pending",
        "created_at":   time.strftime("%Y-%m-%d %H:%M"),
        "assigned_at":  None,
        "completed_at": None,
        "result":       None,
    }

    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    print(task_id)
    print(f"Task queued: {spec[:80]}{'...' if len(spec)>80 else ''}", file=sys.stderr)
    if args.agent:
        print(f"Assigned to: {args.agent}", file=sys.stderr)
    else:
        print("Will be picked up by the next available agent.", file=sys.stderr)

if __name__ == "__main__":
    main()
