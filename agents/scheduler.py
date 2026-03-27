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
import time
import uuid
import threading
import urllib.request
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

TASKS_PATH = Path("/agentOS/memory/tasks.json")
API_BASE = "http://localhost:7777"

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
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._load()

    def submit(
        self,
        description: str,
        submitted_by: str,
        complexity: int = 2,
        context: Optional[dict] = None,
        system_prompt: Optional[str] = None,
    ) -> Task:
        """Submit a task. Scheduler routes it to the right model and runs it."""
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

        # Run synchronously (async scheduling is a v0.5 problem)
        self._run_task(task, system_prompt)
        return task

    def _run_task(self, task: Task, system_prompt: Optional[str] = None):
        role = COMPLEXITY_ROUTING.get(task.complexity, "general")
        task.assigned_to = role
        task.status = "running"
        task.started_at = time.time()

        # Notify submitter
        self._bus.send(
            from_id="scheduler",
            to_id=task.submitted_by,
            content={"task_id": task.task_id, "status": "running", "model_role": role},
            msg_type="log",
        )

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
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

            task.result = {
                "response": resp.get("response", ""),
                "model": resp.get("model"),
                "tokens_in": resp.get("tokens_in"),
                "tokens_out": resp.get("tokens_out"),
                "ms": resp.get("ms"),
            }
            task.status = "done"

            # Update submitter's usage
            self._registry.update_usage(
                task.submitted_by,
                tokens_in=resp.get("tokens_in") or 0,
                tokens_out=resp.get("tokens_out") or 0,
            )

        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.result = None

        task.finished_at = time.time()

        with self._lock:
            self._save()

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

        # Register child
        child, child_token = self._registry.register(
            name=name,
            role=role,
            capabilities=capabilities,
            parent_id=parent_id,
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
        out = {tid: asdict(t) for tid, t in self._tasks.items()}
        TASKS_PATH.write_text(json.dumps(out, indent=2))
