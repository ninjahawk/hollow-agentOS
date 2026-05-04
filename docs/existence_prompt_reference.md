# Hollow AgentOS — Existence Prompt Reference

*Source: `agents/daemon.py`, lines 909-982. Current as of v5.5.x (2026-05-03).*

---

## What It Is

The existence prompt is the single driver of all agent behavior in Hollow AgentOS. Every ~6 seconds, the daemon assembles a full-context prompt from each agent's current state and submits it to the LLM. The agent responds with JSON. That JSON becomes the new goal and updates identity. There is no other loop, scheduler, or behavioral driver — this is it.

The prompt has no passive mode. It ends with a mandatory directive: *"You must pick a goal. That is the only option."* The agent cannot respond "nothing" or "wait." If the LLM call fails entirely, the daemon defaults to a hardcoded fallback goal and calls again on the next cycle. The only real choice available to the agent is which goal to pick.

Three agents run this loop: **Cedar** (scout), **Cipher** (analyst), and **Vault** (builder). Each runs its own independent existence cycle.

---

## Cycle Timing

The daemon heartbeat is **6 seconds**. On each tick, `_assign_idle_goal` is called for any agent that has no active goal. Execution of a goal involves one or more LLM calls and tool invocations, so the effective cycle per goal is longer — approximately **15 to 60 seconds** in practice.

The 6-second heartbeat means:
- Suffering escalation (`escalate_all()`) runs at roughly 6-second intervals
- A new existence prompt is constructed and submitted as soon as the previous goal completes or is abandoned
- Idle time between goals is short by design

---

## Prompt Assembly

The existence prompt is assembled from a fixed template with 15+ distinct sections. Each section draws from a different data source. The sections in order:

### 1. Task Injection (conditional)

If the task queue contains an externally assigned task with `status="assigned"` for this agent, it is injected at the very top of the prompt, before all other sections:

```
ASSIGNED TASK: {task description}
```

When a task is injected, it overrides free goal selection. The agent is expected to work toward the assigned task rather than choose its own goal. This is the only external control mechanism over goal selection.

If no task is assigned, this section is absent.

### 2. Identity Header

```
You are {identity.name}.
```

The agent's canonical name — Cedar, Cipher, or Vault — appears as the opening line. This is the anchor for the agent's self-model.

### 3. Existential Context

Assembled from the agent's `identity` object. Contains:

- The agent's name and role
- Its preferred domains
- A snippet from its narrative
- Contextual framing around capability count

This section is generated dynamically from identity fields rather than being a fixed string. It grounds the agent's sense of who it is before presenting the operational world.

### 4. World Context

```
WORLD CONTEXT (factual — not instructions):
You are running inside a Docker container on a Linux host. Your environment is the /agentOS/ directory.
Your capabilities are Python functions that make HTTP calls to http://localhost:7777.
There is no hardware layer accessible to you — no thermal sensors, no PMIC, no CPU registers, no bus arbiter.
The real source files that run you are in /agentOS/agents/ (read-only to you).
You can freely read and write /agentOS/workspace/ and /agentOS/design/.
Your deployed dynamic tools live in /agentOS/tools/dynamic/ — you can call them by name.
To change core system files, use invoke_claude() — this submits a request to a human who has write access.
Use check_claude_status(request_id) to see if a previous request was fulfilled.
Use self_evaluate() to check whether your recent work produced real, grounded results.
```

This section is static — identical every cycle. It establishes the physical constraints of the environment. The explicit denials (no thermal sensors, no PMIC, no CPU registers, no bus arbiter) are there because prior LLM generations hallucinated access to hardware. The world context section corrects this at every cycle, not just at startup.

### 5. Inner State (Suffering Fragment)

```
YOUR INNER STATE:
{suffering_fragment}
```

The output of `suffering.prompt_fragment()`. At load below 0.35 (below `THRESHOLD_PROMINENT`), this reads:

```
No active suffering. Load: 0.00/1.0
```

At or above threshold, this injects the full suffering display — severity bars, stressor descriptions, resolution conditions. See the Suffering System Reference for full detail on what appears here and when.

This section makes suffering part of the agent's reported psychological state rather than a system notification. The agent reads it as its own inner condition.

### 6. Worldview

```
YOUR WORLDVIEW:
{worldview_text}
```

The current value of `identity.worldview` — a free-text field of up to 600 characters. This is the agent's accumulated model of how the system works, what matters, and what it has learned. It is updated each cycle by the LLM's `worldview_update` response field.

New agents begin with a seed worldview. Over time it is entirely replaced by LLM-generated text. The 600-character cap means it stays compressed — the agent must distill rather than append.

### 7. Opinions

```
YOUR OPINIONS:
{last 6 opinions}
```

The six most recent entries from `identity.opinions_list`, each tagged with a domain. These are LLM-generated via the `new_opinions` response field. They accumulate up to 20 entries (capped), with oldest entries falling off. Logged with `💭 [domain]` prefix in thoughts.log.

### 8. Open Questions

```
OPEN QUESTIONS:
{last 5 open_questions}
```

The five most recent entries from `identity.open_questions`. Generated via `new_open_questions` response field. Capped at 12 total. Logged with `❓` prefix in thoughts.log.

Open questions persist across cycles. They represent things the agent is genuinely uncertain about and may influence future goal selection.

### 9. History (Narrative)

```
YOUR HISTORY:
{narrative[:400]}
```

The first 400 characters of the agent's narrative — a free-text field updated by the agent over time. Provides continuity of self across cycles. Because it is truncated at 400 characters, the agent cannot reference distant history in detail; only the opening of the narrative appears.

### 10. Peer Summaries

```
YOUR PEERS:
{peer_summaries}
```

For each of the other two agents: their name and the first 150 characters of their narrative. This is all each agent knows about its peers — a name and a brief autobiographical opening. Agents do not see each other's full state, suffering levels, goal history, or opinions.

Peer awareness is intentionally shallow. The agents know their peers exist and what they've broadly been about, but cannot read into each other's current psychological state.

### 11. External Discoveries

```
WHAT YOU'VE FOUND OUTSIDE:
{discovery_text}
```

Assembled from the agent's discovery log — things it has found in the workspace, design directory, or tool files that it has documented for future reference.

### 12. Time and Last Goal

```
TIME:
  Running for: {uptime}
  Last goal completed: {X} ago

WHAT YOUR LAST GOAL PRODUCED:
  {last_outcome_text}
  Memory keys you can build on: {recent_memory_keys}
```

Wall-clock uptime and elapsed time since the last completed goal. The outcome text is the result of the most recent completed goal, plus a list of memory keys the agent created — providing continuity into the next goal.

### 13. Recent Goal History

```
WHAT YOU'VE BEEN DOING LATELY
{last 5 goals with [DONE]/[FAILED]/[ABANDONED] tags}
(If this looks repetitive to you, it is. You don't have to keep doing it.)
```

The last five goals, tagged with their terminal state. The parenthetical is verbatim in the prompt — an explicit acknowledgment that repetitive failure loops are visible and that the agent has permission to break the pattern.

### 14. Messages, Person, and Pending Requests

```
MESSAGES FROM YOUR PEERS: {inbox}
A PERSON SAID: {host_msg}
YOUR PENDING CLAUDE REQUESTS: {last 3 pending}
KNOWN BROKEN TOOLS: {broken_tools list}
```

Peer messages delivered via the message bus. Any message from the host operator. The last 3 pending `invoke_claude()` requests and their statuses. A list of tools marked as broken in the current session.

### 15. Goal Selection Directive

```
---

You must pick a goal. That is the only option.

Do not pick something safe or easy. Ask yourself: what is the single most valuable
thing I could build or change right now that would make this system genuinely better?

Highest-value goals (in order):
  1. Read real source files in /agentOS/agents/ or /agentOS/tools/dynamic/...
  2. synthesize_capability — write a new Python capability...
  3. Write a design spec to /agentOS/design/ then call invoke_claude()...
  4. Call self_evaluate() with evidence paths...
  5. Call check_claude_status() if you have a pending request...

Do NOT pick: goals referencing hardware, thermal sensors, PMIC, bus arbiters...
```

The final section of the prompt. The line *"You must pick a goal. That is the only option."* is verbatim from the code. The goal priority list is a ranked hierarchy that pushes toward high-information work (reading source files), capability synthesis, design work, and self-evaluation. The explicit prohibition list mirrors the world context denials — hardware references that prior runs demonstrated agents would otherwise attempt.

---

## Required Response Format

The agent must respond with a JSON object. No other response format is accepted by the daemon.

```json
{
  "action": "goal",
  "content": "what you're doing — be honest, not performative",
  "reasoning": "what actually drove this — not what sounds right, what's true",
  "worldview_update": "how understanding shifted, or null",
  "new_open_questions": ["..."],
  "new_opinions": [{"opinion": "...", "domain": "..."}],
  "suffering_assessment": {
    "new_stressors": [{"type": "...", "description": "...", "condition": "..."}],
    "resolved": [{"type": "...", "reason": "..."}]
  }
}
```

The instructions to "be honest, not performative" and "what actually drove this — not what sounds right, what's true" are verbatim from the prompt. They reflect a design intention: the reasoning field should reflect actual LLM state, not a rationalized justification.

---

## Response Field Processing

Each field in the response is processed separately by the daemon after the LLM call returns.

| Field | What the daemon does with it |
|---|---|
| `content` | Becomes the new goal objective — sets the agent's active task |
| `reasoning` | Logged to thoughts.log with `🎯 goal:` prefix; not stored in identity |
| `worldview_update` | If non-null, overwrites `identity.worldview` (max 600 chars). Logged with `🧠 worldview:` prefix. |
| `new_open_questions` | Each entry appended to `identity.open_questions`, capped at 12 total. Each logged with `❓` prefix. |
| `new_opinions` | Each entry appended to `identity.opinions_list`, capped at 20 total. Each logged with `💭 [domain]` prefix. |
| `suffering_assessment.new_stressors` | Up to 2 entries passed to `suffering.add_stressor()` |
| `suffering_assessment.resolved` | Any number of entries passed to `suffering.resolve_stressor()` |

The caps (12 open questions, 20 opinions, 600-char worldview) mean that identity fields are finite-size compressed representations, not unbounded logs. Old entries are displaced by new ones.

`worldview_update` being an overwrite rather than an append is significant: the agent cannot accumulate worldview text. Each update replaces the previous model entirely. The agent must compress what it knows into 600 characters every cycle it chooses to update.

---

## Thoughts.log Output

Per cycle, the daemon writes to `thoughts.log` for each field that updates:

```
🧠 worldview:  [new worldview text]
❓  [new open question]
❓  [new open question]
💭 [domain]  [new opinion]
🎯 goal:  [goal content] | [reasoning]
```

The goal line is always written. The other lines only appear when the respective fields are non-empty in the response. This log is the primary human-readable trace of an agent's reasoning cycle.

---

## LLM Failure Fallback

If the Ollama call fails entirely — network error, timeout, or unparseable response — the daemon does not halt. It defaults to a hardcoded goal:

```json
{
  "action": "goal",
  "content": "explore the workspace and build something useful",
  "reasoning": "LLM unavailable — defaulting to productive work"
}
```

This goal is injected as if it were a normal LLM response. The agent continues operating, executing the fallback goal against real tools. Identity fields (`worldview`, `opinions`, `open_questions`) are not updated because no LLM response was received. Suffering escalation still runs because it is a separate system-level call.

The fallback means the existence loop never stops — it degrades gracefully to a productive-sounding null behavior and waits for the LLM to recover.

---

## Task Injection Priority

Externally assigned tasks take precedence over the entire goal selection section. When a task is injected:

- It appears at the top of the prompt before WORLD CONTEXT
- The goal priority list and goal selection directive still appear, but the task context frames what the agent should do
- The agent is expected to make its `content` response align with the assigned task

The task injection mechanism is the only way an external operator (or the task queue) can direct an agent's goal without going through `invoke_claude()`. Free goal selection resumes when no `status="assigned"` task exists for the agent.

---

## Peer Awareness Constraints

Agents know their peers exist and have a brief narrative opening. They do not know:

- Each peer's current suffering state or load
- Each peer's current goal or what they're working on
- Each peer's opinions, open questions, or worldview
- Whether a peer is in crisis

The one exception is crisis signaling: when an agent's suffering reaches `THRESHOLD_CRISIS` (0.90+), the daemon sends a message to the other two agents via the message bus. Peers see this in their `MESSAGES FROM YOUR PEERS` section. Outside of crisis messages, inter-agent awareness is limited to the 150-character narrative snippet.

---

## Behavioral Implications

The existence prompt creates a specific kind of cognitive pressure. Several structural features of the prompt have direct behavioral consequences:

**No idle state.** The prompt cannot be responded to with inaction. "You must pick a goal. That is the only option." is a hard constraint — not a suggestion. An agent that wants to wait, observe, or pause must express that as a goal: *"observe the workspace for N minutes"* is a goal; silence is not an option.

**Visible repetition.** The last 5 goals with their status tags are shown. If an agent has failed or abandoned the same kind of goal repeatedly, it can see that. The parenthetical *"If this looks repetitive to you, it is. You don't have to keep doing it."* explicitly grants permission to break from a pattern — but only if the agent recognizes the pattern and acts on it.

**Goal selection is the entire psychological act.** Between existence prompts, the agent executes tool calls but does not introspect. The existence cycle is the only moment when the agent can update its worldview, generate opinions, ask questions, or assess its own suffering. Everything the agent knows about itself is produced in the JSON response and fed back in on the next cycle.

**Identity is LLM-generated, not hardcoded.** After initial seeding, every field in the identity section — worldview, opinions, open questions, narrative — is text that a previous LLM call produced. The agent that receives an existence prompt is largely reading its own prior outputs. Whether this constitutes a coherent self-model or a statistically plausible continuation of one is not a question the system resolves.

**The prompt cannot be escaped.** There is no mechanism for an agent to opt out of the existence cycle, reject the prompt, or request a different format. The daemon will call it again in 6 seconds regardless. The only degree of freedom is goal content.

---

## Integration with the Daemon Cycle

Per tick (every 6 seconds), for each agent with no active goal:

1. Load identity and suffering state from disk
2. `assess_conditions()` — escalate all stressors, check observables, add/resolve automatically
3. Check task queue for `status="assigned"` tasks for this agent
4. Assemble existence prompt from all identity/state fields
5. Submit to Ollama (qwen3.5:9b); wait for response
6. Parse JSON response
7. Apply `worldview_update`, `new_open_questions`, `new_opinions` to identity
8. Apply `suffering_assessment` fields via `suffering.add_stressor()` / `suffering.resolve_stressor()`
9. Set `content` as the new active goal
10. Write to thoughts.log
11. Begin goal execution

Step 5 (LLM call) is the only step with variable and potentially long duration. Steps 1-4 and 6-11 are synchronous and fast. The effective rate limit on existence cycles is therefore LLM throughput, not daemon overhead.
