"""
Task Scheduler — route tasks to the right model/agent, spawn sub-agents.

The scheduler is what makes AgentOS a real OS: it decides WHICH model handles
a task based on complexity, cost, and available capacity. Agents don't pick
their own models — the scheduler does.

Complexity scale:
    1 — trivial (lookup, format, summarize < 200 words)
    2 — simple (short generation, classification)
    3 — moderate (multi-step reasoning, code review)
    4 — complex (architecture, long code generation)
    5 — deep reasoning (math, multi-hop, planning)
"""

import json
import os
import sys
import time
import uuid
import threading
import urllib.request
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

TASKS_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "tasks.json"
SHELL_LOG_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "shell-usage-log.json"
API_BASE = "http://localhost:7777"

# Keep at most this many tasks in memory/disk to prevent unbounded growth
MAX_TASKS = 500
# Thread pool for async task execution (prevents blocking the API event loop)
_task_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hollow-task")

# Complexity → Ollama role → model (from config routing table)
COMPLEXITY_ROUTING = {
    1: "general",          # mistral-nemo:12b — fast, cheap
    2: "general",
    3: "code",             # qwen2.5:14b — better reasoning
    4: "code",
    5: "reasoning",        # qwen3.5-35b-moe — deep reasoning
}

# Estimated tokens per complexity level (for budget pre-check)
COMPLEXITY_TOKEN_ESTIMATE = {
    1: 500,
    2: 1_000,
    3: 3_000,
    4: 8_000,
    5: 20_000,
}


@dataclass
class Task:
    task_id: str
    description: str
    complexity: int          # 1-5
    submitted_by: str        # agent_id
    assigned_to: Optional[str]   # agent_id or model name
    status: str              # queued | running | done | failed
    result: Optional[dict]
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    context: dict = field(default_factory=dict)  # extra data passed to model


class TaskScheduler:
    def __init__(self, registry, bus, master_token: str):
        self._registry = registry
        self._bus = bus
        self._master_token = master_token
        self._event_bus = None
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._load()

    def set_event_bus(self, event_bus) -> None:
        """Inject EventBus after both are created. Called at server startup."""
        self._event_bus = event_bus

    def submit(
        self,
        description: str,
        submitted_by: str,
        complexity: int = 2,
        context: Optional[dict] = None,
        system_prompt: Optional[str] = None,
        wait: bool = True,
    ) -> Task:
        """
        Submit a task. Scheduler routes it to the right model and runs it.

        wait=True (default): blocks until the task completes, returns final Task.
        wait=False: submits and returns immediately with status='queued'.
        The task runs in a thread pool so it never blocks the FastAPI event loop.
        """
        complexity = max(1, min(5, complexity))
        task = Task(
            task_id=str(uuid.uuid4())[:12],
            description=description,
            complexity=complexity,
            submitted_by=submitted_by,
            assigned_to=None,
            status="queued",
            result=None,
            created_at=time.time(),
            context=context or {},
        )

        with self._lock:
            self._tasks[task.task_id] = task
            self._save()

        if self._event_bus:
            self._event_bus.emit("task.queued", task.submitted_by, {
                "task_id":      task.task_id,
                "complexity":   task.complexity,
                "submitted_by": task.submitted_by,
            })

        future = _task_executor.submit(self._run_task, task, system_prompt)
        if wait:
            future.result()  # blocks caller thread, not the event loop
        return task

    def _run_task(self, task: Task, system_prompt: Optional[str] = None,
                  standards_context: Optional[str] = None):
        role = COMPLEXITY_ROUTING.get(task.complexity, "general")

        # Model policy check — does the submitting agent allow this role?
        agent = self._registry.get(task.submitted_by)
        model_for_role = {
            "general": "mistral-nemo:12b",
            "code": "qwen2.5:14b",
            "reasoning": "qwen3.5-35b-moe:latest",
        }.get(role, "mistral-nemo:12b")
        if agent and not self._registry.check_model_policy(task.submitted_by, model_for_role, "ollama"):
            task.status = "failed"
            task.error = f"Agent model policy blocks use of '{model_for_role}' for role '{role}'"
            task.finished_at = time.time()
            with self._lock:
                self._save()
            return

        task.assigned_to = role
        task.status = "running"
        task.started_at = time.time()

        if self._event_bus:
            self._event_bus.emit("task.started", task.submitted_by, {
                "task_id":    task.task_id,
                "model_role": role,
                "model":      model_for_role,
            })

        # Notify submitter
        self._bus.send(
            from_id="scheduler",
            to_id=task.submitted_by,
            content={"task_id": task.task_id, "status": "running", "model_role": role},
            msg_type="log",
        )

        try:
            messages = []

            # Build system prompt — inject standards context if available
            effective_system = system_prompt or ""
            if standards_context:
                effective_system = (effective_system + "\n\n" + standards_context).strip()
            elif not system_prompt:
                # Auto-fetch relevant standards for this task
                try:
                    sys.path.insert(0, str(Path(__file__).parent.parent))
                    from agents.standards import get_relevant_standards_text
                    auto_standards = get_relevant_standards_text(task.description)
                    if auto_standards:
                        effective_system = auto_standards
                except Exception:
                    pass

            if effective_system:
                messages.append({"role": "system", "content": effective_system})
            if task.context.get("history"):
                messages.extend(task.context["history"])
            messages.append({"role": "user", "content": task.description})

            body = json.dumps({
                "role": role,
                "messages": messages,
            }).encode()

            req = urllib.request.Request(
                f"{API_BASE}/ollama/chat",
                data=body,
                headers={
                    "Authorization": f"Bearer {self._master_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read())

            # Ollama chat endpoint returns tokens_prompt / tokens_response /
            # total_duration_ms — map to canonical names used throughout the system
            tokens_in  = resp.get("tokens_prompt") or resp.get("tokens_in") or 0
            tokens_out = resp.get("tokens_response") or resp.get("tokens_out") or 0
            ms         = resp.get("total_duration_ms") or resp.get("ms")

            task.result = {
                "response":  resp.get("response", ""),
                "model":     resp.get("model"),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "ms":        ms,
            }
            task.status = "done"

            # Update submitter's usage — this drives budget.warning / budget.exhausted
            self._registry.update_usage(
                task.submitted_by,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.result = None

        task.finished_at = time.time()

        with self._lock:
            self._save()

        if self._event_bus:
            if task.status == "done":
                r = task.result or {}
                self._event_bus.emit("task.completed", task.submitted_by, {
                    "task_id":    task.task_id,
                    "model":      r.get("model"),
                    "tokens_in":  r.get("tokens_in"),
                    "tokens_out": r.get("tokens_out"),
                    "ms":         r.get("ms"),
                })
            else:
                self._event_bus.emit("task.failed", task.submitted_by, {
                    "task_id": task.task_id,
                    "error":   task.error,
                })

        # Deliver result to submitter's inbox
        self._bus.send(
            from_id="scheduler",
            to_id=task.submitted_by,
            content={
                "task_id": task.task_id,
                "status": task.status,
                "result": task.result,
                "error": task.error,
            },
            msg_type="result",
        )

        # Also update submitter's current_task
        self._registry.set_task(task.submitted_by, None)

    def spawn_agent(
        self,
        parent_id: str,
        name: str,
        role: str,
        task_description: str,
        complexity: int = 2,
        capabilities: Optional[list[str]] = None,
    ) -> dict:
        """
        Spawn a child agent, assign it a task, run it, return result.
        The child agent is terminated after the task completes.
        """
        # Check parent has spawn capability
        parent = self._registry.get(parent_id)
        if not parent or not parent.has_cap("spawn"):
            return {"error": "parent agent lacks spawn capability"}

        # Register child — inherits parent's process group (v0.8.0)
        child, child_token = self._registry.register(
            name=name,
            role=role,
            capabilities=capabilities,
            parent_id=parent_id,
            group_id=parent_id,
        )

        # Notify parent
        self._bus.send(
            from_id="scheduler",
            to_id=parent_id,
            content={"spawned": child.agent_id, "name": name, "role": role, "task": task_description},
            msg_type="log",
        )

        # Run task as the child agent
        self._registry.set_task(child.agent_id, task_description)
        task = self.submit(
            description=task_description,
            submitted_by=child.agent_id,
            complexity=complexity,
        )

        # Terminate child after task
        self._registry.terminate(child.agent_id)

        return {
            "child_agent_id": child.agent_id,
            "task_id": task.task_id,
            "status": task.status,
            "result": task.result,
            "error": task.error,
        }

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list_tasks(self, agent_id: Optional[str] = None, limit: int = 20) -> list[dict]:
        tasks = list(self._tasks.values())
        if agent_id:
            tasks = [t for t in tasks if t.submitted_by == agent_id]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [asdict(t) for t in tasks[:limit]]

    def _load(self):
        if TASKS_PATH.exists():
            try:
                data = json.loads(TASKS_PATH.read_text())
                for d in data.values():
                    t = Task(**d)
                    self._tasks[t.task_id] = t
            except Exception:
                pass

    def _save(self):
        TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Evict oldest tasks beyond MAX_TASKS to prevent unbounded growth
        if len(self._tasks) > MAX_TASKS:
            sorted_ids = sorted(self._tasks, key=lambda tid: self._tasks[tid].created_at)
            for tid in sorted_ids[:len(self._tasks) - MAX_TASKS]:
                del self._tasks[tid]
        out = {tid: asdict(t) for tid, t in self._tasks.items()}
        TASKS_PATH.write_text(json.dumps(out, indent=2))


def log_shell_usage(command: str, agent_id: str = "unknown") -> None:
    """
    Log shell command patterns to drive shell-elimination roadmap.
    Tracks which operations agents reach for /shell to perform — each unique
    pattern is a missing first-class endpoint. Run:
      python3 -c "import json; d=json.load(open('/agentOS/memory/shell-usage-log.json')); \
                  [print(e['pattern'], e['count']) for e in sorted(d['patterns'].values(), \
                  key=lambda x: -x['count'])[:20]]"
    to see what to build next.
    """
    try:
        # Extract a normalized pattern (first word + structure, not argument values)
        parts = command.strip().split()
        if not parts:
            return
        # Pattern = first two tokens (e.g. "df -h" → "df", "git log" → "git log")
        pattern = " ".join(parts[:2]) if len(parts) > 1 else parts[0]

        SHELL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log = json.loads(SHELL_LOG_PATH.read_text()) if SHELL_LOG_PATH.exists() else {"patterns": {}}
        entry = log["patterns"].setdefault(pattern, {"pattern": pattern, "count": 0, "agents": [], "examples": []})
        entry["count"] += 1
        if agent_id not in entry["agents"]:
            entry["agents"].append(agent_id)
        if len(entry["examples"]) < 5 and command not in entry["examples"]:
            entry["examples"].append(command[:200])
        SHELL_LOG_PATH.write_text(json.dumps(log, indent=2))
    except Exception:
        pass  # logging must never break the shell call
