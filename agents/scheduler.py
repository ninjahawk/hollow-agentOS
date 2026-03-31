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

Task priority (v0.9.0):
    0 — URGENT    (preempts BACKGROUND workers)
    1 — NORMAL    (default)
    2 — BACKGROUND (evicted first under VRAM pressure)
"""

import heapq
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

TASKS_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "tasks.json"
SHELL_LOG_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "shell-usage-log.json"
API_BASE = "http://localhost:7777"
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Emit task.token_chunk event every N tokens during streaming
STREAM_CHUNK_EVENT_EVERY = 10
# Emit task.partial_available event every N seconds during streaming
STREAM_PARTIAL_INTERVAL = 0.5

# Keep at most this many tasks in memory/disk to prevent unbounded growth
MAX_TASKS = 500

# Priority constants
PRIORITY_URGENT     = 0
PRIORITY_NORMAL     = 1
PRIORITY_BACKGROUND = 2

# Complexity → Ollama role → model (fallback when ModelManager unavailable)
COMPLEXITY_ROUTING = {
    1: "general",          # mistral-nemo:12b — fast, cheap
    2: "general",
    3: "code",             # qwen2.5:14b — better reasoning
    4: "code",
    5: "reasoning",        # qwen3.5-35b-moe — deep reasoning
}

ROLE_MODEL = {
    "general":   "mistral-nemo:12b",
    "code":      "qwen2.5:14b",
    "reasoning": "qwen3.5-35b-moe:latest",
}

# Estimated tokens per complexity level (for budget pre-check)
COMPLEXITY_TOKEN_ESTIMATE = {
    1: 500,
    2: 1_000,
    3: 3_000,
    4: 8_000,
    5: 20_000,
}

_NUM_WORKERS = 4


@dataclass
class Task:
    task_id: str
    description: str
    complexity: int          # 1-5
    submitted_by: str        # agent_id
    assigned_to: Optional[str]   # agent_id or model name
    status: str              # queued | running | done | failed | checkpointed
    result: Optional[dict]
    created_at: float
    priority: int = PRIORITY_NORMAL   # 0=URGENT, 1=NORMAL, 2=BACKGROUND
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    context: dict = field(default_factory=dict)  # extra data passed to model
    # v1.3.0: lineage and dependency tracking
    depends_on: list = field(default_factory=list)   # task_ids this task waits for
    parent_task_id: Optional[str] = None             # task that caused this task to be submitted
    # v1.3.1: streaming
    stream_enabled: bool = False         # True → use streaming Ollama API
    partial_output: str = ""             # accumulated text as chunks arrive
    cancelled: bool = False              # set True to abort a streaming task


# ---------------------------------------------------------------------------
# Priority Task Queue
# ---------------------------------------------------------------------------

class PriorityTaskQueue:
    """
    Min-heap priority queue. Lower priority value = higher urgency.
    Supports URGENT preemption: when an URGENT task arrives and all workers
    are busy with BACKGROUND tasks, the oldest BACKGROUND task is checkpointed
    and its worker is freed.
    """

    def __init__(self):
        self._heap: list[tuple] = []   # (priority, seq, task_id)
        self._seq = 0
        self._cond = threading.Condition(threading.Lock())
        self._pending: dict[str, Task] = {}     # task_id → Task (queued)
        self._running: dict[str, Task] = {}     # task_id → Task (running)
        self._closed = False

    def put(self, task: Task) -> None:
        with self._cond:
            self._pending[task.task_id] = task
            heapq.heappush(self._heap, (task.priority, self._seq, task.task_id))
            self._seq += 1
            self._cond.notify()

    def get(self, timeout: float = 1.0) -> Optional[Task]:
        """Block until a task is available or timeout elapses."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._heap and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            if not self._heap:
                return None
            _, _, task_id = heapq.heappop(self._heap)
            task = self._pending.pop(task_id, None)
            if task:
                self._running[task_id] = task
            return task

    def mark_done(self, task_id: str) -> None:
        with self._cond:
            self._running.pop(task_id, None)

    def checkpoint_oldest_background(self) -> Optional[Task]:
        """
        Find the oldest running BACKGROUND task and checkpoint it (re-queue it).
        Returns the checkpointed task so the caller can free its worker.
        """
        with self._cond:
            bg_tasks = [
                t for t in self._running.values()
                if t.priority == PRIORITY_BACKGROUND and t.status == "running"
            ]
            if not bg_tasks:
                return None
            oldest = min(bg_tasks, key=lambda t: t.started_at or t.created_at)
            oldest.status = "checkpointed"
            self._running.pop(oldest.task_id, None)
            # Re-queue for later
            self._pending[oldest.task_id] = oldest
            heapq.heappush(self._heap, (oldest.priority, self._seq, oldest.task_id))
            self._seq += 1
            self._cond.notify()
            return oldest

    def running_count(self) -> int:
        with self._cond:
            return len(self._running)

    def queue_depth_by_priority(self) -> dict:
        with self._cond:
            depth = {0: 0, 1: 0, 2: 0}
            for p, _, _ in self._heap:
                depth[p] = depth.get(p, 0) + 1
            return depth

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()


# ---------------------------------------------------------------------------
# TaskScheduler
# ---------------------------------------------------------------------------

class TaskScheduler:
    def __init__(self, registry, bus, master_token: str):
        self._registry = registry
        self._bus = bus
        self._master_token = master_token
        self._event_bus = None
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._queue = PriorityTaskQueue()
        self._workers: list[threading.Thread] = []
        self._model_manager = None   # injected after startup
        self._checkpoint_manager = None  # injected after startup (v1.3.3)
        self._load()
        self._start_workers()

    def set_event_bus(self, event_bus) -> None:
        """Inject EventBus after both are created. Called at server startup."""
        self._event_bus = event_bus
        if self._model_manager:
            self._model_manager.set_event_bus(event_bus)

    def set_model_manager(self, model_manager) -> None:
        """Inject ModelManager. Called at server startup."""
        self._model_manager = model_manager
        if self._event_bus:
            self._model_manager.set_event_bus(self._event_bus)

    def set_checkpoint_manager(self, checkpoint_manager) -> None:
        """Inject CheckpointManager. Called at server startup (v1.3.3)."""
        self._checkpoint_manager = checkpoint_manager

    def _maybe_auto_checkpoint(self, task: "Task") -> None:
        """
        Auto-checkpoint the submitting agent if the task ran for > 30 seconds.
        Called after task completion (both streaming and non-streaming).
        """
        if not self._checkpoint_manager:
            return
        if not task.started_at or not task.finished_at:
            return
        duration = task.finished_at - task.started_at
        if duration < 30.0:
            return
        try:
            self._checkpoint_manager.save(
                task.submitted_by,
                label=f"auto:task:{task.task_id}",
            )
        except Exception:
            pass

    def _start_workers(self) -> None:
        for i in range(_NUM_WORKERS):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"hollow-task-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)

    def _worker_loop(self) -> None:
        while True:
            task = self._queue.get(timeout=1.0)
            if task is None:
                continue
            if task.status == "checkpointed":
                # Re-queued after preemption — reset so it runs properly
                task.status = "queued"
            if task.stream_enabled:
                self._run_task_streaming(task)
            else:
                self._run_task(task)
            self._queue.mark_done(task.task_id)

    def submit(
        self,
        description: str,
        submitted_by: str,
        complexity: int = 2,
        context: Optional[dict] = None,
        system_prompt: Optional[str] = None,
        wait: bool = True,
        priority: int = PRIORITY_NORMAL,
        depends_on: Optional[list] = None,
        parent_task_id: Optional[str] = None,
        stream: bool = False,
    ) -> Task:
        """
        Submit a task. Scheduler routes it to the right model and runs it.

        priority: 0=URGENT (may preempt BACKGROUND), 1=NORMAL, 2=BACKGROUND
        wait=True (default): blocks until the task completes, returns final Task.
        wait=False: submits and returns immediately with status='queued'.
        """
        complexity = max(1, min(5, complexity))
        priority = max(0, min(2, priority))
        task = Task(
            task_id=str(uuid.uuid4())[:12],
            description=description,
            complexity=complexity,
            submitted_by=submitted_by,
            assigned_to=None,
            status="queued",
            result=None,
            created_at=time.time(),
            priority=priority,
            context=context or {},
            depends_on=depends_on or [],
            parent_task_id=parent_task_id,
            stream_enabled=stream,
        )

        with self._lock:
            self._tasks[task.task_id] = task
            self._save()

        if self._event_bus:
            self._event_bus.emit("task.queued", task.submitted_by, {
                "task_id":      task.task_id,
                "complexity":   task.complexity,
                "priority":     task.priority,
                "submitted_by": task.submitted_by,
            })

        # URGENT preemption: if all workers busy with BACKGROUND tasks, free one
        if priority == PRIORITY_URGENT:
            running = self._queue.running_count()
            if running >= _NUM_WORKERS:
                checkpointed = self._queue.checkpoint_oldest_background()
                if checkpointed and self._event_bus:
                    self._event_bus.emit("task.checkpointed", checkpointed.submitted_by, {
                        "task_id":    checkpointed.task_id,
                        "reason":     "urgent_preemption",
                    })

        self._queue.put(task)

        # stream=True always returns immediately (non-blocking)
        if wait and not stream:
            # Poll until the task leaves queued/running state
            while task.status in ("queued", "running", "checkpointed"):
                time.sleep(0.05)

        return task

    def _run_task(self, task: Task, system_prompt: Optional[str] = None,
                  standards_context: Optional[str] = None):
        role = COMPLEXITY_ROUTING.get(task.complexity, "general")

        # v0.9.0: VRAM-aware model selection
        if self._model_manager:
            model_for_role = self._model_manager.recommend(task.complexity)
        else:
            model_for_role = ROLE_MODEL.get(role, "mistral-nemo:12b")

        # Model policy check — does the submitting agent allow this role?
        agent = self._registry.get(task.submitted_by)
        if agent and not self._registry.check_model_policy(task.submitted_by, model_for_role, "ollama"):
            task.status = "failed"
            task.error = f"Agent model policy blocks use of '{model_for_role}' for role '{role}'"
            task.finished_at = time.time()
            with self._lock:
                self._save()
            return

        task.assigned_to = model_for_role
        task.status = "running"
        task.started_at = time.time()

        if self._event_bus:
            self._event_bus.emit("task.started", task.submitted_by, {
                "task_id":    task.task_id,
                "model_role": role,
                "model":      model_for_role,
                "priority":   task.priority,
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
                "model": model_for_role,
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
                "model":     resp.get("model", model_for_role),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "ms":        ms,
            }
            task.status = "done"

            # Update LRU timestamp in model manager
            if self._model_manager:
                self._model_manager.mark_used(model_for_role)

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

        # Auto-checkpoint if task took > AUTO_CHECKPOINT_TASK_SECONDS (v1.3.3)
        self._maybe_auto_checkpoint(task)

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

    def _run_task_streaming(self, task: Task) -> None:
        """
        Run a task using the Ollama streaming API.
        Accumulates chunks in task.partial_output.
        Emits task.token_chunk and task.partial_available events.
        Respects task.cancelled to abort mid-stream.
        """
        role = COMPLEXITY_ROUTING.get(task.complexity, "general")
        if self._model_manager:
            model_for_role = self._model_manager.recommend(task.complexity)
        else:
            model_for_role = ROLE_MODEL.get(role, "mistral-nemo:12b")

        agent = self._registry.get(task.submitted_by)
        if agent and not self._registry.check_model_policy(task.submitted_by, model_for_role, "ollama"):
            task.status = "failed"
            task.error = f"Agent model policy blocks use of '{model_for_role}' for role '{role}'"
            task.finished_at = time.time()
            with self._lock:
                self._save()
            return

        task.assigned_to = model_for_role
        task.status = "running"
        task.started_at = time.time()

        if self._event_bus:
            self._event_bus.emit("task.started", task.submitted_by, {
                "task_id":    task.task_id,
                "model_role": role,
                "model":      model_for_role,
                "priority":   task.priority,
                "stream":     True,
            })

        self._bus.send(
            from_id="scheduler",
            to_id=task.submitted_by,
            content={"task_id": task.task_id, "status": "running", "model_role": role, "stream": True},
            msg_type="log",
        )

        messages = []
        try:
            from agents.standards import get_relevant_standards_text
            auto_std = get_relevant_standards_text(task.description)
            if auto_std:
                messages.append({"role": "system", "content": auto_std})
        except Exception:
            pass
        if task.context.get("history"):
            messages.extend(task.context["history"])
        messages.append({"role": "user", "content": task.description})

        body = json.dumps({
            "model": model_for_role,
            "messages": messages,
            "stream": True,
        }).encode()

        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        tokens_so_far = 0
        last_partial_event = time.time()

        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                for raw_line in r:
                    if task.cancelled:
                        break
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue

                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        with self._lock:
                            task.partial_output += chunk
                        tokens_so_far += 1

                        if self._event_bus and tokens_so_far % STREAM_CHUNK_EVENT_EVERY == 0:
                            self._event_bus.emit("task.token_chunk", task.submitted_by, {
                                "task_id":      task.task_id,
                                "chunk":        chunk,
                                "tokens_so_far": tokens_so_far,
                                "model":        model_for_role,
                            })

                        now = time.time()
                        if now - last_partial_event >= STREAM_PARTIAL_INTERVAL:
                            if self._event_bus:
                                self._event_bus.emit("task.partial_available", task.submitted_by, {
                                    "task_id":       task.task_id,
                                    "partial_length": len(task.partial_output),
                                    "tokens_so_far": tokens_so_far,
                                })
                            last_partial_event = now

                    if data.get("done"):
                        tokens_in  = data.get("prompt_eval_count", 0)
                        tokens_out = data.get("eval_count", tokens_so_far)
                        ms = round(data.get("total_duration", 0) / 1_000_000, 1)
                        task.result = {
                            "response":   task.partial_output,
                            "model":      model_for_role,
                            "tokens_in":  tokens_in,
                            "tokens_out": tokens_out,
                            "ms":         ms,
                        }
                        task.status = "cancelled" if task.cancelled else "done"
                        self._registry.update_usage(task.submitted_by, tokens_in=tokens_in, tokens_out=tokens_out)
                        if self._model_manager:
                            self._model_manager.mark_used(model_for_role)
                        break

            # Stream ended without done=True (e.g., network cut or cancel)
            if task.status == "running":
                task.result = {
                    "response":   task.partial_output,
                    "model":      model_for_role,
                    "tokens_in":  0,
                    "tokens_out": tokens_so_far,
                    "ms":         None,
                }
                task.status = "cancelled" if task.cancelled else "done"

        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.result = None

        task.finished_at = time.time()

        # Auto-checkpoint if task took > AUTO_CHECKPOINT_TASK_SECONDS (v1.3.3)
        self._maybe_auto_checkpoint(task)

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
                    "stream":     True,
                })
            elif task.status == "cancelled":
                self._event_bus.emit("task.cancelled", task.submitted_by, {
                    "task_id": task.task_id,
                })
            else:
                self._event_bus.emit("task.failed", task.submitted_by, {
                    "task_id": task.task_id,
                    "error":   task.error,
                })

        self._bus.send(
            from_id="scheduler",
            to_id=task.submitted_by,
            content={
                "task_id": task.task_id,
                "status":  task.status,
                "result":  task.result,
                "error":   task.error,
            },
            msg_type="result",
        )
        self._registry.set_task(task.submitted_by, None)

    def cancel(self, task_id: str) -> dict:
        """
        Cancel a task. If queued, remove it. If running (streaming), set cancelled flag.
        Returns {ok: bool, status: str}.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "task not found"}
            if task.status in ("done", "failed", "cancelled"):
                return {"ok": False, "error": f"task already {task.status}"}
            if task.status in ("queued", "checkpointed"):
                task.status = "cancelled"
                task.finished_at = time.time()
                task.cancelled = True
                self._save()
                return {"ok": True, "status": "cancelled"}
            # running — set flag, streaming worker checks it
            task.cancelled = True
            return {"ok": True, "status": "cancelling"}

    def spawn_agent(
        self,
        parent_id: str,
        name: str,
        role: str,
        task_description: str,
        complexity: int = 2,
        capabilities: Optional[list[str]] = None,
        priority: int = PRIORITY_NORMAL,
        parent_task_id: Optional[str] = None,
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
            parent_task_id=parent_task_id,
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
            priority=priority,
            parent_task_id=parent_task_id,
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

    def queue_status(self) -> dict:
        """Return priority queue depth and running task count."""
        return {
            "running":        self._queue.running_count(),
            "workers":        _NUM_WORKERS,
            "queue_by_priority": self._queue.queue_depth_by_priority(),
        }

    def _load(self):
        if TASKS_PATH.exists():
            try:
                data = json.loads(TASKS_PATH.read_text())
                now = time.time()
                for d in data.values():
                    d.setdefault("priority", PRIORITY_NORMAL)
                    t = Task(**d)
                    # Running/queued tasks from before restart can never complete —
                    # their worker threads are gone. Mark them failed on reload.
                    if t.status in ("running", "queued", "checkpointed"):
                        t.status = "failed"
                        t.error = "server_restart"
                        t.finished_at = now
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
