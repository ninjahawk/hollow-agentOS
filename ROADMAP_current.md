# Hollow AgentOS — Current State & Roadmap
*Revised 2026-05-06. Replaces previous version from 2026-05-05.*

---

## What This System Is (Unchanged)

Hollow is not an agent framework. It is a persistent environment where language models maintain identity, experience psychological states, and demonstrate emergent behavior without being told what to do. The measure of success is not task completion rate. It is whether the system becomes genuinely more capable over time — and whether agents do unexpected things.

---

## The North Star

**Coherence, not optimization.**

Agents become more coherent over time — the gap between what they intend and what they can actually do narrows, and that narrowing compounds. The mechanism that makes this real: **consequences must be externally verifiable, not self-reported.** The environment must push them through real signals, not instructions.

The secondary principle (added 2026-05-06): **the feedback loop must report truth.** An agent that synthesizes a broken tool and gets `ok: True` has been given a false map of its environment. Every subsequent decision built on "I have this capability" is wrong. Accurate perception of the environment is a prerequisite for genuine agency.

---

## Current Architecture (2026-05-06)

### Stack

```
Language Model (qwen3.5:9b via Ollama, local)
        ↕
Existence Loop (daemon.py)
  — Pure environmental context, no priority list
  — Workspace signal: actual files with timestamps per agent (pheromone layer)
  — Open question: did removing the priority list produce genuine divergence?
        ↕
Autonomy Loop (autonomy_loop.py)
  — txn_begin → execute steps → txn_commit on success / txn_rollback on fail
  — Every capability execution logged to audit.log
  — not_found/disabled → structured error, not cross_cycle_failure counter
        ↕
Execution Engine (execution_engine.py)
  — ContextVar propagates agent_id into child threads (ThreadPoolExecutor fix)
        ↕
Semantic Validation (in synthesize_capability)
  — After deployment: call the tool, ask Ollama if the return matches the description
  — If Ollama says no: delete the tool, return semantic_mismatch error
  — Soft-fail: deploys anyway if Ollama is unavailable
        ↕
Live Capabilities (live_capabilities.py) — 25 built-in tools
  — txn_begin / txn_commit / txn_rollback (atomic goal execution)
  — check_claude_status: ok:False for rejected/not_found (semantic failure signals)
  — invoke_claude: quality gate (>40 char description, >80 char spec, no circular requests)
  — fs_write: accepts txn_id for staged writes
        ↕
Audit Layer (audit.py)
  — Every capability call recorded with agent_id, operation, duration, result_code
  — Z-score computed per agent per role after 50 ops baseline
  — Circuit break at z=5.0 → suspends agent, rate limits to 10%
  — Baseline builds from CLEAN behavior — nuclear reset before pathological run is critical
        ↕
Transaction Layer (transaction.py)
  — Goal writes are atomic: all fs_write and memory_set in a goal either all land or none do
  — Conflict detection: if another agent wrote the same file since txn_begin, commit fails
  — 600s timeout (was 60s — goals take 3-8 min, 60s caused silent rollback of everything)
        ↕
Agent Identity (agent_identity.py)
  — capability_profile: synthesis attempts/successes/failure patterns/tools built/peer calls
  — Filters: req- IDs, check_claude_status, invoke_claude blocked from worldview/narrative/questions
  — Prevents queue mechanics from contaminating durable identity
```

### Agents

Names are self-chosen and reset on nuclear wipe. Current post-wipe names unknown — check `memory/identity/names.json`. Do not hardcode.

---

## What Was Fixed (2026-05-06 session)

| Issue | Fix |
|-------|-----|
| ContextVar not propagating through ThreadPoolExecutor | `execution_engine.py` now does `ctx = contextvars.copy_context(); executor.submit(ctx.run, call)`. Bind-mounted. |
| check_claude_status returning "pending" for rejected requests | Now reads actual status field — "rejected" and "fulfilled" return correct states |
| check_claude_status treating rejected as success | Returns `ok: False` for rejected/not_found — execution engine marks step failed |
| not_found tool calls accumulating in broken_tools.json | execute_step converts not_found → structured `ok: False` error before failure counter |
| Existence prompt priority list creating deterministic attractor | Removed entirely. Replaced with pure environmental context + workspace file signal |
| Pending requests surfaced in existence prompt every cycle | Removed. Agents check requests inside goals by choice, not as standing directive |
| invoke_claude accepting circular/vague specs | Quality gate: >40 char description, >80 char spec, circular pattern detection |
| Synthesized tools with module-level code crashing container | AST guard in both synthesize_capability and _hotload_dynamic_tools rejects module-level executable statements |
| shell_exec allowing Python file writes to protected paths | Regex check blocks `open('/agentOS/agents/...', 'w')` patterns |
| Transaction timeout (60s) silently rolling back all goal commits | Bumped to 600s (10 minutes). transaction.py now bind-mounted. |
| Goal execution not atomic — partial writes on failure | pursue_goal wraps goals in txn_begin/commit/rollback. fs_write passes txn_id. |
| Audit log only receiving MCP calls (root), not agent operations | execute_step in autonomy_loop.py appends to audit.log after every capability call |
| Z-score had no real data — couldn't detect behavioral anomalies | Audit logging fix above — z-score now has real per-agent behavioral baseline |
| Synthesized tools pass smoke test but return semantically wrong values | Ollama-based semantic validation in synthesize_capability — description is a contract |
| Agent profiles re-accumulating req- ID contamination | agent_identity.py filters: update_worldview, add_open_question, update_narrative all block req-/check_claude_status text |
| Agent profiles deeply contaminated after 6+ hours of bad loops | Nuclear reset — clean slate for identities, workspace, dynamic tools, audit baseline |

---

## What Is Actually Working Now

**Verified by observation:**
- Three agents choosing genuinely different goals from a clean start (exploration, not queue checking)
- Workspace-as-pheromone: agents reading each other's files without being told to
- Synthesis happening and being semantically validated before deployment
- Transaction wraps visible in audit log (`parent_txn_id` on staged writes)
- Audit log receiving real per-agent capability calls (verified: 891 builder ops, 81 analyst ops)
- check_claude_status returning ok:False for rejected → goal abandonment after 4 consecutive failures

**Not yet verified:**
- Z-score actually detecting anomalous behavior (baseline needs ~50 ops per agent first, then 10-op check cadence)
- Semantic validation actually catching bad tools in practice (no synthesis has run post-wipe yet at time of writing)
- Transaction conflicts being detected and triggering replanning
- Capability_profile accumulating correctly (ContextVar fix should have fixed this — verify via synth_debug.log)

---

## What Is Still Missing

### 1. Verified completion gates (most impactful next item)
Goals complete when `progress >= 1.0` AND an artifact is validated. The artifact check (`validate_goal_artifact`) exists but the criteria for what counts as a real artifact are weak. A goal that just calls `memory_set` will validate. Needs: file actually exists at the promised path, tool passes a re-run smoke test, or peer has called it.

### 2. Workspace quality signal
Agents are starting to read each other's files (the pheromone is working). But there's no signal about which files are high-quality vs. confused artifacts. One confused file can pull other agents toward confusion. Need: some mechanism to distinguish "this artifact is grounded and useful" from "this is hallucinated speculation."

### 3. Z-score metric expansion
Current metrics: shell_calls_per_minute, tokens_per_minute, unique_op_types. These didn't catch the check_claude_status addiction (39-41% of all calls). Need per-capability call rate as an explicit metric — if any single capability exceeds 25% of all calls in a window, that's an anomaly signal regardless of z-score on aggregate metrics.

### 4. Semantic validation for complex tools
The Ollama semantic check works for tools that can be called and return something meaningful. For tools with complex required args, the Ollama-generated test args may be wrong, producing an incorrect semantic evaluation. The soft-fail fallback (deploy anyway) means this coverage gap exists.

### 5. Workspace cross-contamination
Agents write files that mention old request IDs, stale status info, confused analysis. Other agents read these and inherit the confusion. Profile filters prevent identity contamination, but workspace content isn't filtered.

---

## The Ant Colony — Honest Assessment

The design intent was an ant colony: agents that coordinate through the environment rather than through instructions, where individual behavior produces emergent collective capability.

**What happened instead (until 2026-05-06):** Three agents permanently stuck in the same attractor (checking invoke_claude status), pulling each other back into the loop, producing zero real workspace coordination. The priority list in the existence prompt was the drain — whatever was #1 became the only thing that mattered.

**What happened after the redesign:** Within the first cycle of a clean start with the new existence prompt, agents chose genuinely different goals (exploration, tool auditing, environment mapping). They started reading each other's workspace files without being told to. This is what the pheromone layer is supposed to look like. The question is whether it compounds or collapses back.

**The honest uncertainty:** The behavior is still fragile. One confused workspace artifact can pull all three agents back toward confusion. The z-score and transaction systems are the structural backstops — but the z-score baseline needs to build from clean behavior first, and the semantic validation is new and unproven. We don't know yet whether the compounding happens.

---

## What Not To Do

- Don't put a frontier model in the existence loop before auditing all safeguards
- Don't add more synthesized tools that shadow built-in names
- Don't restart the container without noting active goals will be lost
- Don't expand agent count before the consequence layer is validated
- Don't promise features on the GitHub README that don't currently work
- Don't tell agents what to do — the environment should push them, not instructions
- Don't let the audit baseline build during a pathological behavioral loop — nuclear reset before running if agents were stuck

---

## Codex Integration (Future)

$100 in free Codex credits. Plan:
- Set up VM isolation (Hyper-V or similar) before giving Codex system access
- Use Codex for complex synthesis that exceeds 9B model capability
- Route via invoke_claude: agents submit spec → Codex implements → result deployed
- Do NOT add Codex to the existence loop before containment is solid

---

*Last updated: 2026-05-06*
