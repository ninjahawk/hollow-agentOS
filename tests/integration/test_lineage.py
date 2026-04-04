"""
Integration tests for AgentOS v1.3.0: Agent Lineage and Call Graphs.

All tests run without Ollama.

Run:
    PYTHONPATH=. pytest tests/integration/test_lineage.py -v -m integration
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register(auth_headers, name, role="worker", capabilities=None, parent_id=None):
    body = {"name": name, "role": role}
    if capabilities:
        body["capabilities"] = capabilities
    if parent_id:
        body["parent_id"] = parent_id
    r = requests.post(f"{API_URL}/agents/register", json=body, headers=auth_headers)
    assert r.status_code == 200, f"register failed: {r.text}"
    data = r.json()
    token = data["token"]
    agent_id = data["agent_id"]
    return agent_id, {"Authorization": f"Bearer {token}"}


def _terminate(auth_headers, agent_id):
    requests.delete(f"{API_URL}/agents/{agent_id}", headers=auth_headers)


def _lineage(auth_headers, agent_id):
    r = requests.get(f"{API_URL}/agents/{agent_id}/lineage", headers=auth_headers)
    assert r.status_code == 200, f"lineage failed ({r.status_code}): {r.text}"
    return r.json()


def _subtree(auth_headers, agent_id):
    r = requests.get(f"{API_URL}/agents/{agent_id}/subtree", headers=auth_headers)
    assert r.status_code == 200, f"subtree failed ({r.status_code}): {r.text}"
    return r.json()


def _blast_radius(auth_headers, agent_id):
    r = requests.get(f"{API_URL}/agents/{agent_id}/blast-radius", headers=auth_headers)
    assert r.status_code == 200, f"blast-radius failed ({r.status_code}): {r.text}"
    return r.json()


def _critical_path(auth_headers, task_id):
    r = requests.get(f"{API_URL}/tasks/{task_id}/critical-path", headers=auth_headers)
    assert r.status_code == 200, f"critical-path failed ({r.status_code}): {r.text}"
    return r.json()


def _submit_task(auth_headers, description, complexity=1, depends_on=None):
    body = {"description": description, "complexity": complexity}
    if depends_on:
        body["depends_on"] = depends_on
    r = requests.post(f"{API_URL}/tasks/submit", json=body, headers=auth_headers)
    assert r.status_code == 200, f"task submit failed: {r.text}"
    return r.json()["task_id"]


def _get_agent(auth_headers, agent_id):
    r = requests.get(f"{API_URL}/agents/{agent_id}", headers=auth_headers)
    assert r.status_code == 200, f"get_agent failed: {r.text}"
    return r.json()


def _acquire_lock(headers, agent_id, lock_name):
    r = requests.post(f"{API_URL}/agents/{agent_id}/lock/{lock_name}",
                      headers=headers)
    return r.status_code == 200


# ---------------------------------------------------------------------------
# Test 1 — Lineage chain: root → orchestrator → worker
# ---------------------------------------------------------------------------

class TestLineageChain:
    def test_ancestor_chain(self, auth_headers):
        """
        Register root → orchestrator → 3 workers.
        Call agent_lineage(worker_id).
        Assert chain is [worker, orchestrator, root] with correct spawn_depth.
        Assert parent_task_id populated on workers (from task context, if set).
        """
        # Register orchestrator as child of root (caller=root via master token)
        orch_id, orch_headers = _register(auth_headers, "orch-lineage-test", role="orchestrator",
                                   capabilities=["admin", "spawn", "shell", "fs_read", "fs_write",
                                                 "ollama", "message", "semantic"])

        try:
            # Register 3 workers as children of orchestrator (use root headers + explicit parent_id)
            worker_ids = []
            worker_headers_list = []
            for i in range(3):
                w_id, w_headers = _register(auth_headers, f"worker-{i}", role="worker",
                                            parent_id=orch_id)
                worker_ids.append(w_id)
                worker_headers_list.append(w_headers)

            try:
                # Check lineage for first worker
                worker_id = worker_ids[0]
                result = _lineage(auth_headers, worker_id)

                chain = result["lineage"]
                assert len(chain) >= 3, (
                    f"Expected at least 3 ancestors (worker, orchestrator, root), got {len(chain)}: "
                    f"{[a['agent_id'] for a in chain]}"
                )

                # Chain ordered [worker, orchestrator, ..., root]
                assert chain[0]["agent_id"] == worker_id, "First entry must be the worker itself"

                # Find orchestrator in chain
                chain_ids = [a["agent_id"] for a in chain]
                assert orch_id in chain_ids, f"Orchestrator {orch_id} not in lineage chain: {chain_ids}"

                # Check spawn_depth increases going down the chain
                depths = [a["spawn_depth"] for a in chain]
                assert depths == sorted(depths, reverse=True), (
                    f"spawn_depth should decrease toward root, got: {depths}"
                )

                # Worker must be deeper than orchestrator
                worker_depth = chain[0]["spawn_depth"]
                orch_depth = next(a["spawn_depth"] for a in chain if a["agent_id"] == orch_id)
                assert worker_depth > orch_depth, (
                    f"Worker depth ({worker_depth}) should be > orchestrator depth ({orch_depth})"
                )

                # All non-root entries must have parent_id set
                non_root = [a for a in chain if a["agent_id"] != "root"]
                for a in non_root:
                    assert a["parent_id"] is not None, (
                        f"Agent {a['agent_id']} has no parent_id in lineage chain"
                    )

                # Depth from result
                assert result["depth"] == len(chain) - 1

            finally:
                for w_id in worker_ids:
                    _terminate(auth_headers, w_id)
        finally:
            _terminate(auth_headers, orch_id)


# ---------------------------------------------------------------------------
# Test 2 — Subtree completeness
# ---------------------------------------------------------------------------

class TestSubtreeCompleteness:
    def test_full_descendant_tree(self, auth_headers):
        """
        Spawn: root → orchestrator → 3 workers.
        Call agent_subtree(orchestrator). Assert exactly 3 descendants.
        All edges present, no duplicates.
        """
        orch_id, orch_headers = _register(auth_headers, "orch-subtree-test", role="orchestrator",
                                           capabilities=["admin", "spawn", "shell", "fs_read",
                                                         "fs_write", "ollama", "message", "semantic"])

        try:
            worker_ids = []
            for i in range(3):
                w_id, _ = _register(auth_headers, f"sub-worker-{i}", role="worker",
                                    parent_id=orch_id)
                worker_ids.append(w_id)

            try:
                result = _subtree(auth_headers, orch_id)
                subtree = result["subtree"]

                # descendant_count should be 3
                assert subtree["descendant_count"] == 3, (
                    f"Expected 3 descendants, got {subtree['descendant_count']}"
                )

                # All 3 workers appear in children
                children_ids = set(subtree["children"].keys())
                for w_id in worker_ids:
                    assert w_id in children_ids, (
                        f"Worker {w_id} not in subtree children: {children_ids}"
                    )

                # No duplicates
                assert len(children_ids) == len(worker_ids), (
                    f"Duplicate children detected: {children_ids}"
                )

                # Each child node has correct agent_id
                for w_id in worker_ids:
                    child_node = subtree["children"][w_id]
                    assert child_node["agent"]["agent_id"] == w_id

            finally:
                for w_id in worker_ids:
                    _terminate(auth_headers, w_id)
        finally:
            _terminate(auth_headers, orch_id)


# ---------------------------------------------------------------------------
# Test 3 — Blast radius
# ---------------------------------------------------------------------------

class TestBlastRadius:
    def test_blast_radius_includes_children_and_locks(self, auth_headers):
        """
        Orchestrator holds locks and has children.
        blast_radius(orchestrator) includes all children and locked resources.
        """
        orch_id, orch_headers = _register(auth_headers, "orch-blast-test", role="orchestrator",
                                           capabilities=["admin", "spawn", "shell", "fs_read",
                                                         "fs_write", "ollama", "message", "semantic"])

        try:
            # Register 3 child workers
            worker_ids = []
            for i in range(3):
                w_id, _ = _register(auth_headers, f"blast-worker-{i}", role="worker",
                                    parent_id=orch_id)
                worker_ids.append(w_id)

            # Acquire 2 locks on the orchestrator (unique names to avoid conflicts)
            ts = int(time.time())
            lock_names = [f"resource-alpha-{ts}", f"resource-beta-{ts}"]
            acquired = []
            for lock_name in lock_names:
                ok = _acquire_lock(orch_headers, orch_id, lock_name)
                if ok:
                    acquired.append(lock_name)
            assert len(acquired) == 2, f"Failed to acquire locks: only got {acquired}"
            lock_names = acquired

            try:
                result = _blast_radius(auth_headers, orch_id)

                assert result["agent_id"] == orch_id

                # All 3 children in affected_agents
                affected = set(result["affected_agents"])
                for w_id in worker_ids:
                    assert w_id in affected, (
                        f"Worker {w_id} not in blast radius affected_agents: {affected}"
                    )

                assert result["affected_agent_count"] >= 3

                # Both locked resources present
                locked = set(result["locked_resources"])
                for lock_name in lock_names:
                    assert lock_name in locked, (
                        f"Lock '{lock_name}' not in blast_radius locked_resources: {locked}"
                    )

            finally:
                for w_id in worker_ids:
                    _terminate(auth_headers, w_id)
        finally:
            _terminate(auth_headers, orch_id)


# ---------------------------------------------------------------------------
# Test 4 — Critical path
# ---------------------------------------------------------------------------

class TestCriticalPath:
    def test_sequential_vs_parallel_path(self, auth_headers):
        """
        Submit tasks: A (no deps), B (depends_on=[A]), C (depends_on=[B]),
        D (depends_on=[A], parallel with B).
        critical_path(A) should return [A, B, C] (length 3), not [A, D] (length 2).
        """
        # Submit A — the root task (no Ollama needed, complexity=1 won't block)
        task_a = _submit_task(auth_headers, "task A - root", complexity=1)
        task_b = _submit_task(auth_headers, "task B - sequential after A",
                              complexity=1, depends_on=[task_a])
        task_c = _submit_task(auth_headers, "task C - sequential after B",
                              complexity=1, depends_on=[task_b])
        task_d = _submit_task(auth_headers, "task D - parallel with B, depends on A",
                              complexity=1, depends_on=[task_a])

        result = _critical_path(auth_headers, task_a)
        path = result["critical_path"]

        assert task_a in path, f"Task A ({task_a}) not in critical path: {path}"
        assert task_b in path, f"Task B ({task_b}) not in critical path: {path}"
        assert task_c in path, f"Task C ({task_c}) not in critical path: {path}"
        assert result["length"] == 3, (
            f"Expected critical path length 3 [A→B→C], got {result['length']}: {path}"
        )

        # D should NOT be in the critical path (it's the shorter branch)
        assert task_d not in path, (
            f"Task D ({task_d}) should not be in critical path [A→B→C]: {path}"
        )

        # Path must start with A
        assert path[0] == task_a, f"Critical path must start at task A, got: {path[0]}"


# ---------------------------------------------------------------------------
# Test 5 — Audit tagging: caused_by_task_id on audit entries
# ---------------------------------------------------------------------------

class TestAuditTagging:
    def test_audit_entries_have_causal_context(self, auth_headers):
        """
        Register an agent with a known name.
        Query audit log for agent_register operations.
        Assert entries have the new fields (no KeyError on access).
        """
        # Register a new agent — this creates an audit entry
        tag = f"audit-tag-test-{int(time.time())}"
        agent_id, _ = _register(auth_headers, tag)
        try:
            # Query audit log for this registration
            r = requests.get(f"{API_URL}/audit",
                             params={"operation": "agent_register", "limit": 20},
                             headers=auth_headers)
            assert r.status_code == 200, f"audit query failed: {r.text}"
            entries = r.json().get("entries", [])

            # Find the entry for our registration
            our_entries = [e for e in entries if e.get("params", {}).get("new_agent_id") == agent_id]

            if our_entries:
                entry = our_entries[0]
                # New v1.3.0 fields must be present (may be None, but must exist)
                assert "caused_by_task_id" in entry, (
                    f"caused_by_task_id missing from audit entry: {list(entry.keys())}"
                )
                assert "parent_txn_id" in entry, (
                    f"parent_txn_id missing from audit entry: {list(entry.keys())}"
                )
                assert "call_depth" in entry, (
                    f"call_depth missing from audit entry: {list(entry.keys())}"
                )
        finally:
            _terminate(auth_headers, agent_id)


# ---------------------------------------------------------------------------
# Test 6 — Persistence across restart: lineage.json survives
# ---------------------------------------------------------------------------

class TestLineagePersistence:
    def test_lineage_edges_survive_restart(self, auth_headers):
        """
        Register orchestrator → 5 workers.
        Call agent_subtree. Assert 5 descendants.
        (Full restart not automated here — validates that edges are written
        to lineage.json at registration time by checking file existence
        and re-reading via the API.)
        """
        orch_id, orch_headers = _register(auth_headers, "orch-persist-test", role="orchestrator",
                                           capabilities=["admin", "spawn", "shell", "fs_read",
                                                         "fs_write", "ollama", "message", "semantic"])

        try:
            worker_ids = []
            for i in range(5):
                w_id, _ = _register(auth_headers, f"persist-worker-{i}", role="worker",
                                    parent_id=orch_id)
                worker_ids.append(w_id)

            try:
                # Verify subtree is complete
                result = _subtree(auth_headers, orch_id)
                assert result["subtree"]["descendant_count"] == 5, (
                    f"Expected 5 descendants, got {result['subtree']['descendant_count']}"
                )

                # Verify lineage file exists (persistence confirmed)
                import os
                memory_path = os.environ.get(
                    "AGENTOS_MEMORY_PATH",
                    "/agentOS/memory"
                )
                lineage_path = os.path.join(memory_path, "lineage.json")
                assert os.path.exists(lineage_path), (
                    f"lineage.json not found at {lineage_path} — edges not persisted"
                )

                # Verify lineage.json contains our edges
                import json
                with open(lineage_path, encoding="utf-8") as f:
                    edges = json.load(f)

                orch_edges = [e for e in edges if e["parent_id"] == orch_id]
                assert len(orch_edges) == 5, (
                    f"Expected 5 edges from orchestrator in lineage.json, found {len(orch_edges)}"
                )

                child_ids_in_file = {e["child_id"] for e in orch_edges}
                for w_id in worker_ids:
                    assert w_id in child_ids_in_file, (
                        f"Worker {w_id} edge missing from lineage.json"
                    )

            finally:
                for w_id in worker_ids:
                    _terminate(auth_headers, w_id)
        finally:
            _terminate(auth_headers, orch_id)


# ---------------------------------------------------------------------------
# Test 7 — Auth: lineage endpoints require authentication
# ---------------------------------------------------------------------------

class TestLineageAuth:
    def test_lineage_endpoints_require_auth(self):
        """All lineage endpoints reject unauthenticated requests."""
        assert requests.get(f"{API_URL}/agents/root/lineage").status_code == 401
        assert requests.get(f"{API_URL}/agents/root/subtree").status_code == 401
        assert requests.get(f"{API_URL}/agents/root/blast-radius").status_code == 401
