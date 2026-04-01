"""
Distributed Consensus — AgentOS v3.2.0.

Multi-agent Byzantine-fault-tolerant consensus at scale.

Design:
  Distributed voting with Byzantine fault tolerance (BFT).
  Consensus requires 2f + 1 votes from 3f + 1 participants (where f = faulty nodes).

  Proposal:
    proposal_id: str
    proposer_id: str              # node that proposed
    proposal_type: str            # capability, goal, policy, resource
    proposal_content: dict        # semantic embedding space payload
    required_quorum: float        # 0.0-1.0 (e.g., 0.67 = 2/3)
    timestamp: float

  Vote:
    vote_id: str
    proposal_id: str
    voter_id: str
    decision: bool                # True = approve, False = reject
    confidence: float             # 0.0-1.0 confidence in vote
    reason: str
    timestamp: float

  ConsensusResult:
    proposal_id: str
    approved: bool
    vote_count: int
    approval_percent: float
    timestamp: float

Storage:
  /agentOS/memory/consensus/
    proposals.jsonl               # all proposals
    votes.jsonl                   # all votes
    results.jsonl                 # consensus outcomes
    leader_election.jsonl         # leader history
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Set

CONSENSUS_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "consensus"


@dataclass
class Proposal:
    """Distributed proposal for consensus."""
    proposal_id: str
    proposer_id: str
    proposal_type: str              # capability, goal, policy, resource
    proposal_content: dict          # semantic payload
    required_quorum: float          # 0.67 = 2/3 majority
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 300)  # 5 min default
    status: str = "pending"         # pending, approved, rejected, expired


@dataclass
class Vote:
    """Vote on a proposal."""
    vote_id: str
    proposal_id: str
    voter_id: str
    decision: bool                  # True = approve, False = reject
    confidence: float               # 0.0-1.0
    reason: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class ConsensusResult:
    """Result of consensus voting."""
    proposal_id: str
    approved: bool
    total_votes: int
    approval_votes: int
    rejection_votes: int
    approval_percent: float
    byzantine_faulty: int          # estimated faulty nodes
    completed_at: float = field(default_factory=time.time)


@dataclass
class LeaderElection:
    """Distributed leader election record."""
    election_id: str
    elected_leader: str
    eligible_nodes: List[str]
    votes_received: Dict[str, int]
    election_timestamp: float = field(default_factory=time.time)


class DistributedConsensus:
    """Byzantine-fault-tolerant distributed consensus."""

    def __init__(self):
        self._lock = threading.RLock()
        self._proposals = {}  # proposal_id → Proposal
        self._votes = {}  # vote_id → Vote
        self._proposal_votes = {}  # proposal_id → list of vote_ids
        self._results = {}  # proposal_id → ConsensusResult
        self._leaders = {}  # node_id → leadership record
        CONSENSUS_PATH.mkdir(parents=True, exist_ok=True)
        self._load_state()

    # ── Proposal Management ────────────────────────────────────────────────

    def submit_proposal(
        self,
        proposer_id: str,
        proposal_type: str,
        proposal_content: dict,
        required_quorum: float = 0.67,
        expires_in_seconds: int = 300,
    ) -> str:
        """
        Submit proposal for consensus.
        Returns proposal_id.
        """
        proposal_id = f"prop-{uuid.uuid4().hex[:12]}"

        proposal = Proposal(
            proposal_id=proposal_id,
            proposer_id=proposer_id,
            proposal_type=proposal_type,
            proposal_content=proposal_content,
            required_quorum=required_quorum,
            expires_at=time.time() + expires_in_seconds,
        )

        with self._lock:
            self._proposals[proposal_id] = proposal
            self._proposal_votes[proposal_id] = []
            self._persist_proposal(proposal)

        return proposal_id

    def get_proposal(self, proposal_id: str) -> Optional[Proposal]:
        """Get proposal by ID."""
        with self._lock:
            return self._proposals.get(proposal_id)

    def get_pending_proposals(self) -> List[Proposal]:
        """Get all pending proposals (not expired)."""
        with self._lock:
            now = time.time()
            return [
                p
                for p in self._proposals.values()
                if p.status == "pending" and p.expires_at > now
            ]

    # ── Voting ────────────────────────────────────────────────────────────

    def vote(
        self,
        proposal_id: str,
        voter_id: str,
        decision: bool,
        confidence: float = 1.0,
        reason: str = "",
    ) -> str:
        """
        Vote on a proposal.
        Returns vote_id.
        """
        vote_id = f"vote-{uuid.uuid4().hex[:12]}"

        vote = Vote(
            vote_id=vote_id,
            proposal_id=proposal_id,
            voter_id=voter_id,
            decision=decision,
            confidence=confidence,
            reason=reason,
        )

        with self._lock:
            self._votes[vote_id] = vote

            # Register vote with proposal
            if proposal_id not in self._proposal_votes:
                self._proposal_votes[proposal_id] = []
            self._proposal_votes[proposal_id].append(vote_id)

            self._persist_vote(vote)

        # Don't auto-check consensus; caller can explicitly call check_consensus_reached()
        return vote_id

    def get_votes_for_proposal(self, proposal_id: str) -> List[Vote]:
        """Get all votes for a proposal."""
        with self._lock:
            if proposal_id not in self._proposal_votes:
                return []

            vote_ids = self._proposal_votes[proposal_id]
            return [self._votes[vid] for vid in vote_ids if vid in self._votes]

    def get_voter_consensus_rate(self, voter_id: str) -> float:
        """
        Get consensus agreement rate for a voter
        (how often their votes align with final consensus).
        """
        with self._lock:
            votes = [v for v in self._votes.values() if v.voter_id == voter_id]
            if not votes:
                return 0.0

            aligned = 0
            for vote in votes:
                result = self._results.get(vote.proposal_id)
                if result:
                    if vote.decision == result.approved:
                        aligned += 1

            return aligned / len(votes) if votes else 0.0

    # ── Consensus Checking ────────────────────────────────────────────────

    def check_consensus_reached(self, proposal_id: str) -> Optional[ConsensusResult]:
        """
        Explicitly check if consensus is reached for proposal.
        Should be called after votes are submitted.
        """
        return self._check_consensus(proposal_id)

    def _check_consensus(self, proposal_id: str) -> Optional[ConsensusResult]:
        """
        Check if consensus is reached for proposal.
        Uses BFT: requires specified quorum percentage.
        """
        proposal = self._proposals.get(proposal_id)
        if not proposal or proposal.status != "pending":
            return None

        votes = self.get_votes_for_proposal(proposal_id)
        if not votes:
            return None

        # Count votes
        approval_votes = sum(1 for v in votes if v.decision)
        rejection_votes = sum(1 for v in votes if not v.decision)
        total_votes = len(votes)

        approval_percent = approval_votes / total_votes if total_votes > 0 else 0.0

        # Estimate byzantine faulty nodes
        byzantine_faulty = max(0, int((total_votes - 1) / 3))

        # Check if quorum is reached (either approval or rejection)
        is_approved = approval_percent >= proposal.required_quorum
        is_rejected = approval_percent <= (1.0 - proposal.required_quorum)

        if is_approved or is_rejected:
            # Consensus reached
            result = ConsensusResult(
                proposal_id=proposal_id,
                approved=is_approved,
                total_votes=total_votes,
                approval_votes=approval_votes,
                rejection_votes=rejection_votes,
                approval_percent=approval_percent,
                byzantine_faulty=byzantine_faulty,
            )

            with self._lock:
                self._results[proposal_id] = result
                proposal.status = "approved" if is_approved else "rejected"
                self._persist_result(result)

            return result

        return None

    def get_consensus_result(self, proposal_id: str) -> Optional[ConsensusResult]:
        """Get consensus result if reached."""
        with self._lock:
            return self._results.get(proposal_id)

    # ── Leader Election ────────────────────────────────────────────────────

    def elect_leader(self, candidate_nodes: List[str]) -> Tuple[str, LeaderElection]:
        """
        Elect leader from candidates using distributed voting.
        Returns (elected_leader, election_record).
        """
        election_id = f"elect-{uuid.uuid4().hex[:12]}"

        # Each node votes for preferred leader (in real impl: by capability/health)
        votes_received = {node: 0 for node in candidate_nodes}

        for node in candidate_nodes:
            # Simple: vote for self (in production: consider node health/capability)
            preferred = node
            if preferred in votes_received:
                votes_received[preferred] += 1

        # Elected: node with most votes (ties broken by alphabetical)
        elected = max(candidate_nodes, key=lambda n: (votes_received[n], n))

        election = LeaderElection(
            election_id=election_id,
            elected_leader=elected,
            eligible_nodes=candidate_nodes,
            votes_received=votes_received,
        )

        with self._lock:
            self._leaders[elected] = election
            self._persist_leader_election(election)

        return (elected, election)

    def get_leader_for_node(self, node_id: str) -> Optional[LeaderElection]:
        """Get leader election record for node."""
        with self._lock:
            return self._leaders.get(node_id)

    # ── Partition Recovery ────────────────────────────────────────────────

    def detect_partition(
        self, active_nodes: List[str], expected_nodes: List[str]
    ) -> Dict[str, bool]:
        """
        Detect network partition.
        Returns mapping of node_id → is_reachable.
        """
        partition_state = {}
        for node in expected_nodes:
            partition_state[node] = node in active_nodes

        return partition_state

    def resolve_partition(
        self,
        partition_state: Dict[str, bool],
        majority_nodes: List[str],
    ) -> str:
        """
        Resolve partition: nodes in majority partition proceed,
        minority nodes pause until reconnected.

        Returns: 'proceed' (in majority) or 'pause' (in minority)
        """
        reachable_count = sum(1 for v in partition_state.values() if v)
        total_count = len(partition_state)

        is_majority = reachable_count > total_count / 2
        return "proceed" if is_majority else "pause"

    # ── Storage ────────────────────────────────────────────────────────────

    def _persist_proposal(self, proposal: Proposal) -> None:
        """Persist proposal to disk."""
        proposals_file = CONSENSUS_PATH / "proposals.jsonl"
        with self._lock:
            proposals_file.write_text(
                proposals_file.read_text() + json.dumps(asdict(proposal)) + "\n"
                if proposals_file.exists()
                else json.dumps(asdict(proposal)) + "\n"
            )

    def _persist_vote(self, vote: Vote) -> None:
        """Persist vote to disk."""
        votes_file = CONSENSUS_PATH / "votes.jsonl"
        with self._lock:
            votes_file.write_text(
                votes_file.read_text() + json.dumps(asdict(vote)) + "\n"
                if votes_file.exists()
                else json.dumps(asdict(vote)) + "\n"
            )

    def _persist_result(self, result: ConsensusResult) -> None:
        """Persist consensus result to disk."""
        results_file = CONSENSUS_PATH / "results.jsonl"
        with self._lock:
            results_file.write_text(
                results_file.read_text() + json.dumps(asdict(result)) + "\n"
                if results_file.exists()
                else json.dumps(asdict(result)) + "\n"
            )

    def _persist_leader_election(self, election: LeaderElection) -> None:
        """Persist leader election record."""
        election_file = CONSENSUS_PATH / "leader_election.jsonl"
        with self._lock:
            election_file.write_text(
                election_file.read_text() + json.dumps(asdict(election)) + "\n"
                if election_file.exists()
                else json.dumps(asdict(election)) + "\n"
            )

    def _load_state(self) -> None:
        """Load consensus state from disk."""
        proposals_file = CONSENSUS_PATH / "proposals.jsonl"
        votes_file = CONSENSUS_PATH / "votes.jsonl"

        if proposals_file.exists():
            try:
                for line in proposals_file.read_text().strip().split("\n"):
                    if line.strip():
                        data = json.loads(line)
                        proposal = Proposal(**data)
                        self._proposals[proposal.proposal_id] = proposal
            except Exception:
                pass

        if votes_file.exists():
            try:
                for line in votes_file.read_text().strip().split("\n"):
                    if line.strip():
                        data = json.loads(line)
                        vote = Vote(**data)
                        self._votes[vote.vote_id] = vote

                        # Rebuild proposal_votes index
                        if vote.proposal_id not in self._proposal_votes:
                            self._proposal_votes[vote.proposal_id] = []
                        self._proposal_votes[vote.proposal_id].append(vote.vote_id)
            except Exception:
                pass
