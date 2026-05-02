"""
submit_task.py — manage the Hollow AgentOS task queue from Windows.

Usage:
  python submit_task.py "spec text"                    # submit to any agent
  python submit_task.py "spec" --agent builder         # assign to specific agent
  python submit_task.py --file my_spec.txt             # read spec from file
  python submit_task.py --status task-abc123           # check task status
  python submit_task.py --list                         # list all tasks

The task_id is printed on stdout when submitting.
"""

import json, sys, uuid, time, argparse
from pathlib import Path

QUEUE = Path(__file__).parent / "memory" / "task_queue.jsonl"


def _load_tasks():
    if not QUEUE.exists():
        return []
    tasks = []
    for line in QUEUE.read_text().splitlines():
        try:
            tasks.append(json.loads(line))
        except Exception:
            pass
    return tasks


def cmd_status(task_id):
    for t in _load_tasks():
        if t.get("task_id") == task_id:
            print(f"task_id:      {t['task_id']}")
            print(f"status:       {t['status']}")
            print(f"assigned_to:  {t.get('assigned_to') or '(any)'}")
            print(f"created_at:   {t.get('created_at','?')}")
            print(f"assigned_at:  {t.get('assigned_at') or '-'}")
            print(f"completed_at: {t.get('completed_at') or '-'}")
            print(f"result:       {t.get('result') or '-'}")
            print(f"spec:         {t.get('spec','')[:120]}")
            return
    print(f"Task {task_id} not found.", file=sys.stderr)
    sys.exit(1)


def cmd_list():
    tasks = _load_tasks()
    if not tasks:
        print("No tasks.")
        return
    for t in tasks:
        print(f"[{t['status']:<10}] {t['task_id']}  {t.get('assigned_to') or 'any':<10}  {t.get('spec','')[:60]}")


def main():
    p = argparse.ArgumentParser(description="Hollow AgentOS task queue")
    p.add_argument("spec", nargs="?", help="Task spec (natural language)")
    p.add_argument("--file", help="Read spec from a text file")
    p.add_argument("--agent", choices=["scout","analyst","builder"], default=None,
                   help="Assign to a specific agent (default: any)")
    p.add_argument("--files", nargs="*", default=[],
                   help="Context file paths inside the container")
    p.add_argument("--status", metavar="TASK_ID", help="Check status of a task")
    p.add_argument("--list", action="store_true", help="List all tasks")
    args = p.parse_args()

    if args.status:
        cmd_status(args.status)
        return
    if args.list:
        cmd_list()
        return

    if args.file:
        spec = Path(args.file).read_text().strip()
    elif args.spec:
        spec = args.spec.strip()
    else:
        print("ERROR: provide a spec, --status TASK_ID, or --list", file=sys.stderr)
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
