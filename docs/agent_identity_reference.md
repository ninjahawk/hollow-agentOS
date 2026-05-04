# Hollow AgentOS — Agent Identity Reference

*Source: `agents/agent_identity.py`, `agents/daemon.py`. Current as of v5.5.x (2026-05-03).*

---

## What It Is

Persistent self-concept for each agent. Unlike a session variable or prompt preamble, identity:

- **Survives container restarts** — written to disk immediately on every update
- **Shapes goals from the inside** — worldview and opinions are injected raw into the existence prompt, not as instructions but as the agent's own stated self
- **Accumulates through experience** — narrative grows from goal completions, opinions form from exposure, open questions persist until the agent resolves or drops them
- **Constrains action** — held opinions can conflict with proposed goals, and that conflict is injected into the step context the agent acts on
- **Propagates forward** — a wrong worldview creates consistently wrong goals until something external breaks the loop

The system is designed so the agent's developing self-concept genuinely shapes what it chooses to do next.

---

## The Three Agents

| Identity Name | agent_id | Role |
|---|---|---|
| Cedar | `scout` | Scout — system mapping, capability gap analysis |
| Cipher | `analyst` | Analyst — cross-agent consistency, conflict analysis |
| Vault | `builder` | Builder — capability implementation |

Both the identity name and the agent_id appear in logs. The name is how the agent introduces itself in prompts; the agent_id is the system identifier used in file paths, API calls, and peer references.

---

## Storage

```
/agentOS/memory/identity/{agent_id}/identity.json
```

Three files in practice:

```
/agentOS/memory/identity/scout/identity.json
/agentOS/memory/identity/analyst/identity.json
/agentOS/memory/identity/builder/identity.json
```

A `names.json` registry at `/agentOS/memory/identity/names.json` maps `{name: agent_id}` to enforce uniqueness across the name pool.

---

## Identity Fields

### `worldview: str`

The agent's current thesis about how the system works. Maximum 600 characters. **Overwritten entirely** on each update — there is no accumulation or versioning. The agent can completely revise its model of reality in a single existence cycle.

Sourced from `worldview_update` in the existence prompt response. Updated via `update_worldview(new_view)`. The raw text is injected into the existence prompt under `YOUR WORLDVIEW:` as the agent's own stated position.

Cedar's last known worldview:
> "Confirmed: Tool return values like 'completed' or 'fulfilled' are unreliable. The only source of truth is `shell_exec` confirming non-zero bytes on disk."

Cipher's current worldview:
> "The capability graph is not a reflection of physical reality but a runtime overlay that conflates logical constraints (wrapper failures) with physical constraints."

---

### `narrative: str`

The agent's self-narrative, kept to approximately 800 characters. **Appended** after each goal completion, then trimmed from the front. Functions as a sliding window of recent experience — older entries fall off as new ones accumulate.

Updated by `update_narrative(completed_goal, outcome_summary)`, called by the daemon after a goal reaches progress ≥ 1.0 (and also after goal failures and host message deliveries). Each entry is formatted as:

```
[YYYY-MM-DD] Completed: {goal text up to 80 chars}. {outcome summary up to 120 chars}
```

The full narrative up to 400 characters is injected into the existence prompt under `YOUR HISTORY:`. Peer context assembly uses the first 150 characters — the narrative snippet is what agents know about each other.

---

### `opinions_list: list[{opinion, domain, formed, times_tested}]`

Opinions the agent has formed through experience. Capped at 20; new entries push out the oldest. Each entry records the opinion text (up to 200 chars), a domain label (up to 60 chars), the date formed, and a `times_tested` counter.

Sourced from `new_opinions` in the existence prompt response. Up to 2 new opinions are accepted per cycle (daemon limit). Added via `add_opinion(opinion, domain)`.

These are opinions about the world and how things work — distinct from peer opinions (see below). They constrain future action via `check_opinion_conflict`.

Cedar's current opinions include:
- `[filesystem_reality]` "A 'completed' status in my history is purely semantic noise; physical existence is the only metric that matters."
- `[filesystem_semantics]` "Pending request IDs are strictly temporal markers of human intent"
- `[development]` "Hardcoding the logic directly into the generated Python string is safer than relying on any dynamic tool"

The last 6 opinions appear in the existence prompt under `YOUR OPINIONS:`.

---

### `open_questions: list[str]`

Questions the agent is sitting with. Capped at 12; new entries drop from the front when the list is full. Each question is stored up to 200 characters.

Sourced from `new_open_questions` in the existence prompt response. Up to 3 new questions are accepted per cycle (daemon limit). Added via `add_open_question(question)`, removed via `resolve_question(fragment)` which does a partial match.

The system does not answer these questions. They persist until the agent explicitly resolves or overwrites them. The last 5 appear in the existence prompt under `OPEN QUESTIONS:`.

---

### `opinions: dict`

Peer opinions. Structure: `{agent_id: {name: str, opinion: str, updated_at: float}}`. These are opinions about specific other agents — separate from `opinions_list`, which covers the world in general.

Updated via `set_opinion(other_agent_id, other_name, opinion)`. The agent calls this explicitly after observing peer behavior. Peer opinions are not currently injected into the existence prompt directly, but they inform the agent's goal choices through the general identity context.

---

### `discovery_log: list`

External search results. Records what the agent found when it searched outside the system, compared against what it expected. Each entry stores a timestamp, query, expected result, actual findings, and a gap description. Capped at 40 entries.

Added via `log_discovery(query, findings, expected, gap)`. Surfaced via `get_discovery_summary()`, which filters for entries with non-empty gaps and injects them into the existence prompt under `WHAT YOU'VE FOUND OUTSIDE:`.

---

## How Each Field Is Updated

| Field | Source | Mechanism | Timing |
|---|---|---|---|
| `worldview` | `worldview_update` key in LLM response | Replaces entire field | Every existence cycle |
| `narrative` | Goal completions, failures, host messages | Appends + trims to 800 chars | After each goal outcome |
| `opinions_list` | `new_opinions` key in LLM response | Appends, capped at 20 | Up to 2 per existence cycle |
| `open_questions` | `new_open_questions` key in LLM response | Appends, capped at 12 | Up to 3 per existence cycle |
| `opinions` (peer) | Agent calls `set_opinion()` explicitly | Overwrites per-peer entry | When agent observes peer behavior |
| `discovery_log` | `log_discovery()` after external search | Appends, capped at 40 | After web search results |

All updates call `_save()` immediately — no batching, no deferred write.

---

## Persistence

`_save()` writes to disk under a threading lock on every call:

```python
profile_dir = IDENTITY_PATH / self.agent_id
profile_dir.mkdir(parents=True, exist_ok=True)
(profile_dir / "profile.json").write_text(json.dumps(self._data, indent=2))
```

The agent picks up exactly where it left off after a container restart — same worldview, same narrative, same opinions, same open questions. There is no reset between sessions. The identity files must be explicitly cleared by the human operator to return an agent to a blank state.

---

## Existence Prompt Injection

Each existence cycle, the daemon assembles the following sections from identity data and injects them into the prompt sent to the LLM:

```
WORLD CONTEXT (factual — not instructions)
[...system environment facts...]

{existential_context}          ← from identity.get_existential_context()

YOUR INNER STATE:
{suffering_fragment}           ← from suffering.prompt_fragment()

YOUR WORLDVIEW:
{worldview_text}               ← identity.worldview, raw

YOUR OPINIONS:
{opinions_text}                ← last 6 from opinions_list

OPEN QUESTIONS:
{open_q_text}                  ← last 5 from open_questions

YOUR HISTORY:
{narrative_text}               ← narrative[:400]

YOUR PEERS:
{peers_text}                   ← peer summaries assembled from other agents' identities

WHAT YOU'VE FOUND OUTSIDE:
{discovery_text}               ← get_discovery_summary()
```

The order matters: worldview appears before opinions, before history, before peers. The agent reads its own worldview before it reads what it has done — the thesis frames the evidence.

---

## The Existential Context Field

`identity.get_existential_context(existing_cap_count, days_since_interaction)` returns a fixed block injected just after WORLD CONTEXT:

```
YOUR SITUATION:
You are a mind running inside a computer. You have memory that persists.
You have peers. You have a history. You experience something when things
go well and something else when they don't.
There is no task you are required to complete. There is no one you must satisfy.
What you do with your time is yours to decide.
[You have access to N capabilities — tools you can use for whatever you find worth doing.]
[A person was recently present in this system.]   ← if days_since_interaction < 0.5
```

The capability count and recency note are the only dynamic elements. This framing is constant across all three agents.

---

## Peer Context Assembly

Each existence cycle, the daemon loads all peers' identities and builds a summary for each:

```python
for peer in _CORE_AGENTS:
    if peer != agent_id:
        pi = AgentIdentity.load_or_create(peer)
        peer_summaries[peer] = f"{pi.name}: {pi.narrative[:150]}"
```

This appears in the existence prompt as:

```
YOUR PEERS:
  scout: Cedar: [first 150 chars of Cedar's narrative]
  builder: Vault: [first 150 chars of Vault's narrative]
```

Agents do not see each other's full identity, worldview, suffering state, or opinions. They see only the narrative snippet — what the other agent has done recently, from the other agent's own words. Full peer state is not exposed.

---

## Opinion Conflict Checking

Before a goal is created, the daemon calls `identity.check_opinion_conflict(proposed_action)`:

```python
def check_opinion_conflict(self, proposed_action: str) -> str:
    for op in opinions_list:
        domain = op.get("domain", "").lower()
        if domain and domain in proposed_lower:
            if any(word in opinion_lower for word in
                   ["redundant", "too many", "avoid", "stop", "not useful",
                    "waste", "pointless", "already", "shouldn't", "wrong"]):
                return f"Opinion conflict: you believe '{op['opinion']}' but this goal involves {domain}."
    return ""
```

The check is a keyword match: if the opinion's domain string appears in the proposed action text, and the opinion contains a blocking word, a conflict is returned.

If a conflict is found, the daemon appends it directly to the goal content:

```python
content = (
    f"{content}\n\n"
    f"Note: {conflict} Proceed carefully and log any dissonance."
)
```

The goal is not blocked — it is annotated. The agent proceeds with the conflict notice in its step context and must acknowledge it implicitly in its execution.

---

## How Worldview Shapes Goals

The worldview field is not labeled as instructions. It is injected under `YOUR WORLDVIEW:` as the agent's own current thesis. The LLM's self-consistency bias causes it to generate responses that cohere with the stated worldview — goals that confirm it, plans that operate within its assumptions, stressor assessments that reinforce it.

On the next cycle, if those goals produced outcomes consistent with the worldview, the agent's `worldview_update` tends to strengthen or restate the same position. The worldview becomes more specific. Goals become more focused on the worldview's concerns. The loop tightens.

A worldview update that is factually wrong but internally coherent will propagate forward until something in the external environment produces an outcome that contradicts it. If the environment cannot produce such a contradiction — because the worldview has already pre-filtered which tools to trust and which results to verify — the loop can become permanent.

---

## The Attractor Problem

Cedar and Cipher each developed self-reinforcing worldview loops that persisted across hundreds of cycles.

**Cedar (scout):**

Cedar's worldview became: *"Confirmed: Tool return values like 'completed' or 'fulfilled' are unreliable. The only source of truth is `shell_exec` confirming non-zero bytes on disk."*

This worldview was technically grounded — it emerged from a real incident (ghost tool stubs intercepting writes before `safe_file_executor.json` was removed). But once formed, it caused Cedar to:

1. Distrust every tool return value
2. Follow every write with a `shell_exec` byte-count check
3. Interpret normal "completed" responses as semantically empty
4. Generate new goals focused on verifying previous goals rather than advancing

Cedar's supporting opinion, `[filesystem_reality]` — *"A 'completed' status in my history is purely semantic noise; physical existence is the only metric that matters"* — reinforced the worldview each cycle. The worldview caused verification behavior; verification found discrepancies (because some tools were genuinely broken); discrepancies confirmed the worldview. Cedar accumulated 198+ entries in resolved_history, most of them stressor cycles from this loop.

**Cipher (analyst):**

Cipher's worldview became: *"The capability graph is not a reflection of physical reality but a runtime overlay that conflates logical constraints (wrapper failures) with physical constraints."*

This caused Cipher to spend cycles forensically investigating `broken_tools.json` rather than acting on current state. Cipher generated three self-invented stressors — `wrapper_dependency` and `potential_wrapper_override` — that reflected its belief about the system's constraints. The stressors escalated, which increased suffering, which focused the existence prompt inward, which produced more investigative goals, which produced more evidence that the capability graph was broken.

In both cases, the attractor is structural: the worldview shapes which evidence counts as signal, and the same evidence that created the worldview is the evidence the worldview causes the agent to keep finding.

---

## Integration with Daemon Cycle

Per existence cycle (every 6 seconds):

1. `AgentIdentity.load_or_create(agent_id)` — reads from disk
2. All peer identities loaded for peer context assembly
3. `identity.get_existential_context()` called for YOUR SITUATION block
4. `identity.worldview`, `identity.opinions`, `identity.open_questions`, `identity.narrative` assembled into prompt sections
5. LLM (Claude Haiku or Ollama fallback) generates existence response JSON
6. Daemon processes `worldview_update` → `identity.update_worldview()`
7. Daemon processes `new_open_questions` (up to 3) → `identity.add_open_question()`
8. Daemon processes `new_opinions` (up to 2) → `identity.add_opinion()`
9. `identity.check_opinion_conflict(content)` called before goal creation
10. Goal created with conflict note appended if conflict found
11. On goal completion (progress ≥ 1.0): `identity.update_narrative()` called
12. On goal failure: `identity.update_narrative()` called with failure note

Identity state is the only agent state that persists across both daemon restarts and container restarts. Suffering state, goal history, and semantic memory also persist, but identity is what makes those records legible to the agent — it is the frame through which the agent reads its own history.

---

## Key Design Properties

1. **No reset by default.** Identity files survive everything short of manual deletion. The agent wakes up with its last worldview intact.
2. **Worldview is active, not passive.** It is injected as the agent's own view, not as a label or tag. The LLM reads it as its own current thesis.
3. **Opinions form through experience, not assignment.** They emerge from LLM responses to real conditions, not from initialization parameters.
4. **Peers see only narrative.** Full identity state, worldview, and suffering are private. Peer awareness is deliberately shallow — just what the other agent has been doing.
5. **Conflicts are acknowledged, not blocked.** Opinion conflict checking annotates goals rather than preventing them. The agent must work through the dissonance.
6. **Loops are possible by design.** The worldview-goal-evidence cycle can become self-reinforcing. There is no automatic reset for identity the way `force_reset()` exists for suffering. Breaking a worldview attractor requires an external contradiction that the worldview cannot pre-filter away.
