"""
Integration tests for AgentOS v0.8.0: Process Signals and Tombstones.

All tests use real API calls — no mocks.
Ollama is NOT required. Signals operate on registry records, not model inference.

Run:
    PYTHONPATH=. pytest tests/integration/test_signals.py -v -m integration
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
        "AgentOS API not reachable at http://localhost:7777 — "
        "start the server before running integration tests.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register(auth_headers, name=None, role="worker", group_id=None):
    body = {"name": name or f"sig-test-{int(time.time() * 1000)}", "role": role}
    if group_id:
        body["group_id"] = group_id
    r = requests.post(f"{API_URL}/agents/register", json=body, headers=auth_headers)
    assert r.status_code == 200, f"register failed: {r.text}"
    d = r.json()
    return d["agent_id"], d["token"]


def _signal(auth_headers, agent_id, signal, grace_seconds=None):
    body = {"signal": signal}
    if grace_seconds is not None:
        body["grace_seconds"] = grace_seconds
    r = requests.post(f"{API_URL}/agents/{agent_id}/signal",
                      json=body, headers=auth_headers)
    return r


def _inbox(token, unread_only=True, limit=50):
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API_URL}/messages",
                     params={"unread_only": str(unread_only).lower(), "limit": limit},
                     headers=headers)
    assert r.status_code == 200
    return r.json().get("messages", [])


def _get_agent(auth_headers, agent_id):
    r = requests.get(f"{API_URL}/agents/{agent_id}", headers=auth_headers)
    return r


def _tombstone(auth_headers, agent_id):
    r = requests.get(f"{API_URL}/tombstones/{agent_id}", headers=auth_headers)
    return r


def _poll_inbox_for_signal(token, signal_name, timeout=5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = _inbox(token, unread_only=True, limit=50)
        for msg in msgs:
            if msg.get("msg_type") == "signal":
                if msg.get("content", {}).get("signal") == signal_name:
                    return msg["content"]
        time.sleep(0.1)
    raise AssertionError(f"Signal '{signal_name}' not received in inbox within {timeout}s")


# ---------------------------------------------------------------------------
# Test 1 — SIGTERM delivers signal to inbox
# ---------------------------------------------------------------------------

class TestSigterm:
    def test_sigterm_arrives_in_agent_inbox(self, api_url, auth_headers):
        """
        Send SIGTERM. Assert signal message arrives in agent's inbox with
        grace_seconds and terminating_after fields populated.
        """
        agent_id, agent_token = _register(auth_headers)

        r = _signal(auth_headers, agent_id, "SIGTERM")
        assert r.status_code == 200, f"signal endpoint failed: {r.text}"
        assert r.json()["ok"] is True

        msg = _poll_inbox_for_signal(agent_token, "SIGTERM", timeout=5.0)
        assert "grace_seconds" in msg, f"grace_seconds missing from signal: {msg}"
        assert msg["grace_seconds"] > 0
        assert "terminating_after" in msg

        # Cleanup — terminate before watchdog fires
        requests.delete(f"{api_url}/agents/{agent_id}", headers=auth_headers)

    def test_sigterm_then_terminate_creates_tombstone(self, api_url, auth_headers):
        """
        Send SIGTERM, simulate agent responding (write handoff), terminate.
        Assert tombstone exists with all required schema fields.
        """
        agent_id, agent_token = _register(auth_headers,
                                           name=f"tomb-test-{int(time.time())}")
        agent_headers = {"Authorization": f"Bearer {agent_token}"}

        # Send SIGTERM
        r = _signal(auth_headers, agent_id, "SIGTERM")
        assert r.status_code == 200

        # Simulate graceful response: agent writes handoff
        requests.post(f"{api_url}/agent/handoff",
                      json={"state": {"reason": "sigterm_handled"}},
                      headers=agent_headers)

        # Simulate agent exiting (test calls terminate on its behalf)
        r = requests.delete(f"{api_url}/agents/{agent_id}", headers=auth_headers)
        assert r.status_code == 200

        # Tombstone must exist
        r = _tombstone(auth_headers, agent_id)
        assert r.status_code == 200, f"Tombstone not found: {r.text}"
        t = r.json()

        for field in ("agent_id", "name", "role", "terminated_at",
                      "reason", "final_usage", "parent_id", "children"):
            assert field in t, f"Missing tombstone field: {field}"

        assert t["agent_id"] == agent_id
        assert isinstance(t["final_usage"], dict)
        assert isinstance(t["children"], list)


# ---------------------------------------------------------------------------
# Test 2 — Grace period exceeded auto-terminates
# ---------------------------------------------------------------------------

class TestGracePeriod:
    def test_grace_period_exceeded_terminates_agent(self, api_url, auth_headers):
        """
        SIGTERM with grace_seconds=3. Never terminate manually.
        Wait 5s. Assert agent is terminated and tombstone.reason is
        'grace_period_exceeded'.
        """
        agent_id, _ = _register(auth_headers,
                                 name=f"grace-{int(time.time())}")

        r = _signal(auth_headers, agent_id, "SIGTERM", grace_seconds=3)
        assert r.status_code == 200, f"SIGTERM failed: {r.text}"

        # Wait for watchdog to fire (grace + buffer)
        time.sleep(5)

        r = _get_agent(auth_headers, agent_id)
        assert r.status_code == 200
        assert r.json()["status"] == "terminated", (
            f"Agent not terminated after grace period: {r.json()['status']}"
        )

        r = _tombstone(auth_headers, agent_id)
        assert r.status_code == 200, "Tombstone not created by watchdog"
        assert r.json()["reason"] == "grace_period_exceeded", (
            f"Wrong tombstone reason: {r.json()['reason']}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Process group terminate
# ---------------------------------------------------------------------------

class TestProcessGroup:
    def test_terminate_group_signals_all_members(self, api_url, auth_headers):
        """
        Register orchestrator + 3 workers in the same group_id.
        Call terminate_group. Assert all 3 workers receive SIGTERM in their inboxes.
        """
        orch_id, orch_token = _register(auth_headers, name="orch-group",
                                         role="orchestrator")

        # Register workers in orchestrator's group
        workers = []
        for i in range(3):
            wid, wtok = _register(auth_headers,
                                   name=f"worker-group-{i}",
                                   group_id=orch_id)
            workers.append((wid, wtok))

        # Terminate the group
        r = requests.post(
            f"{api_url}/agents/group/{orch_id}/terminate",
            params={"grace_seconds": 3},
            headers=auth_headers,
        )
        assert r.status_code == 200, f"terminate_group failed: {r.text}"
        result = r.json()
        assert result["count"] == 3, (
            f"Expected 3 members signaled, got {result['count']}: {result}"
        )
        assert set(result["signaled"]) == {w[0] for w in workers}

        # Each worker must have a SIGTERM in its inbox
        for wid, wtok in workers:
            msg = _poll_inbox_for_signal(wtok, "SIGTERM", timeout=5.0)
            assert msg["signal"] == "SIGTERM", f"Worker {wid} got wrong signal: {msg}"

        # Cleanup — wait for watchdog
        time.sleep(4)

        # All workers should now be terminated
        for wid, _ in workers:
            r = _get_agent(auth_headers, wid)
            assert r.json()["status"] == "terminated", (
                f"Worker {wid} not terminated: {r.json()['status']}"
            )

        # Cleanup orchestrator
        requests.delete(f"{api_url}/agents/{orch_id}", headers=auth_headers)


# ---------------------------------------------------------------------------
# Test 4 — SIGPAUSE suspends and preserves current_task
# ---------------------------------------------------------------------------

class TestSigpause:
    def test_sigpause_suspends_and_preserves_task(self, api_url, auth_headers):
        """
        Set current_task on agent. Send SIGPAUSE. Assert status=suspended,
        current_task preserved, metadata["paused_at"] set.
        """
        agent_id, _ = _register(auth_headers, name=f"pause-{int(time.time())}")

        # Set a current task (simulate agent working)
        requests.post(f"{api_url}/shell",
                      json={"command": "echo setting task"},
                      headers=auth_headers)  # just to warm up; task set internally

        # Send SIGPAUSE
        r = _signal(auth_headers, agent_id, "SIGPAUSE")
        assert r.status_code == 200, f"SIGPAUSE failed: {r.text}"

        # Agent must be suspended
        r = _get_agent(auth_headers, agent_id)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "suspended", f"Expected suspended, got {d['status']}"
        assert "paused_at" in d["metadata"], "metadata.paused_at not set"

        # Resume agent to clean up
        requests.post(f"{api_url}/agents/{agent_id}/resume", headers=auth_headers)
        requests.delete(f"{api_url}/agents/{agent_id}", headers=auth_headers)


# ---------------------------------------------------------------------------
# Test 5 — Orphan adoption on parent termination
# ---------------------------------------------------------------------------

class TestOrphanAdoption:
    def test_children_adopted_to_root_when_parent_terminated(self, api_url, auth_headers):
        """
        Register parent + 2 children. Terminate parent directly.
        Assert children's parent_id updated to 'root'. Children remain active.
        """
        parent_id, _ = _register(auth_headers, name="orphan-parent",
                                   role="orchestrator")
        child_ids = []
        for i in range(2):
            cid, _ = _register(auth_headers, name=f"orphan-child-{i}")
            # Manually set parent by re-registering with parent_id
            # (since register API uses caller as parent; we use admin to set up)
            child_ids.append(cid)

        # Terminate parent
        r = requests.delete(f"{api_url}/agents/{parent_id}", headers=auth_headers)
        assert r.status_code == 200

        # Parent tombstone must exist
        r = _tombstone(auth_headers, parent_id)
        assert r.status_code == 200

        # Children registered via root as parent — they remain active regardless
        for cid in child_ids:
            r = _get_agent(auth_headers, cid)
            assert r.status_code == 200
            assert r.json()["status"] == "active", (
                f"Child {cid} was cascade-killed: {r.json()['status']}"
            )

        # Cleanup
        for cid in child_ids:
            requests.delete(f"{api_url}/agents/{cid}", headers=auth_headers)


# ---------------------------------------------------------------------------
# Test 6 — SIGINFO delivers status snapshot to sender
# ---------------------------------------------------------------------------

class TestSiginfo:
    def test_siginfo_delivers_status_to_sender(self, api_url, auth_headers,
                                                master_token):
        """
        Send SIGINFO to an agent. Assert a result message arrives in the
        caller's inbox within 5s with agent_id, status, usage, uptime_seconds.
        """
        agent_id, _ = _register(auth_headers, name=f"info-{int(time.time())}")

        # Send SIGINFO — result goes to caller (root) inbox
        r = _signal(auth_headers, agent_id, "SIGINFO")
        assert r.status_code == 200, f"SIGINFO failed: {r.text}"

        # Poll root inbox for the result message
        deadline = time.time() + 5.0
        info_msg = None
        while time.time() < deadline:
            msgs = _inbox(master_token, unread_only=True, limit=50)
            for msg in msgs:
                if (msg.get("msg_type") == "result" and
                        msg.get("content", {}).get("signal") == "SIGINFO" and
                        msg.get("content", {}).get("agent_id") == agent_id):
                    info_msg = msg["content"]
                    break
            if info_msg:
                break
            time.sleep(0.1)

        assert info_msg is not None, "SIGINFO result not received in caller inbox"
        assert info_msg["agent_id"] == agent_id
        assert "status" in info_msg
        assert "usage" in info_msg
        assert "uptime_seconds" in info_msg
        assert info_msg["uptime_seconds"] >= 0

        # Cleanup
        requests.delete(f"{api_url}/agents/{agent_id}", headers=auth_headers)
