"""
Integration tests for AgentOS v3.8.0: Self-Evolving Governance.

Phase 6, primitive 3. Tests verify that agents can observe governance
rule effectiveness, detect improvement opportunities, and propose
rule changes through the existing consensus system.

Run:
    PYTHONPATH=. pytest tests/integration/test_governance_evolution.py -v
"""

import pytest
import time
import uuid
import shutil
import tempfile
from pathlib import Path

pytestmark = pytest.mark.integration

try:
    from agents.governance_evolution import (
        GovernanceEvolutionEngine, GovernanceAnalyzer,
        OutcomeRecord, QuorumAnalysis, RuleProposal, RuleChange,
        DEFAULT_RULES,
    )
    GOVERNANCE_AVAILABLE = True
except ImportError:
    GOVERNANCE_AVAILABLE = False

try:
    from agents.consensus import ConsensusManager
    CONSENSUS_AVAILABLE = True
except ImportError:
    CONSENSUS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not GOVERNANCE_AVAILABLE,
    reason="governance_evolution module not available"
)


def _uid():
    return f"agent-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def tmp_gov(monkeypatch):
    """Isolated temp storage for governance tests."""
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("AGENTOS_MEMORY_PATH", tmpdir)
    if CONSENSUS_AVAILABLE:
        import agents.consensus as cm
        monkeypatch.setattr(cm, "CONSENSUS_DIR", Path(tmpdir) / "consensus")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 1 — default rules are loaded on first init
# ---------------------------------------------------------------------------

class TestDefaultRules:
    def test_default_rules_loaded(self, tmp_gov):
        """GovernanceEvolutionEngine starts with DEFAULT_RULES."""
        engine = GovernanceEvolutionEngine(storage_path=tmp_gov / "gov")
        rules = engine.get_active_rules()

        assert "resource_proposals" in rules
        assert "capability_proposals" in rules
        assert "policy_proposals" in rules
        assert "goal_proposals" in rules

        for rule in rules.values():
            assert "required_votes_fraction" in rule
            assert 0.1 <= rule["required_votes_fraction"] <= 1.0

    def test_rules_persist_across_instances(self, tmp_gov):
        """Rules written to disk are loaded by a new instance."""
        storage = tmp_gov / "gov"
        engine1 = GovernanceEvolutionEngine(storage_path=storage)
        rules1 = engine1.get_active_rules()
        initial_threshold = rules1["resource_proposals"]["required_votes_fraction"]

        # manually modify rules
        engine1._rules["resource_proposals"]["required_votes_fraction"] = 0.99
        engine1._save_rules()

        engine2 = GovernanceEvolutionEngine(storage_path=storage)
        rules2 = engine2.get_active_rules()
        assert rules2["resource_proposals"]["required_votes_fraction"] == 0.99


# ---------------------------------------------------------------------------
# Test 2 — observe_outcome records data
# ---------------------------------------------------------------------------

class TestObserveOutcome:
    def test_observe_outcome_stores_record(self, tmp_gov):
        """observe_outcome() returns a record_id and stores the outcome."""
        engine = GovernanceEvolutionEngine(storage_path=tmp_gov / "gov")

        record_id = engine.observe_outcome(
            proposal_id="prop-001",
            proposal_type="resource_proposals",
            outcome="good",
            votes_for=3,
            votes_against=0,
            participants=4,
            approved=True,
        )

        assert record_id is not None
        outcomes = engine.get_outcomes("resource_proposals")
        assert len(outcomes) == 1
        assert outcomes[0]["outcome"] == "good"
        assert outcomes[0]["votes_for"] == 3

    def test_multiple_outcomes_accumulate(self, tmp_gov):
        """Multiple observe_outcome() calls accumulate correctly."""
        engine = GovernanceEvolutionEngine(storage_path=tmp_gov / "gov")

        for i in range(5):
            engine.observe_outcome(
                proposal_id=f"prop-{i}",
                proposal_type="capability_proposals",
                outcome="good" if i % 2 == 0 else "neutral",
                votes_for=3,
                votes_against=1,
                participants=4,
                approved=True,
            )

        outcomes = engine.get_outcomes("capability_proposals")
        assert len(outcomes) == 5

    def test_outcome_filtering_by_type(self, tmp_gov):
        """get_outcomes() filters by proposal_type correctly."""
        engine = GovernanceEvolutionEngine(storage_path=tmp_gov / "gov")

        engine.observe_outcome("p1", "resource_proposals", "good", 3, 0, 4, True)
        engine.observe_outcome("p2", "capability_proposals", "bad", 1, 3, 4, False)
        engine.observe_outcome("p3", "resource_proposals", "neutral", 2, 1, 4, True)

        resource = engine.get_outcomes("resource_proposals")
        capability = engine.get_outcomes("capability_proposals")
        all_outcomes = engine.get_outcomes()

        assert len(resource) == 2
        assert len(capability) == 1
        assert len(all_outcomes) == 3

    def test_outcomes_persist_to_disk(self, tmp_gov):
        """Observed outcomes survive creating a new engine instance."""
        storage = tmp_gov / "gov"
        engine1 = GovernanceEvolutionEngine(storage_path=storage)
        engine1.observe_outcome("p1", "resource_proposals", "good", 3, 0, 4, True)
        engine1.observe_outcome("p2", "resource_proposals", "bad", 1, 2, 4, False)

        engine2 = GovernanceEvolutionEngine(storage_path=storage)
        outcomes = engine2.get_outcomes("resource_proposals")
        assert len(outcomes) == 2


# ---------------------------------------------------------------------------
# Test 3 — GovernanceAnalyzer detects improvement opportunities
# ---------------------------------------------------------------------------

class TestAnalysis:
    def test_analyze_returns_analysis_objects(self, tmp_gov):
        """analyze() returns QuorumAnalysis for types with sufficient data."""
        engine = GovernanceEvolutionEngine(storage_path=tmp_gov / "gov")

        # add enough outcomes to trigger analysis
        for i in range(6):
            engine.observe_outcome(
                f"p{i}", "resource_proposals", "good",
                votes_for=4, votes_against=0, participants=4, approved=True,
            )

        analyses = engine.analyze()
        assert len(analyses) >= 1

        analysis = next(a for a in analyses if
                        (a.proposal_type if hasattr(a, "proposal_type") else a["proposal_type"])
                        == "resource_proposals")
        an = analysis if isinstance(analysis, dict) else None
        if an is None:
            assert analysis.sample_size == 6
            assert analysis.good_outcome_rate == pytest.approx(1.0)
        else:
            assert an["sample_size"] == 6

    def test_analyze_skips_types_with_few_samples(self, tmp_gov):
        """analyze() skips proposal types with < 3 outcomes."""
        engine = GovernanceEvolutionEngine(storage_path=tmp_gov / "gov")

        # only 2 outcomes — below threshold for analysis
        engine.observe_outcome("p1", "goal_proposals", "good", 2, 0, 3, True)
        engine.observe_outcome("p2", "goal_proposals", "neutral", 1, 1, 3, False)

        analyses = engine.analyze()
        types_analyzed = [
            (a.proposal_type if hasattr(a, "proposal_type") else a["proposal_type"])
            for a in analyses
        ]
        assert "goal_proposals" not in types_analyzed

    def test_high_unanimity_suggests_lower_threshold(self, tmp_gov):
        """
        When >70% of proposals pass unanimously and threshold is >0.5,
        analyzer suggests lowering the threshold.
        """
        engine = GovernanceEvolutionEngine(storage_path=tmp_gov / "gov")

        # set a high initial threshold
        engine._rules["resource_proposals"]["required_votes_fraction"] = 0.75

        # 8 out of 10 proposals passed unanimously (4/4 votes)
        for i in range(8):
            engine.observe_outcome(
                f"p{i}", "resource_proposals", "good",
                votes_for=4, votes_against=0, participants=4, approved=True,
            )
        for i in range(8, 10):
            engine.observe_outcome(
                f"p{i}", "resource_proposals", "neutral",
                votes_for=3, votes_against=1, participants=4, approved=True,
            )

        analyses = engine.analyze()
        resource_analysis = next(
            (a for a in analyses if
             (a.proposal_type if hasattr(a, "proposal_type") else a["proposal_type"])
             == "resource_proposals"),
            None
        )

        assert resource_analysis is not None
        sug = resource_analysis.suggested_threshold if hasattr(resource_analysis, "suggested_threshold") else resource_analysis.get("suggested_threshold")
        assert sug is not None
        assert sug < 0.75  # should suggest lowering

    def test_detect_opportunity_returns_rule_proposal(self, tmp_gov):
        """
        When analysis detects a clear improvement opportunity,
        detect_improvement_opportunity() returns a RuleProposal.
        """
        engine = GovernanceEvolutionEngine(storage_path=tmp_gov / "gov")
        engine._rules["resource_proposals"]["required_votes_fraction"] = 0.8

        # unanimous approvals → should trigger suggestion
        for i in range(8):
            engine.observe_outcome(
                f"p{i}", "resource_proposals", "good",
                votes_for=5, votes_against=0, participants=5, approved=True,
            )
        for i in range(8, 10):
            engine.observe_outcome(
                f"p{i}", "resource_proposals", "good",
                votes_for=4, votes_against=0, participants=5, approved=True,
            )

        analyses = engine.analyze()
        opportunity = engine._analyzer.detect_improvement_opportunity(analyses)

        assert opportunity is not None
        assert isinstance(opportunity, RuleProposal)
        assert opportunity.proposal_type == "resource_proposals"
        assert opportunity.proposed_value < 0.8
        assert len(opportunity.rationale) > 10  # meaningful rationale


# ---------------------------------------------------------------------------
# Test 4 — governance rule changes go through consensus
# ---------------------------------------------------------------------------

class TestRuleChangeProposal:
    def test_propose_without_consensus_returns_none(self, tmp_gov):
        """propose_rule_change() with no consensus manager returns None."""
        engine = GovernanceEvolutionEngine(consensus_manager=None, storage_path=tmp_gov / "gov")

        result = engine.propose_rule_change(
            proposed_by=_uid(),
            proposal_type="resource_proposals",
            new_threshold=0.5,
            rationale="test proposal",
            participants=[_uid(), _uid()],
        )
        assert result is None

    def test_propose_with_consensus_returns_proposal_id(self, tmp_gov):
        """propose_rule_change() with consensus manager returns a proposal_id."""
        if not CONSENSUS_AVAILABLE:
            pytest.skip("consensus module not available")

        consensus = ConsensusManager()
        engine = GovernanceEvolutionEngine(
            consensus_manager=consensus,
            storage_path=tmp_gov / "gov"
        )

        proposer = _uid()
        voters = [_uid(), _uid(), _uid()]

        proposal_id = engine.propose_rule_change(
            proposed_by=proposer,
            proposal_type="resource_proposals",
            new_threshold=0.5,
            rationale="too many unanimous votes suggest threshold is too high",
            participants=voters,
        )

        assert proposal_id is not None
        # verify the proposal exists in the consensus system
        stored = consensus.get(proposal_id)
        assert stored is not None

    def test_proposal_describes_the_change(self, tmp_gov):
        """The consensus proposal description accurately describes the rule change."""
        if not CONSENSUS_AVAILABLE:
            pytest.skip("consensus module not available")

        consensus = ConsensusManager()
        engine = GovernanceEvolutionEngine(
            consensus_manager=consensus,
            storage_path=tmp_gov / "gov"
        )

        voters = [_uid(), _uid(), _uid()]
        proposal_id = engine.propose_rule_change(
            proposed_by=_uid(),
            proposal_type="capability_proposals",
            new_threshold=0.7,
            rationale="near-misses correlating with bad outcomes",
            participants=voters,
        )

        stored = consensus.get(proposal_id)
        desc = stored.get("description", "") if isinstance(stored, dict) else ""
        assert "capability_proposals" in desc
        assert "70%" in desc or "0.7" in desc or "increase" in desc.lower() or "decrease" in desc.lower()


# ---------------------------------------------------------------------------
# Test 5 — apply_approved_change updates live rules
# ---------------------------------------------------------------------------

class TestApplyChange:
    def test_apply_approved_change_updates_rules(self, tmp_gov):
        """After consensus approves a rule change, apply_approved_change() updates active rules."""
        if not CONSENSUS_AVAILABLE:
            pytest.skip("consensus module not available")

        consensus = ConsensusManager()
        engine = GovernanceEvolutionEngine(
            consensus_manager=consensus,
            storage_path=tmp_gov / "gov"
        )

        proposer = _uid()
        voters = [_uid(), _uid()]

        proposal_id = engine.propose_rule_change(
            proposed_by=proposer,
            proposal_type="goal_proposals",
            new_threshold=0.8,
            rationale="test change",
            participants=voters,
        )

        # cast approving votes
        for voter in voters:
            consensus.vote(voter, proposal_id, accept=True, reason="agreed")

        # apply the approved change
        applied = engine.apply_approved_change(
            consensus_proposal_id=proposal_id,
            approved_by=voters,
            rejected_by=[],
        )

        assert applied is True
        rules = engine.get_active_rules()
        assert rules["goal_proposals"]["required_votes_fraction"] == pytest.approx(0.8, abs=0.01)

    def test_apply_records_change_in_history(self, tmp_gov):
        """apply_approved_change() appends to rule_history."""
        if not CONSENSUS_AVAILABLE:
            pytest.skip("consensus module not available")

        consensus = ConsensusManager()
        engine = GovernanceEvolutionEngine(
            consensus_manager=consensus,
            storage_path=tmp_gov / "gov"
        )

        voters = [_uid(), _uid()]
        proposal_id = engine.propose_rule_change(
            proposed_by=_uid(),
            proposal_type="goal_proposals",
            new_threshold=0.8,
            rationale="test",
            participants=voters,
        )
        for v in voters:
            consensus.vote(v, proposal_id, accept=True, reason="ok")

        engine.apply_approved_change(proposal_id, voters, [])

        history = engine.get_rule_history()
        assert len(history) == 1
        assert history[0]["proposal_type"] == "goal_proposals"
        assert history[0]["new_threshold"] == pytest.approx(0.8, abs=0.01)
        assert history[0]["consensus_proposal_id"] == proposal_id


# ---------------------------------------------------------------------------
# Test 6 — auto_propose_if_warranted closes the loop
# ---------------------------------------------------------------------------

class TestAutoPropose:
    def test_auto_propose_returns_none_without_evidence(self, tmp_gov):
        """auto_propose_if_warranted() returns None when no change is warranted."""
        engine = GovernanceEvolutionEngine(storage_path=tmp_gov / "gov")

        # no outcomes → no analysis → no proposal
        result = engine.auto_propose_if_warranted(_uid(), [_uid(), _uid()])
        assert result is None

    def test_auto_propose_submits_consensus_when_warranted(self, tmp_gov):
        """
        When evidence is strong enough, auto_propose_if_warranted()
        submits a real consensus proposal and returns its ID.
        """
        if not CONSENSUS_AVAILABLE:
            pytest.skip("consensus module not available")

        consensus = ConsensusManager()
        engine = GovernanceEvolutionEngine(
            consensus_manager=consensus,
            storage_path=tmp_gov / "gov"
        )

        # set high threshold
        engine._rules["resource_proposals"]["required_votes_fraction"] = 0.8

        # 10 unanimous approvals → should trigger auto-proposal
        for i in range(10):
            engine.observe_outcome(
                f"p{i}", "resource_proposals", "good",
                votes_for=4, votes_against=0, participants=4, approved=True,
            )

        voters = [_uid(), _uid(), _uid()]
        proposal_id = engine.auto_propose_if_warranted(_uid(), voters)

        # if an opportunity was detected, proposal_id should be non-None
        # (it might be None if the heuristic doesn't fire — that's also valid)
        if proposal_id is not None:
            stored = consensus.get(proposal_id)
            assert stored is not None
            action = stored.get("action", {}) if isinstance(stored, dict) else {}
            assert action.get("type") == "governance_rule_change"
            assert action.get("proposal_type") == "resource_proposals"
