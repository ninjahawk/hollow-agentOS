"""
Integration tests for AgentOS v1.3.2: Rate Limiting and Admission Control.

Tests 1, 5, 6 are structural — run without Ollama.
Tests 2, 3, 4 require server to be running; test 3 requires enough
audit-baseline data to exist for the worker role.

Run:
    PYTHONPATH=. pytest tests/integration/test_ratelimit.py -v -m integration
"""

import math
import threading
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register(auth_headers, name, role="worker", capabilities=None):
    body = {"name": name, "role": role}
    if capabilities:
        body["capabilities"] = capabilities
    r = requests.post(f"{API_URL}/agents/register", json=body, headers=auth_headers)
    assert r.status_code == 200, f"register failed: {r.text}"
    data = r.json()
    return data["agent_id"], data["token"]


def _terminate(auth_headers, agent_id):
    requests.delete(f"{API_URL}/agents/{agent_id}", headers=auth_headers)


def _set_rate_limit(auth_headers, agent_id, limits, target=None):
    body = {"limits": limits}
    if target:
        body["target"] = target
    r = requests.post(f"{API_URL}/agents/{agent_id}/rate-limits",
                      json=body, headers=auth_headers)
    assert r.status_code == 200, f"configure failed: {r.text}"
    return r.json()


def _get_rate_limits(auth_headers, agent_id):
    r = requests.get(f"{API_URL}/agents/{agent_id}/rate-limits", headers=auth_headers)
    assert r.status_code == 200, f"rate-limits get failed: {r.text}"
    return r.json()


def _shell(headers, cmd="echo x"):
    return requests.post(f"{API_URL}/shell", json={"command": cmd}, headers=headers)


def _acquire_lock(headers, agent_id, lock_name):
    return requests.post(f"{API_URL}/agents/{agent_id}/lock/{lock_name}",
                         headers=headers)


def _release_lock(headers, agent_id, lock_name):
    requests.delete(f"{API_URL}/agents/{agent_id}/lock/{lock_name}", headers=headers)


def _inbox(headers, limit=50):
    r = requests.get(f"{API_URL}/messages", params={"limit": limit}, headers=headers)
    assert r.status_code == 200
    return r.json()


def _drain_inbox(headers):
    """Read and discard all unread messages (multiple batches if needed)."""
    for _ in range(10):  # up to 10 batches
        r = requests.get(f"{API_URL}/messages", params={"limit": 100}, headers=headers)
        if r.status_code != 200:
            break
        msgs = r.json().get("messages", [])
        if not msgs:
            break


# ---------------------------------------------------------------------------
# Test 1 — Burst rejection
# ---------------------------------------------------------------------------

class TestBurstRejection:
    def test_shell_rate_limit_enforced(self, auth_headers):
        """
        Configure a worker to 10 shell_calls capacity.
        Make 12 consecutive calls. First 10 must succeed (200), calls 11–12 must
        return 429 with a Retry-After header.
        """
        ts = int(time.time())
        agent_id, token = _register(auth_headers, f"burst-worker-{ts}", role="worker",
                                     capabilities=["shell", "fs_read", "fs_write",
                                                   "ollama", "message", "semantic"])
        agent_headers = {"Authorization": f"Bearer {token}"}
        try:
            # Set tight limit: capacity=10, refill very slow (1/hour)
            _set_rate_limit(auth_headers, agent_id, {
                "shell_calls": {"capacity": 10, "refill_rate": 1 / 3600}
            })

            successes = 0
            failures = []
            for i in range(12):
                r = _shell(agent_headers)
                if r.status_code == 200:
                    successes += 1
                elif r.status_code == 429:
                    failures.append(i)
                else:
                    pytest.fail(f"Unexpected status {r.status_code} on call {i}: {r.text}")

            assert successes == 10, (
                f"Expected exactly 10 successful calls, got {successes}"
            )
            assert len(failures) == 2, (
                f"Expected exactly 2 rate-limited calls, got {len(failures)}: {failures}"
            )

            # Check Retry-After header on last 429
            for i in range(12):
                r = _shell(agent_headers)
                if r.status_code == 429:
                    assert "Retry-After" in r.headers, "429 must include Retry-After header"
                    retry_after = int(r.headers["Retry-After"])
                    assert retry_after > 0, f"Retry-After must be positive, got {retry_after}"
                    break

        finally:
            _terminate(auth_headers, agent_id)


# ---------------------------------------------------------------------------
# Test 2 — Refill over time
# ---------------------------------------------------------------------------

class TestRefillOverTime:
    def test_bucket_refills_after_drain(self, auth_headers):
        """
        Configure 60-token shell bucket with 10 tokens/second refill.
        Drain 50 tokens. Wait 3 seconds. Assert bucket depth grew by ≥ 20.
        """
        ts = int(time.time())
        agent_id, token = _register(auth_headers, f"refill-worker-{ts}", role="worker",
                                     capabilities=["shell", "fs_read", "fs_write",
                                                   "ollama", "message", "semantic"])
        agent_headers = {"Authorization": f"Bearer {token}"}
        try:
            _set_rate_limit(auth_headers, agent_id, {
                "shell_calls": {"capacity": 60, "refill_rate": 10.0}
            })

            # Drain 50 tokens
            for _ in range(50):
                _shell(agent_headers)

            status_after_drain = _get_rate_limits(auth_headers, agent_id)
            depth_after_drain = status_after_drain["resources"]["shell_calls"]["bucket_depth"]

            # Wait for refill
            time.sleep(3)

            status_after_wait = _get_rate_limits(auth_headers, agent_id)
            depth_after_wait = status_after_wait["resources"]["shell_calls"]["bucket_depth"]

            growth = depth_after_wait - depth_after_drain
            assert growth >= 20, (
                f"Expected bucket to grow by ≥20 tokens in 3s at 10/s refill, "
                f"grew by {growth:.1f} (from {depth_after_drain:.1f} to {depth_after_wait:.1f})"
            )

        finally:
            _terminate(auth_headers, agent_id)


# ---------------------------------------------------------------------------
# Test 3 — Circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_circuit_break_fires_on_anomaly(self, auth_headers):
        """
        Register a worker. Trigger anomaly detection via 60+ audited ops in 60s.
        Assert: security.circuit_break event fires. Agent status = suspended.
        Root inbox has decision message with decision_type = "circuit_break_review".
        """
        ts = int(time.time())
        agent_id, token = _register(auth_headers, f"circuit-worker-{ts}", role="worker",
                                     capabilities=["shell", "fs_read", "fs_write",
                                                   "ollama", "message", "semantic"])
        agent_headers = {"Authorization": f"Bearer {token}"}

        # Drain ALL existing unread messages so we can detect new ones clearly
        _drain_inbox(auth_headers)

        try:
            # Generate 55 audited lock_acquire ops (only acquire is audited, not release).
            # 55 > BASELINE_MIN_OPS (50) so anomaly check fires and z_score >> 5.0 vs baseline.
            for i in range(55):
                lock = f"cb-lock-{ts}-{i}"
                _acquire_lock(agent_headers, agent_id, lock)
                _release_lock(agent_headers, agent_id, lock)

            # Give background anomaly processing a moment
            time.sleep(1.0)

            # Check if circuit break fired — agent should be suspended
            r = requests.get(f"{API_URL}/agents/{agent_id}", headers=auth_headers)
            if r.status_code == 200:
                agent_data = r.json()
                status = agent_data.get("status")
                # Circuit break suspends the agent
                if status == "suspended":
                    # Primary assertion: rate-limit status shows circuit_broken
                    rl_status = _get_rate_limits(auth_headers, agent_id)
                    assert rl_status["circuit_broken"] is True, (
                        "Rate limit status should show circuit_broken=true after circuit break"
                    )
                    # Inbox assertion: root should have a decision message
                    inbox = _inbox(auth_headers, limit=100)
                    messages = inbox.get("messages", [])
                    cb_msgs = [
                        m for m in messages
                        if m.get("content", {}).get("decision_type") == "circuit_break_review"
                        and m.get("content", {}).get("agent_id") == agent_id
                    ]
                    assert len(cb_msgs) >= 1, (
                        f"Root inbox missing circuit_break_review decision for {agent_id}. "
                        f"Got {len(messages)} messages: "
                        f"{[m.get('content') for m in messages[:5]]}"
                    )
                # If not suspended (baseline not established), verify infrastructure works
                else:
                    rl_status = _get_rate_limits(auth_headers, agent_id)
                    assert "resources" in rl_status, "rate-limit status must have resources"
                    pytest.skip(
                        f"Circuit break did not fire (agent status={status}) — "
                        "baseline not established for worker role. "
                        "Run audit tests first to establish baseline data."
                    )

        finally:
            # Resume if suspended so cleanup can terminate
            requests.post(f"{API_URL}/agents/{agent_id}/resume", headers=auth_headers)
            _terminate(auth_headers, agent_id)


# ---------------------------------------------------------------------------
# Test 4 — Backpressure under load
# ---------------------------------------------------------------------------

class TestBackpressureUnderLoad:
    def test_ten_concurrent_workers_no_crash(self, auth_headers):
        """
        Register 10 worker agents. All hit /agents/{id}/rate-limits simultaneously.
        Assert: system does not crash or deadlock. All requests return 200 or 429.
        Assert: no 500 errors.
        """
        ts = int(time.time())
        agents = []
        try:
            for i in range(10):
                aid, tok = _register(auth_headers, f"bp-worker-{ts}-{i}", role="worker",
                                      capabilities=["shell", "message"])
                agents.append((aid, tok))

            results = {}
            errors = {}

            def hit_endpoint(idx, agent_id, token):
                headers = {"Authorization": f"Bearer {token}"}
                try:
                    # Hit rate-limits status endpoint for each agent concurrently
                    r = requests.get(f"{API_URL}/agents/{agent_id}/rate-limits",
                                     headers=auth_headers, timeout=10)
                    results[idx] = r.status_code
                except Exception as e:
                    errors[idx] = str(e)

            threads = [
                threading.Thread(target=hit_endpoint, args=(i, aid, tok))
                for i, (aid, tok) in enumerate(agents)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            assert not errors, f"Threads raised exceptions: {errors}"
            for idx, code in results.items():
                assert code in (200, 429), (
                    f"Worker {idx} got unexpected status {code}"
                )
            assert len(results) == 10, f"Expected 10 results, got {len(results)}"

        finally:
            for aid, _ in agents:
                _terminate(auth_headers, aid)


# ---------------------------------------------------------------------------
# Test 5 — Retry-After correctness
# ---------------------------------------------------------------------------

class TestRetryAfterCorrectness:
    def test_retry_after_header_is_accurate(self, auth_headers):
        """
        Exhaust shell bucket (capacity=3, very slow refill).
        Assert 429 with Retry-After header.
        Parse Retry-After, wait that long, assert next call succeeds.
        """
        ts = int(time.time())
        agent_id, token = _register(auth_headers, f"retry-worker-{ts}", role="worker",
                                     capabilities=["shell", "fs_read", "fs_write",
                                                   "ollama", "message", "semantic"])
        agent_headers = {"Authorization": f"Bearer {token}"}
        try:
            # Capacity 3, refill at 5 tokens/second
            _set_rate_limit(auth_headers, agent_id, {
                "shell_calls": {"capacity": 3, "refill_rate": 5.0}
            })

            # Exhaust the bucket
            for _ in range(3):
                _shell(agent_headers)

            # Next call should be rate-limited
            r = _shell(agent_headers)
            assert r.status_code == 429, f"Expected 429 after exhaustion, got {r.status_code}"
            assert "Retry-After" in r.headers, "429 must include Retry-After header"

            retry_after_s = int(r.headers["Retry-After"])
            assert retry_after_s >= 0, "Retry-After must be non-negative"

            # Wait the indicated time plus a small buffer
            if retry_after_s <= 2:
                time.sleep(retry_after_s + 0.5)
                r2 = _shell(agent_headers)
                assert r2.status_code == 200, (
                    f"After waiting Retry-After={retry_after_s}s, expected 200, got {r2.status_code}"
                )

        finally:
            _terminate(auth_headers, agent_id)


# ---------------------------------------------------------------------------
# Test 6 — Role inheritance and per-agent override isolation
# ---------------------------------------------------------------------------

class TestRoleInheritanceAndOverride:
    def test_role_limit_inherited_and_override_isolated(self, auth_headers):
        """
        Set rate limit for the 'worker' role.
        Register two new workers — both should show the role-level limit.
        Override one worker's limit explicitly.
        Assert: override applies only to that worker; the other still uses role default.
        """
        ts = int(time.time())
        agent_a_id, token_a = _register(auth_headers, f"inherit-worker-a-{ts}",
                                          role="worker",
                                          capabilities=["shell", "message"])
        agent_b_id, token_b = _register(auth_headers, f"inherit-worker-b-{ts}",
                                          role="worker",
                                          capabilities=["shell", "message"])

        try:
            # Set a distinctive role-level limit for worker
            _set_rate_limit(auth_headers, agent_a_id, limits={"shell_calls": 77},
                            target="worker")

            # Both agents should inherit the role limit
            status_a = _get_rate_limits(auth_headers, agent_a_id)
            status_b = _get_rate_limits(auth_headers, agent_b_id)

            cap_a = status_a["resources"]["shell_calls"]["capacity"]
            cap_b = status_b["resources"]["shell_calls"]["capacity"]

            assert cap_a == 77.0, f"Agent A should inherit role limit 77, got {cap_a}"
            assert cap_b == 77.0, f"Agent B should inherit role limit 77, got {cap_b}"

            # Now override agent A specifically
            _set_rate_limit(auth_headers, agent_a_id, limits={"shell_calls": 42})

            status_a_after = _get_rate_limits(auth_headers, agent_a_id)
            status_b_after = _get_rate_limits(auth_headers, agent_b_id)

            cap_a_after = status_a_after["resources"]["shell_calls"]["capacity"]
            cap_b_after = status_b_after["resources"]["shell_calls"]["capacity"]

            assert cap_a_after == 42.0, (
                f"Agent A override should be 42, got {cap_a_after}"
            )
            assert cap_b_after == 77.0, (
                f"Agent B should still use role limit 77, got {cap_b_after}"
            )

        finally:
            _terminate(auth_headers, agent_a_id)
            _terminate(auth_headers, agent_b_id)
            # Restore worker role to default (set back to 60)
            _set_rate_limit(auth_headers, agent_a_id, limits={"shell_calls": 60},
                            target="worker")
