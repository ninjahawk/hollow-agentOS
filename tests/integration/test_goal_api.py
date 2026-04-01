"""
Integration tests for AgentOS v3.12.1: Goal API.

Verifies that persistent goals can be created, listed, retrieved,
and updated via the live HTTP endpoints backed by PersistentGoalEngine.

Run:
    PYTHONPATH=. pytest tests/integration/test_goal_api.py -v
"""

import pytest
import uuid
import json

pytestmark = pytest.mark.integration

try:
    import httpx
    _cfg = json.loads(open("/agentOS/config.json").read())
    _TOKEN = _cfg["api"]["token"]
    _BASE = "http://localhost:7777"
    _r = httpx.get(f"{_BASE}/state",
                   headers={"Authorization": f"Bearer {_TOKEN}"}, timeout=3)
    API_REACHABLE = _r.status_code == 200
except Exception:
    API_REACHABLE = False
    _TOKEN = ""
    _BASE = "http://localhost:7777"

pytestmark = pytest.mark.skipif(
    not API_REACHABLE, reason="AgentOS API not reachable"
)


def _uid():
    return f"agent-{uuid.uuid4().hex[:8]}"


def _headers():
    return {"Authorization": f"Bearer {_TOKEN}"}


def _post(path, body):
    import httpx
    r = httpx.post(f"{_BASE}{path}", json=body, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def _get(path, params=None):
    import httpx
    r = httpx.get(f"{_BASE}{path}", params=params, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def _patch(path, body):
    import httpx
    r = httpx.patch(f"{_BASE}{path}", json=body, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- #
# Test 1 — create + retrieve
# --------------------------------------------------------------------------- #

class TestCreateAndRetrieve:
    def test_create_returns_goal_id(self):
        """POST /goals/{agent_id} returns a goal_id."""
        agent = _uid()
        result = _post(f"/goals/{agent}",
                       {"objective": "improve system performance", "priority": 7})
        assert result["ok"] is True
        assert result["goal_id"].startswith("goal-")

    def test_created_goal_is_retrievable(self):
        """GET /goals/{agent_id}/{goal_id} returns the created goal."""
        agent = _uid()
        created = _post(f"/goals/{agent}",
                        {"objective": "reduce latency", "priority": 5})
        goal_id = created["goal_id"]

        goal = _get(f"/goals/{agent}/{goal_id}")
        assert goal["goal_id"] == goal_id
        assert goal["agent_id"] == agent
        assert "latency" in goal["objective"]
        assert goal["status"] == "active"

    def test_goal_has_expected_fields(self):
        """Created goals have all required fields."""
        agent = _uid()
        created = _post(f"/goals/{agent}",
                        {"objective": "monitor health", "priority": 8})
        goal = _get(f"/goals/{agent}/{created['goal_id']}")

        for field in ["goal_id", "agent_id", "objective", "priority",
                      "status", "created_at", "updated_at", "metrics"]:
            assert field in goal, f"missing field: {field}"

    def test_get_nonexistent_goal_returns_404(self):
        """GET for unknown goal_id returns 404."""
        import httpx
        agent = _uid()
        r = httpx.get(f"{_BASE}/goals/{agent}/goal-doesnotexist",
                      headers=_headers(), timeout=5)
        assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Test 2 — list goals
# --------------------------------------------------------------------------- #

class TestListGoals:
    def test_list_empty_agent_returns_zero(self):
        """Fresh agent has no goals."""
        agent = _uid()
        result = _get(f"/goals/{agent}")
        assert result["count"] == 0
        assert result["goals"] == []

    def test_list_shows_created_goals(self):
        """After creating goals, list returns them."""
        agent = _uid()
        _post(f"/goals/{agent}", {"objective": "goal one", "priority": 5})
        _post(f"/goals/{agent}", {"objective": "goal two", "priority": 3})

        result = _get(f"/goals/{agent}")
        assert result["count"] >= 2
        objectives = [g["objective"] for g in result["goals"]]
        assert "goal one" in objectives
        assert "goal two" in objectives

    def test_list_count_matches_goals_length(self):
        """count field matches the length of goals array."""
        agent = _uid()
        for i in range(3):
            _post(f"/goals/{agent}", {"objective": f"task {i}", "priority": i + 1})

        result = _get(f"/goals/{agent}")
        assert result["count"] == len(result["goals"])


# --------------------------------------------------------------------------- #
# Test 3 — next focus goal
# --------------------------------------------------------------------------- #

class TestNextGoal:
    def test_next_returns_none_for_empty_agent(self):
        """GET /goals/{agent_id}/next returns null goal for empty agent."""
        agent = _uid()
        result = _get(f"/goals/{agent}/next")
        assert result["goal"] is None

    def test_next_returns_highest_priority(self):
        """next picks the highest-priority active goal."""
        agent = _uid()
        _post(f"/goals/{agent}", {"objective": "low priority task", "priority": 2})
        _post(f"/goals/{agent}", {"objective": "critical task", "priority": 9})
        _post(f"/goals/{agent}", {"objective": "medium task", "priority": 5})

        result = _get(f"/goals/{agent}/next")
        assert result["goal"] is not None
        assert result["goal"]["priority"] == 9


# --------------------------------------------------------------------------- #
# Test 4 — update status + progress
# --------------------------------------------------------------------------- #

class TestUpdate:
    def test_update_progress_metrics(self):
        """PATCH with metrics updates the goal's metrics dict."""
        agent = _uid()
        created = _post(f"/goals/{agent}",
                        {"objective": "optimize query speed", "priority": 6})
        goal_id = created["goal_id"]

        result = _patch(f"/goals/{agent}/{goal_id}",
                        {"metrics": {"steps_completed": 3, "progress": 0.3}})
        assert result["ok"] is True
        updated = _get(f"/goals/{agent}/{goal_id}")
        assert updated["metrics"].get("steps_completed") == 3

    def test_complete_goal_changes_status(self):
        """PATCH status=completed marks the goal completed."""
        agent = _uid()
        created = _post(f"/goals/{agent}",
                        {"objective": "deploy feature", "priority": 7})
        goal_id = created["goal_id"]

        result = _patch(f"/goals/{agent}/{goal_id}", {"status": "completed"})
        assert result["ok"] is True

        goal = _get(f"/goals/{agent}/{goal_id}")
        assert goal["status"] == "completed"

    def test_pause_goal_changes_status(self):
        """PATCH status=paused marks the goal paused."""
        agent = _uid()
        created = _post(f"/goals/{agent}",
                        {"objective": "background research", "priority": 3})
        goal_id = created["goal_id"]

        _patch(f"/goals/{agent}/{goal_id}", {"status": "paused"})
        goal = _get(f"/goals/{agent}/{goal_id}")
        assert goal["status"] == "paused"

    def test_invalid_status_returns_400(self):
        """PATCH with invalid status returns 400."""
        import httpx
        agent = _uid()
        created = _post(f"/goals/{agent}", {"objective": "test", "priority": 1})
        r = httpx.patch(f"{_BASE}/goals/{agent}/{created['goal_id']}",
                        json={"status": "invalid_status"},
                        headers=_headers(), timeout=5)
        assert r.status_code == 400
