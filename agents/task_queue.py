"""
Task Queue — AgentOS offload layer.

Allows external systems (Claude Code, scripts, users) to drop structured
tasks into /agentOS/memory/task_queue.jsonl. The daemon picks them up in
_assign_idle_goal and treats them as higher-priority than self-directed goals.

Task lifecycle:
  pending → assigned → completed | failed

While a task is active:
  - The agent's existence prompt includes the full spec as a hard constraint
  - Follow-on goals are anchored to the task objective (no drift)
  - When the task completes, the agent returns to self-direction

Format (one JSON object per line):
  {
    "task_id":      "task-<hex>",
    "spec":         "Full natural-language spec for the agent",
    "files":        ["/agentOS/path/to/file.py", ...],   # optional context files
    "assigned_to":  "scout" | "analyst" | "builder" | null,  # null = any
    "status":       "pending" | "assigned" | "completed" | "failed",
    "created_at":   "2026-05-02 01:40",
    "assigned_at":  null,
    "completed_at": null,
    "result":       null    # filled on completion
  }

Usage — drop a task from Claude Code:
  from agents.task_queue import submit_task
  task_id = submit_task(
      spec="Read /agentOS/agents/autonomy_loop.py and write a summary...",
      files=["/agentOS/agents/autonomy_loop.py"],
      assigned_to="analyst"
  )

Usage — check status:
  from agents.task_queue import get_task
  task = get_task(task_id)
  print(task["status"], task["result"])
"""

import json
import time
import uuid
from pathlib import Path
from typing import Optional

QUEUE_PATH = Path("/agentOS/memory/task_queue.jsonl")


# ── Write side (external caller) ─────────────────────────────────────────────

def submit_task(spec: str,
                files: Optional[list] = None,
                assigned_to: Optional[str] = None) -> str:
    """
    Submit a task. Returns the task_id.
    Safe to call from outside the container (bind-mount makes the file visible).
    """
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    entry = {
        "task_id":      task_id,
        "spec":         spec,
        "files":        files or [],
        "assigned_to":  assigned_to,
        "status":       "pending",
        "created_at":   time.strftime("%Y-%m-%d %H:%M"),
        "assigned_at":  None,
        "completed_at": None,
        "result":       None,
    }
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return task_id


def get_task(task_id: str) -> Optional[dict]:
    """Read the current state of a task by ID."""
    if not QUEUE_PATH.exists():
        return None
    for line in QUEUE_PATH.read_text().splitlines():
        try:
            t = json.loads(line)
            if t.get("task_id") == task_id:
                return t
        except Exception:
            pass
    return None


# ── Read/update side (daemon) ─────────────────────────────────────────────────

TASK_TIMEOUT_HOURS = 2  # tasks assigned but not completed within this window are auto-failed


def expire_stale_tasks() -> int:
    """
    Mark assigned tasks as failed if they've been assigned for >TASK_TIMEOUT_HOURS.
    Called at the start of each claim_task so the queue self-cleans.
    Returns count of tasks expired.
    """
    if not QUEUE_PATH.exists():
        return 0
    now = time.time()
    lines = QUEUE_PATH.read_text().splitlines()
    updated, expired = [], 0
    for line in lines:
        try:
            t = json.loads(line)
            if t.get("status") == "assigned" and t.get("assigned_at"):
                try:
                    assigned_dt = time.mktime(time.strptime(t["assigned_at"], "%Y-%m-%d %H:%M"))
                    hours_elapsed = (now - assigned_dt) / 3600
                    if hours_elapsed > TASK_TIMEOUT_HOURS:
                        t["status"] = "failed"
                        t["completed_at"] = time.strftime("%Y-%m-%d %H:%M")
                        t["result"] = f"timed out after {hours_elapsed:.1f}h"
                        expired += 1
                except Exception:
                    pass
        except Exception:
            pass
        updated.append(json.dumps(t) if isinstance(t, dict) else line)
    if expired:
        QUEUE_PATH.write_text("\n".join(updated) + "\n")
    return expired


def claim_task(agent_id: str) -> Optional[dict]:
    """
    Claim the oldest pending task for this agent (or unassigned).
    Atomically marks it assigned. Returns the task dict or None.
    """
    if not QUEUE_PATH.exists():
        return None

    expire_stale_tasks()
    lines = QUEUE_PATH.read_text().splitlines()
    claimed = None
    updated = []

    for line in lines:
        try:
            t = json.loads(line)
        except Exception:
            updated.append(line)
            continue

        if (t.get("status") == "pending"
                and claimed is None
                and (t.get("assigned_to") in (None, agent_id))):
            t["status"] = "assigned"
            t["assigned_to"] = agent_id
            t["assigned_at"] = time.strftime("%Y-%m-%d %H:%M")
            claimed = t

        updated.append(json.dumps(t))

    if claimed:
        QUEUE_PATH.write_text("\n".join(updated) + "\n")

    return claimed


def complete_task(task_id: str, result: str = "") -> None:
    """Mark a task completed with an optional result summary."""
    _update_task(task_id, status="completed",
                 completed_at=time.strftime("%Y-%m-%d %H:%M"),
                 result=result[:500])


def fail_task(task_id: str, reason: str = "") -> None:
    """Mark a task failed."""
    _update_task(task_id, status="failed",
                 completed_at=time.strftime("%Y-%m-%d %H:%M"),
                 result=reason[:200])


def _update_task(task_id: str, **kwargs) -> None:
    if not QUEUE_PATH.exists():
        return
    lines = QUEUE_PATH.read_text().splitlines()
    updated = []
    for line in lines:
        try:
            t = json.loads(line)
            if t.get("task_id") == task_id:
                t.update(kwargs)
        except Exception:
            pass
        updated.append(json.dumps(t) if isinstance(t, dict) else line)
    QUEUE_PATH.write_text("\n".join(updated) + "\n")


def pending_count() -> int:
    """How many tasks are pending or assigned."""
    if not QUEUE_PATH.exists():
        return 0
    count = 0
    for line in QUEUE_PATH.read_text().splitlines():
        try:
            t = json.loads(line)
            if t.get("status") in ("pending", "assigned"):
                count += 1
        except Exception:
            pass
    return count


def existence_prompt_fragment(task: dict) -> str:
    """
    Text injected into the existence prompt while a task is active.
    Hard constraint — agent cannot choose a different goal while this is present.
    """
    spec = task.get("spec", "")
    files = task.get("files", [])
    file_str = "\n".join(f"  - {f}" for f in files) if files else "  (none specified)"

    return (
        f"\n!! EXTERNAL TASK ASSIGNED (task_id={task['task_id']}) !!\n"
        f"You have been given a specific task by an external system.\n"
        f"Your ONLY goal this cycle is to make progress on this task.\n"
        f"Do NOT choose a self-directed goal. Do NOT drift from this spec.\n\n"
        f"TASK SPEC:\n{spec}\n\n"
        f"RELEVANT FILES:\n{file_str}\n\n"
        f"When complete: call memory_set with key='task_result_{task['task_id']}' "
        f"and value=a summary of what you produced and where it is.\n"
    )
