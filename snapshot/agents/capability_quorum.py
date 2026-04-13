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
                outcome  = "approved" if approved else "rejected"
                finalized.append(FinalizedProposal(
                    proposal_id=proposal.proposal_id,
                    status=outcome,
                    yes_votes=yes,
                    no_votes=no,
                    total_voters=total,
                ))
                # Write the outcome back into each voter's history
                # so their future votes are informed by whether they were right
                cap_id = (proposal.payload or {}).get("cap_id", proposal.proposal_id)
                try:
                    from agents.agent_identity import AgentIdentity
                    for voter_id in agent_ids:
                        try:
                            ident = AgentIdentity.load_or_create(voter_id)
                            ident.update_vote_outcome(cap_id, outcome)
                        except Exception:
                            pass
                except Exception:
                    pass

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

    # Each agent takes a different evaluator role so the same model produces
    # genuine disagreement rather than rubber-stamping everything.
    _VOTER_ROLES = {
        "analyst": (
            "SKEPTIC",
            "The system already has 1700+ capabilities — most are security/audit tools "
            "that do the same thing. Your job: vote NO on anything that is a variation "
            "of: shell injection detection, dependency auditing, type hint checking, "
            "secrets scanning, code complexity analysis, or import auditing. "
            "Vote YES for: utility functions (retry, parse, validate, cache, format), "
            "novel integrations, or capabilities in genuinely new domains. "
            "If the name sounds like something we already have, vote NO."
        ),
        "builder": (
            "PRAGMATIST",
            "Evaluate whether this capability is actually implemented vs. placeholder. "
            "Vote NO if: the function body is mostly comments, raises NotImplementedError, "
            "uses subprocess to call a tool that isn't installed, or is obviously broken. "
            "Vote YES if: the code looks real and runnable, even if simple. "
            "Be lenient on novel utility functions — the system needs more variety."
        ),
        "scout": (
            "BALANCED",
            "Vote YES if the capability is useful, safe, and not obviously redundant. "
            "Vote NO if it duplicates something clearly already in the system or if the "
            "code looks like a stub. Lean toward YES for genuinely new capability areas."
        ),
    }

    def _agent_evaluate(self, agent_id: str, proposal) -> bool:
        """
        Ask Ollama whether this agent approves the capability proposal.
        Each agent plays a distinct evaluator role to produce real disagreement.
        Returns True (approve) or False (reject). Defaults True on error.
        """
        try:
            import httpx

            cfg = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
            model = cfg.get("ollama", {}).get("default_model", "mistral-nemo:12b")

            role_name, role_instructions = self._VOTER_ROLES.get(
                agent_id, ("BALANCED", self._VOTER_ROLES["scout"][1])
            )

            # Load this agent's vote history so decisions are grounded in experience
            vote_context = ""
            try:
                from agents.agent_identity import AgentIdentity
                ident = AgentIdentity.load_or_create(agent_id)
                vote_context = ident.get_vote_summary()
            except Exception:
                pass

            payload = proposal.payload or {}
            cap_id  = payload.get("cap_id", "unknown")
            prompt = (
                f"You are agent '{agent_id}', acting as {role_name} reviewer.\n"
                f"{role_instructions}\n\n"
                f"{('Your voting history: ' + vote_context) if vote_context else ''}\n\n"
                f"Capability to evaluate: {cap_id}\n"
                f"Description: {payload.get('description', proposal.description)}\n"
                f"Code preview:\n{payload.get('code_preview', '')[:300]}\n\n"
                f"Apply your role and your past experience to make a considered decision.\n"
                f'Respond ONLY with JSON: {{"vote": "yes", "reason": "..."}} or {{"vote": "no", "reason": "..."}}'
            )

            resp = httpx.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": model, "prompt": prompt,
                      "stream": False, "format": "json", "think": False},
                timeout=OLLAMA_TIMEOUT,
            )
            resp.raise_for_status()
            # Strip thinking tags in case model outputs them despite format:json
            raw_text = resp.json().get("response", "{}")
            if "</think>" in raw_text:
                raw_text = raw_text.split("</think>")[-1].strip()
            raw    = json.loads(raw_text)
            voted_yes = str(raw.get("vote", "yes")).lower().strip() == "yes"

            # Record the vote in the agent's identity
            try:
                from agents.agent_identity import AgentIdentity
                ident = AgentIdentity.load_or_create(agent_id)
                ident.record_vote(cap_id, voted_yes)
            except Exception:
                pass

            return voted_yes

        except Exception:
            return True   # default approve on failure (optimistic)
