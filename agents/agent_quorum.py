"""
Agent-Quorum Governance — AgentOS v2.3.0.

Multi-agent approval for system changes. Agents vote on proposed modifications
(new capabilities, goal changes, resource allocation). Consensus via quorum.

Design:
  ProposalRecord:
    proposal_id: str
    proposer_id: str                # which agent proposed
    proposal_type: str              # 'capability', 'goal_change', 'resource', 'policy'
    description: str                # semantic description
    payload: dict                   # data being proposed (serialized)
    votes: dict[agent_id -> bool]   # {agent_id: True/False/None}
    required_quorum: int            # minimum votes needed
    status: str                     # 'pending', 'approved', 'rejected', 'withdrawn'
    created_at: float
    expires_at: float              # proposals expire after N seconds
    embedding: np.ndarray          # (768,) embedding of description

  AgentQuorum:
    propose(proposer_id, proposal_type, description, payload) → proposal_id
    get_proposal(proposal_id) → ProposalRecord
    vote(proposal_id, voter_id, vote: bool) → None
    get_pending_proposals(limit=100) → list[ProposalRecord]
    get_voting_status(proposal_id) → (votes_yes, votes_no, votes_abstain, status)
    finalize_proposal(proposal_id) → bool (True if approved, False if rejected)
    withdraw_proposal(proposal_id, withdrawer_id) → bool

Storage:
  /agentOS/memory/quorum/
    proposals.jsonl          # all proposals + voting records
    index.json               # {proposal_id: row_index}
    voting_history.jsonl     # audit trail of all votes
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Dict, List
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False

QUORUM_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "quorum"
DEFAULT_VECTOR_DIM = 768
DEFAULT_QUORUM_PERCENTAGE = 0.66  # 66% quorum requirement


@dataclass
class ProposalRecord:
    """A proposal for system change voted on by quorum."""
    proposal_id: str
    proposer_id: str
    proposal_type: str              # capability, goal_change, resource, policy
    description: str
    payload: dict = field(default_factory=dict)
    votes: Dict[str, Optional[bool]] = field(default_factory=dict)  # voter_id -> True/False/None
    required_quorum: int = 1        # minimum votes to approve
    status: str = "pending"         # pending, approved, rejected, withdrawn
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + (7 * 86400))  # 7 days
    finalized_at: Optional[float] = None


class AgentQuorum:
    """Consensus-based governance for agent system changes."""

    def __init__(self, quorum_percentage: float = DEFAULT_QUORUM_PERCENTAGE, vector_dim: int = DEFAULT_VECTOR_DIM):
        self._quorum_percentage = quorum_percentage
        self._vector_dim = vector_dim
        self._lock = threading.RLock()
        self._embedder = None
        self._init_embedder()
        QUORUM_PATH.mkdir(parents=True, exist_ok=True)

    def _init_embedder(self) -> None:
        """Load embedding model."""
        if not EMBEDDING_AVAILABLE:
            return
        try:
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:
            pass

    def _embed(self, text: str) -> Optional[np.ndarray]:
        """Embed text to vector."""
        if self._embedder is None:
            return None
        try:
            return np.array(self._embedder.encode(text, convert_to_numpy=True), dtype=np.float32)
        except Exception:
            return None

    def _get_active_agents(self) -> List[str]:
        """
        Get list of active agent IDs from the system.
        Reads from registry or returns empty list if not available.
        """
        # For now, we don't have a centralized agent registry
        # In a real system, this would query the agent registry
        return []

    # ── API ────────────────────────────────────────────────────────────────

    def propose(self, proposer_id: str, proposal_type: str, description: str, payload: dict = None) -> str:
        """
        Create a new proposal for system change.
        Returns proposal_id.
        """
        embedding = self._embed(description)
        if embedding is None:
            raise ValueError("Embedding unavailable")

        proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
        now = time.time()

        # Calculate required quorum (minimum number of votes needed)
        # For MVP, require at least 2 votes
        required_quorum = 2

        record = ProposalRecord(
            proposal_id=proposal_id,
            proposer_id=proposer_id,
            proposal_type=proposal_type,
            description=description,
            payload=payload or {},
            votes={},
            required_quorum=required_quorum,
            status="pending",
            created_at=now,
            expires_at=now + (7 * 86400),
        )

        with self._lock:
            proposals_file = QUORUM_PATH / "proposals.jsonl"
            proposals_file.write_text(
                proposals_file.read_text() + json.dumps(asdict(record)) + "\n"
                if proposals_file.exists()
                else json.dumps(asdict(record)) + "\n"
            )

            # Update index
            index_file = QUORUM_PATH / "index.json"
            index = json.loads(index_file.read_text()) if index_file.exists() else {}
            num_lines = len(proposals_file.read_text().strip().split("\n"))
            index[proposal_id] = num_lines - 1
            index_file.write_text(json.dumps(index, indent=2))

        return proposal_id

    def get_proposal(self, proposal_id: str) -> Optional[ProposalRecord]:
        """Retrieve a proposal by ID."""
        with self._lock:
            index_file = QUORUM_PATH / "index.json"
            proposals_file = QUORUM_PATH / "proposals.jsonl"

            if not index_file.exists() or not proposals_file.exists():
                return None

            index = json.loads(index_file.read_text())
            if proposal_id not in index:
                return None

            idx = index[proposal_id]
            proposals_lines = proposals_file.read_text().strip().split("\n")

            if idx >= len(proposals_lines):
                return None

            proposal_dict = json.loads(proposals_lines[idx])
            return ProposalRecord(**proposal_dict)

    def vote(self, proposal_id: str, voter_id: str, vote: bool) -> bool:
        """
        Record a vote on a proposal.
        vote: True = yes, False = no.
        Returns True if vote recorded, False if proposal not found.
        """
        with self._lock:
            index_file = QUORUM_PATH / "index.json"
            proposals_file = QUORUM_PATH / "proposals.jsonl"

            if not index_file.exists() or not proposals_file.exists():
                return False

            index = json.loads(index_file.read_text())
            if proposal_id not in index:
                return False

            idx = index[proposal_id]
            proposals_lines = proposals_file.read_text().strip().split("\n")
            proposal_dict = json.loads(proposals_lines[idx])

            # Record vote
            proposal_dict["votes"][voter_id] = vote
            proposals_lines[idx] = json.dumps(proposal_dict)
            proposals_file.write_text("\n".join(proposals_lines) + "\n")

            # Log vote in history
            vote_history_file = QUORUM_PATH / "voting_history.jsonl"
            vote_record = {
                "proposal_id": proposal_id,
                "voter_id": voter_id,
                "vote": vote,
                "timestamp": time.time(),
            }
            vote_history_file.write_text(
                vote_history_file.read_text() + json.dumps(vote_record) + "\n"
                if vote_history_file.exists()
                else json.dumps(vote_record) + "\n"
            )

            return True

    def get_voting_status(self, proposal_id: str) -> tuple:
        """
        Get voting status for a proposal.
        Returns (yes_votes, no_votes, abstain_votes, current_status).
        """
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            return (0, 0, 0, "not_found")

        yes_votes = sum(1 for v in proposal.votes.values() if v is True)
        no_votes = sum(1 for v in proposal.votes.values() if v is False)
        abstain_votes = sum(1 for v in proposal.votes.values() if v is None)

        return (yes_votes, no_votes, abstain_votes, proposal.status)

    def get_pending_proposals(self, limit: int = 100) -> List[ProposalRecord]:
        """List all pending proposals."""
        with self._lock:
            proposals_file = QUORUM_PATH / "proposals.jsonl"
            if not proposals_file.exists():
                return []

            try:
                proposals = [
                    ProposalRecord(**json.loads(line))
                    for line in proposals_file.read_text().strip().split("\n")
                    if line.strip() and json.loads(line)["status"] == "pending"
                ]
                proposals.sort(key=lambda p: p.created_at)
                return proposals[:limit]
            except Exception:
                return []

    def finalize_proposal(self, proposal_id: str) -> bool:
        """
        Finalize a proposal based on votes.
        Returns True if approved, False if rejected or insufficient votes.
        """
        proposal = self.get_proposal(proposal_id)
        if proposal is None or proposal.status != "pending":
            return False

        yes_votes = sum(1 for v in proposal.votes.values() if v is True)
        total_votes = len([v for v in proposal.votes.values() if v is not None])

        # Check if quorum met
        approval = False
        if total_votes >= proposal.required_quorum:
            # Check if yes votes exceed quorum percentage
            approval = yes_votes >= (total_votes * self._quorum_percentage)

        # Always finalize and update status
        with self._lock:
            index_file = QUORUM_PATH / "index.json"
            proposals_file = QUORUM_PATH / "proposals.jsonl"

            index = json.loads(index_file.read_text())
            idx = index[proposal_id]
            proposals_lines = proposals_file.read_text().strip().split("\n")
            proposal_dict = json.loads(proposals_lines[idx])

            proposal_dict["status"] = "approved" if approval else "rejected"
            proposal_dict["finalized_at"] = time.time()
            proposals_lines[idx] = json.dumps(proposal_dict)
            proposals_file.write_text("\n".join(proposals_lines) + "\n")

        return approval

    def withdraw_proposal(self, proposal_id: str, withdrawer_id: str) -> bool:
        """
        Withdraw a proposal (only the proposer can withdraw).
        Returns True if withdrawn, False otherwise.
        """
        proposal = self.get_proposal(proposal_id)
        if proposal is None or proposal.status != "pending":
            return False

        if proposal.proposer_id != withdrawer_id:
            return False

        with self._lock:
            index_file = QUORUM_PATH / "index.json"
            proposals_file = QUORUM_PATH / "proposals.jsonl"

            index = json.loads(index_file.read_text())
            idx = index[proposal_id]
            proposals_lines = proposals_file.read_text().strip().split("\n")
            proposal_dict = json.loads(proposals_lines[idx])

            proposal_dict["status"] = "withdrawn"
            proposals_lines[idx] = json.dumps(proposal_dict)
            proposals_file.write_text("\n".join(proposals_lines) + "\n")

        return True

    def get_proposal_history(self, proposal_id: str, limit: int = 100) -> List[dict]:
        """Get voting history for a proposal."""
        with self._lock:
            voting_history_file = QUORUM_PATH / "voting_history.jsonl"
            if not voting_history_file.exists():
                return []

            try:
                history = [
                    json.loads(line)
                    for line in voting_history_file.read_text().strip().split("\n")
                    if line.strip() and json.loads(line)["proposal_id"] == proposal_id
                ]
                return history[:limit]
            except Exception:
                return []
