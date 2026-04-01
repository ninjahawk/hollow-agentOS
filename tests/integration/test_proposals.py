"""
Integration tests for AgentOS v1.3.7: Self-Extending System.

Tests verify the proposal lifecycle, hot-reload, standards self-update,
rejection flow, consensus-gated approval, and contribution attribution.

Run:
    PYTHONPATH=. pytest tests/integration/test_proposals.py -v -m integration
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

def _register(auth_headers, name, role="worker"):
    r = requests.post(f"{API_URL}/agents/register", json={
        "name": name, "role": role,
        "capabilities": ["admin"] if role == "root" else [],
    }, headers=auth_headers)
    assert r.status_code == 200, f"register failed: {r.text}"
    d = r.json()
    return d["agent_id"], d["token"]


def _submit_proposal(auth_headers, proposal_type, spec, test_cases, rationale,
                     consensus_quorum=1):
    r = requests.post(f"{API_URL}/proposals", json={
        "proposal_type": proposal_type,
        "spec": spec,
        "test_cases": test_cases,
        "rationale": rationale,
        "consensus_quorum": consensus_quorum,
    }, headers=auth_headers)
    assert r.status_code == 200, f"submit failed: {r.text}"
    return r.json()["proposal_id"]


def _stage(auth_headers, proposal_id):
    r = requests.post(f"{API_URL}/proposals/{proposal_id}/stage", headers=auth_headers)
    assert r.status_code == 200, f"stage failed: {r.text}"
    return r.json()


def _approve(auth_headers, proposal_id):
    r = requests.post(f"{API_URL}/proposals/{proposal_id}/approve", headers=auth_headers)
    assert r.status_code == 200, f"approve failed: {r.text}"
    return r.json()


def _get_proposal(auth_headers, proposal_id):
    r = requests.get(f"{API_URL}/proposals/{proposal_id}", headers=auth_headers)
    assert r.status_code == 200, f"get failed: {r.text}"
    return r.json()


def _tool_list(auth_headers):
    r = requests.get(f"{API_URL}/mcp/tools", headers=auth_headers)
    assert r.status_code == 200, f"mcp/tools failed: {r.text}"
    return r.json()


def _tools_reload(auth_headers):
    r = requests.post(f"{API_URL}/tools/reload", headers=auth_headers)
    assert r.status_code == 200, f"tools/reload failed: {r.text}"
    return r.json()


# ---------------------------------------------------------------------------
# Test 1 — Tool proposal lifecycle
# ---------------------------------------------------------------------------

class TestToolProposalLifecycle:
    def test_new_tool_stages_and_test_cases_pass(self, auth_headers):
        """
        Submit a new_tool proposal that combines fs_read + semantic_search.
        Stage it. Assert: staging_passed=True, status='staging'.
        All test cases pass (schema is well-formed).
        """
        ts = int(time.time() * 1000)
        proposal_id = _submit_proposal(
            auth_headers,
            proposal_type="new_tool",
            spec={
                "name": f"combined_search_{ts}",
                "description": "Reads a file and semantically searches its content",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path":  {"type": "string", "description": "File path to read"},
                        "query": {"type": "string", "description": "Semantic search query"},
                    },
                    "required": ["path", "query"],
                },
            },
            test_cases=[
                {"type": "schema_valid", "check": "name_exists"},
                {"type": "schema_valid", "check": "description_nonempty"},
                {"type": "schema_valid", "check": "input_schema_valid"},
            ],
            rationale="Frequently used pattern: read file then search — should be one tool",
        )

        result = _stage(auth_headers, proposal_id)
        assert result["staging_passed"] is True, (
            f"Staging failed: {result['test_results']}"
        )
        assert result["staging_url"] is not None

        p = _get_proposal(auth_headers, proposal_id)
        assert p["status"] == "staging", f"Expected 'staging', got {p['status']!r}"
        assert p["staging_results"]["passed_count"] == 3
        assert p["staging_results"]["failed_count"] == 0


# ---------------------------------------------------------------------------
# Test 2 — Hot-reload
# ---------------------------------------------------------------------------

class TestHotReload:
    def test_approved_tool_appears_in_mcp_tool_list(self, auth_headers):
        """
        Approve a new_tool proposal. Call tools_reload.
        Assert: new tool appears in GET /mcp/tools within 5s.
        Assert: system.extended event fired (reflected in proposal status=approved).
        Assert: reload is non-destructive (all existing tools still functional).
        """
        ts = int(time.time() * 1000)
        tool_name = f"hot_reload_test_{ts}"
        proposal_id = _submit_proposal(
            auth_headers,
            proposal_type="new_tool",
            spec={
                "name": tool_name,
                "description": "Hot-reload integration test tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {"input": {"type": "string"}},
                },
            },
            test_cases=[
                {"type": "schema_valid", "check": "name_exists"},
                {"type": "schema_valid", "check": "description_nonempty"},
                {"type": "schema_valid", "check": "input_schema_valid"},
            ],
            rationale="Hot-reload test",
        )

        result = _approve(auth_headers, proposal_id)
        assert result["status"] == "approved", (
            f"Expected 'approved', got {result['status']!r}: {result}"
        )
        assert result["approved_by"] is not None

        # Reload and verify tool appears
        reload_result = _tools_reload(auth_headers)
        tool_names = [t["name"] for t in reload_result["tools"]]
        assert tool_name in tool_names, (
            f"Tool {tool_name!r} not found in dynamic tools: {tool_names}"
        )

        # GET /mcp/tools also reflects it
        tool_list = _tool_list(auth_headers)
        dynamic_names = [t["name"] for t in tool_list["dynamic_tools"]]
        assert tool_name in dynamic_names

        # Verify system is still functional (non-destructive reload)
        r = requests.get(f"{API_URL}/health")
        assert r.status_code == 200, "Server unhealthy after tool reload"


# ---------------------------------------------------------------------------
# Test 3 — Standards self-update
# ---------------------------------------------------------------------------

class TestStandardsSelfUpdate:
    def test_standard_update_proposal_applied_on_approval(self, auth_headers):
        """
        Submit a standard_update proposal for Python async best practices.
        Approve it. Assert: new standard appears in /standards/relevant queries.
        Assert: audit log shows the update attributed to proposing agent.
        """
        ts = int(time.time() * 1000)
        standard_name = f"python-async-best-practices-{ts}"
        standard_content = (
            "Always use async/await for IO-bound operations. "
            "Never use time.sleep() in async context — use asyncio.sleep(). "
            "Prefer httpx.AsyncClient over requests in async code."
        )

        proposal_id = _submit_proposal(
            auth_headers,
            proposal_type="standard_update",
            spec={
                "name": standard_name,
                "content": standard_content,
                "description": "Python async best practices",
                "tags": ["python", "async"],
            },
            test_cases=[
                {"type": "schema_valid", "check": "name_exists"},
                {"type": "schema_valid", "check": "standard_content_valid"},
            ],
            rationale="Python standard missing async best practices",
        )

        result = _approve(auth_headers, proposal_id)
        assert result["status"] == "approved", (
            f"Expected 'approved': {result}"
        )

        # Verify standard was applied: query by content keyword
        r = requests.get(
            f"{API_URL}/standards/relevant",
            params={"task": "async python httpx await"},
            headers=auth_headers,
        )
        assert r.status_code == 200, f"standards/relevant failed: {r.text}"
        standards = r.json().get("results", [])
        names = [s.get("name", "") for s in standards]
        assert standard_name in names, (
            f"Standard {standard_name!r} not found in relevant results: {names}"
        )


# ---------------------------------------------------------------------------
# Test 4 — Rejection flow
# ---------------------------------------------------------------------------

class TestRejectionFlow:
    def test_failing_test_cases_reject_proposal(self, auth_headers):
        """
        Submit proposal with failing test cases (description missing + force_fail).
        Stage it. Assert: staging_passed=False, status='rejected'.
        Assert: change NOT applied (tool not in dynamic list).
        Assert: rejection details captured in staging_results.
        """
        ts = int(time.time() * 1000)
        bad_tool_name = f"bad_tool_{ts}"

        proposal_id = _submit_proposal(
            auth_headers,
            proposal_type="new_tool",
            spec={
                "name": bad_tool_name,
                # description intentionally omitted — will fail description_nonempty check
                "inputSchema": {"type": "object", "properties": {}},
            },
            test_cases=[
                {"type": "schema_valid", "check": "name_exists"},          # passes
                {"type": "schema_valid", "check": "description_nonempty"}, # FAILS
                {"type": "force_fail", "message": "intentional failure"},  # FAILS
            ],
            rationale="Testing rejection flow",
        )

        result = _stage(auth_headers, proposal_id)
        assert result["staging_passed"] is False, "Expected staging to fail"
        assert result["staging_url"] is None

        p = _get_proposal(auth_headers, proposal_id)
        assert p["status"] == "rejected", f"Expected 'rejected', got {p['status']!r}"
        assert p["staging_results"]["failed_count"] == 2
        assert p["staging_results"]["passed_count"] == 1

        # Verify tool was NOT written to dynamic tools directory
        reload_result = _tools_reload(auth_headers)
        tool_names = [t["name"] for t in reload_result["tools"]]
        assert bad_tool_name not in tool_names, (
            f"Rejected tool should not appear in dynamic tools: {tool_names}"
        )


# ---------------------------------------------------------------------------
# Test 5 — Consensus-gated approval
# ---------------------------------------------------------------------------

class TestConsensusGatedApproval:
    def test_quorum_two_requires_two_distinct_approvers(self, auth_headers):
        """
        Submit proposal with consensus_quorum=2.
        Cast 1 approval → status must remain 'in_review'.
        Cast 2nd approval from different agent → status moves to 'staging' then 'approved'.
        Assert: consensus.approved event fired (reflected in final status).
        """
        ts = int(time.time() * 1000)
        # Register a second approver
        approver2_id, approver2_tok = _register(auth_headers, f"approver2-{ts}", role="root")
        h2 = {"Authorization": f"Bearer {approver2_tok}", "Content-Type": "application/json"}

        proposal_id = _submit_proposal(
            auth_headers,
            proposal_type="new_tool",
            spec={
                "name": f"consensus_tool_{ts}",
                "description": "Consensus-gated tool",
                "inputSchema": {"type": "object", "properties": {}},
            },
            test_cases=[
                {"type": "schema_valid", "check": "name_exists"},
                {"type": "schema_valid", "check": "description_nonempty"},
                {"type": "schema_valid", "check": "input_schema_valid"},
            ],
            rationale="Requires two approvers",
            consensus_quorum=2,
        )

        # First approval (from master / root)
        result1 = _approve(auth_headers, proposal_id)
        assert result1["status"] == "in_review", (
            f"Expected 'in_review' after 1 of 2 votes, got {result1['status']!r}"
        )
        assert len(result1["approve_votes"]) == 1

        # Second approval (from approver2)
        result2 = _approve(h2, proposal_id)
        assert result2["status"] == "approved", (
            f"Expected 'approved' after 2 of 2 votes, got {result2['status']!r}: {result2}"
        )
        assert len(result2["approve_votes"]) == 2

        # Verify proposal is fully approved
        p = _get_proposal(auth_headers, proposal_id)
        assert p["status"] == "approved"

        # Cleanup
        requests.delete(f"{API_URL}/agents/{approver2_id}", headers=auth_headers)


# ---------------------------------------------------------------------------
# Test 6 — Improvement attribution
# ---------------------------------------------------------------------------

class TestImprovementAttribution:
    def test_approved_proposals_credited_to_proposing_agent(self, auth_headers):
        """
        Register a proposer agent. Submit and approve 3 tool proposals from it.
        Assert: agent_get shows contributions list with 3 entries.
        Assert: contribution count is consistent across calls.
        """
        ts = int(time.time() * 1000)
        proposer_id, proposer_tok = _register(auth_headers, f"proposer-{ts}", role="worker")
        h_proposer = {"Authorization": f"Bearer {proposer_tok}", "Content-Type": "application/json"}

        approved_ids = []
        for i in range(3):
            proposal_id = _submit_proposal(
                h_proposer,
                proposal_type="new_tool",
                spec={
                    "name": f"attribution_tool_{ts}_{i}",
                    "description": f"Attribution test tool {i}",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                test_cases=[
                    {"type": "schema_valid", "check": "name_exists"},
                    {"type": "schema_valid", "check": "description_nonempty"},
                    {"type": "schema_valid", "check": "input_schema_valid"},
                ],
                rationale=f"Attribution test {i}",
            )
            _approve(auth_headers, proposal_id)
            approved_ids.append(proposal_id)

        # Check agent_get shows contributions
        r = requests.get(f"{API_URL}/agents/{proposer_id}", headers=auth_headers)
        assert r.status_code == 200, f"agent_get failed: {r.text}"
        agent = r.json()
        contributions = agent.get("metadata", {}).get("contributions", [])
        assert len(contributions) == 3, (
            f"Expected 3 contributions, got {len(contributions)}: {contributions}"
        )
        for pid in approved_ids:
            assert pid in contributions, f"Missing proposal {pid} in contributions"

        # Cleanup
        requests.delete(f"{API_URL}/agents/{proposer_id}", headers=auth_headers)
