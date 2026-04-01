"""
Integration tests for AgentOS v3.2.0: Distributed Consensus.

Tests verify Byzantine fault tolerance, cross-node voting,
distributed leader election, and partition recovery.

Run:
    PYTHONPATH=. pytest tests/integration/test_distributed_consensus.py -v
"""

import pytest
import shutil
import tempfile
import os
from pathlib import Path

try:
    from agents.distributed_consensus import (
        DistributedConsensus,
        Proposal,
        Vote,
        ConsensusResult,
        LeaderElection,
    )

    CONSENSUS_AVAILABLE = True
except ImportError:
    CONSENSUS_AVAILABLE = False


@pytest.fixture(autouse=True)
def fresh_consensus_storage(monkeypatch):
    """Fresh temporary directory for consensus storage."""
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)

    import agents.distributed_consensus as consensus_module

    consensus_module.CONSENSUS_PATH = Path(tmpdir) / "consensus"

    yield

    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)


@pytest.mark.skipif(not CONSENSUS_AVAILABLE, reason="distributed consensus not available")
class TestProposalManagement:
    """Test proposal submission and retrieval."""

    def test_submit_proposal(self):
        """Submit a proposal for consensus."""
        consensus = DistributedConsensus()

        proposal_id = consensus.submit_proposal(
            proposer_id="node-1",
            proposal_type="capability",
            proposal_content={"name": "new_capability", "description": "test"},
        )

        assert proposal_id.startswith("prop-")

    def test_get_proposal(self):
        """Retrieve submitted proposal."""
        consensus = DistributedConsensus()

        proposal_id = consensus.submit_proposal(
            proposer_id="node-1",
            proposal_type="capability",
            proposal_content={"name": "cap"},
        )

        proposal = consensus.get_proposal(proposal_id)
        assert proposal is not None
        assert proposal.proposal_id == proposal_id
        assert proposal.proposer_id == "node-1"
        assert proposal.status == "pending"

    def test_get_nonexistent_proposal(self):
        """Getting nonexistent proposal returns None."""
        consensus = DistributedConsensus()
        proposal = consensus.get_proposal("prop-nonexistent")
        assert proposal is None

    def test_get_pending_proposals(self):
        """Get all pending (non-expired) proposals."""
        consensus = DistributedConsensus()

        id1 = consensus.submit_proposal(
            "node-1", "capability", {"name": "cap1"}, expires_in_seconds=300
        )
        id2 = consensus.submit_proposal(
            "node-1", "goal", {"name": "goal1"}, expires_in_seconds=300
        )

        pending = consensus.get_pending_proposals()
        assert len(pending) == 2

    def test_proposal_expiration(self):
        """Expired proposals are not included in pending."""
        consensus = DistributedConsensus()

        # Create proposal that expires immediately
        consensus.submit_proposal(
            "node-1", "capability", {"name": "cap"}, expires_in_seconds=0
        )

        pending = consensus.get_pending_proposals()
        assert len(pending) == 0


@pytest.mark.skipif(not CONSENSUS_AVAILABLE, reason="distributed consensus not available")
class TestVotingMechanism:
    """Test voting on proposals."""

    def test_vote_on_proposal(self):
        """Vote on a proposal."""
        consensus = DistributedConsensus()

        proposal_id = consensus.submit_proposal(
            "node-1", "capability", {"name": "cap"}
        )

        vote_id = consensus.vote(
            proposal_id, voter_id="node-2", decision=True, confidence=0.95
        )

        assert vote_id.startswith("vote-")

    def test_get_votes_for_proposal(self):
        """Retrieve all votes for a proposal."""
        consensus = DistributedConsensus()

        proposal_id = consensus.submit_proposal(
            "node-1", "capability", {"name": "cap"}
        )

        consensus.vote(proposal_id, "node-2", True)
        consensus.vote(proposal_id, "node-3", True)
        consensus.vote(proposal_id, "node-4", False)

        votes = consensus.get_votes_for_proposal(proposal_id)
        assert len(votes) == 3
        assert sum(1 for v in votes if v.decision) == 2
        assert sum(1 for v in votes if not v.decision) == 1

    def test_voter_consensus_rate(self):
        """Calculate how often voter aligns with consensus."""
        consensus = DistributedConsensus()

        # Create two proposals
        prop1 = consensus.submit_proposal("node-1", "capability", {"name": "cap1"})
        prop2 = consensus.submit_proposal("node-1", "capability", {"name": "cap2"})

        # Node-2 votes True on both, consensus is True on both
        consensus.vote(prop1, "node-2", True)
        consensus.vote(prop1, "node-3", True)
        consensus.vote(prop1, "node-4", True)

        consensus.vote(prop2, "node-2", True)
        consensus.vote(prop2, "node-3", True)
        consensus.vote(prop2, "node-4", True)

        rate = consensus.get_voter_consensus_rate("node-2")
        assert rate >= 0.0 and rate <= 1.0


@pytest.mark.skipif(not CONSENSUS_AVAILABLE, reason="distributed consensus not available")
class TestConsensusCheck:
    """Test consensus reaching and result."""

    def test_unanimous_approval_consensus(self):
        """Consensus reached with unanimous approval."""
        consensus = DistributedConsensus()

        proposal_id = consensus.submit_proposal(
            "node-1", "capability", {"name": "cap"}, required_quorum=0.67
        )

        # All nodes vote yes
        for node in ["node-2", "node-3", "node-4"]:
            consensus.vote(proposal_id, node, True)

        # Check if consensus is reached
        result = consensus.check_consensus_reached(proposal_id)
        assert result is not None
        assert result.approved
        assert result.approval_percent == 1.0

    def test_majority_approval_consensus(self):
        """Consensus reached with majority approval."""
        consensus = DistributedConsensus()

        proposal_id = consensus.submit_proposal(
            "node-1", "capability", {"name": "cap"}, required_quorum=0.65
        )

        # 2 yes, 1 no -> 67% approval (higher than 65% quorum)
        consensus.vote(proposal_id, "node-2", True)
        consensus.vote(proposal_id, "node-3", True)
        consensus.vote(proposal_id, "node-4", False)

        # Check consensus
        result = consensus.check_consensus_reached(proposal_id)
        assert result is not None
        assert result.approved
        assert result.total_votes == 3
        assert result.approval_votes == 2

    def test_consensus_rejection(self):
        """Consensus reached with rejection."""
        consensus = DistributedConsensus()

        proposal_id = consensus.submit_proposal(
            "node-1", "capability", {"name": "cap"}, required_quorum=0.67
        )

        # 0 yes, 3 no -> 0% approval (triggers rejection)
        consensus.vote(proposal_id, "node-2", False)
        consensus.vote(proposal_id, "node-3", False)
        consensus.vote(proposal_id, "node-4", False)

        result = consensus.check_consensus_reached(proposal_id)
        assert result is not None
        assert not result.approved
        assert result.approval_percent == 0.0


@pytest.mark.skipif(not CONSENSUS_AVAILABLE, reason="distributed consensus not available")
class TestByzantineFaultTolerance:
    """Test Byzantine fault tolerance."""

    def test_byzantine_faulty_estimation(self):
        """Estimate Byzantine faulty nodes from vote count."""
        consensus = DistributedConsensus()

        # With 7 votes, we can tolerate 2 faulty: f = (7-1)/3 = 2
        proposal_id = consensus.submit_proposal(
            "node-1", "capability", {"name": "cap"}, required_quorum=0.67
        )

        for i in range(7):
            consensus.vote(proposal_id, f"node-{i}", True)

        result = consensus.check_consensus_reached(proposal_id)
        assert result is not None
        assert result.total_votes == 7
        # f = (n-1)/3, so with 7 votes: f = 2
        assert result.byzantine_faulty == 2

    def test_bft_resilience_with_minority_dissent(self):
        """System tolerates minority dissent (Byzantine nodes)."""
        consensus = DistributedConsensus()

        proposal_id = consensus.submit_proposal(
            "node-1", "capability", {"name": "cap"}, required_quorum=0.67
        )

        # 10 nodes: 7 honest (yes), 3 Byzantine (no)
        # With BFT tolerance f=2, we still have majority (70% > 67%)
        for i in range(7):
            consensus.vote(proposal_id, f"honest-{i}", True)
        for i in range(3):
            consensus.vote(proposal_id, f"byzantine-{i}", False)

        result = consensus.check_consensus_reached(proposal_id)
        assert result is not None
        assert result.approved
        assert result.approval_percent >= 0.67


@pytest.mark.skipif(not CONSENSUS_AVAILABLE, reason="distributed consensus not available")
class TestLeaderElection:
    """Test distributed leader election."""

    def test_elect_leader(self):
        """Elect leader from candidates."""
        consensus = DistributedConsensus()

        candidates = ["node-1", "node-2", "node-3"]
        elected, election = consensus.elect_leader(candidates)

        assert elected in candidates
        assert election.elected_leader == elected
        assert len(election.eligible_nodes) == 3

    def test_leader_election_record(self):
        """Leader election creates audit trail."""
        consensus = DistributedConsensus()

        candidates = ["node-a", "node-b", "node-c"]
        elected, election = consensus.elect_leader(candidates)

        retrieved = consensus.get_leader_for_node(elected)
        assert retrieved is not None
        assert retrieved.elected_leader == elected

    def test_multiple_leader_elections(self):
        """Support multiple sequential elections."""
        consensus = DistributedConsensus()

        # First election
        elected1, _ = consensus.elect_leader(["node-1", "node-2", "node-3"])

        # Second election with different candidates
        elected2, _ = consensus.elect_leader(["node-4", "node-5", "node-6"])

        assert elected1 in ["node-1", "node-2", "node-3"]
        assert elected2 in ["node-4", "node-5", "node-6"]


@pytest.mark.skipif(not CONSENSUS_AVAILABLE, reason="distributed consensus not available")
class TestPartitionRecovery:
    """Test network partition detection and recovery."""

    def test_detect_partition(self):
        """Detect network partition."""
        consensus = DistributedConsensus()

        all_nodes = ["node-1", "node-2", "node-3", "node-4"]
        active_nodes = ["node-1", "node-2"]  # Partition: 2/4 nodes

        partition_state = consensus.detect_partition(active_nodes, all_nodes)

        assert partition_state["node-1"] is True
        assert partition_state["node-2"] is True
        assert partition_state["node-3"] is False
        assert partition_state["node-4"] is False

    def test_resolve_partition_majority(self):
        """Majority partition can proceed."""
        consensus = DistributedConsensus()

        all_nodes = ["node-1", "node-2", "node-3", "node-4"]
        active_nodes = ["node-1", "node-2", "node-3"]  # 3/4 is majority

        partition_state = consensus.detect_partition(active_nodes, all_nodes)
        decision = consensus.resolve_partition(partition_state, active_nodes)

        assert decision == "proceed"

    def test_resolve_partition_minority(self):
        """Minority partition pauses."""
        consensus = DistributedConsensus()

        all_nodes = ["node-1", "node-2", "node-3", "node-4"]
        active_nodes = ["node-1"]  # 1/4 is minority

        partition_state = consensus.detect_partition(active_nodes, all_nodes)
        decision = consensus.resolve_partition(partition_state, active_nodes)

        assert decision == "pause"

    def test_split_brain_prevention(self):
        """Prevent split-brain: only majority partition proceeds."""
        consensus = DistributedConsensus()

        # Partition A: 2 nodes (minority)
        partition_a = ["node-1", "node-2"]
        all_nodes = ["node-1", "node-2", "node-3", "node-4"]

        state_a = consensus.detect_partition(partition_a, all_nodes)
        decision_a = consensus.resolve_partition(state_a, partition_a)

        # Partition B: 2 nodes (minority)
        partition_b = ["node-3", "node-4"]
        state_b = consensus.detect_partition(partition_b, all_nodes)
        decision_b = consensus.resolve_partition(state_b, partition_b)

        # Both should pause (neither has majority)
        assert decision_a == "pause"
        assert decision_b == "pause"


@pytest.mark.skipif(not CONSENSUS_AVAILABLE, reason="distributed consensus not available")
class TestMultiProposalScenario:
    """Test consensus with multiple proposals."""

    def test_multiple_concurrent_proposals(self):
        """Multiple proposals can reach consensus concurrently."""
        consensus = DistributedConsensus()

        # Submit 3 proposals
        proposals = [
            consensus.submit_proposal(
                "node-1", "capability", {"name": f"cap-{i}"}
            )
            for i in range(3)
        ]

        # Vote on all proposals
        for proposal_id in proposals:
            for voter in ["node-2", "node-3", "node-4"]:
                consensus.vote(proposal_id, voter, True)

        # All should be approved
        for proposal_id in proposals:
            result = consensus.check_consensus_reached(proposal_id)
            assert result is not None
            assert result.approved


@pytest.mark.skipif(not CONSENSUS_AVAILABLE, reason="distributed consensus not available")
class TestFullConsensusScenario:
    """Test complete consensus scenario with all components."""

    def test_full_distributed_governance(self):
        """
        Full scenario: 5 nodes, 3 proposals, consensus, leadership.

        - Submit capability, goal, and policy proposals
        - Vote across all nodes
        - Reach consensus on all
        - Elect leader
        - Detect and resolve partition
        """
        consensus = DistributedConsensus()

        # 1. Submit proposals
        cap_prop = consensus.submit_proposal(
            "node-1", "capability", {"name": "distributed_capability"}
        )
        goal_prop = consensus.submit_proposal(
            "node-2", "goal", {"objective": "scale to 10 nodes"}
        )
        policy_prop = consensus.submit_proposal(
            "node-3", "policy", {"rule": "require 2/3 quorum"}
        )

        # 2. Vote on proposals
        nodes = ["node-1", "node-2", "node-3", "node-4", "node-5"]
        for proposal_id in [cap_prop, goal_prop, policy_prop]:
            for node in nodes:
                consensus.vote(proposal_id, node, True)

        # 3. Verify consensus reached
        for proposal_id in [cap_prop, goal_prop, policy_prop]:
            result = consensus.check_consensus_reached(proposal_id)
            assert result is not None
            assert result.approved

        # 4. Elect leader from nodes
        elected_leader, election = consensus.elect_leader(nodes)
        assert elected_leader in nodes

        # 5. Simulate partition
        active = ["node-1", "node-2", "node-3"]  # Majority
        partition_state = consensus.detect_partition(active, nodes)
        decision = consensus.resolve_partition(partition_state, active)
        assert decision == "proceed"

        # 6. Verify state persistence
        # After consensus is reached, proposals are no longer "pending"
        # Instead, verify they have results
        for proposal_id in [cap_prop, goal_prop, policy_prop]:
            result = consensus.get_consensus_result(proposal_id)
            assert result is not None
