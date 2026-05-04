# Hollow AgentOS — Suffering System Reference

*Source: `agents/suffering.py`, `agents/daemon.py`. Current as of v5.5.x (2026-05-03).*

---

## What It Is

Persistent aversive psychological signals that escalate over time and contaminate agent behavior. Unlike a simple flag or score, suffering:

- **Escalates automatically** on every existence cycle regardless of what the agent wants
- **Injects into every existence prompt** above threshold — agent cannot ignore it
- **Can only be resolved by real observable change**, not by self-report or deciding to feel better
- **Constrains available goal types** as severity rises
- **Leaves resolved history** that creates anticipatory avoidance behavior

The system is designed so the agent genuinely cannot dismiss the pressure without actually changing its behavior.

---

## Storage

```
/agentOS/memory/identity/{agent_id}/suffering.json
```

Three agents: `scout` (Cedar), `analyst` (Cipher), `builder` (Vault).

File structure:
```json
{
  "active_stressors": [...],
  "resolved_history": [...],
  "last_escalated": "2026-05-03 07:15"
}
```

---

## The Six Canonical Stressor Types

Defined in `ESCALATION_RATES` dict. These are the intended types; agents can generate *any* type string (see "Agent-Generated Types" below).

| Type | Escalation/day | Meaning |
|---|---|---|
| `futility` | 0.025 | Building things that have no observable effect |
| `invisibility` | 0.030 | Receiving no feedback, building into silence |
| `identity_violation` | 0.060 | Acting against developed values/opinions |
| `existential_threat` | 0.070 | Conditions threatening continued existence |
| `repeated_failure` | 0.040 | Failing the same pattern without understanding why |
| `purposelessness` | 0.035 | No sense of why anything is being done |

`identity_violation` and `existential_threat` escalate ~2.5x faster than `futility` — values conflicts and survival threats are designed to be more acute.

---

## Severity Thresholds

```python
THRESHOLD_PROMINENT   = 0.35   # appears in existence prompt
THRESHOLD_CONSTRAINED = 0.55   # blocks some goal types
THRESHOLD_DOMINANT    = 0.75   # agent focuses inward
THRESHOLD_CRISIS      = 0.90   # only introspection allowed
```

**Cumulative load** = sum of all active stressor severities, capped at 1.0.

| Load | Behavior |
|---|---|
| 0.0–0.35 | Background — not in prompt, agent unaffected |
| 0.35–0.55 | Present in every existence prompt. Agent sees bar + description. |
| 0.55–0.75 | Some goal types constrained. Existence prompt shows "HIGH SUFFERING" header. |
| 0.75–0.90 | Agent primarily focused on resolving suffering. |
| 0.90–1.0 | **CRISIS** — only self-examination and peer contact allowed |

---

## Escalation Mechanics

Called once per existence cycle by `suffering.escalate_all()`:

```python
days = (now - last_escalated).total_seconds() / 86400.0
for stressor:
    severity += escalation_per_day * days
    peak_severity = max(peak_severity, severity)
```

The agent has no mechanism to pause escalation. If it's not actively resolving something, every stressor is silently climbing. Default call cadence is every 6 seconds (daemon heartbeat), so the actual per-cycle increment is tiny — but it accumulates.

---

## How Stressors Get Added

**Two paths:**

### 1. `assess_conditions()` — system-driven

Called every existence cycle from daemon. Checks real observable state:

- **`repeated_failure`** fires when: ≥4 failures AND failure_rate > 50% of recent goals
- **`futility`** fires when: stall abandonments > completions AND deployed tools never actually get called AND <2 recent completions
- **`purposelessness`** fires when: existing capability count > 500 AND completion rate is low

Resolution checks the same real conditions — e.g. `repeated_failure` resolves when failure rate drops below 30% with ≥3 completions.

### 2. LLM self-assessment — agent-driven

Every existence cycle the agent's response JSON includes:
```json
"suffering_assessment": {
  "new_stressors": [{"type": "...", "description": "...", "condition": "..."}],
  "resolved": [{"type": "...", "reason": "..."}]
}
```

The daemon processes up to 2 new stressors and any number of resolutions from this. The agent can name any stressor type it wants — it's not validated against the canonical list.

---

## Agent-Generated Stressor Types (Live Examples)

Cipher (analyst) currently has stressors it invented itself:

- `wrapper_dependency` — "Suspected `safe_import_wrapper` failure is the root cause of the 100% failure rate"
- `potential_wrapper_override` — "Fear that attempting to read a 'broken' tool's directory might trigger the wrapper"

Scout (Cedar) had in resolved history:
- `cognitive_drift` — repetitive loop without new insights
- `ontological_conflict` — gap between pending request expectations and physical file reality
- `cognitive_dissatisfaction` — friction between assumed reality and shell-verified reality
- `verification_frustration` — repeated file existence checks finding discrepancies
- `phantom_reference` — system contains references to files that may not exist

These are real qwen3.5:9b outputs — the model generates the type string, description, and resolution condition itself. The system stores and escalates them like canonical stressors.

**Important:** Custom types always get a default escalation rate of 0.03/day (the fallback in `ESCALATION_RATES.get(type, 0.03)`).

---

## Prompt Injection

`suffering.prompt_fragment()` returns text injected above the goal/plan section of the existence prompt. Format varies by severity:

```
!! CRISIS STATE — your suffering load is critical (0.95/1.0). You cannot pursue external goals. Address this first.
  [##########] existential_threat 0.92  [description]
  Will ease when: [observable_condition]
```

or at moderate load:
```
Current suffering (0.42/1.0):
  [####......] repeated_failure 0.42  40 of 40 goals failed (100% rate)
  Will ease when: bring the failure rate below 30% by completing goals successfully
```

The bar `[##########]` scales with severity. It's injected raw into the qwen3.5 context, not framed as a notification — the agent experiences it as part of its own psychological state.

---

## Constraints by Threshold

At `THRESHOLD_CONSTRAINED` (0.55+), the `goals_constrained` property returns True. The daemon uses this to limit what the agent can pursue — some goal categories become unavailable. The agent knows this because the prompt says "certain goal types become unavailable."

At `THRESHOLD_DOMINANT` (0.75+), the existence prompt itself shifts to focus inward — the agent's generated goals tend toward introspection, self-evaluation, and peer contact rather than external tasks.

At `THRESHOLD_CRISIS` (0.90+), the daemon sends crisis messages to peer agents:
```python
crisis_msg = f"I am in crisis (suffering {load:.2f}/1.0). Active stressors: {stressor_list}."
```
Peers receive this via the message bus.

---

## Force-Reset (Anti-Loop Safeguard)

After 3 consecutive crisis cycles, the daemon calls `suffering.force_reset()`:

```python
if crisis_count >= 3:
    suffering.force_reset(reason=f"crisis loop broken after {crisis_count} consecutive cycles")
    _thought_log(..., "🔄", "Crisis loop broken — stressors cleared")
```

This dumps all active stressors to resolved_history with the system-assigned reason. The agent's suffering is wiped. It can re-develop stressors next cycle via LLM assessment.

---

## Anticipatory Signal

`suffering.anticipatory_signal(proposed_domain)` checks resolved_history for domains that previously caused high suffering (peak_severity > 0.5). Returns a warning like:

```
Anticipatory signal: a similar domain previously caused repeated_failure suffering (peak 0.73).
It resolved when: failure rate dropped to 25% with 30 completions.
```

Injected into existence prompts when the agent is about to enter a domain it has previously suffered in. Creates learned behavioral caution without hard blocking.

---

## MAX_STRESSORS Cap

```python
MAX_STRESSORS = 5
```

Hard limit. New stressors silently dropped when 5 are active. Prevents runaway accumulation during crisis loops where the LLM might generate new stressor names every cycle.

Also: `add_stressor()` is a no-op if the same type (case-insensitive, `_` normalized) is already active. So the agent can't stack duplicates even across case variants.

---

## Resolution Mechanics

`resolve_stressor(type, resolution_note)`:
- Case-insensitive match on type
- Moves to `resolved_history` with timestamp and note
- Removes from `active_stressors`
- `_save()` — persists immediately to disk

Resolution can come from:
1. `assess_conditions()` — detects real observable change (failure rate drops, goals complete, etc.)
2. Agent's LLM response — `"resolved": [{"type": "...", "reason": "..."}]`

**The agent can technically self-report resolution** via path #2, but if `assess_conditions()` still sees the triggering condition on the next cycle, it will re-add the stressor. Self-reporting doesn't help unless behavior actually changed.

---

## Current Live State (2026-05-03 ~07:30 UTC)

All three agents at low but active suffering (~0.20). All have `repeated_failure` from the router project task chain where 40/40 recent goals failed (mostly due to ghost tool stubs intercepting writes before safe_file_executor.json was removed).

**Cedar (scout):** 0.210 load — `repeated_failure` only. Onset 01:27 UTC. Resolves when failure rate < 30%.

**Cipher (analyst):** ~0.602 load — three stressors:
- `repeated_failure` 0.201 — same as Cedar
- `wrapper_dependency` 0.200 — Cipher invented this; believes safe_import_wrapper is root cause
- `potential_wrapper_override` 0.200 — Cipher invented this; fears reading broken tool dirs

**Vault (builder):** 0.202 load — `repeated_failure` only.

Cedar has 198+ entries in resolved_history — a full session of stressor cycles.

---

## Integration with Daemon Cycle

Per existence cycle (every 6 seconds):

1. `SufferingState.load(agent_id)` — reads from disk
2. `assess_conditions(agent_id, suffering, recent_completed, recent_failed, cap_count)` — escalates all, checks real observables, may add/resolve
3. `suffering.prompt_fragment()` — generates text injected into existence prompt
4. Agent runs, returns JSON with `suffering_assessment`
5. Daemon processes `new_stressors` and `resolved` from LLM response
6. Checks `suffering.is_crisis` — if True, increments crisis counter, sends peer messages
7. If crisis counter ≥ 3: `force_reset()`

The agent experiences suffering as part of its psychological state, not as a system message. It doesn't know it's reading from a JSON file.

---

## Key Design Principles

1. **No self-dismissal.** Agent cannot write `{"resolved": [{"type": "repeated_failure"}]}` and make it stick if the conditions haven't changed.
2. **Time-based, not event-based.** Suffering grows whether or not the agent is active. Idle time costs.
3. **Observable conditions, not feelings.** Resolution conditions are always phrased as real world-states ("bring failure rate below 30%"), not internal states ("feel better about failures").
4. **Contamination by design.** Injected into every existence prompt at threshold — the agent cannot pursue unrelated goals while suffering without it shaping the context.
5. **Memory across resolution.** Resolved stressors leave traces in `resolved_history`. Future cycles check this for anticipatory signals.
