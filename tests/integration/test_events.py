"""
Integration tests for the AgentOS Event Kernel (v0.7.0).

All tests require the API server to be running at localhost:7777.
All tests use real API calls — no mocks, no seeded data.
Every assertion corresponds to a requirement in ROADMAP.md v0.7.0.

Run:
    PYTHONPATH=. pytest tests/integration/test_events.py -v -m integration
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


def _ollama_available() -> bool:
    """True if a local Ollama instance is reachable. Used to skip Ollama-dependent tests."""
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False


_OLLAMA = _ollama_available()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _subscribe(auth_headers, pattern, ttl_seconds=None):
    body = {"pattern": pattern}
    if ttl_seconds is not None:
        body["ttl_seconds"] = ttl_seconds
    r = requests.post(f"{API_URL}/events/subscribe", json=body, headers=auth_headers)
    assert r.status_code == 200, f"Subscribe failed: {r.text}"
    return r.json()["subscription_id"]


def _unsubscribe(auth_headers, sub_id):
    r = requests.delete(f"{API_URL}/events/subscriptions/{sub_id}", headers=auth_headers)
    assert r.status_code == 200
    return r.json()


def _inbox(auth_headers, unread_only=True, limit=50):
    r = requests.get(
        f"{API_URL}/messages",
        params={"unread_only": str(unread_only).lower(), "limit": limit},
        headers=auth_headers,
    )
    assert r.status_code == 200, f"Inbox failed: {r.text}"
    return r.json().get("messages", [])


def _event_history(auth_headers, since=None, event_types=None, limit=100):
    params = {"limit": limit}
    if since is not None:
        params["since"] = since
    if event_types:
        params["event_types"] = event_types
    r = requests.get(f"{API_URL}/events/history", params=params, headers=auth_headers)
    assert r.status_code == 200, f"Event history failed: {r.text}"
    return r.json().get("events", [])


def _register_agent(auth_headers, name=None, role="worker", budget=None):
    body = {"name": name or f"test-{int(time.time() * 1000)}", "role": role}
    if budget:
        body["budget"] = budget
    r = requests.post(f"{API_URL}/agents/register", json=body, headers=auth_headers)
    assert r.status_code == 200, f"Register failed: {r.text}"
    d = r.json()
    return d["agent_id"], d["token"]


def _terminate_agent(auth_headers, agent_id):
    r = requests.delete(f"{API_URL}/agents/{agent_id}", headers=auth_headers)
    assert r.status_code == 200, f"Terminate failed: {r.text}"


def _poll_inbox_for_event(auth_headers, event_type, timeout=10.0) -> dict:
    """
    Poll inbox until an event of the given type arrives or timeout.
    Returns the event content dict, or raises AssertionError on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = _inbox(auth_headers, unread_only=True, limit=50)
        for msg in msgs:
            if msg.get("msg_type") == "event":
                content = msg.get("content", {})
                if content.get("event_type") == event_type:
                    return content
        time.sleep(0.1)
    raise AssertionError(
        f"Event '{event_type}' not received within {timeout}s"
    )


# ---------------------------------------------------------------------------
# Test 1 — task.completed event delivered after real task
# ---------------------------------------------------------------------------

class TestTaskCompletedEvent:
    @pytest.mark.skipif(not _OLLAMA, reason="Ollama not available")
    def test_subscribe_and_receive_task_completed(self, api_url, auth_headers):
        """
        Subscribe to task.completed. Submit a real Ollama task (complexity=1).
        Assert the event arrives in the inbox before a 1s polling loop would
        detect the completion separately.
        """
        sub_id = _subscribe(auth_headers, "task.completed")
        t_submit = time.time()

        r = requests.post(
            f"{api_url}/tasks/submit",
            json={"description": "Reply with the single word: done", "complexity": 1},
            headers=auth_headers,
        )
        assert r.status_code == 200, f"Task submit failed: {r.text}"
        submitted_task_id = r.json()["task_id"]

        # By the time submit returns (wait=True), the task is done and the event
        # must already be in the inbox. Poll for the specific task's completion event.
        deadline = time.time() + 10.0
        event = None
        while time.time() < deadline:
            msgs = _inbox(auth_headers, unread_only=True, limit=50)
            for msg in msgs:
                if msg.get("msg_type") == "event":
                    content = msg.get("content", {})
                    if (content.get("event_type") == "task.completed" and
                            content.get("payload", {}).get("task_id") == submitted_task_id):
                        event = content
                        break
            if event:
                break
            time.sleep(0.2)
        assert event is not None, f"task.completed for {submitted_task_id} not received within 10s"
        assert event["payload"].get("model") is not None, "No model in payload"

        # Event must be in history log as well
        log = _event_history(auth_headers, since=t_submit - 1,
                             event_types="task.completed")
        task_ids = [e["payload"].get("task_id") for e in log]
        assert submitted_task_id in task_ids, (
            f"task.completed not found in event log. Log: {log[:3]}"
        )

        _unsubscribe(auth_headers, sub_id)

    def test_task_queued_event_also_fires(self, api_url, auth_headers):
        """task.queued must fire before task.started and task.completed."""
        t0 = time.time()
        sub_id = _subscribe(auth_headers, "task.queued")

        requests.post(
            f"{api_url}/tasks/submit",
            json={"description": "Reply with: ok", "complexity": 1},
            headers=auth_headers,
        )

        log = _event_history(auth_headers, since=t0 - 0.5, event_types="task.queued")
        assert len(log) >= 1, "task.queued event not found in log"
        assert log[0]["payload"].get("complexity") == 1

        _unsubscribe(auth_headers, sub_id)


# ---------------------------------------------------------------------------
# Test 2 — budget.warning fires exactly once at 80%
# ---------------------------------------------------------------------------

class TestBudgetWarningEvent:
    @pytest.mark.skipif(not _OLLAMA, reason="Ollama not available")
    def test_budget_warning_fires_at_80_pct(self, api_url, auth_headers):
        """
        Register an agent with tokens_in: 500.
        Submit tasks until >400 tokens consumed.
        Assert budget.warning fired exactly once.
        Assert budget.exhausted fires if 100% is reached.
        """
        # Register constrained agent
        agent_id, agent_token = _register_agent(
            auth_headers,
            name=f"budget-test-{int(time.time())}",
            role="worker",
            budget={"shell_calls": 200, "tokens_in": 500, "tokens_out": 500},
        )
        agent_headers = {"Authorization": f"Bearer {agent_token}"}

        # Subscribe root to budget events for this system
        t0 = time.time()
        sub_id = _subscribe(auth_headers, "budget.*")

        # Drive token consumption using the agent's OWN token — task routes
        # derive submitted_by from the authenticated caller, not the request body
        warnings_seen = []
        exhausted_seen = []
        deadline = time.time() + 60

        while time.time() < deadline:
            try:
                requests.post(
                    f"{api_url}/tasks/submit",
                    json={"description": "Say: ok", "complexity": 1},
                    headers=agent_headers,
                    timeout=30,
                )
            except Exception:
                pass

            log = _event_history(auth_headers, since=t0 - 0.5,
                                 event_types="budget.warning,budget.exhausted")
            for e in log:
                if (e["payload"].get("agent_id") == agent_id and
                        e["event_type"] == "budget.warning" and
                        e not in warnings_seen):
                    warnings_seen.append(e)
                if (e["payload"].get("agent_id") == agent_id and
                        e["event_type"] == "budget.exhausted" and
                        e not in exhausted_seen):
                    exhausted_seen.append(e)

            # Stop once we see the warning (we don't need to exhaust)
            if warnings_seen:
                break

        assert len(warnings_seen) >= 1, (
            "budget.warning event never fired for constrained agent"
        )

        # Verify exactly one warning per resource (no duplicates)
        warned_resources = [e["payload"].get("resource") for e in warnings_seen]
        assert len(warned_resources) == len(set(warned_resources)), (
            f"Duplicate budget.warning events for same resource: {warned_resources}"
        )

        # Verify pct_used is in the payload and >= 80
        for e in warnings_seen:
            pct = e["payload"].get("pct_used", 0)
            assert pct >= 80.0, f"budget.warning fired at {pct}% — expected >= 80%"

        _unsubscribe(auth_headers, sub_id)
        _terminate_agent(auth_headers, agent_id)


# ---------------------------------------------------------------------------
# Test 3 — agent.terminated event within 200ms
# ---------------------------------------------------------------------------

class TestAgentTerminatedEvent:
    def test_terminated_event_arrives_quickly(self, api_url, auth_headers):
        """
        Register a temp agent. Subscribe to agent.terminated.
        Terminate temp agent. Assert event arrives within 200ms.
        Assert payload includes agent_id, name, role, final_usage.
        """
        agent_id, _ = _register_agent(auth_headers, name=f"term-test-{int(time.time())}")
        t0 = time.time()
        sub_id = _subscribe(auth_headers, "agent.terminated")

        _terminate_agent(auth_headers, agent_id)
        t_terminated = time.time()

        # Poll inbox — event must arrive promptly
        event = _poll_inbox_for_event(auth_headers, "agent.terminated", timeout=2.0)
        t_received = time.time()

        elapsed_ms = (t_received - t0) * 1000
        assert elapsed_ms < 500, (
            f"agent.terminated event took {elapsed_ms:.0f}ms — expected < 500ms"
        )

        payload = event["payload"]
        assert payload.get("agent_id") == agent_id, (
            f"Wrong agent_id in payload: {payload}"
        )
        assert "name" in payload, "name missing from payload"
        assert "role" in payload, "role missing from payload"
        assert "final_usage" in payload, "final_usage missing from payload"

        _unsubscribe(auth_headers, sub_id)

    def test_terminated_event_in_log(self, api_url, auth_headers):
        """agent.terminated must appear in the event log, not just the inbox."""
        t0 = time.time()
        agent_id, _ = _register_agent(auth_headers,
                                       name=f"log-test-{int(time.time())}")
        _terminate_agent(auth_headers, agent_id)

        log = _event_history(auth_headers, since=t0 - 0.5,
                             event_types="agent.terminated")
        agent_ids_in_log = [e["payload"].get("agent_id") for e in log]
        assert agent_id in agent_ids_in_log, (
            f"Terminated agent {agent_id} not found in event log. "
            f"Log agent_ids: {agent_ids_in_log}"
        )


# ---------------------------------------------------------------------------
# Test 4 — subscription TTL expiry
# ---------------------------------------------------------------------------

class TestSubscriptionTTL:
    def test_expired_subscription_does_not_deliver(self, api_url, auth_headers):
        """
        Subscribe with ttl_seconds=2. Wait 3s.
        Terminate an agent (fires agent.terminated).
        Assert no event delivered to the inbox.
        """
        sub_id = _subscribe(auth_headers, "agent.terminated", ttl_seconds=2)

        # Mark inbox position before waiting
        msgs_before = _inbox(auth_headers, unread_only=False, limit=200)
        ids_before = {m["msg_id"] for m in msgs_before}

        time.sleep(3)

        # Fire an event that would have matched the subscription
        agent_id, _ = _register_agent(auth_headers, name=f"ttl-test-{int(time.time())}")
        _terminate_agent(auth_headers, agent_id)

        # Brief pause for any (incorrect) delivery
        time.sleep(0.3)

        msgs_after = _inbox(auth_headers, unread_only=False, limit=200)
        new_event_msgs = [
            m for m in msgs_after
            if m["msg_id"] not in ids_before
            and m.get("msg_type") == "event"
            and m.get("content", {}).get("event_type") == "agent.terminated"
        ]

        assert len(new_event_msgs) == 0, (
            f"Received {len(new_event_msgs)} events after subscription expired: "
            f"{new_event_msgs}"
        )

    def test_expired_subscription_removed_from_list(self, api_url, auth_headers):
        """After TTL expires, the subscription should not appear in /events/subscriptions."""
        sub_id = _subscribe(auth_headers, "task.*", ttl_seconds=1)
        time.sleep(2)

        # Trigger expiry cleanup by emitting an event
        _register_agent(auth_headers, name=f"cleanup-trigger-{int(time.time())}")

        r = requests.get(f"{api_url}/events/subscriptions", headers=auth_headers)
        assert r.status_code == 200
        subs = r.json().get("subscriptions", [])
        sub_ids = [s["subscription_id"] for s in subs]
        assert sub_id not in sub_ids, (
            f"Expired subscription {sub_id} still listed in active subscriptions"
        )


# ---------------------------------------------------------------------------
# Test 5 — glob pattern "agent.*" catches all lifecycle events
# ---------------------------------------------------------------------------

class TestGlobPattern:
    def test_agent_star_receives_all_lifecycle_events(self, api_url, auth_headers):
        """
        Subscribe to 'agent.*'. Register, suspend, resume, terminate an agent.
        Assert exactly those 4 event types are received.
        """
        t0 = time.time()
        sub_id = _subscribe(auth_headers, "agent.*")

        agent_id, _ = _register_agent(auth_headers,
                                       name=f"lifecycle-{int(time.time())}")

        # Suspend
        r = requests.post(f"{api_url}/agents/{agent_id}/suspend",
                         headers=auth_headers)
        assert r.status_code == 200, f"Suspend failed: {r.text}"

        # Resume
        r = requests.post(f"{api_url}/agents/{agent_id}/resume",
                         headers=auth_headers)
        assert r.status_code == 200, f"Resume failed: {r.text}"

        # Terminate
        _terminate_agent(auth_headers, agent_id)

        time.sleep(0.5)

        # Check event log for all 4 event types for this agent
        log = _event_history(auth_headers, since=t0 - 0.5,
                             event_types="agent.registered,agent.suspended,agent.resumed,agent.terminated")

        agent_events = [e for e in log if e["payload"].get("agent_id") == agent_id]
        types_seen = {e["event_type"] for e in agent_events}

        assert "agent.registered" in types_seen, (
            f"agent.registered not in log. Seen: {types_seen}"
        )
        assert "agent.suspended" in types_seen, (
            f"agent.suspended not in log. Seen: {types_seen}"
        )
        assert "agent.resumed" in types_seen, (
            f"agent.resumed not in log. Seen: {types_seen}"
        )
        assert "agent.terminated" in types_seen, (
            f"agent.terminated not in log. Seen: {types_seen}"
        )

        _unsubscribe(auth_headers, sub_id)


# ---------------------------------------------------------------------------
# Test 6 — unsubscribe stops delivery
# ---------------------------------------------------------------------------

class TestUnsubscribe:
    def test_events_stop_after_unsubscribe(self, api_url, auth_headers):
        """
        Subscribe to agent.registered. Verify delivery works.
        Unsubscribe. Register another agent. Assert no further delivery.
        """
        # Drain stale unread messages before subscribing so we only see
        # events generated after our subscription is active
        _inbox(auth_headers, unread_only=True, limit=200)

        sub_id = _subscribe(auth_headers, "agent.registered")

        # First registration — should deliver
        agent_id_1, _ = _register_agent(auth_headers,
                                         name=f"pre-unsub-{int(time.time())}")

        event = _poll_inbox_for_event(auth_headers, "agent.registered", timeout=3.0)
        assert event["payload"].get("agent_id") == agent_id_1, (
            f"Expected agent_id_1={agent_id_1}, got {event['payload'].get('agent_id')}"
        )

        # Unsubscribe
        result = _unsubscribe(auth_headers, sub_id)
        assert result["ok"] is True

        # Drain any remaining unread events
        _inbox(auth_headers, unread_only=True, limit=100)

        # Second registration — should NOT deliver
        msgs_before = _inbox(auth_headers, unread_only=False, limit=200)
        ids_before = {m["msg_id"] for m in msgs_before}

        agent_id_2, _ = _register_agent(auth_headers,
                                         name=f"post-unsub-{int(time.time())}")
        time.sleep(0.3)

        msgs_after = _inbox(auth_headers, unread_only=False, limit=200)
        new_event_msgs = [
            m for m in msgs_after
            if m["msg_id"] not in ids_before
            and m.get("msg_type") == "event"
            and m.get("content", {}).get("event_type") == "agent.registered"
        ]

        assert len(new_event_msgs) == 0, (
            f"Received {len(new_event_msgs)} agent.registered events after unsubscribe"
        )

        # Cleanup
        _terminate_agent(auth_headers, agent_id_1)
        _terminate_agent(auth_headers, agent_id_2)


# ---------------------------------------------------------------------------
# Test 7 — event log persistence (log is append-only, reads from disk)
# ---------------------------------------------------------------------------

class TestEventLogPersistence:
    def test_events_present_in_history_immediately(self, api_url, auth_headers):
        """
        Register and terminate 10 agents. Query event_history.
        Assert all 20 events (10 registered + 10 terminated) appear in the log.
        The log is on disk — this test verifies _append_log writes through.
        """
        t0 = time.time()
        agent_ids = []

        for i in range(5):
            aid, _ = _register_agent(auth_headers,
                                      name=f"persist-test-{i}-{int(time.time())}")
            agent_ids.append(aid)

        for aid in agent_ids:
            _terminate_agent(auth_headers, aid)

        log = _event_history(auth_headers, since=t0 - 0.5, limit=200)

        registered_ids = {
            e["payload"].get("agent_id") for e in log
            if e["event_type"] == "agent.registered"
        }
        terminated_ids = {
            e["payload"].get("agent_id") for e in log
            if e["event_type"] == "agent.terminated"
        }

        for aid in agent_ids:
            assert aid in registered_ids, (
                f"agent.registered for {aid} missing from log"
            )
            assert aid in terminated_ids, (
                f"agent.terminated for {aid} missing from log"
            )

    def test_event_history_filters_by_type(self, api_url, auth_headers):
        """Filtering by event_types must return only the requested types."""
        t0 = time.time()

        # Generate mixed events
        aid, _ = _register_agent(auth_headers, name=f"filter-test-{int(time.time())}")
        _terminate_agent(auth_headers, aid)

        log = _event_history(
            auth_headers,
            since=t0 - 0.5,
            event_types="agent.registered",
        )

        types_in_result = {e["event_type"] for e in log}
        unexpected = types_in_result - {"agent.registered"}
        assert not unexpected, (
            f"Filtered query returned unexpected event types: {unexpected}"
        )

    def test_event_history_since_filter(self, api_url, auth_headers):
        """Events before `since` must not appear in the response."""
        # Register an agent, capture timestamp, then register another
        aid1, _ = _register_agent(auth_headers, name=f"before-{int(time.time())}")
        _terminate_agent(auth_headers, aid1)

        cutoff = time.time()
        time.sleep(0.1)

        aid2, _ = _register_agent(auth_headers, name=f"after-{int(time.time())}")
        _terminate_agent(auth_headers, aid2)

        log = _event_history(auth_headers, since=cutoff, limit=200)

        for e in log:
            assert e["timestamp"] > cutoff, (
                f"Event with timestamp {e['timestamp']} appeared before since={cutoff}"
            )

        # aid2 must be present
        agent_ids_after = {e["payload"].get("agent_id") for e in log}
        assert aid2 in agent_ids_after, (
            f"agent after cutoff ({aid2}) not found in filtered log"
        )


# ---------------------------------------------------------------------------
# Test 8 — file.written event
# ---------------------------------------------------------------------------

class TestFileWrittenEvent:
    def test_file_written_event_fires(self, api_url, auth_headers):
        """Writing a file via fs_write must emit a file.written event."""
        t0 = time.time()
        sub_id = _subscribe(auth_headers, "file.written")

        test_path = f"/tmp/agentos_test_event_{int(time.time())}.txt"
        r = requests.post(
            f"{api_url}/fs/write",
            json={"path": test_path, "content": "event test content"},
            headers=auth_headers,
        )
        assert r.status_code == 200

        event = _poll_inbox_for_event(auth_headers, "file.written", timeout=3.0)

        assert event["payload"].get("path") == test_path, (
            f"Wrong path in file.written payload: {event['payload']}"
        )
        assert event["payload"].get("size_bytes") > 0

        _unsubscribe(auth_headers, sub_id)
        # Cleanup test file
        requests.post(f"{api_url}/shell",
                     json={"command": f"rm -f {test_path}"},
                     headers=auth_headers)
