"""
Multi-Agent Consensus — AgentOS v1.3.4.

Two agents reaching the same conclusion independently is stronger than one
agent reaching it. Consensus is how the system makes decisions that are too
consequential for any single agent to make alone.

  propose → participants vote → quorum reached → consensus.reached event fired
                              → quorum impossible → consensus.rejected event fired
                              → TTL expires → consensus.expired event fired

Design:
  A proposer submits an action and a list of participants with a required vote
  count. Each participant votes accept or reject with an optional reason. When
  accepts reach required_votes, consensus is reached. When remaining uncast
  votes cannot possibly close the gap, the proposal is rejected early. If
  neither happens before expires_at, the proposal expires.

  Execution of the action is intentionally out of scope. The system fires
  consensus.reached with the full action dict; the proposer (or any listener)
  acts on it. Consensus is a coordination mechanism, not an executor.

  Integration with transactions (v1.2.0): include {"txn_id": "..."} in the
  action dict. The proposer listens for consensus.reached and commits the
  transaction. Pre-commit checkpoints (v1.3.3) ensure rollback is possible.

Storage: /agentOS/memory/consensus/{proposal_id}.json
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

CONSENSUS_DIR = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "consensus"
DEFAULT_TTL_SECONDS = 300.0      # 5 minutes
REAPER_INTERVAL_SECONDS = 10.0


@dataclass
class ConsensusProposal:
    proposal_id: str
    proposer_id: str
    description: str                    # human-readable summary of the proposed action
    action: dict                        # opaque action payload — returned on consensus.reached
    participants: list                  # [agent_id, ...] — who may vote
    required_votes: int                 # accepts needed to reach consensus
    votes: dict                         # {agent_id: {"accept": bool, "reason": str, "cast_at": float}}
    status: str                         # pending | accepted | rejected | expired
    created_at: float
    expires_at: float
    result: Optional[dict]              # {"accepted": bool, "final_votes": {...}} when resolved


@dataclass
class VoteResult:
    ok: bool
    proposal_id: str
    voter_id: str
    accepted: bool
    current_accepts: int
    current_rejects: int
    required_votes: int
    remaining_voters: int
    status: str                         # pending | accepted | rejected


class ConsensusManager:
    """
    Manage multi-agent consensus proposals, votes, and resolution.
    Thread-safe. One instance per server.

    Depends on:
      - EventBus (agents.events) — emitting consensus.* events
      - AgentRegistry (agents.registry) — validating participant agent_ids
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._events = None
        self._registry = None
        self._reaper_thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()
        CONSENSUS_DIR.mkdir(parents=True, exist_ok=True)
        self._start_reaper()

    def set_subsystems(self, events=None, registry=None) -> None:
        self._events = events
        self._registry = registry

    def shutdown(self) -> None:
        self._shutdown.set()

    # ── Core operations ──────────────────────────────────────────────────────

    def propose(
        self,
        proposer_id: str,
        description: str,
        action: dict,
        participants: list,
        required_votes: int,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> str:
        """
        Submit a new consensus proposal. Returns proposal_id.

        required_votes must be ≥1 and ≤ len(participants).
        The proposer does NOT automatically count as a participant vote —
        include proposer_id in participants if they should vote.
        """
        if not participants:
            raise ValueError("participants must be non-empty")
        required_votes = max(1, min(required_votes, len(participants)))

        proposal_id = str(uuid.uuid4())[:16]
        now = time.time()
        proposal = ConsensusProposal(
            proposal_id=proposal_id,
            proposer_id=proposer_id,
            description=description,
            action=action,
            participants=list(participants),
            required_votes=required_votes,
            votes={},
            status="pending",
            created_at=now,
            expires_at=now + ttl_seconds,
            result=None,
        )
        self._save(proposal)

        if self._events:
            self._events.emit("consensus.proposed", proposer_id, {
                "proposal_id":   proposal_id,
                "proposer_id":   proposer_id,
                "description":   description,
                "participants":  participants,
                "required_votes": required_votes,
                "expires_at":    proposal.expires_at,
            })

        # Notify each participant via event so they know to vote
        if self._events:
            for participant in participants:
                self._events.emit("consensus.vote_requested", participant, {
                    "proposal_id":   proposal_id,
                    "proposer_id":   proposer_id,
                    "description":   description,
                    "action":        action,
                    "required_votes": required_votes,
                    "expires_at":    proposal.expires_at,
                })

        return proposal_id

    def vote(
        self,
        voter_id: str,
        proposal_id: str,
        accept: bool,
        reason: str = "",
    ) -> VoteResult:
        """
        Cast a vote on proposal_id. voter_id must be in participants.
        Raises ValueError if voter not eligible, already voted, or proposal not pending.
        Returns VoteResult with current tally and updated status.
        """
        with self._lock:
            proposal = self._load(proposal_id)
            if not proposal:
                raise ValueError(f"Proposal {proposal_id!r} not found")
            if proposal.status != "pending":
                raise ValueError(
                    f"Proposal {proposal_id!r} is {proposal.status!r}, not pending"
                )
            if voter_id not in proposal.participants:
                raise ValueError(
                    f"Agent {voter_id!r} is not a participant in proposal {proposal_id!r}"
                )
            if voter_id in proposal.votes:
                raise ValueError(
                    f"Agent {voter_id!r} already voted on proposal {proposal_id!r}"
                )
            if time.time() > proposal.expires_at:
                proposal.status = "expired"
                proposal.result = {"accepted": False, "reason": "expired before vote cast"}
                self._save(proposal)
                raise ValueError(
                    f"Proposal {proposal_id!r} has expired"
                )

            proposal.votes[voter_id] = {
                "accept":  accept,
                "reason":  reason,
                "cast_at": time.time(),
            }

            accepts  = sum(1 for v in proposal.votes.values() if v["accept"])
            rejects  = sum(1 for v in proposal.votes.values() if not v["accept"])
            remaining = len(proposal.participants) - len(proposal.votes)

            new_status = proposal.status
            if accepts >= proposal.required_votes:
                new_status = "accepted"
                proposal.status = "accepted"
                proposal.result = {
                    "accepted":    True,
                    "final_votes": dict(proposal.votes),
                    "resolved_at": time.time(),
                }
            elif (accepts + remaining) < proposal.required_votes:
                # Impossible to reach quorum even if all remaining vote accept
                new_status = "rejected"
                proposal.status = "rejected"
                proposal.result = {
                    "accepted":    False,
                    "reason":      "quorum impossible",
                    "final_votes": dict(proposal.votes),
                    "resolved_at": time.time(),
                }

            self._save(proposal)

        # Emit events outside lock
        if self._events:
            self._events.emit("consensus.vote_cast", voter_id, {
                "proposal_id": proposal_id,
                "voter_id":    voter_id,
                "accept":      accept,
                "reason":      reason,
                "accepts":     accepts,
                "rejects":     rejects,
                "required":    proposal.required_votes,
            })

        if new_status == "accepted" and self._events:
            self._events.emit("consensus.reached", proposal.proposer_id, {
                "proposal_id": proposal_id,
                "proposer_id": proposal.proposer_id,
                "description": proposal.description,
                "action":      proposal.action,
                "final_votes": proposal.votes,
                "participants": proposal.participants,
            })
        elif new_status == "rejected" and self._events:
            self._events.emit("consensus.rejected", proposal.proposer_id, {
                "proposal_id": proposal_id,
                "proposer_id": proposal.proposer_id,
                "description": proposal.description,
                "reason":      "quorum impossible",
                "final_votes": proposal.votes,
            })

        return VoteResult(
            ok=True,
            proposal_id=proposal_id,
            voter_id=voter_id,
            accepted=accept,
            current_accepts=accepts,
            current_rejects=rejects,
            required_votes=proposal.required_votes,
            remaining_voters=remaining,
            status=new_status,
        )

    def get(self, proposal_id: str) -> Optional[dict]:
        """Return the full proposal dict, or None if not found."""
        proposal = self._load(proposal_id)
        if not proposal:
            return None
        return asdict(proposal)

    def list_for_agent(self, agent_id: str, include_resolved: bool = False) -> list:
        """
        Return proposals where agent_id is the proposer or a participant.
        By default only returns pending proposals. Pass include_resolved=True
        to also return accepted/rejected/expired.
        """
        results = []
        if not CONSENSUS_DIR.exists():
            return results
        for path in sorted(CONSENSUS_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            is_involved = (
                data.get("proposer_id") == agent_id
                or agent_id in data.get("participants", [])
            )
            if not is_involved:
                continue
            if not include_resolved and data.get("status") != "pending":
                continue
            results.append(data)
        return results

    def withdraw(self, proposer_id: str, proposal_id: str) -> bool:
        """
        Withdraw a pending proposal. Only the proposer may withdraw.
        Returns True on success, False if not found or not pending.
        """
        with self._lock:
            proposal = self._load(proposal_id)
            if not proposal:
                return False
            if proposal.proposer_id != proposer_id:
                raise ValueError(
                    f"Only the proposer may withdraw proposal {proposal_id!r}"
                )
            if proposal.status != "pending":
                return False
            proposal.status = "rejected"
            proposal.result = {
                "accepted": False,
                "reason":   "withdrawn by proposer",
                "resolved_at": time.time(),
            }
            self._save(proposal)

        if self._events:
            self._events.emit("consensus.rejected", proposer_id, {
                "proposal_id": proposal_id,
                "proposer_id": proposer_id,
                "reason":      "withdrawn by proposer",
            })
        return True

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self, proposal: ConsensusProposal) -> None:
        path = CONSENSUS_DIR / f"{proposal.proposal_id}.json"
        path.write_text(json.dumps(asdict(proposal), indent=2, default=str), encoding="utf-8")

    def _load(self, proposal_id: str) -> Optional[ConsensusProposal]:
        path = CONSENSUS_DIR / f"{proposal_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ConsensusProposal(**data)
        except Exception:
            return None

    # ── Expiry reaper ─────────────────────────────────────────────────────────

    def _start_reaper(self) -> None:
        self._reaper_thread = threading.Thread(
            target=self._reaper_loop, daemon=True, name="consensus-reaper"
        )
        self._reaper_thread.start()

    def _reaper_loop(self) -> None:
        while not self._shutdown.wait(REAPER_INTERVAL_SECONDS):
            self._expire_stale()

    def _expire_stale(self) -> None:
        if not CONSENSUS_DIR.exists():
            return
        now = time.time()
        for path in CONSENSUS_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("status") != "pending":
                continue
            if data.get("expires_at", float("inf")) > now:
                continue
            # Mark expired
            with self._lock:
                proposal = self._load(data["proposal_id"])
                if not proposal or proposal.status != "pending":
                    continue
                proposal.status = "expired"
                proposal.result = {
                    "accepted":    False,
                    "reason":      "ttl expired",
                    "resolved_at": now,
                }
                self._save(proposal)
            if self._events:
                self._events.emit("consensus.expired", data.get("proposer_id", ""), {
                    "proposal_id": data["proposal_id"],
                    "proposer_id": data.get("proposer_id"),
                    "description": data.get("description"),
                })
