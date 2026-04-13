"""
Self-Evolving Governance — AgentOS v3.8.0.

Phase 6, primitive 3. Depends on v3.7.0 (Meta-Synthesis) and v1.3.4 (Consensus).

The current governance system has hardcoded quorum rules: 2-of-3, 3-of-5, etc.
These were chosen by humans who didn't know how the swarm would actually behave.
This module lets the swarm observe how its governance rules perform in practice,
and propose changes when the evidence warrants it.

Honest scope:
  This is NOT autonomous self-modification of core code. It is:
  - Tracking quorum rule effectiveness (how often does a given threshold
    produce good outcomes vs. blocking everything vs. rubber-stamping?)
  - Generating structured change proposals when patterns warrant it
  - Routing those proposals through the existing consensus system
    (agents vote on changing the rules, just like they vote on anything else)
  - Recording the history of rule changes and their outcomes

What this enables:
  A swarm that started with required_votes=3-of-5 on resource proposals might
  discover after 100 rounds that 90% of those proposals passed unanimously —
  evidence that required_votes=2 would be equally safe with less friction.
  Or the opposite: near-misses where a bad proposal barely failed → raise threshold.

Design:
  GovernanceAnalyzer:
    analyze_quorum_effectiveness(proposal_history) → list[QuorumAnalysis]
    detect_rule_improvement_opportunity(analysis) → Optional[RuleProposal]

  GovernanceEvolutionEngine:
    observe_outcome(proposal_id, outcome: "good" | "bad" | "neutral")
    propose_rule_change(proposed_by, change_spec, participants) → proposal_id
    apply_approved_change(proposal_id) → bool
    get_active_rules() → dict
    get_rule_history() → list[RuleChange]

Storage:
  /agentOS/memory/governance/
    rules.json                    # current governance rules
    outcome_log.jsonl             # observed outcomes per proposal
    rule_history.jsonl            # log of rule changes and their approval votes
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

GOVERNANCE_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "governance"

# Default governance rules that can be evolved
DEFAULT_RULES = {
    "resource_proposals": {
        "required_votes_fraction": 0.6,  # 60% of participants must approve
        "min_participants": 2,
        "ttl_seconds": 300,
        "description": "Proposals about resource allocation (VRAM, tokens, workers)",
    },
    "capability_proposals": {
        "required_votes_fraction": 0.5,
        "min_participants": 2,
        "ttl_seconds": 600,
        "description": "Proposals to add or modify capabilities",
    },
    "policy_proposals": {
        "required_votes_fraction": 0.75,
        "min_participants": 3,
        "ttl_seconds": 900,
        "description": "Proposals to change governance rules or system policies",
    },
    "goal_proposals": {
        "required_votes_fraction": 0.5,
        "min_participants": 2,
        "ttl_seconds": 300,
        "description": "Proposals about agent goals and objectives",
    },
}


@dataclass
class OutcomeRecord:
    """Observed outcome for a consensus proposal."""
    record_id: str
    proposal_id: str
    proposal_type: str          # matches a key in rules
    outcome: str                # "good" | "bad" | "neutral"
    votes_for: int
    votes_against: int
    participants: int
    approved: bool
    notes: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class QuorumAnalysis:
    """Effectiveness analysis for one proposal type's quorum rule."""
    proposal_type: str
    sample_size: int            # number of proposals analyzed
    approval_rate: float        # fraction that were approved
    good_outcome_rate: float    # fraction of outcomes rated "good"
    bad_outcome_rate: float     # fraction of outcomes rated "bad"
    avg_votes_for_fraction: float  # average yes-vote fraction
    near_miss_count: int        # approved by exactly required_votes (could have failed)
    unanimous_count: int        # approved unanimously (threshold may be too high)
    current_threshold: float    # current required_votes_fraction
    suggested_threshold: Optional[float]  # if evidence suggests a change
    suggestion_reason: str


@dataclass
class RuleProposal:
    """A proposed change to a governance rule."""
    rule_proposal_id: str
    proposed_by: str
    proposal_type: str          # which rule is being changed
    current_value: float        # current required_votes_fraction
    proposed_value: float       # what it should be
    rationale: str              # evidence-based reason
    supporting_data: dict       # the analysis that motivated this
    created_at: float = field(default_factory=time.time)


@dataclass
class RuleChange:
    """A recorded governance rule change."""
    change_id: str
    proposal_type: str
    old_threshold: float
    new_threshold: float
    approved_by: list           # agent_ids that voted yes
    rejected_by: list           # agent_ids that voted no
    consensus_proposal_id: str  # links back to the vote
    applied_at: float = field(default_factory=time.time)
    outcome: str = "applied"    # "applied" | "rolled_back"


class GovernanceAnalyzer:
    """
    Analyzes quorum rule effectiveness from proposal outcome history.
    Pure analysis — no side effects.
    """

    def analyze_quorum_effectiveness(
        self, outcomes: list, current_rules: dict
    ) -> list:
        """
        Given a list of OutcomeRecord dicts and current rules,
        compute QuorumAnalysis for each proposal type with ≥5 samples.
        """
        analyses = []

        # group outcomes by proposal_type
        by_type: dict = {}
        for rec in outcomes:
            t = rec.get("proposal_type", "unknown") if isinstance(rec, dict) else rec.proposal_type
            by_type.setdefault(t, []).append(rec)

        for proposal_type, records in by_type.items():
            if len(records) < 3:
                continue  # not enough data

            rule = current_rules.get(proposal_type, {})
            current_threshold = rule.get("required_votes_fraction", 0.5)
            min_participants = rule.get("min_participants", 2)

            approved_count = sum(
                1 for r in records
                if (r.get("approved") if isinstance(r, dict) else r.approved)
            )
            good_count = sum(
                1 for r in records
                if (r.get("outcome") if isinstance(r, dict) else r.outcome) == "good"
            )
            bad_count = sum(
                1 for r in records
                if (r.get("outcome") if isinstance(r, dict) else r.outcome) == "bad"
            )

            # compute average yes-fraction
            fractions = []
            near_miss_count = 0
            unanimous_count = 0

            for r in records:
                if isinstance(r, dict):
                    vf = r.get("votes_for", 0)
                    p = r.get("participants", max(min_participants, 1))
                    approved = r.get("approved", False)
                    votes_against = r.get("votes_against", 0)
                else:
                    vf = r.votes_for
                    p = r.participants
                    approved = r.approved
                    votes_against = r.votes_against

                if p > 0:
                    frac = vf / p
                    fractions.append(frac)
                    required = current_threshold * p
                    if approved and vf <= required + 0.5:  # approved by barely required
                        near_miss_count += 1
                    if approved and votes_against == 0:
                        unanimous_count += 1

            n = len(records)
            avg_frac = sum(fractions) / len(fractions) if fractions else 0.0

            # heuristic threshold suggestion
            suggested = None
            reason = "no change needed"

            if n >= 5:
                unan_rate = unanimous_count / n
                near_rate = near_miss_count / n
                bad_rate = bad_count / n

                if unan_rate > 0.7 and current_threshold > 0.5:
                    # almost always unanimous → threshold too high, adds friction
                    suggested = max(current_threshold - 0.15, 0.4)
                    reason = (
                        f"{unan_rate:.0%} of proposals pass unanimously — "
                        f"threshold {current_threshold:.0%} is adding friction without safety"
                    )
                elif bad_rate > 0.3 and near_rate > 0.3:
                    # many bad outcomes slipped through near-misses → raise threshold
                    suggested = min(current_threshold + 0.15, 0.9)
                    reason = (
                        f"{bad_rate:.0%} bad outcomes + {near_rate:.0%} near-misses — "
                        f"threshold {current_threshold:.0%} may be too low"
                    )

            analyses.append(QuorumAnalysis(
                proposal_type=proposal_type,
                sample_size=n,
                approval_rate=approved_count / n,
                good_outcome_rate=good_count / n,
                bad_outcome_rate=bad_count / n,
                avg_votes_for_fraction=round(avg_frac, 3),
                near_miss_count=near_miss_count,
                unanimous_count=unanimous_count,
                current_threshold=current_threshold,
                suggested_threshold=suggested,
                suggestion_reason=reason,
            ))

        return analyses

    def detect_improvement_opportunity(
        self, analyses: list
    ) -> Optional[RuleProposal]:
        """
        From a list of QuorumAnalysis, return the strongest improvement
        opportunity as a RuleProposal, or None if no change is warranted.
        """
        candidates = [
            a for a in analyses
            if (a.get("suggested_threshold") if isinstance(a, dict) else a.suggested_threshold)
            is not None
        ]
        if not candidates:
            return None

        # pick the one with most evidence (largest sample)
        best = max(
            candidates,
            key=lambda a: (a.get("sample_size") if isinstance(a, dict) else a.sample_size),
        )

        if isinstance(best, dict):
            pt = best["proposal_type"]
            cur = best["current_threshold"]
            sug = best["suggested_threshold"]
            reason = best["suggestion_reason"]
            data = best
        else:
            pt = best.proposal_type
            cur = best.current_threshold
            sug = best.suggested_threshold
            reason = best.suggestion_reason
            data = asdict(best)

        return RuleProposal(
            rule_proposal_id=str(uuid.uuid4())[:8],
            proposed_by="governance_analyzer",
            proposal_type=pt,
            current_value=cur,
            proposed_value=sug,
            rationale=reason,
            supporting_data=data,
        )


class GovernanceEvolutionEngine:
    """
    Tracks governance rule effectiveness and routes improvement proposals
    through the existing consensus system.

    Wire it up with a ConsensusManager to enable voting on rule changes.
    """

    def __init__(self, consensus_manager=None, storage_path: Path = None):
        self._consensus = consensus_manager
        self._lock = threading.Lock()
        self._analyzer = GovernanceAnalyzer()
        self._storage_path = storage_path or GOVERNANCE_PATH
        self._storage_path.mkdir(parents=True, exist_ok=True)
        self._rules = self._load_rules()
        self._outcomes: list = self._load_outcomes()
        self._rule_history: list = self._load_rule_history()

    # ------------------------------------------------------------------ #
    #  Core API                                                            #
    # ------------------------------------------------------------------ #

    def observe_outcome(
        self,
        proposal_id: str,
        proposal_type: str,
        outcome: str,           # "good" | "bad" | "neutral"
        votes_for: int,
        votes_against: int,
        participants: int,
        approved: bool,
        notes: str = "",
    ) -> str:
        """
        Record the outcome of a consensus proposal.
        This is the data that feeds into rule effectiveness analysis.
        """
        assert outcome in ("good", "bad", "neutral"), f"outcome must be good/bad/neutral, got {outcome!r}"

        rec = OutcomeRecord(
            record_id=str(uuid.uuid4())[:8],
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            outcome=outcome,
            votes_for=votes_for,
            votes_against=votes_against,
            participants=participants,
            approved=approved,
            notes=notes,
        )
        with self._lock:
            self._outcomes.append(asdict(rec))
            self._append_outcome(rec)
        return rec.record_id

    def analyze(self) -> list:
        """
        Run analysis on all observed outcomes. Returns QuorumAnalysis list.
        """
        with self._lock:
            outcomes = list(self._outcomes)
            rules = dict(self._rules)
        return self._analyzer.analyze_quorum_effectiveness(outcomes, rules)

    def propose_rule_change(
        self,
        proposed_by: str,
        proposal_type: str,
        new_threshold: float,
        rationale: str,
        participants: list,
    ) -> Optional[str]:
        """
        Propose a governance rule change. Routes it through the consensus
        system so agents vote on whether to adopt the new threshold.

        Returns consensus proposal_id, or None if consensus manager unavailable.
        """
        assert 0.1 <= new_threshold <= 1.0, "threshold must be 0.1–1.0"
        assert proposal_type in self._rules, f"unknown proposal_type: {proposal_type}"

        current = self._rules[proposal_type]["required_votes_fraction"]
        direction = "increase" if new_threshold > current else "decrease"

        description = (
            f"Governance evolution: {direction} {proposal_type} quorum threshold "
            f"from {current:.0%} to {new_threshold:.0%}. Rationale: {rationale}"
        )
        action = {
            "type": "governance_rule_change",
            "proposal_type": proposal_type,
            "field": "required_votes_fraction",
            "old_value": current,
            "new_value": new_threshold,
            "rationale": rationale,
        }

        if self._consensus is None:
            return None

        # policy_proposals always require the highest threshold
        # (changing rules is more consequential than using them)
        rule = self._rules.get("policy_proposals", {})
        req_frac = rule.get("required_votes_fraction", 0.75)
        required_votes = max(1, round(req_frac * len(participants)))
        ttl = rule.get("ttl_seconds", 900)

        return self._consensus.propose(
            proposer_id=proposed_by,
            description=description,
            action=action,
            participants=participants,
            required_votes=required_votes,
            ttl_seconds=ttl,
        )

    def apply_approved_change(
        self,
        consensus_proposal_id: str,
        approved_by: list,
        rejected_by: list,
    ) -> bool:
        """
        Apply a governance rule change that has been approved by consensus.

        This is called after the consensus system confirms approval.
        Updates the live rules and writes to history.
        """
        if self._consensus is None:
            return False

        proposal = self._consensus.get(consensus_proposal_id)
        if proposal is None:
            return False

        # Extract the action from the proposal
        action = proposal.get("action", {}) if isinstance(proposal, dict) else {}
        if action.get("type") != "governance_rule_change":
            return False

        pt = action.get("proposal_type")
        new_val = action.get("new_value")
        old_val = action.get("old_value")

        if pt not in self._rules or new_val is None:
            return False

        # apply the change
        with self._lock:
            self._rules[pt]["required_votes_fraction"] = new_val
            change = RuleChange(
                change_id=str(uuid.uuid4())[:8],
                proposal_type=pt,
                old_threshold=old_val,
                new_threshold=new_val,
                approved_by=approved_by,
                rejected_by=rejected_by,
                consensus_proposal_id=consensus_proposal_id,
            )
            self._rule_history.append(asdict(change))
            self._save_rules()
            self._append_rule_change(change)

        return True

    def auto_propose_if_warranted(
        self, proposed_by: str, participants: list
    ) -> Optional[str]:
        """
        Run analysis, detect improvement opportunity, and if one exists,
        submit it as a consensus proposal automatically.

        Returns consensus proposal_id, or None if nothing to propose.
        """
        analyses = self.analyze()
        opportunity = self._analyzer.detect_improvement_opportunity(analyses)

        if opportunity is None:
            return None

        return self.propose_rule_change(
            proposed_by=proposed_by,
            proposal_type=opportunity.proposal_type,
            new_threshold=opportunity.proposed_value,
            rationale=opportunity.rationale,
            participants=participants,
        )

    def get_active_rules(self) -> dict:
        """Return the current governance rules."""
        with self._lock:
            return dict(self._rules)

    def get_rule_history(self) -> list:
        """Return all governance rule changes ever applied."""
        with self._lock:
            return list(self._rule_history)

    def get_outcomes(self, proposal_type: str = None) -> list:
        """Return observed outcomes, optionally filtered by proposal type."""
        with self._lock:
            if proposal_type:
                return [o for o in self._outcomes if o.get("proposal_type") == proposal_type]
            return list(self._outcomes)

    # ------------------------------------------------------------------ #
    #  Persistence                                                         #
    # ------------------------------------------------------------------ #

    def _load_rules(self) -> dict:
        path = self._storage_path / "rules.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {k: dict(v) for k, v in DEFAULT_RULES.items()}

    def _save_rules(self) -> None:
        path = self._storage_path / "rules.json"
        path.write_text(json.dumps(self._rules, indent=2))

    def _load_outcomes(self) -> list:
        path = self._storage_path / "outcome_log.jsonl"
        if not path.exists():
            return []
        outcomes = []
        for line in path.read_text().strip().splitlines():
            try:
                outcomes.append(json.loads(line))
            except Exception:
                continue
        return outcomes

    def _append_outcome(self, rec: OutcomeRecord) -> None:
        path = self._storage_path / "outcome_log.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(asdict(rec)) + "\n")

    def _load_rule_history(self) -> list:
        path = self._storage_path / "rule_history.jsonl"
        if not path.exists():
            return []
        history = []
        for line in path.read_text().strip().splitlines():
            try:
                history.append(json.loads(line))
            except Exception:
                continue
        return history

    def _append_rule_change(self, change: RuleChange) -> None:
        path = self._storage_path / "rule_history.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(asdict(change)) + "\n")
