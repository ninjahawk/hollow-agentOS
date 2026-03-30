"""
Integration tests for the AgentOS API at http://localhost:7777

These tests require the API server to be running. They are skipped automatically
if the server is not reachable.

Run from project root:
    PYTHONPATH=. pytest tests/integration/test_api.py -v -m integration

Mark:
    pytest.ini or pyproject.toml should declare the 'integration' marker.
    Add: [tool.pytest.ini_options] markers = ["integration: live API tests"]
"""

import json
import time
import pytest
import requests
from datetime import datetime, timezone, timedelta

pytestmark = pytest.mark.integration

API_URL = "http://localhost:7777"


# ---------------------------------------------------------------------------
# Module-level reachability skip
# ---------------------------------------------------------------------------

def _api_reachable() -> bool:
    try:
        r = requests.get(f"{API_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


if not _api_reachable():
    pytest.skip(
        "AgentOS API is not reachable at http://localhost:7777 — "
        "start the server before running integration tests.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past_iso(seconds: float = 30.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _future_iso(seconds: float = 60.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, api_url):
        r = requests.get(f"{api_url}/health")
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True

    def test_health_no_auth_required(self, api_url):
        """Health endpoint must be publicly accessible."""
        r = requests.get(f"{api_url}/health")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class TestState:
    def test_state_returns_200_with_token(self, api_url, auth_headers):
        r = requests.get(f"{api_url}/state", headers=auth_headers)
        assert r.status_code == 200

    def test_state_has_system_key(self, api_url, auth_headers):
        r = requests.get(f"{api_url}/state", headers=auth_headers)
        data = r.json()
        assert "system" in data

    def test_state_without_token_returns_401(self, api_url):
        r = requests.get(f"{api_url}/state")
        assert r.status_code == 401

    def test_state_fields_projection_returns_only_requested(self, api_url, auth_headers):
        r = requests.get(f"{api_url}/state?fields=system", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "system" in data
        # No other top-level keys apart from "system" should be present
        other_keys = set(data.keys()) - {"system"}
        assert not other_keys, f"Unexpected keys in projected response: {other_keys}"

    def test_state_fields_projection_is_smaller_than_full(self, api_url, auth_headers):
        full_r = requests.get(f"{api_url}/state", headers=auth_headers)
        proj_r = requests.get(f"{api_url}/state?fields=system", headers=auth_headers)
        assert len(proj_r.content) < len(full_r.content), (
            "Projected response should be smaller than full state response"
        )

    def test_state_system_contains_expected_subkeys(self, api_url, auth_headers):
        r = requests.get(f"{api_url}/state", headers=auth_headers)
        system = r.json().get("system", {})
        # At least one of the standard system metrics should appear
        assert any(k in system for k in ("disk", "memory", "load"))


# ---------------------------------------------------------------------------
# State diff
# ---------------------------------------------------------------------------

class TestStateDiff:
    def test_diff_with_past_since_returns_changed_format(self, api_url, auth_headers):
        since = _past_iso(60)
        r = requests.get(f"{api_url}/state/diff", params={"since": since}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "changed" in data

    def test_diff_changed_keys_are_dotted_paths(self, api_url, auth_headers):
        since = _past_iso(3600)  # 1 hour ago — should have changes
        r = requests.get(f"{api_url}/state/diff", params={"since": since}, headers=auth_headers)
        data = r.json()
        changed = data.get("changed", {})
        # If there are any changed keys, they should be dotted path strings
        for k in changed.keys():
            assert isinstance(k, str), f"Key {k!r} is not a string"

    def test_diff_with_future_since_returns_empty_changed(self, api_url, auth_headers):
        since = _future_iso(3600)  # 1 hour in the future
        r = requests.get(f"{api_url}/state/diff", params={"since": since}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        # Nothing should be newer than a future timestamp
        changed = data.get("changed", {})
        assert changed == {}, f"Expected empty changed for future since, got: {changed}"

    def test_diff_has_since_field_in_response(self, api_url, auth_headers):
        since = _past_iso(10)
        r = requests.get(f"{api_url}/state/diff", params={"since": since}, headers=auth_headers)
        data = r.json()
        assert "since" in data
        assert data["since"] == since


# ---------------------------------------------------------------------------
# Agent registration
# ---------------------------------------------------------------------------

class TestAgentRegistration:
    def _register(self, api_url, auth_headers, name="test-agent", role="worker"):
        return requests.post(
            f"{api_url}/agents/register",
            json={"name": name, "role": role},
            headers=auth_headers,
        )

    def test_register_returns_agent_id_and_token(self, api_url, auth_headers):
        r = self._register(api_url, auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "agent_id" in data
        assert "token" in data

    def test_register_unknown_role_defaults_to_custom(self, api_url, auth_headers):
        r = self._register(api_url, auth_headers, role="totally-unknown-role")
        assert r.status_code == 200
        data = r.json()
        assert data.get("role") == "custom"

    def test_new_agent_token_authenticates(self, api_url, auth_headers):
        """A freshly registered agent's token should work on authenticated endpoints."""
        r = self._register(api_url, auth_headers, name="auth-test-agent")
        assert r.status_code == 200
        token = r.json()["token"]

        # Use the new token to hit /state
        agent_headers = {"Authorization": f"Bearer {token}"}
        state_r = requests.get(f"{api_url}/state", headers=agent_headers)
        assert state_r.status_code == 200

    def test_register_without_auth_returns_401(self, api_url):
        r = requests.post(
            f"{api_url}/agents/register",
            json={"name": "sneaky", "role": "worker"},
        )
        assert r.status_code == 401

    def test_register_response_includes_capabilities(self, api_url, auth_headers):
        r = self._register(api_url, auth_headers, role="readonly")
        data = r.json()
        assert "capabilities" in data
        assert isinstance(data["capabilities"], list)


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------

class TestShell:
    def test_shell_returns_stdout(self, api_url, auth_headers):
        r = requests.post(
            f"{api_url}/shell",
            json={"command": "echo hello-world"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "hello-world" in data.get("stdout", "")

    def test_shell_without_auth_returns_401(self, api_url):
        r = requests.post(f"{api_url}/shell", json={"command": "echo hi"})
        assert r.status_code == 401

    def test_shell_exit_code_present(self, api_url, auth_headers):
        r = requests.post(
            f"{api_url}/shell",
            json={"command": "exit 0"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "exit_code" in data or "returncode" in data or "success" in data


# ---------------------------------------------------------------------------
# Agent handoff / pickup
# ---------------------------------------------------------------------------

class TestHandoffPickup:
    def test_pickup_returns_handoff_key(self, api_url, auth_headers):
        r = requests.get(f"{api_url}/agent/pickup", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "handoff" in data

    def test_handoff_then_pickup_round_trip(self, api_url, auth_headers):
        """Write a handoff, read it back via ?agent_id=X."""
        agent_id = f"test-roundtrip-{int(time.time())}"
        summary = f"Round-trip summary at {_now_iso()}"

        # Write handoff
        post_r = requests.post(
            f"{api_url}/agent/handoff",
            json={
                "agent_id": agent_id,
                "summary": summary,
                "in_progress": ["task-X"],
                "next_steps": ["step-Y"],
            },
            headers=auth_headers,
        )
        assert post_r.status_code == 200
        assert post_r.json().get("ok") is True

        # Read it back with matching agent_id
        get_r = requests.get(
            f"{api_url}/agent/pickup?agent_id={agent_id}",
            headers=auth_headers,
        )
        assert get_r.status_code == 200
        data = get_r.json()
        assert "handoff" in data
        handoff = data["handoff"]
        assert handoff is not None
        assert handoff.get("summary") == summary

    def test_different_agent_ids_dont_mix_handoffs(self, api_url, auth_headers):
        """Two agents writing handoffs should not overwrite each other."""
        ts = int(time.time())
        agent_a = f"agent-a-{ts}"
        agent_b = f"agent-b-{ts}"

        requests.post(
            f"{api_url}/agent/handoff",
            json={"agent_id": agent_a, "summary": "Alpha summary"},
            headers=auth_headers,
        )
        requests.post(
            f"{api_url}/agent/handoff",
            json={"agent_id": agent_b, "summary": "Beta summary"},
            headers=auth_headers,
        )

        ha = requests.get(
            f"{api_url}/agent/pickup?agent_id={agent_a}",
            headers=auth_headers,
        ).json()
        hb = requests.get(
            f"{api_url}/agent/pickup?agent_id={agent_b}",
            headers=auth_headers,
        ).json()

        assert ha["handoff"]["summary"] == "Alpha summary"
        assert hb["handoff"]["summary"] == "Beta summary"

    def test_pickup_without_auth_returns_401(self, api_url):
        r = requests.get(f"{api_url}/agent/pickup")
        assert r.status_code == 401

    def test_handoff_without_auth_returns_401(self, api_url):
        r = requests.post(
            f"{api_url}/agent/handoff",
            json={"agent_id": "x", "summary": "no auth"},
        )
        assert r.status_code == 401
