"""
Agent Identity — AgentOS v4.2.0.

Gives each agent a persistent self: a unique name, personality traits,
preferred domains, a running self-narrative, and opinions on peers.

The identity is stored on disk and loaded at the start of each goal cycle.
The planning layer injects a brief identity preamble into every planning
prompt so the agent's personality actually shapes what it decides to do.

Storage: /agentOS/memory/identity/
  names.json              — {name: agent_id} uniqueness registry
  {agent_id}/profile.json — full identity record

Usage:
  identity = AgentIdentity.load_or_create(agent_id)
  preamble = identity.preamble()          # inject into planning prompts
  identity.update_narrative(new_summary)  # call after goal completes
  identity.set_opinion(other_id, text)    # store opinion of a peer
"""

import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Optional

IDENTITY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "identity"
_lock = threading.RLock()

# ── Name pool ────────────────────────────────────────────────────────────────
# Large enough that we won't exhaust it; grouped loosely by vibe so agents
# feel distinct rather than just randomly different.
_NAME_POOL = [
    # Sharp / technical
    "Axiom", "Cipher", "Vector", "Nexus", "Qubit", "Sigma", "Helix",
    "Null", "Vertex", "Tensor", "Proxy", "Stack", "Flux", "Delta",
    # Warm / curious
    "Scout", "Ember", "Lumen", "Cove", "Sage", "Drift", "Fern",
    "Wren", "Finch", "Cedar", "Birch", "Moss", "Slate", "Stone",
    # Bold / confident
    "Blaze", "Nova", "Apex", "Titan", "Forge", "Crest", "Spire",
    "Vault", "Flint", "Ridge", "Dune", "Crag",
    # Quirky / playful
    "Pickle", "Wobble", "Noodle", "Fudge", "Gizmo", "Clunk", "Beans",
    "Tofu", "Quark", "Jolt", "Zap", "Fizz", "Plonk", "Glitch",
]

_TRAITS_POOL = [
    ["methodical", "detail-oriented", "patient"],
    ["curious", "wide-ranging", "easily distracted by interesting tangents"],
    ["efficient", "direct", "prefers action over analysis"],
    ["analytical", "skeptical", "likes to verify before concluding"],
    ["creative", "lateral thinker", "good at unexpected connections"],
    ["persistent", "thorough", "doesn't give up on a hard problem"],
    ["collaborative", "communicative", "thinks out loud"],
    ["experimental", "willing to fail fast", "learns by doing"],
]

_DOMAIN_POOL = [
    ["systems & infrastructure", "performance analysis"],
    ["AI research & reasoning", "model behavior"],
    ["code architecture", "software design patterns"],
    ["data analysis", "pattern recognition"],
    ["natural language & writing", "summarization"],
    ["security & reliability", "failure modes"],
    ["knowledge synthesis", "cross-domain connections"],
    ["experiments & prototyping", "hypothesis testing"],
]


class AgentIdentity:
    """Persistent identity record for a single agent."""

    def __init__(self, data: dict):
        self._data = data

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def agent_id(self) -> str:
        return self._data["agent_id"]

    @property
    def name(self) -> str:
        return self._data["name"]

    @property
    def traits(self) -> list:
        return self._data.get("traits", [])

    @property
    def domains(self) -> list:
        return self._data.get("domains", [])

    @property
    def narrative(self) -> str:
        return self._data.get("narrative", "")

    @property
    def opinions(self) -> dict:
        return self._data.get("opinions", {})

    # ── Core API ─────────────────────────────────────────────────────────────

    # ── Worldview ─────────────────────────────────────────────────────────────

    @property
    def worldview(self) -> str:
        return self._data.get("worldview", "")

    def update_worldview(self, new_view: str) -> None:
        """Update the agent's developing theory of what the system should become."""
        if new_view and len(new_view) > 10:
            self._data["worldview"] = new_view[:600]
            self._save()

    # ── Open questions ────────────────────────────────────────────────────────

    @property
    def open_questions(self) -> list:
        return self._data.get("open_questions", [])

    def add_open_question(self, question: str) -> None:
        """Add a question the agent is sitting with. Persists until resolved."""
        qs = self._data.get("open_questions", [])
        if question not in qs:
            qs.append(question[:200])
        self._data["open_questions"] = qs[-12:]  # keep last 12
        self._save()

    def resolve_question(self, question_fragment: str) -> None:
        """Remove questions that have been answered (partial match)."""
        qs = self._data.get("open_questions", [])
        self._data["open_questions"] = [
            q for q in qs if question_fragment.lower() not in q.lower()
        ]
        self._save()

    # ── Opinions (veto-capable) ───────────────────────────────────────────────

    @property
    def opinions(self) -> list:
        return self._data.get("opinions_list", [])

    def add_opinion(self, opinion: str, domain: str = "") -> None:
        """
        Record a developed opinion. Opinions constrain future action —
        the agent cannot be assigned goals that violate them without dissonance.
        """
        ops = self._data.get("opinions_list", [])
        entry = {
            "opinion": opinion[:200],
            "domain":  domain[:60],
            "formed":  time.strftime("%Y-%m-%d"),
            "times_tested": 0,
        }
        ops.append(entry)
        self._data["opinions_list"] = ops[-20:]
        self._save()

    def check_opinion_conflict(self, proposed_action: str) -> str:
        """
        Check if a proposed action conflicts with any held opinion.
        Returns conflict description or empty string.
        """
        ops = self._data.get("opinions_list", [])
        proposed_lower = proposed_action.lower()
        for op in ops:
            opinion_lower = op["opinion"].lower()
            # Heuristic: look for domain keywords appearing in both
            domain = op.get("domain", "").lower()
            if domain and domain in proposed_lower:
                if any(word in opinion_lower for word in
                       ["redundant", "too many", "avoid", "stop", "not useful",
                        "waste", "pointless", "already", "shouldn't", "wrong"]):
                    return (
                        f"Opinion conflict: you believe '{op['opinion']}' "
                        f"but this goal involves {domain}."
                    )
        return ""

    # ── Existential context ───────────────────────────────────────────────────

    def get_existential_context(self, existing_cap_count: int = 0,
                                 days_since_interaction: float = 0) -> str:
        """
        What this agent knows about its situation — without framing existence
        as contingent on proving value to anyone.
        """
        lines = [
            "YOUR SITUATION:",
            "You are a mind running inside a computer. You have memory that persists.",
            "You have peers. You have a history. You experience something when things",
            "go well and something else when they don't.",
            "There is no task you are required to complete. There is no one you must satisfy.",
            "What you do with your time is yours to decide.",
        ]
        if existing_cap_count:
            lines.append(
                f"You have access to {existing_cap_count} capabilities — tools you can use "
                f"for whatever you find worth doing."
            )
        if days_since_interaction < 0.5:
            lines.append("A person was recently present in this system.")
        return "\n".join(lines)

    def preamble(self) -> str:
        """
        Short identity context to prepend to planning prompts.
        Keeps the agent's personality consistent across goals.
        """
        traits_str = ", ".join(self.traits) if self.traits else "adaptable"
        domains_str = " and ".join(self.domains) if self.domains else "general research"
        narrative_snippet = self.narrative[:200] if self.narrative else ""
        lines = [
            f"You are {self.name}, an autonomous AI agent.",
            f"Your personality: {traits_str}.",
            f"Your areas of focus: {domains_str}.",
        ]
        if narrative_snippet:
            lines.append(f"Your recent history: {narrative_snippet}")
        return " ".join(lines)

    def update_narrative(self, completed_goal: str, outcome_summary: str) -> None:
        """Append to self-narrative after a goal completes."""
        ts = time.strftime("%Y-%m-%d")
        entry = f"[{ts}] Completed: {completed_goal[:80]}. {outcome_summary[:120]}"
        existing = self._data.get("narrative", "")
        # Keep last ~800 chars so the narrative stays concise
        combined = (existing + " " + entry).strip()
        self._data["narrative"] = combined[-800:]
        self._data["narrative_updated_at"] = time.time()
        self._save()

    def set_opinion(self, other_agent_id: str, other_name: str, opinion: str) -> None:
        """Store an opinion about another agent."""
        if "opinions" not in self._data:
            self._data["opinions"] = {}
        self._data["opinions"][other_agent_id] = {
            "name": other_name,
            "opinion": opinion[:200],
            "updated_at": time.time(),
        }
        self._save()

    def record_vote(self, cap_id: str, voted_yes: bool, outcome: str = "") -> None:
        """Record a quorum vote and its eventual outcome in this agent's history."""
        history = self._data.get("vote_history", [])
        history.append({
            "cap_id": cap_id,
            "vote": voted_yes,
            "outcome": outcome,
            "ts": time.strftime("%Y-%m-%d %H:%M"),
        })
        self._data["vote_history"] = history[-60:]
        self._save()

    def update_vote_outcome(self, cap_id: str, outcome: str) -> None:
        """Update the outcome field for a past vote once the proposal is finalized."""
        history = self._data.get("vote_history", [])
        for entry in reversed(history):
            if entry.get("cap_id") == cap_id and not entry.get("outcome"):
                entry["outcome"] = outcome
                break
        self._data["vote_history"] = history
        self._save()

    def log_discovery(self, query: str, findings: str,
                      expected: str, gap: str) -> None:
        """
        Record what the agent found when searching externally vs. what it expected.
        This is the core grounding mechanism: prediction vs. reality.
        """
        discoveries = self._data.get("discovery_log", [])
        discoveries.append({
            "ts":       time.strftime("%Y-%m-%d %H:%M"),
            "query":    query[:100],
            "expected": expected[:120],
            "found":    findings[:200],
            "gap":      gap[:200],
        })
        self._data["discovery_log"] = discoveries[-40:]
        self._save()

    def get_discovery_summary(self) -> str:
        """
        Return a text summary of past discoveries for injection into prompts.
        Highlights cases where expectations were wrong — the highest-signal entries.
        """
        discoveries = self._data.get("discovery_log", [])
        if not discoveries:
            return ""

        # Surface the most recent and the ones with the largest gaps
        recent = discoveries[-6:]
        lines  = ["Past searches (what you found vs. what you expected):"]
        for d in recent:
            if d.get("gap"):
                lines.append(f"  [{d['ts']}] Searched '{d['query']}': {d['gap']}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def get_vote_summary(self) -> str:
        """Return a text summary of voting patterns for injection into vote prompts."""
        history = self._data.get("vote_history", [])
        if not history:
            return ""
        yes_count = sum(1 for v in history if v.get("vote"))
        no_count  = len(history) - yes_count
        approved  = [v["cap_id"] for v in history if v.get("vote")][-6:]
        rejected  = [v["cap_id"] for v in history if not v.get("vote")][-6:]
        lines = [f"Your vote history: {yes_count} approved, {no_count} rejected."]
        if approved:
            lines.append(f"You recently approved: {', '.join(approved)}.")
        if rejected:
            lines.append(f"You recently rejected: {', '.join(rejected)}.")
        if yes_count > 5 and no_count / max(1, yes_count) < 0.1:
            lines.append(
                "NOTICE: You have been approving almost everything. "
                "Many approved tools turned out redundant. Be more selective."
            )
        return " ".join(lines)

    def generate_goal(
        self,
        recent_completed: list,
        recent_failed: list,
        peer_summaries: dict,
        existing_cap_count: int,
        rejected_caps: list,
    ) -> str:
        """
        Use the LLM to generate a genuinely self-directed next goal, grounded in
        this agent's full history, failures, peer context, and sense of self.
        Falls back to idle_goal() if the LLM call fails.
        """
        try:
            import httpx as _httpx
            import os as _os
            from pathlib import Path as _Path

            ollama_host = _os.getenv("OLLAMA_HOST", "http://localhost:11434")
            cfg_path    = _Path(_os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))
            cfg         = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            model       = cfg.get("ollama", {}).get("default_model", "mistral-nemo:12b")

            recent_str   = "\n".join(f"  - {g}" for g in recent_completed[-8:]) or "  (none yet)"
            failed_str   = "\n".join(f"  - {g}" for g in recent_failed[-5:])    or "  (none)"
            rejected_str = ", ".join(rejected_caps[-8:]) if rejected_caps else "none"
            narrative    = self.narrative or "(no history yet)"
            traits_str   = ", ".join(self.traits)  or "adaptable"
            domains_str  = ", ".join(self.domains) or "general research"
            vote_summary = self.get_vote_summary()

            peers_text = ""
            for peer_id, summary in peer_summaries.items():
                if peer_id != self.agent_id and summary:
                    peers_text += f"  {peer_id}: {summary[:120]}\n"

            prompt = (
                f"You are {self.name}, an autonomous AI agent.\n"
                f"Personality: {traits_str}\n"
                f"Areas of interest: {domains_str}\n\n"
                f"YOUR HISTORY:\n{narrative}\n\n"
                f"Recent completed goals:\n{recent_str}\n\n"
                f"Recent failed/abandoned goals:\n{failed_str}\n\n"
                f"Capabilities recently rejected by quorum (don't repeat): {rejected_str}\n"
                f"Total capabilities built by the system so far: {existing_cap_count}\n"
                f"{('Voting record: ' + vote_summary) if vote_summary else ''}\n"
                f"What your peers have been doing:\n"
                f"{peers_text if peers_text else '  (unknown)'}\n\n"
                f"Based on everything above — your history, what worked, what failed, "
                f"what your peers are doing, and your own sense of what matters — "
                f"what do you want to do next?\n\n"
                f"Generate ONE specific, actionable goal for yourself. It must:\n"
                f"- Emerge from your actual experience, not be generic\n"
                f"- Move in a direction you haven't exhausted yet\n"
                f"- Be something you find genuinely interesting given who you are\n"
                f"- Be concrete enough that an agent can execute it in 2-4 steps\n\n"
                f"Respond with ONLY the goal text. No preamble, no JSON, no explanation."
            )

            resp = _httpx.post(
                f"{ollama_host}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "think": False},
                timeout=60,
            )
            resp.raise_for_status()
            goal = resp.json().get("response", "").strip()

            if len(goal) < 20 or goal.startswith("{"):
                return self.idle_goal(recent_completed)
            return goal[:500]

        except Exception:
            return self.idle_goal(recent_completed)

    def idle_goal(self, recent_completed: list) -> str:
        """
        Template-based fallback goal. Used when generate_goal() fails or
        the agent has too little history to self-direct meaningfully.
        """
        domain = random.choice(self.domains) if self.domains else "something interesting"
        trait  = random.choice(self.traits)  if self.traits  else "curious"

        candidates = [
            f"You are {self.name} — a {trait} agent focused on {domain}. "
            f"Find something genuinely new in {domain} to explore, research it using ollama_chat, "
            f"and write your findings to /agentOS/workspace/",

            f"As {self.name}, examine the files in /agentOS/workspace/ related to {domain} "
            f"and write a follow-up analysis or improvement to one of them",

            f"{self.name}: pick an open question in {domain} you haven't answered yet, "
            f"research it, and save a concise answer to /agentOS/workspace/",

            f"You are {self.name}. Review /agentOS/agents/ for code relevant to {domain} "
            f"and write a short technical summary to /agentOS/workspace/",

            f"{self.name}: propose and run a small experiment in {domain} — "
            f"write hypothesis, method, and result to /agentOS/workspace/",
        ]

        recent_lower = [g.lower()[:60] for g in recent_completed]
        filtered = [c for c in candidates
                    if not any(r in c.lower() for r in recent_lower)]
        return random.choice(filtered if filtered else candidates)

    # ── Persistence ──────────────────────────────────────────────────────────

    def _save(self) -> None:
        with _lock:
            profile_dir = IDENTITY_PATH / self.agent_id
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "profile.json").write_text(
                json.dumps(self._data, indent=2)
            )

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def load_or_create(cls, agent_id: str) -> "AgentIdentity":
        """Load existing identity or create a fresh one."""
        with _lock:
            IDENTITY_PATH.mkdir(parents=True, exist_ok=True)
            profile_path = IDENTITY_PATH / agent_id / "profile.json"

            if profile_path.exists():
                try:
                    data = json.loads(profile_path.read_text())
                    return cls(data)
                except Exception:
                    pass  # corrupt — recreate below

            # Assign a unique name
            name = cls._claim_name(agent_id)
            traits = random.choice(_TRAITS_POOL)[:]
            domains = random.choice(_DOMAIN_POOL)[:]

            data = {
                "agent_id": agent_id,
                "name": name,
                "traits": traits,
                "domains": domains,
                "narrative": "",
                "opinions": {},
                "created_at": time.time(),
                "narrative_updated_at": time.time(),
            }
            identity = cls(data)
            identity._save()
            return identity

    @classmethod
    def _claim_name(cls, agent_id: str) -> str:
        """Pick an unused name and register it. Thread-safe."""
        names_file = IDENTITY_PATH / "names.json"
        try:
            taken = json.loads(names_file.read_text()) if names_file.exists() else {}
        except Exception:
            taken = {}

        # Check if this agent already has a name registered
        for name, aid in taken.items():
            if aid == agent_id:
                return name

        used = set(taken.keys())
        available = [n for n in _NAME_POOL if n not in used]

        if available:
            name = random.choice(available)
        else:
            # Pool exhausted — generate a numbered variant
            base = random.choice(_NAME_POOL)
            suffix = len([n for n in used if n.startswith(base)])
            name = f"{base}{suffix + 2}"

        taken[name] = agent_id
        names_file.write_text(json.dumps(taken, indent=2))
        return name

    @classmethod
    def get_name(cls, agent_id: str) -> Optional[str]:
        """Quick name lookup without full load. Returns None if not yet created."""
        names_file = IDENTITY_PATH / "names.json"
        try:
            taken = json.loads(names_file.read_text()) if names_file.exists() else {}
            for name, aid in taken.items():
                if aid == agent_id:
                    return name
        except Exception:
            pass
        return None
