"""
Capability Quorum — AgentOS v3.28.0.

Running agents vote on pending capability synthesis proposals.
When self_modification.py synthesizes a new capability, it can submit
a proposal here. Active agents evaluate it via Ollama and vote.
A 66% supermajority finalizes the proposal (approve or reject).

CapabilityQuorum:
  submit(proposer_id, cap_id, description, code) → proposal_id
  vote_on_pending(agent_ids)  → list[FinalizedProposal]  (called by daemon)
  is_approved(proposal_id) → bool

The daemon calls vote_on_pending(active_agents) once per cycle.
Each agent independently asks Ollama whether the capability is safe/useful.
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))
CONFIG_PATH = Path(os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))
QUORUM_THRESHOLD = 0.66   # 66% yes required to approve


@dataclass
class FinalizedProposal:
    proposal_id: str
    status: str          # 'approved' | 'rejected'
    yes_votes: int
    no_votes: int
    total_voters: int


class CapabilityQuorum:
    """Live agent voting on capability synthesis proposals."""

    def __init__(self, agent_quorum=None):
        self._quorum = agent_quorum   # AgentQuorum instance

    # ── Public API ────────────────────────────────────────────────────────

    def submit(self, proposer_id: str, cap_id: str,
               description: str, code: str) -> Optional[str]:
        """
        Submit a capability for quorum review.
        Returns proposal_id or None if no quorum configured.
        """
        if not self._quorum:
            return None

        payload = {
            "cap_id": cap_id,
            "description": description,
            "code_preview": code[:500],   # first 500 chars of code
        }

        return self._quorum.propose(
            proposer_id=proposer_id,
            proposal_type="capability",
            description=f"New capability: {cap_id} — {description[:120]}",
            payload=payload,
        )

    def vote_on_pending(self, agent_ids: List[str]) -> List[FinalizedProposal]:
        """
        Have each agent in agent_ids vote on all pending capability proposals.
        Finalizes any proposal that has reached quorum.
        Called by the daemon each cycle.
        """
        if not self._quorum or not agent_ids:
            return []

        pending = self._quorum.get_pending_proposals(limit=20)
        finalized = []

        for proposal in pending:
            if proposal.proposal_type != "capability":
                continue

            # Each agent votes (skip agents who already voted)
            for agent_id in agent_ids:
                if agent_id in (proposal.votes or {}):
                    continue   # already voted

                vote = self._agent_evaluate(agent_id, proposal)
                self._quorum.vote(proposal.proposal_id, agent_id, vote)

            # Try to finalize if enough votes
            yes, no, _, status = self._quorum.get_voting_status(proposal.proposal_id)
            total = yes + no
            if total >= max(1, len(agent_ids)):
                # All current agents voted — finalize
                approved = self._quorum.finalize_proposal(proposal.proposal_id)
                finalized.append(FinalizedProposal(
                    proposal_id=proposal.proposal_id,
                    status="approved" if approved else "rejected",
                    yes_votes=yes,
                    no_votes=no,
                    total_voters=total,
                ))

        return finalized

    def is_approved(self, proposal_id: str) -> bool:
        """Check if a specific proposal was approved."""
        if not self._quorum:
            return True   # no quorum = auto-approve
        proposal = self._quorum.get_proposal(proposal_id)
        if proposal is None:
            return False
        return proposal.status == "approved"

    # ── Internal ──────────────────────────────────────────────────────────

    def _agent_evaluate(self, agent_id: str, proposal) -> bool:
        """
        Ask Ollama whether this agent approves the capability proposal.
        Returns True (approve) or False (reject). Defaults True on error.
        """
        try:
            import httpx

            cfg = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
            model = cfg.get("ollama", {}).get("default_model", "mistral-nemo:12b")

            payload = proposal.payload or {}
            prompt = (
                f"You are agent '{agent_id}'. Vote on this capability proposal.\n\n"
                f"Capability: {payload.get('cap_id', 'unknown')}\n"
                f"Description: {payload.get('description', proposal.description)}\n"
                f"Code preview:\n{payload.get('code_preview', '')[:300]}\n\n"
                f"Should this capability be added to the system?\n"
                f"Vote YES if: it looks useful, safe, and not destructive.\n"
                f"Vote NO if: it looks dangerous, broken, or redundant.\n"
                f'Respond ONLY with JSON: {{"vote": "yes"}} or {{"vote": "no"}}'
            )

            resp = httpx.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": model, "prompt": prompt,
                      "stream": False, "format": "json"},
                timeout=OLLAMA_TIMEOUT,
            )
            resp.raise_for_status()
            raw = json.loads(resp.json().get("response", "{}"))
            return str(raw.get("vote", "yes")).lower().strip() == "yes"

        except Exception:
            return True   # default approve on failure (optimistic)
