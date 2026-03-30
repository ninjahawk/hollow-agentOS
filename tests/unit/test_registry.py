"""
Unit tests for agents/registry.py

Run from project root:
    PYTHONPATH=. pytest tests/unit/test_registry.py -v
"""

import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def registry(tmp_path, monkeypatch):
    """
    Return a fresh AgentRegistry wired to a temp directory so no real files
    at /agentOS/ are touched. Also redirects WORKSPACE_ROOT so mkdir calls
    don't escape the sandbox.
    """
    reg_file = tmp_path / "agent-registry.json"
    workspace = tmp_path / "agents"
    workspace.mkdir()

    import agents.registry as reg_mod
    monkeypatch.setattr(reg_mod, "REGISTRY_PATH", reg_file)
    monkeypatch.setattr(reg_mod, "WORKSPACE_ROOT", workspace)

    from agents.registry import AgentRegistry
    return AgentRegistry(master_token="test-master-secret")


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_returns_record_and_token(self, registry):
        record, token = registry.register(name="alice", role="worker")
        assert record.agent_id
        assert token
        assert record.name == "alice"
        assert record.role == "worker"

    def test_register_applies_role_defaults(self, registry):
        from agents.registry import ROLE_DEFAULTS
        record, _ = registry.register(name="bob", role="readonly")
        # All granted caps must be a subset of the role's allowed set
        assert set(record.capabilities) <= ROLE_DEFAULTS["readonly"]

    def test_register_capability_intersection(self, registry):
        """Requested caps are intersected with role defaults — no privilege escalation."""
        from agents.registry import ROLE_DEFAULTS
        # "readonly" does NOT include "shell"; request it and it should be dropped
        record, _ = registry.register(
            name="charlie", role="readonly",
            capabilities=["fs_read", "shell", "message"]
        )
        assert "shell" not in record.capabilities
        assert "fs_read" in record.capabilities

    def test_register_unknown_role_becomes_custom(self, registry):
        record, _ = registry.register(name="dave", role="totally_made_up")
        assert record.role == "custom"

    def test_register_default_budget_set(self, registry):
        from agents.registry import ROLE_BUDGETS
        record, _ = registry.register(name="eve", role="worker")
        assert record.budget == ROLE_BUDGETS["worker"]

    def test_register_custom_budget_overrides_default(self, registry):
        custom_budget = {"shell_calls": 3, "tokens_in": 1000, "tokens_out": 1000}
        record, _ = registry.register(name="frank", role="worker", budget=custom_budget)
        assert record.budget == custom_budget

    def test_register_status_is_active(self, registry):
        record, _ = registry.register(name="grace", role="worker")
        assert record.status == "active"

    def test_register_metadata_stored(self, registry):
        meta = {"team": "alpha", "priority": 1}
        record, _ = registry.register(name="heidi", role="worker", metadata=meta)
        assert record.metadata == meta


# ---------------------------------------------------------------------------
# authenticate()
# ---------------------------------------------------------------------------

class TestAuthenticate:
    def test_authenticate_valid_token_returns_agent(self, registry):
        record, token = registry.register(name="ivan", role="worker")
        found = registry.authenticate(token)
        assert found is not None
        assert found.agent_id == record.agent_id

    def test_authenticate_invalid_token_returns_none(self, registry):
        assert registry.authenticate("not-a-real-token") is None

    def test_authenticate_empty_string_returns_none(self, registry):
        assert registry.authenticate("") is None

    def test_authenticate_master_token_returns_root(self, registry):
        root = registry.authenticate("test-master-secret")
        assert root is not None
        assert root.agent_id == "root"

    def test_authenticate_terminated_agent_returns_none(self, registry):
        record, token = registry.register(name="judy", role="worker")
        registry.terminate(record.agent_id)
        assert registry.authenticate(token) is None

    def test_authenticate_uses_o1_index(self, registry):
        """Token index lookup — no linear scan; token_hash maps directly to agent_id."""
        record, token = registry.register(name="ken", role="worker")
        import hashlib
        from agents.registry import _hash_token
        h = _hash_token(token)
        assert registry._token_index.get(h) == record.agent_id


# ---------------------------------------------------------------------------
# terminate()
# ---------------------------------------------------------------------------

class TestTerminate:
    def test_terminate_removes_from_token_index(self, registry):
        record, token = registry.register(name="lara", role="worker")
        registry.terminate(record.agent_id)
        # Direct index check
        from agents.registry import _hash_token
        h = _hash_token(token)
        assert h not in registry._token_index

    def test_terminate_sets_status(self, registry):
        record, _ = registry.register(name="mike", role="worker")
        registry.terminate(record.agent_id)
        assert registry.get(record.agent_id).status == "terminated"

    def test_terminate_root_raises(self, registry):
        with pytest.raises(ValueError, match="root"):
            registry.terminate("root")

    def test_terminated_agent_not_auth(self, registry):
        record, token = registry.register(name="nina", role="worker")
        registry.terminate(record.agent_id)
        assert registry.authenticate(token) is None


# ---------------------------------------------------------------------------
# spawn_depth
# ---------------------------------------------------------------------------

class TestSpawnDepth:
    def test_root_level_agent_has_depth_zero(self, registry):
        record, _ = registry.register(name="oscar", role="worker")
        assert record.spawn_depth == 0

    def test_child_inherits_parent_depth_plus_one(self, registry):
        parent, _ = registry.register(name="parent", role="orchestrator")
        child, _ = registry.register(name="child", role="worker", parent_id=parent.agent_id)
        assert child.spawn_depth == parent.spawn_depth + 1

    def test_deep_chain_increments_correctly(self, registry):
        from agents.registry import MAX_SPAWN_DEPTH
        current_id = None
        for i in range(MAX_SPAWN_DEPTH):
            record, _ = registry.register(
                name=f"agent-{i}", role="worker", parent_id=current_id
            )
            assert record.spawn_depth == i
            current_id = record.agent_id

    def test_exceeding_max_spawn_depth_raises(self, registry):
        from agents.registry import MAX_SPAWN_DEPTH
        # Build a chain exactly at the limit
        current_id = None
        for i in range(MAX_SPAWN_DEPTH + 1):
            record, _ = registry.register(
                name=f"deep-{i}", role="worker", parent_id=current_id
            )
            current_id = record.agent_id
        # record.spawn_depth is now MAX_SPAWN_DEPTH; next one should fail
        assert record.spawn_depth == MAX_SPAWN_DEPTH
        with pytest.raises(ValueError, match="Spawn depth limit"):
            registry.register(name="too-deep", role="worker", parent_id=current_id)


# ---------------------------------------------------------------------------
# acquire_lock() / release_lock()
# ---------------------------------------------------------------------------

class TestLocks:
    def test_first_acquire_returns_true(self, registry):
        a, _ = registry.register(name="lock-a", role="worker")
        assert registry.acquire_lock(a.agent_id, "mylock") is True

    def test_second_acquire_by_same_agent_returns_true(self, registry):
        """Same agent re-acquiring its own lock is allowed (no contention)."""
        a, _ = registry.register(name="lock-self", role="worker")
        registry.acquire_lock(a.agent_id, "samelock")
        assert registry.acquire_lock(a.agent_id, "samelock") is True

    def test_different_agent_cannot_acquire_held_lock(self, registry):
        a, _ = registry.register(name="lock-holder", role="worker")
        b, _ = registry.register(name="lock-waiter", role="worker")
        registry.acquire_lock(a.agent_id, "contested")
        assert registry.acquire_lock(b.agent_id, "contested") is False

    def test_release_allows_reacquire_by_other(self, registry):
        a, _ = registry.register(name="releaser", role="worker")
        b, _ = registry.register(name="acquirer", role="worker")
        registry.acquire_lock(a.agent_id, "shared")
        registry.release_lock(a.agent_id, "shared")
        assert registry.acquire_lock(b.agent_id, "shared") is True

    def test_release_nonexistent_lock_returns_false(self, registry):
        a, _ = registry.register(name="no-lock", role="worker")
        assert registry.release_lock(a.agent_id, "does-not-exist") is False

    def test_stale_lock_cleaned_up_on_next_acquire(self, registry):
        a, _ = registry.register(name="stale-holder", role="worker")
        b, _ = registry.register(name="stale-waiter", role="worker")
        # Acquire with a very short TTL
        registry.acquire_lock(a.agent_id, "expiring", ttl_seconds=0.01)
        # Wait for it to expire
        time.sleep(0.05)
        # b should be able to acquire now — stale lock cleaned on next acquire
        assert registry.acquire_lock(b.agent_id, "expiring") is True

    def test_acquire_unknown_agent_returns_false(self, registry):
        assert registry.acquire_lock("nonexistent-id", "anything") is False


# ---------------------------------------------------------------------------
# over_budget()
# ---------------------------------------------------------------------------

class TestOverBudget:
    def test_under_limits_returns_none(self, registry):
        record, _ = registry.register(name="budget-ok", role="worker")
        assert record.over_budget() is None

    def test_at_limit_triggers(self, registry):
        """over_budget() uses >= comparison — at-limit counts as over."""
        record, _ = registry.register(
            name="budget-edge", role="worker",
            budget={"shell_calls": 1, "tokens_in": 1000, "tokens_out": 1000}
        )
        record.usage["shell_calls"] = 1  # exactly at limit
        assert record.over_budget() == "shell_calls"

    def test_over_limit_returns_resource_name(self, registry):
        record, _ = registry.register(
            name="budget-blown", role="worker",
            budget={"shell_calls": 5, "tokens_in": 100, "tokens_out": 100}
        )
        record.usage["tokens_in"] = 200
        assert record.over_budget() == "tokens_in"

    def test_multiple_over_returns_first(self, registry):
        record, _ = registry.register(
            name="budget-multi", role="worker",
            budget={"shell_calls": 1, "tokens_in": 1, "tokens_out": 1}
        )
        record.usage = {"shell_calls": 5, "tokens_in": 5, "tokens_out": 5}
        # Should return something — exactly which key depends on dict order (Python 3.7+)
        result = record.over_budget()
        assert result in {"shell_calls", "tokens_in", "tokens_out"}


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_registry_reloads_agents(self, tmp_path, monkeypatch):
        """Agents saved to disk are available after re-loading the registry."""
        reg_file = tmp_path / "agent-registry.json"
        workspace = tmp_path / "agents"
        workspace.mkdir()

        import agents.registry as reg_mod
        monkeypatch.setattr(reg_mod, "REGISTRY_PATH", reg_file)
        monkeypatch.setattr(reg_mod, "WORKSPACE_ROOT", workspace)

        from agents.registry import AgentRegistry
        reg1 = AgentRegistry("secret")
        record, token = reg1.register(name="persistent", role="worker")

        # Create a second instance reading the same file
        reg2 = AgentRegistry("secret")
        found = reg2.authenticate(token)
        assert found is not None
        assert found.agent_id == record.agent_id
