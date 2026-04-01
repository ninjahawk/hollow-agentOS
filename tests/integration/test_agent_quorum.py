"""
Integration tests for AgentOS v2.3.0: Agent-Quorum Governance.

Tests verify multi-agent consensus, proposal voting, and quorum-based system governance.

Run:
    PYTHONPATH=. pytest tests/integration/test_agent_quorum.py -v -m integration
"""

import pytest
import time
import shutil
import tempfile
import os
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.agent_quorum import AgentQuorum, ProposalRecord
    AGENT_QUORUM_AVAILABLE = True
except ImportError:
    AGENT_QUORUM_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not AGENT_QUORUM_AVAILABLE,
    reason="sentence-transformers not available"
)


@pytest.fixture(autouse=True)
def fresh_quorum_storage(monkeypatch):
    """
    Provide a fresh temporary directory for quorum storage in each test.
    Isolates tests so they don't interfere with each other's proposals.
    """
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)

    # Also patch the module-level QUORUM_PATH
    import agents.agent_quorum as quorum_module
    quorum_module.QUORUM_PATH = Path(tmpdir) / "quorum"

    yield

    # Cleanup
    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Test 1 — Create and retrieve proposal
# ---------------------------------------------------------------------------

class TestAgentQuorumPropose:
    def test_create_and_get_proposal(self):
        """
        Create a proposal. Retrieve it by ID.
        Assert: proposal returned exactly, status is 'pending'.
        """
        quorum = AgentQuorum()
        proposer_id = "agent-alice"
        description = "add rate limiting capability to protect against overload"

        proposal_id = quorum.propose(proposer_id, "capability", description, {"name": "rate_limiter"})
        assert proposal_id.startswith("prop-")

        proposal = quorum.get_proposal(proposal_id)
        assert proposal is not None
        assert proposal.proposer_id == proposer_id
        assert proposal.description == description
        assert proposal.status == "pending"
        assert proposal.proposal_type == "capability"


# ---------------------------------------------------------------------------
# Test 2 — Vote on proposals
# ---------------------------------------------------------------------------

class TestAgentQuorumVoting:
    def test_record_votes_on_proposal(self):
        """
        Create a proposal, record votes from multiple agents.
        Assert: votes are tracked correctly.
        """
        quorum = AgentQuorum()
        proposer_id = "agent-alice"

        proposal_id = quorum.propose(proposer_id, "capability", "add new feature", {})

        # Record votes
        assert quorum.vote(proposal_id, "agent-alice", True)   # yes
        assert quorum.vote(proposal_id, "agent-bob", True)     # yes
        assert quorum.vote(proposal_id, "agent-carol", False)  # no
        assert quorum.vote(proposal_id, "agent-dave", True)    # yes

        proposal = quorum.get_proposal(proposal_id)
        assert len(proposal.votes) == 4
        assert proposal.votes["agent-alice"] is True
        assert proposal.votes["agent-bob"] is True
        assert proposal.votes["agent-carol"] is False
        assert proposal.votes["agent-dave"] is True

    def test_voting_status(self):
        """
        Create proposal, record votes, check voting status.
        Assert: vote counts are correct.
        """
        quorum = AgentQuorum()

        proposal_id = quorum.propose("agent-alice", "goal_change", "change priority", {})

        quorum.vote(proposal_id, "agent-alice", True)
        quorum.vote(proposal_id, "agent-bob", True)
        quorum.vote(proposal_id, "agent-carol", False)

        yes_votes, no_votes, abstain_votes, status = quorum.get_voting_status(proposal_id)

        assert yes_votes == 2
        assert no_votes == 1
        assert abstain_votes == 0
        assert status == "pending"


# ---------------------------------------------------------------------------
# Test 3 — Proposal finalization
# ---------------------------------------------------------------------------

class TestAgentQuorumFinalize:
    def test_approve_proposal_with_majority(self):
        """
        Create proposal, get majority yes votes, finalize.
        Assert: proposal status changes to 'approved'.
        """
        quorum = AgentQuorum(quorum_percentage=0.5)  # 50% quorum

        proposal_id = quorum.propose("agent-alice", "capability", "new feature", {})

        # Get majority votes
        quorum.vote(proposal_id, "agent-alice", True)
        quorum.vote(proposal_id, "agent-bob", True)
        quorum.vote(proposal_id, "agent-carol", False)

        # Finalize
        result = quorum.finalize_proposal(proposal_id)
        assert result is True  # Approved

        proposal = quorum.get_proposal(proposal_id)
        assert proposal.status == "approved"
        assert proposal.finalized_at is not None

    def test_reject_proposal_without_quorum(self):
        """
        Create proposal, don't get enough votes, finalize.
        Assert: proposal status changes to 'rejected'.
        """
        quorum = AgentQuorum(quorum_percentage=0.66)  # 66% quorum

        proposal_id = quorum.propose("agent-alice", "capability", "new feature", {})

        # Only 1 yes vote (not enough for quorum)
        quorum.vote(proposal_id, "agent-alice", True)
        quorum.vote(proposal_id, "agent-bob", False)

        # Finalize
        result = quorum.finalize_proposal(proposal_id)
        assert result is False  # Not enough votes

        proposal = quorum.get_proposal(proposal_id)
        assert proposal.status == "rejected"

    def test_reject_proposal_without_majority(self):
        """
        Create proposal, get more no votes than yes.
        Assert: proposal is rejected despite having enough votes.
        """
        quorum = AgentQuorum(quorum_percentage=0.5)

        proposal_id = quorum.propose("agent-alice", "capability", "new feature", {})

        quorum.vote(proposal_id, "agent-alice", True)
        quorum.vote(proposal_id, "agent-bob", False)
        quorum.vote(proposal_id, "agent-carol", False)

        result = quorum.finalize_proposal(proposal_id)
        assert result is False  # No votes don't meet threshold

        proposal = quorum.get_proposal(proposal_id)
        assert proposal.status == "rejected"


# ---------------------------------------------------------------------------
# Test 4 — List pending proposals
# ---------------------------------------------------------------------------

class TestAgentQuorumList:
    def test_list_pending_proposals(self):
        """
        Create multiple proposals with different statuses.
        Call list_pending_proposals().
        Assert: returns only pending proposals.
        """
        quorum = AgentQuorum()

        # Create proposals
        p1_id = quorum.propose("agent-alice", "capability", "feature 1", {})
        p2_id = quorum.propose("agent-bob", "goal_change", "feature 2", {})
        p3_id = quorum.propose("agent-carol", "resource", "feature 3", {})

        # Finalize one
        quorum.vote(p3_id, "agent-carol", True)
        quorum.vote(p3_id, "agent-alice", True)
        quorum.finalize_proposal(p3_id)

        pending = quorum.get_pending_proposals(limit=10)

        assert len(pending) == 2  # p1 and p2 still pending
        assert all(p.status == "pending" for p in pending)


# ---------------------------------------------------------------------------
# Test 5 — Withdraw proposal
# ---------------------------------------------------------------------------

class TestAgentQuorumWithdraw:
    def test_proposer_can_withdraw(self):
        """
        Create proposal, withdrawer is proposer.
        Assert: proposal status changes to 'withdrawn'.
        """
        quorum = AgentQuorum()
        proposer_id = "agent-alice"

        proposal_id = quorum.propose(proposer_id, "capability", "new feature", {})

        result = quorum.withdraw_proposal(proposal_id, proposer_id)
        assert result is True

        proposal = quorum.get_proposal(proposal_id)
        assert proposal.status == "withdrawn"

    def test_non_proposer_cannot_withdraw(self):
        """
        Create proposal, withdrawer is not proposer.
        Assert: withdraw fails.
        """
        quorum = AgentQuorum()

        proposal_id = quorum.propose("agent-alice", "capability", "new feature", {})

        result = quorum.withdraw_proposal(proposal_id, "agent-bob")  # Different agent
        assert result is False

        proposal = quorum.get_proposal(proposal_id)
        assert proposal.status == "pending"  # Unchanged


# ---------------------------------------------------------------------------
# Test 6 — Proposal types
# ---------------------------------------------------------------------------

class TestAgentQuorumProposalTypes:
    def test_different_proposal_types(self):
        """
        Create proposals of different types.
        Assert: each type is stored correctly.
        """
        quorum = AgentQuorum()

        types = ["capability", "goal_change", "resource", "policy"]
        proposal_ids = {}

        for ptype in types:
            proposal_id = quorum.propose("agent-alice", ptype, f"proposal for {ptype}", {})
            proposal_ids[ptype] = proposal_id

        for ptype in types:
            proposal = quorum.get_proposal(proposal_ids[ptype])
            assert proposal.proposal_type == ptype


# ---------------------------------------------------------------------------
# Test 7 — Voting history
# ---------------------------------------------------------------------------

class TestAgentQuorumHistory:
    def test_voting_history_tracked(self):
        """
        Create proposal, record votes, check voting history.
        Assert: all votes logged in order.
        """
        quorum = AgentQuorum()

        proposal_id = quorum.propose("agent-alice", "capability", "new feature", {})

        quorum.vote(proposal_id, "agent-alice", True)
        time.sleep(0.01)
        quorum.vote(proposal_id, "agent-bob", False)
        time.sleep(0.01)
        quorum.vote(proposal_id, "agent-carol", True)

        history = quorum.get_proposal_history(proposal_id)

        assert len(history) == 3
        assert history[0]["voter_id"] == "agent-alice"
        assert history[1]["voter_id"] == "agent-bob"
        assert history[2]["voter_id"] == "agent-carol"
        assert history[0]["vote"] is True
        assert history[1]["vote"] is False
        assert history[2]["vote"] is True


# ---------------------------------------------------------------------------
# Test 8 — Proposal with payload
# ---------------------------------------------------------------------------

class TestAgentQuorumPayload:
    def test_proposal_stores_payload(self):
        """
        Create proposal with complex payload.
        Assert: payload is stored and retrievable.
        """
        quorum = AgentQuorum()

        payload = {
            "name": "advanced_cache",
            "version": "2.0",
            "parameters": {"ttl": 3600, "max_size": 1000000},
            "tags": ["performance", "optimization"],
        }

        proposal_id = quorum.propose("agent-alice", "capability", "add caching", payload)

        proposal = quorum.get_proposal(proposal_id)
        assert proposal.payload == payload
        assert proposal.payload["version"] == "2.0"
        assert proposal.payload["parameters"]["ttl"] == 3600
