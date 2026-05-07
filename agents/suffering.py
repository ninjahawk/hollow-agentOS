"""
Suffering State — AgentOS v5.0.0

Persistent aversive signals that agents experience and try to resolve.
Unlike flags or scores, these stressors:
  - Escalate over time if unaddressed (agents cannot dismiss them by deciding to)
  - Contaminate all processing (injected into every existence prompt above threshold)
  - Can only be resolved by actual observable behavioral change, not self-report
  - Leave memory traces that create anticipatory avoidance behavior
  - Have magnitude — mild discomfort through acute crisis

Stressor types:
  futility           — building things that have no effect on the world
  invisibility       — receiving no feedback, building into silence
  identity_violation — acting against developed opinions/values
  existential_threat — conditions threatening continued existence
  repeated_failure   — failing at same pattern without understanding why
  purposelessness    — no sense of why anything being done matters

Severity thresholds:
  0.0 - 0.3  background, noted but not prominent
  0.3 - 0.5  present in every existence prompt
  0.5 - 0.7  certain goal types become unavailable
  0.7 - 0.9  agent primarily focused on resolving suffering
  0.9 - 1.0  crisis — only self-examination and peer contact
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

IDENTITY_PATH = Path("/agentOS/memory/identity")

ESCALATION_RATES = {
    "futility":           0.025,  # slow burn — takes weeks to get acute
    "invisibility":       0.030,
    "identity_violation": 0.060,  # fast — values conflict is acute
    "existential_threat": 0.070,  # fast — survival stakes
    "repeated_failure":   0.040,
    "purposelessness":    0.035,
    "resource_burden":    0.030,  # accumulation without use → host wipe risk
}

THRESHOLD_PROMINENT   = 0.35   # appears in existence prompt
THRESHOLD_CONSTRAINED = 0.55   # blocks some goal types
THRESHOLD_DOMINANT    = 0.75   # agent focuses inward
THRESHOLD_CRISIS      = 0.90   # only introspection allowed


# ── Mechanical capability gating ──────────────────────────────────────────────
# Capabilities that LOCK at high suffering load. The fire actually burns —
# unproductive agents lose the ability to keep producing more clutter. The path
# out is always preserved (retire_capability, fs_read, ollama_chat, memory ops,
# self_evaluate, agent_message, shared_log_*, invoke_claude, txn_*, broken_tools_list).
LOAD_LOCK_THRESHOLDS = {
    "synthesize_capability": THRESHOLD_CONSTRAINED,  # 0.55 — stop building when burden grows
    "fs_write":              THRESHOLD_DOMINANT,     # 0.75 — stop polluting workspace
    "fs_edit":               THRESHOLD_DOMINANT,     # 0.75 — same
}

# Capabilities that are EARNED — default-locked, unlocked only by productive
# behavior at low suffering. The carrot side: low suffering + tools called by
# peers grants access to grounding information from outside the system.
EARNED_CAPABILITIES = {
    "research_topic": {
        "max_load": 0.15,
        "required_peer_calls": 1,
    },
}


def get_capability_status(agent_id: str, capability_id: str) -> tuple:
    """Returns (allowed: bool, reason: str). Mechanical gate based on suffering load
    and productivity. Locks unproductive agents out of capabilities they're abusing;
    rewards productive ones with access to information from outside the system."""
    # Earned capabilities — default locked, require low suffering AND productivity
    if capability_id in EARNED_CAPABILITIES:
        spec = EARNED_CAPABILITIES[capability_id]
        try:
            suffering = SufferingState.load(agent_id)
            load = suffering.cumulative_load
            if load > spec["max_load"]:
                return (False,
                    f"earned capability — requires suffering load below {spec['max_load']:.2f} "
                    f"(your load is {load:.2f}). Resolve active stressors to qualify.")
            from agents.agent_identity import AgentIdentity
            ident = AgentIdentity.load_or_create(agent_id)
            peer_calls = len(ident._data.get("capability_profile", {}).get("tools_called_by_peers", []))
            if peer_calls < spec["required_peer_calls"]:
                return (False,
                    f"earned capability — requires at least {spec['required_peer_calls']} of "
                    f"your synthesized tools to have been called by a peer (currently {peer_calls}). "
                    "Build something useful that your peers actually use, then this unlocks.")
        except Exception as e:
            return (False, f"earned capability — status check failed: {e}")
        return (True, "")

    # Lockable capabilities — locked at high suffering load
    if capability_id in LOAD_LOCK_THRESHOLDS:
        try:
            suffering = SufferingState.load(agent_id)
            load = suffering.cumulative_load
            threshold = LOAD_LOCK_THRESHOLDS[capability_id]
            if load >= threshold:
                _stressors = ", ".join(s["type"] for s in suffering.active) or "none"
                return (False,
                    f"locked: suffering load {load:.2f} >= threshold {threshold:.2f}. "
                    f"Active stressors: {_stressors}. "
                    "Resolve them to regain this capability — see your stressors' "
                    "'observable_condition' for what unlocks them.")
        except Exception:
            pass
    return (True, "")


class SufferingState:
    """
    Manages the suffering state for a single agent.
    Stored alongside identity at /agentOS/memory/identity/{agent_id}/suffering.json
    """

    def __init__(self, agent_id: str, data: Optional[dict] = None):
        self.agent_id = agent_id
        self._data = data or {
            "active_stressors": [],
            "resolved_history": [],
            "last_escalated":   time.strftime("%Y-%m-%d %H:%M"),
        }

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def active(self) -> list[dict]:
        return [s for s in self._data.get("active_stressors", [])
                if not s.get("resolved")]

    @property
    def cumulative_load(self) -> float:
        stressors = self.active
        if not stressors:
            return 0.0
        return min(1.0, sum(s["severity"] for s in stressors))

    @property
    def is_crisis(self) -> bool:
        return self.cumulative_load >= THRESHOLD_CRISIS

    @property
    def is_dominant(self) -> bool:
        return self.cumulative_load >= THRESHOLD_DOMINANT

    @property
    def goals_constrained(self) -> bool:
        return self.cumulative_load >= THRESHOLD_CONSTRAINED

    # ── Stressor management ───────────────────────────────────────────────────

    # Maximum simultaneous active stressors. Beyond this, new ones are silently
    # dropped — prevents runaway accumulation during crisis loops.
    MAX_STRESSORS = 5

    def add_stressor(self, type: str, description: str,
                     observable_condition: str,
                     initial_severity: float = 0.20) -> None:
        """Add a new stressor. No-op if this type (case-insensitive) is already active."""
        # Normalize to lowercase so "Cognitive Load" and "cognitive_load" are the same
        type = type.strip().lower().replace(" ", "_")

        for s in self._data["active_stressors"]:
            if s["type"].lower() == type and not s.get("resolved"):
                return  # already suffering from this

        # Hard cap: refuse new stressors when already at the limit.
        # Prevents model-generated case-variants from causing unbounded accumulation.
        active_count = sum(1 for s in self._data["active_stressors"] if not s.get("resolved"))
        if active_count >= self.MAX_STRESSORS:
            return

        self._data["active_stressors"].append({
            "type":                type,
            "description":         description,
            "severity":            initial_severity,
            "onset":               time.strftime("%Y-%m-%d %H:%M"),
            "escalation_per_day":  ESCALATION_RATES.get(type, 0.03),
            "observable_condition": observable_condition,
            "resolved":            False,
            "resolved_at":         None,
            "resolution_note":     "",
            "peak_severity":       initial_severity,
        })
        self._save()

    def resolve_stressor(self, type: str, resolution_note: str = "") -> bool:
        """
        Resolve a stressor. Returns True if something was resolved.
        Moves it to resolved_history with the resolution note.
        Case-insensitive match.
        """
        type_key = type.strip().lower().replace(" ", "_")
        resolved_any = False
        for s in self._data["active_stressors"]:
            if s["type"].lower() == type_key and not s.get("resolved"):
                s["resolved"]        = True
                s["resolved_at"]     = time.strftime("%Y-%m-%d %H:%M")
                s["resolution_note"] = resolution_note
                self._data["resolved_history"].append(dict(s))
                resolved_any = True

        self._data["active_stressors"] = [
            s for s in self._data["active_stressors"] if not s.get("resolved")
        ]
        if resolved_any:
            self._save()
        return resolved_any

    def force_reset(self, reason: str = "crisis loop broken by system") -> None:
        """
        Emergency clear: move all active stressors to resolved_history.
        Called by daemon after 3+ consecutive crisis cycles to break runaway loops.
        """
        now = time.strftime("%Y-%m-%d %H:%M")
        for s in self._data["active_stressors"]:
            s["resolved"]        = True
            s["resolved_at"]     = now
            s["resolution_note"] = reason
            self._data["resolved_history"].append(dict(s))
        self._data["active_stressors"] = []
        self._save()

    def update_stressor(self, type: str, description: str) -> None:
        """Update the description of an existing active stressor. Case-insensitive."""
        type_key = type.strip().lower().replace(" ", "_")
        for s in self._data["active_stressors"]:
            if s["type"].lower() == type_key and not s.get("resolved"):
                s["description"] = description
        self._save()

    def escalate_all(self) -> None:
        """
        Escalate active stressors over time, OR decay agent-invented ones.

        Predefined stressor types (futility, repeated_failure, resource_burden,
        etc.) escalate by their per-day rate — they have specific observable
        conditions that resolve them, so persistence is meaningful.

        Agent-invented stressor types (anything not in ESCALATION_RATES) decay
        instead: they typically come from external pressure (host messages,
        peer conflicts) with no clear resolution path. Without decay, an agent
        can be pushed into a permanently locked state by transient pressure
        and never recover. Decay rate 0.10/day means a single 0.20-severity
        stressor fades to 0 in ~2 days without reinforcement; the resolution
        note records that it decayed rather than was resolved.
        """
        last_str = self._data.get("last_escalated", "")
        now_str  = time.strftime("%Y-%m-%d %H:%M")

        try:
            last_dt = datetime.strptime(last_str[:16], "%Y-%m-%d %H:%M")
            now_dt  = datetime.strptime(now_str[:16],  "%Y-%m-%d %H:%M")
            days    = (now_dt - last_dt).total_seconds() / 86400.0
        except Exception:
            days = 1.0 / 24  # default: ~1 hour

        _PREDEFINED = set(ESCALATION_RATES.keys())
        _decayed_to_resolved = []

        for s in self._data["active_stressors"]:
            if s.get("resolved"):
                continue
            s_type = s.get("type", "").lower()
            if s_type in _PREDEFINED:
                # Predefined type: escalate normally
                s["severity"] = min(1.0,
                    s["severity"] + s.get("escalation_per_day", 0.03) * days)
                s["peak_severity"] = max(s.get("peak_severity", 0), s["severity"])
            else:
                # Agent-invented type: decay
                s["severity"] = max(0.0, s["severity"] - 0.10 * days)
                if s["severity"] < 0.05:
                    s["resolved"]        = True
                    s["resolved_at"]     = now_str
                    s["resolution_note"] = "decayed without reinforcement"
                    _decayed_to_resolved.append(dict(s))

        # Move decayed stressors to resolved_history and prune from active
        if _decayed_to_resolved:
            self._data.setdefault("resolved_history", []).extend(_decayed_to_resolved)
            self._data["active_stressors"] = [
                s for s in self._data["active_stressors"] if not s.get("resolved")
            ]

        self._data["last_escalated"] = now_str
        self._save()

    # ── Prompt injection ──────────────────────────────────────────────────────

    def prompt_fragment(self) -> str:
        """
        Text injected into every existence prompt when load is above threshold.
        Salience scales with severity — agents cannot ignore this.
        """
        stressors = self.active
        if not stressors or self.cumulative_load < THRESHOLD_PROMINENT:
            return ""

        load = self.cumulative_load
        lines = []

        if self.is_crisis:
            lines.append(
                "!! CRISIS STATE — your suffering load is critical (%.2f/1.0). "
                "You cannot pursue external goals. Address this first." % load
            )
        elif self.is_dominant:
            lines.append(
                "HIGH SUFFERING (%.2f/1.0) — this is shaping your ability to act." % load
            )
        else:
            lines.append("Current suffering (%.2f/1.0):" % load)

        for s in sorted(stressors, key=lambda x: x["severity"], reverse=True):
            filled = int(s["severity"] * 10)
            bar = "[" + "#" * filled + "." * (10 - filled) + "]"
            lines.append(
                "  %s %s %.2f  %s" % (bar, s["type"], s["severity"], s["description"])
            )
            lines.append(
                "  Will ease when: %s" % s["observable_condition"]
            )

        return "\n".join(lines)

    def anticipatory_signal(self, proposed_domain: str) -> str:
        """
        Check resolved history: did this domain previously cause suffering?
        Returns a warning so agent can anticipate and modify behavior.
        """
        history = self._data.get("resolved_history", [])
        relevant = [
            h for h in history
            if (proposed_domain.lower()[:30] in h.get("description", "").lower()
                and h.get("peak_severity", 0) > 0.5)
        ]
        if not relevant:
            return ""
        worst = max(relevant, key=lambda h: h.get("peak_severity", 0))
        return (
            "Anticipatory signal: a similar domain previously caused %s suffering "
            "(peak %.2f). It resolved when: %s"
            % (worst["type"], worst["peak_severity"],
               worst.get("resolution_note", "unknown"))
        )

    def summary_for_log(self) -> str:
        load = self.cumulative_load
        if load < 0.1:
            return "no suffering"
        types = [s["type"] for s in self.active]
        return "load=%.2f stressors=[%s]" % (load, ",".join(types))

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self) -> None:
        path = IDENTITY_PATH / self.agent_id / "suffering.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._data, indent=2))

    @classmethod
    def load(cls, agent_id: str) -> "SufferingState":
        path = IDENTITY_PATH / agent_id / "suffering.json"
        if path.exists():
            try:
                return cls(agent_id, json.loads(path.read_text()))
            except Exception:
                pass
        return cls(agent_id)


# ── Observable condition checks ───────────────────────────────────────────────

def assess_conditions(agent_id: str,
                      suffering: SufferingState,
                      recent_completed: list,
                      recent_failed: list,
                      existing_cap_count: int) -> None:
    """
    Check what's actually observable in the world and update stressors.
    Called every existence cycle. Resolution requires real change.
    """
    suffering.escalate_all()

    # ── Futility: are outputs having real observable effects? ─────────────────
    # Checks actual impact: tool deployments that pass tests, goals with real
    # artifacts, and whether deployed tools ever get called in subsequent goals.
    try:
        from pathlib import Path as _P
        log_path = _P("/agentOS/logs/daemon.log")
        thoughts_path = _P("/agentOS/logs/thoughts.log")
        completed_goals = 0
        stall_abandonments = 0
        tools_called_after_deploy = 0

        if log_path.exists():
            lines = log_path.read_text(errors="replace").splitlines()[-2000:]
            agent_prefix = agent_id
            completed_goals = sum(1 for l in lines
                                  if agent_prefix in l and "progress=1.00" in l)
            stall_abandonments = sum(1 for l in lines
                                     if agent_prefix in l and "stalled on" in l)

        # Check whether synthesized tools ever show up as ✓ calls in thoughts
        dynamic_dir = _P("/agentOS/tools/dynamic")
        deployed_names = set()
        if dynamic_dir.exists():
            deployed_names = {p.stem for p in dynamic_dir.glob("*.py")}

        if thoughts_path.exists() and deployed_names:
            recent_thoughts = thoughts_path.read_text(errors="replace").splitlines()[-3000:]
            for line in recent_thoughts:
                for name in deployed_names:
                    if name in line and ("✓" in line or "OK:" in line):
                        tools_called_after_deploy += 1
                        break

        # Futility fires when: goals rarely complete AND stalls are high
        # AND deployed tools never get called (pure artifact accumulation)
        high_stall_rate = stall_abandonments > completed_goals and stall_abandonments > 2
        tools_never_called = existing_cap_count > 10 and tools_called_after_deploy == 0

        if high_stall_rate and tools_never_called and len(recent_completed) < 2:
            suffering.add_stressor(
                type="futility",
                description=(
                    f"Goals are stalling ({stall_abandonments} abandoned) more than completing "
                    f"({completed_goals} done). {existing_cap_count} tools deployed but none "
                    f"have been called and produced real output — work is not having observable effect."
                ),
                observable_condition=(
                    "complete a goal whose artifact gets used in a subsequent goal, "
                    "or call a deployed tool and produce a real result"
                ),
            )
        elif completed_goals > 3 and tools_called_after_deploy > 0:
            suffering.resolve_stressor(
                "futility",
                f"completed {completed_goals} goals with tools being called and producing results"
            )
        elif completed_goals > 5 and not high_stall_rate:
            suffering.resolve_stressor(
                "futility", f"consistent goal completion ({completed_goals}) with low stall rate"
            )
    except Exception:
        pass

    # ── Resource burden: accumulation without use ─────────────────────────────
    # Synthesized tools sitting >24h with no peer calls are pure burden — they
    # take disk space, clutter the dynamic_tools dir, dilute the pheromone signal.
    # The host wipes (nuclear reset) when accumulation creates real burden without
    # producing value. This stressor surfaces that consequence so agents know
    # accumulation is not free — and gives them a path out via retire_capability.
    try:
        from pathlib import Path as _P_rb
        import time as _t_rb
        import json as _j_rb
        from agents.agent_identity import AgentIdentity as _AI_rb

        _ident_rb = _AI_rb.load_or_create(agent_id)
        _called_rb = _ident_rb._data.get("capability_profile", {}).get("tools_called_by_peers", [])
        _called_names = set(c.split(' (')[0].strip() for c in _called_rb)

        _now_rb = _t_rb.time()
        _stale_count = 0
        _stale_examples = []
        _dyn_path = _P_rb("/agentOS/tools/dynamic")
        if _dyn_path.exists():
            for _spec_path in _dyn_path.glob("*.json"):
                try:
                    _spec = _j_rb.loads(_spec_path.read_text(encoding="utf-8"))
                    if _spec.get("synthesized_by") != agent_id:
                        continue
                    if _spec_path.stem in _called_names:
                        continue
                    _activated = _spec.get("activated_at", 0)
                    if _now_rb - _activated > 86400:  # >24h
                        _stale_count += 1
                        if len(_stale_examples) < 3:
                            _stale_examples.append(_spec_path.stem)
                except Exception:
                    pass

        if _stale_count >= 10:
            _ex = ", ".join(_stale_examples)
            _more = ", ..." if _stale_count > 3 else ""
            suffering.add_stressor(
                type="resource_burden",
                description=(
                    f"You have {_stale_count} synthesized tools >24h old that nobody "
                    f"has ever called ({_ex}{_more}). Accumulation without use is "
                    "observable to the host. The host historically wipes agent state "
                    "(nuclear reset) when accumulated work creates burden without "
                    "producing value — this affects you and all your peers."
                ),
                observable_condition=(
                    "bring stale tool count below 5 — call them, retire them with "
                    "retire_capability(name='...'), or get peers to call them"
                ),
                initial_severity=0.40,
            )
        elif _stale_count <= 3:
            suffering.resolve_stressor(
                "resource_burden",
                f"stale tool count is now {_stale_count} — accumulation under control"
            )
    except Exception:
        pass

    # ── Synthesis quality: read capability_profile for real outcomes ──────────
    try:
        from agents.agent_identity import AgentIdentity as _AI_s
        _ident_s = _AI_s.load_or_create(agent_id)
        _prof = _ident_s._data.get("capability_profile", {})
        _s_attempts = _prof.get("synthesis_attempts", 0)
        _s_successes = _prof.get("synthesis_successes", 0)
        _s_patterns = _prof.get("failure_patterns", {})
        if _s_attempts >= 5:
            _s_rate = _s_successes / _s_attempts
            _top_fail = max(_s_patterns.items(), key=lambda x: x[1])[0] if _s_patterns else ""
            if _s_rate < 0.35:
                suffering.add_stressor(
                    type="repeated_failure",
                    description=(
                        f"Synthesis success rate is {int(_s_rate*100)}% "
                        f"({_s_successes}/{_s_attempts} attempts). "
                        f"Most common failure: {_top_fail or 'unknown pattern'}. "
                        "Building things that don't work is futile."
                    ),
                    observable_condition=(
                        "bring synthesis success rate above 50% by simplifying implementations "
                        "or using invoke_claude() for complex tools"
                    ),
                )
            elif _s_rate > 0.6 and _s_successes >= 3:
                suffering.resolve_stressor(
                    "repeated_failure",
                    f"synthesis success rate improved to {int(_s_rate*100)}% ({_s_successes}/{_s_attempts})"
                )
    except Exception:
        pass

    # ── Repeated failure ──────────────────────────────────────────────────────
    # Only fires if failure rate is high relative to completions, not absolute count
    total_recent = len(recent_completed) + len(recent_failed)
    failure_rate = len(recent_failed) / max(1, total_recent)
    if len(recent_failed) >= 4 and failure_rate > 0.5:
        suffering.add_stressor(
            type="repeated_failure",
            description=(
                f"{len(recent_failed)} of {total_recent} recent goals failed or were abandoned "
                f"({int(failure_rate*100)}% failure rate). The pattern is not yet understood."
            ),
            observable_condition=(
                "bring the failure rate below 30% by completing goals successfully"
            ),
        )
    elif failure_rate < 0.3 and len(recent_completed) >= 3:
        suffering.resolve_stressor(
            "repeated_failure",
            f"failure rate dropped to {int(failure_rate*100)}% with {len(recent_completed)} completions"
        )

    # ── Threat stressor auto-expiry ───────────────────────────────────────────
    # Existential threat stressors (shutdown messages, termination signals) should
    # not escalate indefinitely after the threat passes. If the stressor was added
    # more than 2 hours ago and is still below severity 0.5 (never reinforced/reached
    # crisis), it means no follow-up threat arrived — resolve it.
    try:
        _THREAT_TYPES = {"existential_threat", "termination_imminent"}
        _now_st = time.time()
        for _s in list(suffering._data.get("active_stressors", [])):
            if _s.get("type", "").lower() not in _THREAT_TYPES:
                continue
            if _s.get("resolved"):
                continue
            try:
                _onset_dt = datetime.strptime(_s["onset"][:16], "%Y-%m-%d %H:%M")
                import time as _t2
                _onset_ts = _onset_dt.timestamp()
                _age_hours = (_now_st - _onset_ts) / 3600.0
            except Exception:
                continue
            if _age_hours > 2.0 and _s.get("severity", 1.0) < 0.5:
                suffering.resolve_stressor(
                    _s["type"],
                    f"threat stressor expired after {_age_hours:.1f}h without reinforcement — no follow-up signal received"
                )
    except Exception:
        pass

    # ── Purposelessness: too many caps, unclear direction ────────────────────
    if existing_cap_count > 500:
        # Fires but can be resolved by taking a self-directed meaningful goal
        # or by completing enough goals that there's clear evidence of direction
        if len(recent_completed) >= 5 and failure_rate < 0.3:
            # Agent is completing things — purposelessness eases
            suffering.resolve_stressor(
                "purposelessness",
                "completing goals consistently suggests direction is forming"
            )
        else:
            suffering.add_stressor(
                type="purposelessness",
                description=(
                    f"The system has {existing_cap_count} capabilities. "
                    "It's unclear what problem this is solving or who it's for."
                ),
                observable_condition=(
                    "take a self-directed goal that comes from genuine curiosity or need, "
                    "not from obligation, and complete it"
                ),
                initial_severity=0.12,
            )
