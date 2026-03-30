"""
Unit tests for memory/manager.py

Run from project root:
    PYTHONPATH=. pytest tests/unit/test_manager.py -v
"""

import json
import time
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past_iso(seconds: float = 5.0) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(seconds=seconds))


def _future_iso(seconds: float = 5.0) -> str:
    return _iso(datetime.now(timezone.utc) + timedelta(seconds=seconds))


# ---------------------------------------------------------------------------
# Fixtures — redirect every module-level path to tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_memory_paths(tmp_path, monkeypatch):
    """
    Redirect all path constants in memory.manager to tmp_path so tests are
    fully isolated and never touch /agentOS/.
    """
    import memory.manager as mgr

    monkeypatch.setattr(mgr, "MEMORY_PATH", tmp_path)
    monkeypatch.setattr(mgr, "HANDOFF_PATH", tmp_path / "handoff.json")
    monkeypatch.setattr(mgr, "WORKSPACE_MAP", tmp_path / "workspace-map.json")
    monkeypatch.setattr(mgr, "SESSION_LOG", tmp_path / "session-log.json")
    monkeypatch.setattr(mgr, "TOOL_REGISTRY", tmp_path / "tool-registry.json")
    monkeypatch.setattr(mgr, "PROJECT_CONTEXT", tmp_path / "project-context.json")
    monkeypatch.setattr(mgr, "DECISIONS", tmp_path / "decisions-needed.json")
    monkeypatch.setattr(mgr, "TOKEN_TOTALS", tmp_path / "token-totals.json")
    monkeypatch.setattr(mgr, "STATE_HISTORY", tmp_path / "state-history.json")
    monkeypatch.setattr(mgr, "SPECS_STORE", tmp_path / "specs.json")
    # Also patch CONFIG_PATH so token_totals calls don't read real config
    monkeypatch.setattr(mgr, "CONFIG_PATH", tmp_path / "config.json")


# ---------------------------------------------------------------------------
# write_handoff / read_handoff
# ---------------------------------------------------------------------------

class TestHandoff:
    def test_write_creates_per_agent_file(self, tmp_path):
        from memory.manager import write_handoff
        write_handoff("agent-42", summary="Did things")
        per_agent = tmp_path / "handoff-agent-42.json"
        assert per_agent.exists()

    def test_write_also_creates_legacy_shared_file(self, tmp_path):
        from memory.manager import write_handoff
        write_handoff("agent-99", summary="Done")
        assert (tmp_path / "handoff.json").exists()

    def test_write_content_matches_read(self, tmp_path):
        from memory.manager import write_handoff, read_handoff
        write_handoff(
            "agent-01",
            summary="Summary text",
            in_progress=["task-A"],
            next_steps=["step-B"],
        )
        h = read_handoff("agent-01")
        assert h["summary"] == "Summary text"
        assert h["in_progress"] == ["task-A"]
        assert h["next_steps"] == ["step-B"]
        assert h["written_by"] == "agent-01"

    def test_read_none_agent_id_reads_shared_file(self, tmp_path):
        from memory.manager import write_handoff, read_handoff
        write_handoff("agent-shared", summary="Legacy summary")
        h = read_handoff(None)
        assert h is not None
        assert h["summary"] == "Legacy summary"

    def test_read_missing_agent_falls_back_to_shared(self, tmp_path):
        from memory.manager import write_handoff, read_handoff
        write_handoff("agent-x", summary="fallback")
        # agent-missing has no per-agent file, should fall back to shared
        h = read_handoff("agent-missing")
        assert h is not None
        assert h["summary"] == "fallback"

    def test_read_returns_none_when_no_file(self, tmp_path):
        from memory.manager import read_handoff
        assert read_handoff(None) is None
        assert read_handoff("nobody") is None

    def test_two_agents_do_not_overwrite_each_other(self, tmp_path):
        from memory.manager import write_handoff, read_handoff
        write_handoff("agent-A", summary="Alpha work")
        write_handoff("agent-B", summary="Beta work")

        ha = read_handoff("agent-A")
        hb = read_handoff("agent-B")
        assert ha["summary"] == "Alpha work"
        assert hb["summary"] == "Beta work"

    def test_handoff_contains_written_at(self, tmp_path):
        from memory.manager import write_handoff, read_handoff
        write_handoff("agent-ts", summary="Time test")
        h = read_handoff("agent-ts")
        assert "written_at" in h
        # Verify it parses as a valid ISO timestamp
        datetime.fromisoformat(h["written_at"])

    def test_path_traversal_sanitised(self, tmp_path):
        """agent_id with slashes should not escape MEMORY_PATH."""
        from memory.manager import write_handoff, _handoff_path
        path = _handoff_path("../../etc/passwd")
        # Must stay inside tmp_path (parent dir should be tmp_path)
        assert str(path).startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# record_state_snapshot / get_state_history
# ---------------------------------------------------------------------------

class TestStateHistory:
    def test_snapshot_appended(self, tmp_path):
        from memory.manager import record_state_snapshot, get_state_history
        record_state_snapshot({"load": 0.5})
        history = get_state_history()
        assert len(history) == 1
        assert history[0]["state"]["load"] == 0.5

    def test_snapshot_has_recorded_at(self, tmp_path):
        from memory.manager import record_state_snapshot, get_state_history
        record_state_snapshot({"x": 1})
        entry = get_state_history()[0]
        assert "recorded_at" in entry
        datetime.fromisoformat(entry["recorded_at"])

    def test_multiple_snapshots_accumulate(self, tmp_path):
        from memory.manager import record_state_snapshot, get_state_history
        for i in range(5):
            record_state_snapshot({"i": i})
        assert len(get_state_history()) == 5

    def test_get_history_since_filters(self, tmp_path):
        from memory.manager import record_state_snapshot, get_state_history
        record_state_snapshot({"phase": "old"})
        checkpoint = _now_iso()
        time.sleep(0.01)  # ensure timestamps differ
        record_state_snapshot({"phase": "new"})

        after = get_state_history(since=checkpoint)
        assert len(after) == 1
        assert after[0]["state"]["phase"] == "new"

    def test_get_history_since_future_returns_empty(self, tmp_path):
        from memory.manager import record_state_snapshot, get_state_history
        record_state_snapshot({"x": 1})
        future = _future_iso(60)
        assert get_state_history(since=future) == []

    def test_get_history_since_none_returns_all(self, tmp_path):
        from memory.manager import record_state_snapshot, get_state_history
        for i in range(3):
            record_state_snapshot({"n": i})
        assert len(get_state_history(since=None)) == 3

    def test_history_rolls_at_max(self, tmp_path):
        from memory.manager import record_state_snapshot, get_state_history
        import memory.manager as mgr
        limit = mgr.STATE_HISTORY_MAX
        for i in range(limit + 10):
            record_state_snapshot({"i": i})
        history = get_state_history()
        # Should be trimmed to at most STATE_HISTORY_MAX
        assert len(history) <= limit
        # The oldest should have been evicted — last entry should have i = limit+9
        assert history[-1]["state"]["i"] == limit + 9


# ---------------------------------------------------------------------------
# get_state_diff_since
# ---------------------------------------------------------------------------

class TestStateDiff:
    def test_diff_returns_only_changed_keys(self, tmp_path):
        from memory.manager import record_state_snapshot, get_state_diff_since
        record_state_snapshot({"load": 0.1, "mem": 1000, "stable": "same"})
        checkpoint = _now_iso()
        time.sleep(0.01)
        record_state_snapshot({"load": 0.9, "mem": 1000, "stable": "same"})

        diff = get_state_diff_since(checkpoint)
        assert "load" in diff
        assert diff["load"] == 0.9
        # mem and stable didn't change — they should not appear
        assert "mem" not in diff
        assert "stable" not in diff

    def test_diff_returns_empty_when_nothing_changed(self, tmp_path):
        from memory.manager import record_state_snapshot, get_state_diff_since
        state = {"x": 1, "y": 2}
        record_state_snapshot(state)
        checkpoint = _now_iso()
        time.sleep(0.01)
        record_state_snapshot(state)

        diff = get_state_diff_since(checkpoint)
        assert diff == {}

    def test_diff_returns_latest_when_no_baseline(self, tmp_path):
        """If `since` is earlier than all snapshots, returns the latest full state."""
        from memory.manager import record_state_snapshot, get_state_diff_since
        record_state_snapshot({"only": "snapshot"})
        far_past = _past_iso(1000)
        result = get_state_diff_since(far_past)
        # With no baseline, returns the latest snapshot unchanged
        assert result == {"only": "snapshot"}

    def test_diff_empty_history_returns_empty(self, tmp_path):
        from memory.manager import get_state_diff_since
        assert get_state_diff_since(_past_iso(10)) == {}

    def test_diff_new_key_appears_in_diff(self, tmp_path):
        from memory.manager import record_state_snapshot, get_state_diff_since
        record_state_snapshot({"existing": 1})
        checkpoint = _now_iso()
        time.sleep(0.01)
        record_state_snapshot({"existing": 1, "new_key": "appeared"})

        diff = get_state_diff_since(checkpoint)
        assert "new_key" in diff


# ---------------------------------------------------------------------------
# log_action (sanity — doesn't blow up, writes to session log)
# ---------------------------------------------------------------------------

class TestLogAction:
    def test_log_action_creates_session_log(self, tmp_path):
        from memory.manager import log_action
        log_action("test_event", {"detail": "value"})
        assert (tmp_path / "session-log.json").exists()

    def test_log_action_appends_entry(self, tmp_path):
        from memory.manager import log_action, get_recent_actions
        log_action("action_one")
        log_action("action_two")
        actions = get_recent_actions(10)
        assert any(a["action"] == "action_one" for a in actions)
        assert any(a["action"] == "action_two" for a in actions)
