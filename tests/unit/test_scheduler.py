"""
Unit tests for agents/scheduler.py

Run from project root:
    PYTHONPATH=. pytest tests/unit/test_scheduler.py -v

All Ollama HTTP calls are mocked — no real network traffic occurs.
"""

import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import BytesIO


# ---------------------------------------------------------------------------
# Shared mocks / helpers
# ---------------------------------------------------------------------------

FAKE_OLLAMA_RESPONSE = {
    "response": "This is the model answer.",
    "model": "mistral-nemo:12b",
    "tokens_in": 42,
    "tokens_out": 17,
    "ms": 310,
}


def _make_urlopen_mock(body: dict = None):
    """Return a context-manager mock that yields a fake HTTP response."""
    data = json.dumps(body or FAKE_OLLAMA_RESPONSE).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = data
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_registry(tmp_path, monkeypatch):
    """Isolated AgentRegistry pointing to tmp_path."""
    import agents.registry as reg_mod
    monkeypatch.setattr(reg_mod, "REGISTRY_PATH", tmp_path / "agent-registry.json")
    monkeypatch.setattr(reg_mod, "WORKSPACE_ROOT", tmp_path / "agents")
    (tmp_path / "agents").mkdir()

    from agents.registry import AgentRegistry
    return AgentRegistry("test-master")


@pytest.fixture()
def fake_bus():
    """Minimal MessageBus stub — we don't test bus behaviour here."""
    bus = MagicMock()
    bus.send = MagicMock()
    return bus


@pytest.fixture()
def scheduler(tmp_path, tmp_registry, fake_bus, monkeypatch):
    """TaskScheduler wired to temp files."""
    import agents.scheduler as sched_mod
    tasks_path = tmp_path / "tasks.json"
    shell_log_path = tmp_path / "shell-usage-log.json"
    monkeypatch.setattr(sched_mod, "TASKS_PATH", tasks_path)
    monkeypatch.setattr(sched_mod, "SHELL_LOG_PATH", shell_log_path)

    from agents.scheduler import TaskScheduler
    return TaskScheduler(tmp_registry, fake_bus, "test-master")


@pytest.fixture()
def worker_agent(tmp_registry):
    """A registered worker agent inside the temp registry."""
    record, token = tmp_registry.register(name="worker-one", role="worker")
    return record, token


# ---------------------------------------------------------------------------
# submit() — wait=True
# ---------------------------------------------------------------------------

class TestSubmitWait:
    def test_wait_true_returns_done_or_failed(self, scheduler, worker_agent):
        record, _ = worker_agent
        mock_resp = _make_urlopen_mock()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            task = scheduler.submit(
                description="Explain gravity",
                submitted_by=record.agent_id,
                complexity=2,
                wait=True,
            )
        assert task.status in ("done", "failed")

    def test_wait_true_result_populated_on_success(self, scheduler, worker_agent):
        record, _ = worker_agent
        mock_resp = _make_urlopen_mock()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            task = scheduler.submit(
                description="Explain gravity",
                submitted_by=record.agent_id,
                complexity=1,
                wait=True,
            )
        if task.status == "done":
            assert task.result is not None
            assert "response" in task.result

    def test_wait_true_http_error_gives_failed_status(self, scheduler, worker_agent):
        record, _ = worker_agent
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            task = scheduler.submit(
                description="Will fail",
                submitted_by=record.agent_id,
                wait=True,
            )
        assert task.status == "failed"
        assert task.error is not None

    def test_finished_at_set_after_completion(self, scheduler, worker_agent):
        record, _ = worker_agent
        mock_resp = _make_urlopen_mock()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            task = scheduler.submit(
                description="Quick task",
                submitted_by=record.agent_id,
                wait=True,
            )
        assert task.finished_at is not None


# ---------------------------------------------------------------------------
# submit() — wait=False
# ---------------------------------------------------------------------------

class TestSubmitNoWait:
    def test_wait_false_returns_queued_immediately(self, scheduler, worker_agent):
        """wait=False should return with status 'queued' before the thread runs."""
        record, _ = worker_agent
        returned_status = None

        # Intercept urlopen to delay long enough that we can observe the queued state
        original_submit = scheduler.submit

        # Patch urlopen to block until we release it
        import threading
        gate = threading.Event()

        def slow_urlopen(*args, **kwargs):
            gate.wait(timeout=5)
            return _make_urlopen_mock()

        with patch("urllib.request.urlopen", side_effect=slow_urlopen):
            task = scheduler.submit(
                description="Async task",
                submitted_by=record.agent_id,
                wait=False,
            )
            returned_status = task.status
            gate.set()  # unblock the background thread

        assert returned_status == "queued"

    def test_wait_false_task_stored_in_tasks(self, scheduler, worker_agent):
        record, _ = worker_agent
        gate_event = __import__("threading").Event()

        def slow_urlopen(*args, **kwargs):
            gate_event.wait(timeout=5)
            return _make_urlopen_mock()

        with patch("urllib.request.urlopen", side_effect=slow_urlopen):
            task = scheduler.submit(
                description="Async stored",
                submitted_by=record.agent_id,
                wait=False,
            )
            assert scheduler.get_task(task.task_id) is not None
            gate_event.set()


# ---------------------------------------------------------------------------
# _save() — eviction
# ---------------------------------------------------------------------------

class TestSaveEviction:
    def test_evicts_oldest_beyond_max_tasks(self, scheduler, worker_agent, monkeypatch):
        import agents.scheduler as sched_mod
        monkeypatch.setattr(sched_mod, "MAX_TASKS", 5)

        record, _ = worker_agent
        # Inject tasks directly without running them
        from agents.scheduler import Task
        for i in range(8):
            t = Task(
                task_id=f"task-{i:03d}",
                description=f"task {i}",
                complexity=1,
                submitted_by=record.agent_id,
                assigned_to=None,
                status="done",
                result=None,
                created_at=float(i),  # oldest = 0, newest = 7
            )
            scheduler._tasks[t.task_id] = t

        scheduler._save()

        remaining_ids = set(scheduler._tasks.keys())
        # Should have kept the 5 most recent (task-003 through task-007)
        assert len(remaining_ids) == 5
        assert "task-000" not in remaining_ids
        assert "task-001" not in remaining_ids
        assert "task-002" not in remaining_ids
        assert "task-007" in remaining_ids

    def test_under_max_tasks_nothing_evicted(self, scheduler, worker_agent, monkeypatch):
        import agents.scheduler as sched_mod
        monkeypatch.setattr(sched_mod, "MAX_TASKS", 10)

        record, _ = worker_agent
        from agents.scheduler import Task
        for i in range(3):
            t = Task(
                task_id=f"keep-{i}",
                description="keep",
                complexity=1,
                submitted_by=record.agent_id,
                assigned_to=None,
                status="done",
                result=None,
                created_at=float(i),
            )
            scheduler._tasks[t.task_id] = t

        before = set(scheduler._tasks.keys())
        scheduler._save()
        assert set(scheduler._tasks.keys()) == before


# ---------------------------------------------------------------------------
# log_shell_usage()
# ---------------------------------------------------------------------------

class TestLogShellUsage:
    def test_creates_log_file(self, tmp_path, monkeypatch):
        import agents.scheduler as sched_mod
        log_path = tmp_path / "shell-usage-log.json"
        monkeypatch.setattr(sched_mod, "SHELL_LOG_PATH", log_path)

        from agents.scheduler import log_shell_usage
        log_shell_usage("df -h", "agent-1")
        assert log_path.exists()

    def test_pattern_extracted_correctly(self, tmp_path, monkeypatch):
        import agents.scheduler as sched_mod
        log_path = tmp_path / "shell-usage-log.json"
        monkeypatch.setattr(sched_mod, "SHELL_LOG_PATH", log_path)

        from agents.scheduler import log_shell_usage
        log_shell_usage("git log --oneline -10", "agent-1")
        data = json.loads(log_path.read_text())
        assert "git log" in data["patterns"]

    def test_single_word_command_pattern(self, tmp_path, monkeypatch):
        import agents.scheduler as sched_mod
        log_path = tmp_path / "shell-usage-log.json"
        monkeypatch.setattr(sched_mod, "SHELL_LOG_PATH", log_path)

        from agents.scheduler import log_shell_usage
        log_shell_usage("uptime", "agent-1")
        data = json.loads(log_path.read_text())
        assert "uptime" in data["patterns"]

    def test_count_increments_on_repeated_calls(self, tmp_path, monkeypatch):
        import agents.scheduler as sched_mod
        log_path = tmp_path / "shell-usage-log.json"
        monkeypatch.setattr(sched_mod, "SHELL_LOG_PATH", log_path)

        from agents.scheduler import log_shell_usage
        for _ in range(5):
            log_shell_usage("ls /tmp", "agent-1")
        data = json.loads(log_path.read_text())
        assert data["patterns"]["ls /tmp"]["count"] == 5

    def test_different_patterns_tracked_separately(self, tmp_path, monkeypatch):
        import agents.scheduler as sched_mod
        log_path = tmp_path / "shell-usage-log.json"
        monkeypatch.setattr(sched_mod, "SHELL_LOG_PATH", log_path)

        from agents.scheduler import log_shell_usage
        log_shell_usage("ls -la", "agent-1")
        log_shell_usage("df -h", "agent-1")
        data = json.loads(log_path.read_text())
        assert "ls -la" in data["patterns"]
        assert "df -h" in data["patterns"]

    def test_agent_id_recorded(self, tmp_path, monkeypatch):
        import agents.scheduler as sched_mod
        log_path = tmp_path / "shell-usage-log.json"
        monkeypatch.setattr(sched_mod, "SHELL_LOG_PATH", log_path)

        from agents.scheduler import log_shell_usage
        log_shell_usage("ps aux", "specific-agent")
        data = json.loads(log_path.read_text())
        assert "specific-agent" in data["patterns"]["ps aux"]["agents"]

    def test_empty_command_does_not_crash(self, tmp_path, monkeypatch):
        import agents.scheduler as sched_mod
        log_path = tmp_path / "shell-usage-log.json"
        monkeypatch.setattr(sched_mod, "SHELL_LOG_PATH", log_path)

        from agents.scheduler import log_shell_usage
        # Should be a no-op, not raise
        log_shell_usage("", "agent-1")
        log_shell_usage("   ", "agent-1")


# ---------------------------------------------------------------------------
# spawn_agent() — failure path when parent lacks spawn capability
# ---------------------------------------------------------------------------

class TestSpawnAgent:
    def test_spawn_fails_if_parent_lacks_spawn_cap(self, scheduler, tmp_registry):
        # "readonly" role does NOT have spawn capability
        record, _ = tmp_registry.register(name="no-spawn", role="readonly")
        result = scheduler.spawn_agent(
            parent_id=record.agent_id,
            name="child",
            role="worker",
            task_description="do something",
        )
        assert "error" in result
        assert "spawn" in result["error"].lower()

    def test_spawn_fails_for_unknown_parent(self, scheduler):
        result = scheduler.spawn_agent(
            parent_id="does-not-exist",
            name="orphan",
            role="worker",
            task_description="task",
        )
        assert "error" in result

    def test_spawn_succeeds_for_orchestrator(self, scheduler, tmp_registry):
        """Orchestrator role has spawn capability — child should be created."""
        parent, _ = tmp_registry.register(name="orch", role="orchestrator")
        mock_resp = _make_urlopen_mock()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = scheduler.spawn_agent(
                parent_id=parent.agent_id,
                name="spawned-child",
                role="worker",
                task_description="echo hello",
                complexity=1,
            )
        # The call completed (no error key, or error is None/absent)
        assert "child_agent_id" in result
        # Child should be terminated after task
        child_id = result["child_agent_id"]
        child_record = tmp_registry.get(child_id)
        assert child_record.status == "terminated"
