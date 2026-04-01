"""
Integration tests for AgentOS v1.3.4: Multi-Agent Consensus.

Tests cover: propose, vote to acceptance, vote to rejection, early rejection
(quorum impossible), expiry, withdrawal, and list/status.

All tests are structural — they run without Ollama. Consensus is a
coordination mechanism and does not require model inference.

Run:
    PYTHONPATH=. pytest tests/integration/test_consensus.py -v -m integration
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


def _propose(headers, description, action, participants, required_votes, ttl_seconds=60.0):
    r = requests.post(f"{API_URL}/consensus/propose", json={
        "description":   description,
        "action":        action,
        "participants":  participants,
        "required_votes": required_votes,
        "ttl_seconds":   ttl_seconds,
    }, headers=headers)
    assert r.status_code == 200, f"propose failed: {r.text}"
    return r.json()["proposal_id"]


def _vote(headers, proposal_id, accept, reason=""):
    r = requests.post(f"{API_URL}/consensus/{proposal_id}/vote",
                      json={"accept": accept, "reason": reason},
                      headers=headers)
    return r


def _status(auth_headers, proposal_id):
    r = requests.get(f"{API_URL}/consensus/{proposal_id}", headers=auth_headers)
    assert r.status_code == 200, f"status failed: {r.text}"
    return r.json()


def _list(auth_headers, agent_id, include_resolved=False):
    r = requests.get(f"{API_URL}/agents/{agent_id}/consensus",
                     params={"include_resolved": str(include_resolved).lower()},
                     headers=auth_headers)
    assert r.status_code == 200, f"list failed: {r.text}"
    return r.json()["proposals"]


def _withdraw(headers, proposal_id):
    r = requests.delete(f"{API_URL}/consensus/{proposal_id}", headers=headers)
    return r


# ---------------------------------------------------------------------------
# Test 1 — Unanimous acceptance (2-of-2 quorum)
# ---------------------------------------------------------------------------

class TestConsensusAccepted:
    def test_two_agents_reach_consensus(self, auth_headers):
        """
        Register 2 voter agents. Proposer submits proposal requiring 2 votes.
        Both voters accept. Assert: status = accepted, result.accepted = True.
        """
        ts = int(time.time())
        id_a, tok_a = _register(auth_headers, f"con-voter-a-{ts}")
        id_b, tok_b = _register(auth_headers, f"con-voter-b-{ts}")
        headers_a = {"Authorization": f"Bearer {tok_a}"}
        headers_b = {"Authorization": f"Bearer {tok_b}"}
        try:
            proposal_id = _propose(
                headers_a,
                description="Deploy updated scheduler config",
                action={"type": "config_update", "key": "scheduler.max_workers", "value": 8},
                participants=[id_a, id_b],
                required_votes=2,
            )

            # Vote A
            r = _vote(headers_a, proposal_id, accept=True, reason="looks good")
            assert r.status_code == 200, f"vote A failed: {r.text}"
            tally = r.json()
            assert tally["current_accepts"] == 1
            assert tally["status"] == "pending"

            # Vote B — should tip to accepted
            r = _vote(headers_b, proposal_id, accept=True, reason="approved")
            assert r.status_code == 200, f"vote B failed: {r.text}"
            tally = r.json()
            assert tally["current_accepts"] == 2
            assert tally["status"] == "accepted", (
                f"Expected status=accepted after 2/2 votes, got {tally['status']}"
            )

            # Verify via GET
            proposal = _status(auth_headers, proposal_id)
            assert proposal["status"] == "accepted"
            assert proposal["result"]["accepted"] is True
            assert len(proposal["votes"]) == 2

        finally:
            _terminate(auth_headers, id_a)
            _terminate(auth_headers, id_b)


# ---------------------------------------------------------------------------
# Test 2 — Majority rejection (quorum impossible)
# ---------------------------------------------------------------------------

class TestConsensusRejected:
    def test_early_rejection_when_quorum_impossible(self, auth_headers):
        """
        3 participants, required_votes=3 (unanimous). One voter rejects.
        Assert: status = rejected immediately (quorum impossible — even if the
        remaining 2 accept, total accepts can never reach 3).
        """
        ts = int(time.time())
        id_a, tok_a = _register(auth_headers, f"con-rej-a-{ts}")
        id_b, tok_b = _register(auth_headers, f"con-rej-b-{ts}")
        id_c, tok_c = _register(auth_headers, f"con-rej-c-{ts}")
        headers_a = {"Authorization": f"Bearer {tok_a}"}
        headers_b = {"Authorization": f"Bearer {tok_b}"}
        try:
            proposal_id = _propose(
                headers_a,
                description="Terminate all idle workers",
                action={"type": "mass_terminate", "filter": "idle"},
                participants=[id_a, id_b, id_c],
                required_votes=3,  # unanimous required
            )

            # Agent A rejects — now max possible accepts = 2 < 3 required
            r = _vote(headers_a, proposal_id, accept=False, reason="too aggressive")
            assert r.status_code == 200, f"vote A failed: {r.text}"
            result = r.json()
            assert result["status"] == "rejected", (
                f"Expected immediate rejection when quorum impossible, got {result['status']}"
            )

            # Further votes should be refused (proposal not pending)
            r2 = _vote(headers_b, proposal_id, accept=True)
            assert r2.status_code == 400, (
                f"Expected 400 on vote to non-pending proposal, got {r2.status_code}"
            )

            # Verify via GET
            proposal = _status(auth_headers, proposal_id)
            assert proposal["status"] == "rejected"
            assert proposal["result"]["accepted"] is False

        finally:
            _terminate(auth_headers, id_a)
            _terminate(auth_headers, id_b)
            _terminate(auth_headers, id_c)


# ---------------------------------------------------------------------------
# Test 3 — Partial acceptance (majority, not unanimous)
# ---------------------------------------------------------------------------

class TestConsensusPartialQuorum:
    def test_majority_quorum_reached(self, auth_headers):
        """
        3 participants, required_votes=2. Two accept, one rejects.
        Assert: status = accepted after 2 accepts, third vote not required.
        """
        ts = int(time.time())
        id_a, tok_a = _register(auth_headers, f"con-maj-a-{ts}")
        id_b, tok_b = _register(auth_headers, f"con-maj-b-{ts}")
        id_c, tok_c = _register(auth_headers, f"con-maj-c-{ts}")
        headers_a = {"Authorization": f"Bearer {tok_a}"}
        headers_b = {"Authorization": f"Bearer {tok_b}"}
        headers_c = {"Authorization": f"Bearer {tok_c}"}
        try:
            proposal_id = _propose(
                headers_a,
                description="Increase memory heap TTL to 600s",
                action={"type": "config_update", "key": "heap.default_ttl", "value": 600},
                participants=[id_a, id_b, id_c],
                required_votes=2,
            )

            _vote(headers_a, proposal_id, accept=True)
            r = _vote(headers_b, proposal_id, accept=True)
            assert r.status_code == 200
            assert r.json()["status"] == "accepted", (
                f"Expected accepted after 2/2 votes, got {r.json()['status']}"
            )

            # Third vote should be refused — proposal already resolved
            r3 = _vote(headers_c, proposal_id, accept=False)
            assert r3.status_code == 400

        finally:
            _terminate(auth_headers, id_a)
            _terminate(auth_headers, id_b)
            _terminate(auth_headers, id_c)


# ---------------------------------------------------------------------------
# Test 4 — Only participants may vote
# ---------------------------------------------------------------------------

class TestConsensusParticipantEnforcement:
    def test_non_participant_cannot_vote(self, auth_headers):
        """
        Create proposal with 1 participant. A different agent (outsider) tries to vote.
        Assert: 400 — not a participant.
        """
        ts = int(time.time())
        id_voter, tok_voter   = _register(auth_headers, f"con-p-voter-{ts}")
        id_outsider, tok_out  = _register(auth_headers, f"con-p-outsider-{ts}")
        headers_voter   = {"Authorization": f"Bearer {tok_voter}"}
        headers_outsider = {"Authorization": f"Bearer {tok_out}"}
        try:
            proposal_id = _propose(
                headers_voter,
                description="Rotate API token",
                action={"type": "token_rotate"},
                participants=[id_voter],
                required_votes=1,
            )

            r = _vote(headers_outsider, proposal_id, accept=True)
            assert r.status_code == 400, (
                f"Expected 400 for non-participant vote, got {r.status_code}: {r.text}"
            )
            assert "participant" in r.json()["detail"].lower(), (
                f"Expected 'participant' in error detail: {r.json()['detail']}"
            )

        finally:
            _terminate(auth_headers, id_voter)
            _terminate(auth_headers, id_outsider)


# ---------------------------------------------------------------------------
# Test 5 — Duplicate vote rejected
# ---------------------------------------------------------------------------

class TestConsensusDuplicateVote:
    def test_agent_cannot_vote_twice(self, auth_headers):
        """
        Agent votes once. Same agent votes again. Assert: 400 — already voted.
        """
        ts = int(time.time())
        id_a, tok_a = _register(auth_headers, f"con-dupe-{ts}")
        id_b, tok_b = _register(auth_headers, f"con-dupe-b-{ts}")
        headers_a = {"Authorization": f"Bearer {tok_a}"}
        try:
            proposal_id = _propose(
                headers_a,
                description="Enable streaming for all tasks",
                action={"type": "feature_flag", "flag": "streaming", "value": True},
                participants=[id_a, id_b],
                required_votes=2,
            )

            _vote(headers_a, proposal_id, accept=True)
            r2 = _vote(headers_a, proposal_id, accept=False)
            assert r2.status_code == 400, (
                f"Expected 400 for duplicate vote, got {r2.status_code}: {r2.text}"
            )
            assert "already voted" in r2.json()["detail"].lower(), (
                f"Expected 'already voted' in error: {r2.json()['detail']}"
            )

        finally:
            _terminate(auth_headers, id_a)
            _terminate(auth_headers, id_b)


# ---------------------------------------------------------------------------
# Test 6 — Proposal withdrawal
# ---------------------------------------------------------------------------

class TestConsensusWithdrawal:
    def test_proposer_can_withdraw_pending_proposal(self, auth_headers):
        """
        Proposer submits proposal. Before any votes, withdraws it.
        Assert: status = rejected, reason = withdrawn by proposer.
        Assert: votes are now refused (proposal not pending).
        """
        ts = int(time.time())
        id_a, tok_a = _register(auth_headers, f"con-wd-a-{ts}")
        id_b, tok_b = _register(auth_headers, f"con-wd-b-{ts}")
        headers_a = {"Authorization": f"Bearer {tok_a}"}
        headers_b = {"Authorization": f"Bearer {tok_b}"}
        try:
            proposal_id = _propose(
                headers_a,
                description="Roll back to v1.3.2",
                action={"type": "rollback", "target_version": "v1.3.2"},
                participants=[id_a, id_b],
                required_votes=2,
            )

            r = _withdraw(headers_a, proposal_id)
            assert r.status_code == 200, f"withdraw failed: {r.text}"
            assert r.json()["ok"] is True

            proposal = _status(auth_headers, proposal_id)
            assert proposal["status"] == "rejected"
            assert "withdrawn" in proposal["result"]["reason"]

            # Voting now refused
            r2 = _vote(headers_b, proposal_id, accept=True)
            assert r2.status_code == 400

        finally:
            _terminate(auth_headers, id_a)
            _terminate(auth_headers, id_b)


# ---------------------------------------------------------------------------
# Test 7 — List and status
# ---------------------------------------------------------------------------

class TestConsensusListAndStatus:
    def test_list_returns_agent_proposals(self, auth_headers):
        """
        Agent proposes two proposals, is participant in a third.
        Assert: list_for_agent returns all three while pending.
        Assert: proposal status fields are accurate.
        """
        ts = int(time.time())
        id_a, tok_a = _register(auth_headers, f"con-ls-a-{ts}")
        id_b, tok_b = _register(auth_headers, f"con-ls-b-{ts}")
        headers_a = {"Authorization": f"Bearer {tok_a}"}
        headers_b = {"Authorization": f"Bearer {tok_b}"}
        try:
            p1 = _propose(headers_a, "proposal one",
                          {"op": 1}, [id_a], required_votes=1)
            p2 = _propose(headers_a, "proposal two",
                          {"op": 2}, [id_a, id_b], required_votes=2)
            # B proposes, A is participant
            p3 = _propose(headers_b, "proposal three",
                          {"op": 3}, [id_a, id_b], required_votes=1)

            proposals_a = _list(auth_headers, id_a)
            ids_a = {p["proposal_id"] for p in proposals_a}
            assert p1 in ids_a, f"p1 missing from agent A list: {ids_a}"
            assert p2 in ids_a, f"p2 missing from agent A list: {ids_a}"
            assert p3 in ids_a, f"p3 (participant) missing from agent A list: {ids_a}"

            # Status endpoint returns full detail
            detail = _status(auth_headers, p1)
            assert detail["proposal_id"] == p1
            assert detail["status"] == "pending"
            assert detail["required_votes"] == 1
            assert detail["proposer_id"] == id_a

        finally:
            _terminate(auth_headers, id_a)
            _terminate(auth_headers, id_b)
