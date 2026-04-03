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

    def idle_goal(self, recent_completed: list) -> str:
        """
        Return a self-directed exploratory goal seeded by this agent's
        domains and traits, filtered against recent completed goals.
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
