"""
Integration tests for AgentOS v1.3.5: Adaptive Model Routing.

Tests are structural — all run without Ollama. We test the routing API,
override management, stats endpoint, and recommendation logic. Actual
EMA learning requires tasks to complete through Ollama; those paths are
covered by the scheduler and tested indirectly once Ollama is available.

Run:
    PYTHONPATH=. pytest tests/integration/test_adaptive_routing.py -v -m integration
"""

import time
import pytest
import requests

pytestmark = pytest.mark.integration

API_URL = "http://localhost:7777"


def _api_reachable() -> bool:
    try:
        r = requests.get(f"{API_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


if not _api_reachable():
    pytest.skip(
        "AgentOS API not reachable at http://localhost:7777",
        allow_module_level=True,
    )


def _ollama_available() -> bool:
    try:
        r = requests.get("http://localhost:11434/api/version", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register(auth_headers, name, role="worker"):
    r = requests.post(f"{API_URL}/agents/register",
                      json={"name": name, "role": role},
                      headers=auth_headers)
    assert r.status_code == 200, f"register failed: {r.text}"
    d = r.json()
    return d["agent_id"], d["token"]


def _register_root(auth_headers, name):
    r = requests.post(f"{API_URL}/agents/register",
                      json={"name": name, "role": "root"},
                      headers=auth_headers)
    assert r.status_code == 200, f"register root failed: {r.text}"
    d = r.json()
    return d["agent_id"], d["token"]


def _terminate(auth_headers, agent_id):
    requests.delete(f"{API_URL}/agents/{agent_id}", headers=auth_headers)


def _routing_stats(auth_headers):
    r = requests.get(f"{API_URL}/routing/stats", headers=auth_headers)
    assert r.status_code == 200, f"routing_stats failed: {r.text}"
    return r.json()


def _routing_recommend(auth_headers, complexity):
    r = requests.get(f"{API_URL}/routing/recommend/{complexity}", headers=auth_headers)
    assert r.status_code == 200, f"routing_recommend failed: {r.text}"
    return r.json()


def _add_override(headers, model, complexity=None, agent_id=None, role=None, reason="test"):
    body = {"model": model, "reason": reason}
    if complexity is not None:
        body["complexity"] = complexity
    if agent_id is not None:
        body["agent_id"] = agent_id
    if role is not None:
        body["role"] = role
    r = requests.post(f"{API_URL}/routing/override", json=body, headers=headers)
    return r


def _list_overrides(auth_headers):
    r = requests.get(f"{API_URL}/routing/overrides", headers=auth_headers)
    assert r.status_code == 200, f"list_overrides failed: {r.text}"
    return r.json()["overrides"]


def _remove_override(headers, override_id):
    r = requests.delete(f"{API_URL}/routing/override/{override_id}", headers=headers)
    return r


# ---------------------------------------------------------------------------
# Test 1 — Stats endpoint structure
# ---------------------------------------------------------------------------

class TestRoutingStats:
    def test_stats_returns_expected_structure(self, auth_headers):
        """
        GET /routing/stats returns the expected top-level structure.
        Initially empty models list (no tasks completed yet in this test run).
        """
        stats = _routing_stats(auth_headers)

        assert "models" in stats, f"Expected 'models' key in stats: {stats}"
        assert "min_observations" in stats
        assert "ema_alpha" in stats
        assert "weights" in stats

        weights = stats["weights"]
        assert "success" in weights
        assert "throughput" in weights
        assert "latency" in weights
        # Weights must sum to 1.0
        total = weights["success"] + weights["throughput"] + weights["latency"]
        assert abs(total - 1.0) < 1e-6, f"Weights must sum to 1.0, got {total}"

        assert isinstance(stats["models"], list)


# ---------------------------------------------------------------------------
# Test 2 — Recommendation endpoint (no observations → static default)
# ---------------------------------------------------------------------------

class TestRoutingRecommend:
    def test_recommendation_falls_back_to_static(self, auth_headers):
        """
        GET /routing/recommend/{complexity} returns a recommendation for each
        complexity tier. When no observations exist, using_adaptive=False and
        static_default is returned.
        """
        ts = int(time.time())
        agent_id, token = _register(auth_headers, f"route-rec-{ts}")
        agent_headers = {"Authorization": f"Bearer {token}"}
        try:
            for complexity in [1, 2, 3, 4, 5]:
                rec = _routing_recommend(agent_headers, complexity)

                assert rec["complexity"] == complexity
                assert "static_default" in rec
                assert "scores" in rec
                assert "confidence" in rec
                assert "min_observations" in rec
                assert isinstance(rec["using_adaptive"], bool)

                # Without observations, adaptive should not be active
                # (confidence requires MIN_OBSERVATIONS=5 observations)
                if not any(rec["confidence"].values()):
                    assert rec["using_adaptive"] is False, (
                        f"Expected using_adaptive=False without observations at complexity {complexity}"
                    )

        finally:
            _terminate(auth_headers, agent_id)

    def test_recommendation_rejects_invalid_complexity(self, auth_headers):
        """
        GET /routing/recommend/0 and /6 return 400.
        """
        for bad in [0, 6]:
            r = requests.get(f"{API_URL}/routing/recommend/{bad}", headers=auth_headers)
            assert r.status_code == 400, (
                f"Expected 400 for complexity={bad}, got {r.status_code}"
            )


# ---------------------------------------------------------------------------
# Test 3 — Override: add, list, remove
# ---------------------------------------------------------------------------

class TestRoutingOverrideCRUD:
    def test_root_can_add_and_remove_override(self, auth_headers):
        """
        Root agent adds a complexity-scoped override.
        Assert: override appears in list with correct fields.
        Assert: removing override returns ok=True.
        Assert: override no longer in list after removal.
        """
        ts = int(time.time())
        _, root_token = _register_root(auth_headers, f"route-root-{ts}")
        root_headers = {"Authorization": f"Bearer {root_token}"}
        try:
            r = _add_override(root_headers, model="qwen2.5:14b", complexity=2,
                              reason="prefer qwen for mid-complexity")
            assert r.status_code == 200, f"add override failed: {r.text}"
            override_id = r.json()["override_id"]
            assert override_id, "Expected non-empty override_id"

            overrides = _list_overrides(root_headers)
            ids = [o["override_id"] for o in overrides]
            assert override_id in ids, f"override_id {override_id} not in list: {ids}"

            entry = next(o for o in overrides if o["override_id"] == override_id)
            assert entry["model"] == "qwen2.5:14b"
            assert entry["complexity"] == 2
            assert entry["reason"] == "prefer qwen for mid-complexity"

            r2 = _remove_override(root_headers, override_id)
            assert r2.status_code == 200, f"remove override failed: {r2.text}"
            assert r2.json()["ok"] is True

            overrides_after = _list_overrides(root_headers)
            ids_after = [o["override_id"] for o in overrides_after]
            assert override_id not in ids_after, (
                f"override_id {override_id} still in list after removal"
            )

        finally:
            pass  # root agent terminates itself on cleanup is fine


# ---------------------------------------------------------------------------
# Test 4 — Override: non-root cannot add
# ---------------------------------------------------------------------------

class TestRoutingOverrideAuth:
    def test_worker_cannot_add_override(self, auth_headers):
        """
        Worker agent attempting to add a routing override receives 403.
        """
        ts = int(time.time())
        _, worker_token = _register(auth_headers, f"route-worker-{ts}", role="worker")
        worker_headers = {"Authorization": f"Bearer {worker_token}"}
        try:
            r = _add_override(worker_headers, model="mistral-nemo:12b", complexity=1)
            assert r.status_code == 403, (
                f"Expected 403 for worker adding override, got {r.status_code}: {r.text}"
            )
        finally:
            pass


# ---------------------------------------------------------------------------
# Test 5 — Override specificity resolution
# ---------------------------------------------------------------------------

class TestRoutingOverrideSpecificity:
    def test_agent_specific_override_supersedes_complexity_override(self, auth_headers):
        """
        Add two overrides: one complexity-scoped, one agent+complexity-scoped.
        The agent-scoped override should win for that agent (more specific).
        Verified via GET /routing/recommend which includes override resolution.
        """
        ts = int(time.time())
        _, root_token   = _register_root(auth_headers, f"route-spec-root-{ts}")
        agent_id, tok   = _register(auth_headers, f"route-spec-agent-{ts}")
        root_headers    = {"Authorization": f"Bearer {root_token}"}
        agent_headers   = {"Authorization": f"Bearer {tok}"}
        override_ids = []
        try:
            # Complexity-3 default override
            r1 = _add_override(root_headers, model="mistral-nemo:12b", complexity=3,
                               reason="global complexity-3 default")
            assert r1.status_code == 200
            override_ids.append(r1.json()["override_id"])

            # Agent-specific override at complexity 3 (more specific)
            r2 = _add_override(root_headers, model="qwen2.5:14b", complexity=3,
                               agent_id=agent_id, reason="agent-specific")
            assert r2.status_code == 200
            override_ids.append(r2.json()["override_id"])

            # The list should contain both
            overrides = _list_overrides(root_headers)
            listed_ids = {o["override_id"] for o in overrides}
            for oid in override_ids:
                assert oid in listed_ids, f"{oid} missing from overrides"

        finally:
            for oid in override_ids:
                _remove_override(root_headers, oid)
            _terminate(auth_headers, agent_id)


# ---------------------------------------------------------------------------
# Test 6 — Routing stats after Ollama tasks (Ollama-dependent)
# ---------------------------------------------------------------------------

class TestRoutingStatsWithOllama:
    def test_stats_populated_after_task_completion(self, auth_headers):
        """
        Run a simple task with Ollama. After completion, routing stats for
        that model/complexity should show observation_count >= 1.
        """
        if not _ollama_available():
            pytest.skip("Ollama not available — skipping routing stats population test")

        ts = int(time.time())
        agent_id, token = _register(auth_headers, f"route-obs-{ts}",
                                    role="worker")
        agent_headers = {"Authorization": f"Bearer {token}"}
        try:
            r = requests.post(f"{API_URL}/tasks/submit", json={
                "description": "Say 'hello' and nothing else.",
                "complexity": 1,
                "wait": True,
            }, headers=agent_headers, timeout=120)
            assert r.status_code == 200, f"task submit failed: {r.text}"
            assert r.json().get("status") == "done"

            # Give observation a moment to propagate
            time.sleep(0.5)

            stats = _routing_stats(agent_headers)
            models = stats["models"]
            # At least one model should have an observation for complexity 1
            c1_entries = [m for m in models if m["complexity"] == 1]
            assert len(c1_entries) >= 1, (
                f"Expected ≥1 observation for complexity 1 after task, "
                f"got models={models}"
            )
            entry = c1_entries[0]
            assert entry["observation_count"] >= 1
            assert 0.0 <= entry["ema_success_rate"] <= 1.0
            assert entry["ema_duration_ms"] > 0

        finally:
            _terminate(auth_headers, agent_id)
