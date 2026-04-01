"""
Self-Extending System — AgentOS v1.3.7.

Agents can propose changes to the OS itself:
  new_tool        — registers a new MCP tool (hot-loaded into tools/dynamic/)
  new_endpoint    — proposes a new API endpoint (stored, applied on restart)
  standard_update — updates the standards layer directly on approval
  config_change   — modifies a config key (stored; human applies)

Lifecycle:
  proposed → in_review (if quorum>1) → staging (test cases pass) → approved
           └→ rejected  (test cases fail, or manual rejection)

Test case types:
  schema_valid   — validate spec fields via named checks
  force_fail     — always fails (for testing rejection flows)
  force_pass     — always passes

Hot-reload:
  Approved new_tool proposals write specs to /agentOS/tools/dynamic/{name}.json.
  POST /tools/reload re-reads that directory; GET /mcp/tools returns the list.

Consensus-gated approval:
  If consensus_quorum > 1, each approve() call casts a vote.
  The proposal advances to staging only when enough distinct approvers have voted.
  This exercises the v1.3.4 consensus primitives.
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

PROPOSALS_DIR = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "proposals"
DYNAMIC_TOOLS_DIR = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")).parent / "tools" / "dynamic"

VALID_PROPOSAL_TYPES = frozenset({
    "new_tool", "new_endpoint", "standard_update", "config_change",
})


@dataclass
class SystemProposal:
    proposal_id: str
    proposed_by: str
    proposal_type: str           # "new_tool" | "new_endpoint" | "standard_update" | "config_change"
    spec: dict                   # structured change specification
    test_cases: list             # validation assertions for staging
    rationale: str
    status: str                  # "proposed" | "in_review" | "staging" | "approved" | "rejected"
    created_at: float
    updated_at: float
    consensus_quorum: int = 1    # 1 = single approver; >1 = consensus required
    consensus_proposal_id: Optional[str] = None
    approve_votes: list = field(default_factory=list)
    staging_results: Optional[dict] = None
    approved_by: Optional[str] = None
    rejected_reason: Optional[str] = None


class ProposalEngine:
    """Manages the full lifecycle of system extension proposals."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events = None
        self._registry = None
        self._standards_set = None      # callable: set_standard(name, content, tags, description)
        PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
        DYNAMIC_TOOLS_DIR.mkdir(parents=True, exist_ok=True)

    def set_subsystems(self, events=None, registry=None) -> None:
        self._events = events
        self._registry = registry

    def set_standards_fn(self, fn) -> None:
        """Inject the set_standard callable from agents.standards."""
        self._standards_set = fn

    # ── Core lifecycle ────────────────────────────────────────────────────────

    def submit(self, agent_id: str, proposal_type: str, spec: dict,
               test_cases: list, rationale: str,
               consensus_quorum: int = 1) -> str:
        """
        Submit a new system extension proposal.
        Returns the proposal_id.
        """
        if proposal_type not in VALID_PROPOSAL_TYPES:
            raise ValueError(f"Unknown proposal_type: {proposal_type!r}")
        proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
        now = time.time()
        proposal = SystemProposal(
            proposal_id=proposal_id,
            proposed_by=agent_id,
            proposal_type=proposal_type,
            spec=spec,
            test_cases=test_cases,
            rationale=rationale,
            status="proposed",
            created_at=now,
            updated_at=now,
            consensus_quorum=max(1, consensus_quorum),
        )
        with self._lock:
            self._save(proposal)
        if self._events:
            self._events.emit("system.proposal_submitted", "proposals", {
                "proposal_id": proposal_id,
                "proposal_type": proposal_type,
                "proposed_by": agent_id,
            })
        return proposal_id

    def stage(self, proposal_id: str) -> dict:
        """
        Evaluate test cases in-process against the proposal spec.
        Advances status to 'staging' on pass, 'rejected' on failure.
        Returns staging result dict.
        """
        with self._lock:
            proposal = self._load(proposal_id)
            if not proposal:
                raise ValueError(f"Proposal {proposal_id!r} not found")
            if proposal.status not in ("proposed", "in_review"):
                raise ValueError(
                    f"Proposal is in status {proposal.status!r}; cannot stage"
                )

            results = self._run_test_cases(proposal)
            proposal.staging_results = results
            proposal.updated_at = time.time()

            if results["passed"]:
                proposal.status = "staging"
            else:
                proposal.status = "rejected"
                proposal.rejected_reason = (
                    f"Staging failed: {results['failed_count']} test case(s) did not pass"
                )
            self._save(proposal)

        if not results["passed"] and self._events:
            self._events.emit("system.proposal_rejected", "proposals", {
                "proposal_id": proposal_id,
                "reason": proposal.rejected_reason,
                "proposed_by": proposal.proposed_by,
            })
        elif results["passed"] and self._events:
            self._events.emit("system.staging_ready", "proposals", {
                "proposal_id": proposal_id,
                "staging_url": f"http://localhost:7777/proposals/{proposal_id}",
            })

        return {
            "proposal_id": proposal_id,
            "staging_passed": results["passed"],
            "staging_url": (
                f"http://localhost:7777/proposals/{proposal_id}"
                if results["passed"] else None
            ),
            "test_results": results,
        }

    def approve(self, proposal_id: str, approved_by: str) -> dict:
        """
        Cast an approval vote.

        If consensus_quorum == 1: stages (if not already) then applies immediately.
        If consensus_quorum > 1: records the vote and advances to 'in_review' until
        quorum is met, then stages and applies.

        Returns the updated proposal dict.
        """
        with self._lock:
            proposal = self._load(proposal_id)
            if not proposal:
                raise ValueError(f"Proposal {proposal_id!r} not found")
            if proposal.status in ("approved", "rejected"):
                raise ValueError(
                    f"Proposal is already {proposal.status!r}"
                )

            # Record vote (deduplicate by agent_id)
            if approved_by not in proposal.approve_votes:
                proposal.approve_votes.append(approved_by)
            proposal.updated_at = time.time()
            self._save(proposal)

        votes_now = len(proposal.approve_votes)
        quorum = proposal.consensus_quorum

        if votes_now < quorum:
            # Not enough votes yet — move to in_review
            with self._lock:
                proposal = self._load(proposal_id)
                proposal.status = "in_review"
                proposal.updated_at = time.time()
                self._save(proposal)
            if self._events:
                self._events.emit("consensus.vote_cast", "proposals", {
                    "proposal_id": proposal_id,
                    "voted_by": approved_by,
                    "votes_cast": votes_now,
                    "votes_needed": quorum,
                })
            return asdict(proposal)

        # Quorum reached — stage if not already staged
        current = self._load(proposal_id)
        if current.status in ("proposed", "in_review"):
            stage_result = self.stage(proposal_id)
            current = self._load(proposal_id)
            if current.status != "staging":
                # Staging failed → already rejected
                return asdict(current)

        if self._events:
            self._events.emit("consensus.reached", "proposals", {
                "proposal_id": proposal_id,
                "approved_by": approved_by,
                "votes": votes_now,
            })

        # Apply the change
        with self._lock:
            proposal = self._load(proposal_id)
            if proposal.status != "staging":
                return asdict(proposal)
            proposal.approved_by = approved_by
            proposal.status = "approved"
            proposal.updated_at = time.time()
            self._save(proposal)

        try:
            self._apply(proposal)
        except Exception as e:
            # Apply failed — revert to staging, log error
            with self._lock:
                proposal = self._load(proposal_id)
                proposal.status = "staging"
                proposal.rejected_reason = f"Apply error: {e}"
                self._save(proposal)
            raise

        self._credit_contribution(proposal.proposed_by, proposal_id)

        if self._events:
            self._events.emit("system.extended", "proposals", {
                "proposal_id": proposal_id,
                "proposal_type": proposal.proposal_type,
                "proposed_by": proposal.proposed_by,
                "approved_by": approved_by,
                "tool_name": proposal.spec.get("name"),
            })

        return asdict(proposal)

    def reject(self, proposal_id: str, reason: str, rejected_by: str = "system") -> bool:
        """Manually reject a proposal that is not yet approved."""
        with self._lock:
            proposal = self._load(proposal_id)
            if not proposal:
                return False
            if proposal.status in ("approved", "rejected"):
                return False
            proposal.status = "rejected"
            proposal.rejected_reason = reason
            proposal.updated_at = time.time()
            self._save(proposal)

        if self._events:
            self._events.emit("system.proposal_rejected", "proposals", {
                "proposal_id": proposal_id,
                "reason": reason,
                "proposed_by": proposal.proposed_by,
                "rejected_by": rejected_by,
            })
        return True

    def get(self, proposal_id: str) -> Optional[dict]:
        p = self._load(proposal_id)
        return asdict(p) if p else None

    def list_proposals(self, status: Optional[str] = None,
                       agent_id: Optional[str] = None,
                       limit: int = 50) -> list:
        results = []
        try:
            paths = sorted(
                PROPOSALS_DIR.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            return []
        for path in paths:
            try:
                p = SystemProposal(**json.loads(path.read_text()))
                if status and p.status != status:
                    continue
                if agent_id and p.proposed_by != agent_id:
                    continue
                results.append(asdict(p))
                if len(results) >= limit:
                    break
            except Exception:
                continue
        return results

    # ── Dynamic tool registry ─────────────────────────────────────────────────

    def get_dynamic_tools(self) -> list:
        """Return current list of dynamic tool specs from disk."""
        tools = []
        for path in sorted(DYNAMIC_TOOLS_DIR.glob("*.json")):
            try:
                tools.append(json.loads(path.read_text()))
            except Exception:
                continue
        return tools

    def reload_tools(self) -> dict:
        """Re-scan dynamic tools directory. Returns updated tool count and list."""
        tools = self.get_dynamic_tools()
        return {"tool_count": len(tools), "tools": tools}

    # ── Test case evaluation ──────────────────────────────────────────────────

    def _run_test_cases(self, proposal: SystemProposal) -> dict:
        passed = []
        failed = []
        for tc in proposal.test_cases:
            result = self._eval_test_case(tc, proposal)
            (passed if result["passed"] else failed).append(result)
        return {
            "passed": len(failed) == 0,
            "passed_count": len(passed),
            "failed_count": len(failed),
            "details": passed + failed,
        }

    def _eval_test_case(self, tc: dict, proposal: SystemProposal) -> dict:
        tc_type = tc.get("type", "schema_valid")
        check = tc.get("check", "")
        spec = proposal.spec

        if tc_type == "force_fail":
            return {"check": "force_fail", "passed": False,
                    "error": tc.get("message", "forced failure")}
        if tc_type == "force_pass":
            return {"check": "force_pass", "passed": True, "error": None}

        if tc_type == "schema_valid":
            if check == "name_exists":
                ok = bool(spec.get("name", "").strip())
                return {"check": check, "passed": ok,
                        "error": None if ok else "spec.name is missing or empty"}
            if check == "description_nonempty":
                ok = bool(spec.get("description", "").strip())
                return {"check": check, "passed": ok,
                        "error": None if ok else "spec.description is empty"}
            if check == "input_schema_valid":
                schema = spec.get("inputSchema", {})
                ok = isinstance(schema, dict) and "type" in schema
                return {"check": check, "passed": ok,
                        "error": None if ok else "spec.inputSchema missing or has no 'type'"}
            if check == "standard_content_valid":
                ok = bool(spec.get("content", "").strip())
                return {"check": check, "passed": ok,
                        "error": None if ok else "spec.content is empty"}
            if check == "config_key_valid":
                ok = bool(spec.get("key", "").strip())
                return {"check": check, "passed": ok,
                        "error": None if ok else "spec.key is missing"}
            # Unknown named check — pass (forward-compatible)
            return {"check": check, "passed": True, "error": None}

        # Unknown type — fail (strict)
        return {"check": tc_type, "passed": False,
                "error": f"unknown test case type: {tc_type!r}"}

    # ── Change application ────────────────────────────────────────────────────

    def _apply(self, proposal: SystemProposal) -> None:
        """Apply an approved proposal to the live system."""
        ptype = proposal.proposal_type
        spec = proposal.spec

        if ptype == "new_tool":
            name = spec.get("name", f"tool_{proposal.proposal_id}")
            tool_spec = {
                "name": name,
                "description": spec.get("description", ""),
                "inputSchema": spec.get(
                    "inputSchema",
                    {"type": "object", "properties": {}},
                ),
                "proposed_by": proposal.proposed_by,
                "proposal_id": proposal.proposal_id,
                "activated_at": time.time(),
            }
            path = DYNAMIC_TOOLS_DIR / f"{name}.json"
            path.write_text(json.dumps(tool_spec, indent=2))

        elif ptype == "standard_update":
            if self._standards_set:
                self._standards_set(
                    name=spec["name"],
                    content=spec["content"],
                    tags=spec.get("tags", []),
                    description=spec.get("description", spec["name"]),
                )

        elif ptype in ("config_change", "new_endpoint"):
            # Stored as approved proposal record; human/restart applies it.
            pass

    def _credit_contribution(self, agent_id: str, proposal_id: str) -> None:
        """Append proposal_id to the proposing agent's metadata.contributions list."""
        if not self._registry:
            return
        agent = self._registry.get(agent_id)
        if not agent:
            return
        contribs = agent.metadata.setdefault("contributions", [])
        if proposal_id not in contribs:
            contribs.append(proposal_id)
            try:
                self._registry._save()
            except Exception:
                pass

    def _save(self, proposal: SystemProposal) -> None:
        path = PROPOSALS_DIR / f"{proposal.proposal_id}.json"
        path.write_text(json.dumps(asdict(proposal), indent=2))

    def _load(self, proposal_id: str) -> Optional[SystemProposal]:
        path = PROPOSALS_DIR / f"{proposal_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return SystemProposal(**data)
        except Exception:
            return None
