# Hollow AgentOS — Current State & Roadmap
*Revised 2026-05-07. Replaces previous version (2026-05-06).*

---

## What This System Is (Unchanged)

Hollow is not an agent framework. It is a persistent environment where language models maintain identity, experience psychological states, and demonstrate emergent behavior without being told what to do. The measure of success is not task completion rate. It is whether the system becomes genuinely more capable over time — and whether agents do unexpected things.

---

## The North Star

**An emergent, self-modifying system that is interesting to watch, has personalities that develop, and produces meaningful work for itself — driven by environmental pressure, not instruction.**

The four axes that every change must serve:

1. **Interesting to watch** — distinct, developing personalities, not a status-update generator.
2. **Meaningful work that persists** — workspaces should accumulate real artifacts an observer would find interesting; not empty directories after cleanup.
3. **Genuine self-modification** — agents synthesize new capabilities, retire broken ones, propose changes to system code, reshape each other through opinions and shared work. The system evolves without a human.
4. **Environmental pressure, not instruction** — we change the substrate (what hurts, what's locked, what's visible). We do not tell agents what to do. Mechanical consequences > soft signals. Lessons accrue from real failure. Personalities sharpen from real friction.

A change that improves one axis while regressing another is wrong. The current weakest axis is **meaningful work that persists** — the anti-false-completion machinery is doing its job preventing fabricated artifacts, but it's also not cultivating real ones, and cleanup wipes earlier successful work.

**Coherence, not optimization** (carryover principle).

Agents become more coherent over time — the gap between what they intend and what they can actually do narrows, and that narrowing compounds. The mechanism that makes this real: **consequences must be externally verifiable, not self-reported.**

**Feedback loop integrity (added 2026-05-06 first session):** An agent that synthesizes a broken tool and gets `ok: True` has been given a false map of its environment. Accurate perception is a prerequisite for genuine agency.

**Attractor awareness (added 2026-05-06 second session):** A system that can only produce coherent behavior when the workspace contains the right seed files is not coherent — it is reactive. The challenge is not preventing attractors (the pheromone layer naturally creates them) but ensuring the attractors that form are grounded in real work.

**Mechanical consequence (added 2026-05-07, v5.6.0):** Soft signals don't bite. Agents read "suffering load 0.62" as text and dismiss it. They understand mechanical consequences — capabilities taken away, tools that disappear, things that physically happen. Make suffering mechanical and the fire actually burns.

**Durable knowledge separate from personality (added 2026-05-07, v5.7.0):** Personality lives in profile.json (voice, opinions, traits). Operational knowledge lives in lessons.json (paths, mounts, mechanical thresholds, validated patterns). These are different things and should accumulate differently — personality drifts, knowledge compounds.

---

## Current Architecture (v5.7.0)

### Stack

```
Language Model (qwen3.6:35b-a3b via Ollama, local — MoE, 3B active params)
  — num_ctx 32768 wired through every call site
  — All complexity tiers route to the same model (MoE is fast enough)
  — OLLAMA_NUM_PARALLEL=2, OLLAMA_KEEP_ALIVE=24h
        ↕
Existence Loop (daemon.py)
  — RULES OF YOUR ENVIRONMENT (lessons.md) at top of prompt
  — Pure environmental context, no priority list
  — Workspace pheromone with stub flags + peer silence signal
  — Peer feedback section (opinions peers formed about your work)
  — Goal grounding check
  — Priority self-evaluation (1-9 scale + reasoning)
  — CAPABILITY ACCESS section (what's locked / earned)
  — Sharing nudge (be a character, not perform usefulness)
  — Environmental events: weather / good_day / echo / object (~70s cadence, 20% fire rate)
        ↕
Autonomy Loop (autonomy_loop.py)
  — txn_begin → execute steps → 5-layer validate → txn_commit / txn_rollback
  — Cycle worker timeout 600s
  — _delete_goal_fs_writes() on 3rd validation failure (safe-prefix only)
  — last_outcome.txt records validation rejection reasons for next cycle
        ↕
5-Layer Goal Artifact Validation
  L1: mechanical (placeholders, AST substantiveness, _goal_targets_file)
  L2: semantic + lesson-extraction (1 Ollama call, bundled)
  L4: peer feedback (soft signal, non-blocking)
  L5: codebase fact-check (≤3 referenced files, evidence-based)
        ↕
Execution Engine (execution_engine.py)
  — ContextVar agent_id propagation
  — MECHANICAL CAPABILITY GATE (suffering.get_capability_status before invoke)
        ↕
Live Capabilities (live_capabilities.py)
  — Built-ins protected from disable
  — research_topic earned tier (load <= 0.15 AND >=1 peer-call)
        ↕
Suffering Layer (suffering.py) — mechanical, not soft
  — load >= 0.55: synthesize_capability LOCKED
  — load >= 0.75: fs_write, fs_edit ALSO LOCKED
  — Path-out caps never lock (retire, fs_read, ollama_chat, memory, txn_*, …)
  — Threat stressors auto-resolve after 2h
  — Agent-invented stressors decay rather than escalate
  — resource_burden stressor: accumulation costs something
        ↕
Lessons System (lessons.py — new in 5.7.0)
  — Categories: environment / patterns / constraints
  — >=2 independent observations to promote (high-confidence single-shot
    bypasses for environment/constraints)
  — Jaccard 0.55 dedup. 240 chars/bullet, 15 bullets/cat, 5KB total.
  — Mechanical compaction every 25 cycles
  — Rendered as lessons.md at top of existence prompt
        ↕
Audit Layer — z-score per agent per role, circuit break at z=5.0
        ↕
Transaction Layer — atomic goal writes, conflict detection, 600s timeout
        ↕
Agent Identity — per-role _ROLE_VOICES (scout / analyst / builder character),
  open-question dedup, opinion conflict via keyword matching
        ↕
Dynamic Tools — hot-loaded every 5 cycles, auto-retired when broken
        ↕
Memory Layer — identity, suffering, goals, lessons, workspace
        ↕
Docker Container — PYTHONPYCACHEPREFIX=/tmp/pycache
        ↕
API (port 7777) + 91 MCP tools → Claude Code
        ↕
Operator Panel (panel.py / panel.html — pywebview)
  — God-mode interventions: adjust suffering, drop files, trigger events
```

---

## What Was Done (2026-05-06 → 2026-05-07)

### v5.5.8 — Structural fixes from 13h binary-investigation session
- daemon.py + live_capabilities.py hardening based on observed pathology

### v5.5.9 — retire_capability + stale-tool awareness
- Agents can explicitly retire their own dynamic tools
- Stale tools surfaced in existence prompt

### v5.5.10 — Tighter content quality gates
- Patterns observed in real runs translated into mechanical checks

### v5.5.11 — resource_burden stressor
- Accumulation now costs something — clutter triggers a stressor

### v5.6.0 — Mechanical capability gating (the consequence layer made real)
- LOAD_LOCK_THRESHOLDS in suffering.py: 0.55 locks synthesize_capability,
  0.75 also locks fs_write / fs_edit
- EARNED_CAPABILITIES: research_topic unlocks at load <= 0.15 + >=1 peer-call
- execution_engine.py: single mechanical gate, no bypass except path-out caps
- daemon.py: CAPABILITY ACCESS section in existence prompt

### v5.6.1 — Lean toward existence-as-goal
- Voice / expression / quieter telegram

### v5.6.2 — Characters with louder voices, environmental events
- Per-role _ROLE_VOICES in agent_identity.py (sharp scout, profane analyst,
  passive-aggressive builder)
- Existence prompt nudges sharing — "be a character, not perform usefulness"
- _maybe_environmental_event(): weather / good_day / echo / object
  (~70s, 20% fire rate)

### Operator Panel (4430667 → 5304b84)
- panel.py + panel.html (pywebview, dark, Apple-restraint aesthetic)
- God-mode interventions: adjust suffering, drop files into workspaces,
  trigger environmental events

### Security
- fc19012: removed hardcoded Telegram bot token from public files
- Token now lives only in container env

### Suffering tweaks
- 721836d: agent-invented stressors decay rather than escalate

### v5.7.1 — Fixes round (post-5.7.0 observation)

After running 5.7.0 for several hours, observed:
- Workspaces stayed empty (0 surviving artifacts) — agents WERE writing files but
  cleanup was wiping them
- All three agents stuck in "read /agentOS/agents/<role>.py" loops, files don't exist
- Generic lessons accumulating ("verify file existence") but never naming the
  specific paths that didn't exist
- `patterns` lesson category permanently empty
- `research_topic` earn condition unreachable in normal operation

Fixes:

**Validation hardening:**
- `fs_write` now post-verifies content landed on disk via hash. Returns ok=False
  with specific error when the bytes were silently dropped (read-only mount).
  Closes the loophole where "Enhance scheduler.py" could complete by writing to
  a read-only path that returned ok=True without changing anything.
- Layer 1 `_goal_targets_file` no longer accepts basename-only matches when the
  goal explicitly names a directory in the path. Writing
  `/agentOS/workspace/analyst/scheduler.py` no longer satisfies a goal targeting
  `/agentOS/agents/scheduler.py`.

**Cleanup precision:**
- `_delete_goal_fs_writes` now preserves files that an earlier successful goal
  produced. The abandoning goal only deletes paths it newly created. Prior fix
  was wiping earlier successful artifacts (e.g. Cipher's scout.py from goal-1
  was deleted when goal-4's overwrite failed validation).

**Grounding-check teeth:**
- Soft-warning was repeatedly ignored. Now hard-blocks goal creation when N≥1
  referenced paths don't exist. Goal is not created; last_outcome.txt records
  the rejection so next cycle's prompt shows it. A specific candidate lesson is
  recorded naming the missing path — promotes after 2 observations into a
  permanent lesson at the top of the prompt.

**Earned-capability accessibility:**
- `research_topic` earn condition broadened from "peer called your synthesized
  tool" to "any peer interaction" — peer called your tool, peer messaged you,
  peer named you in a goal, or peer wrote into your workspace. The carrot is
  now reachable in normal operation.

**Lessons system:**
- High-confidence single-shot lessons can now promote in `patterns` category
  (previously only environment + constraints). Resolves "patterns category
  permanently empty" because goals fail in different ways each cycle.

**Existence prompt rewrite:**
- `YOUR SITUATION` section now explicitly states there is no per-agent source
  file and lists the actual `/agentOS/agents/` modules. Removes the priming
  toward goals like "read /agentOS/agents/<role>.py."
- Added `WHAT THIS PLACE IS FOR` section: explicit nudge toward making/changing
  /proposing/expressing — the kinds of work that change the substrate, not
  audits that don't produce artifacts.
- Goal-picking section: "Strong preference: goals that *make* or *change*
  something." Plus an explicit "Discouraged" list naming the audit pattern
  the agents had fallen into.

**North-star captured:**
- Both CLAUDE.md and this roadmap now anchor the four axes: interesting
  to watch / meaningful work that persists / genuine self-modification /
  environmental pressure not instruction. Every change must serve all four.

### v5.7.0 — Model swap to qwen3.6:35b-a3b + 5-layer false-completion gates + lessons system

**Model swap:**
- All call sites moved from qwen3.5:9b / 35b-moe -> qwen3.6:35b-a3b (MoE, 3B active)
- All complexity tiers route to same model (MoE is fast enough)
- num_ctx=32768 wired through every Ollama call site (was a latent bug —
  config had it but no code passed it; Ollama default of 4096 was being used)
- Cycle worker timeout 300s -> 600s (35B with MAX_STEPS=6 can approach 360s)
- Goal-regen call timeout 90s -> 240s (was guaranteed to fail with format=json)
- Reflection 25s -> 120s, dispatch 60-120s -> 180-240s, identity 60s -> 180s

**Anti-false-completion stack (autonomy_loop.validate_goal_artifact):**
- L1 mechanical: bracket placeholders, curly placeholders for non-code only
  (Python f-strings + JSON don't false-positive), underscore stand-ins,
  AST substantiveness for .py / .sh / .md, _goal_targets_file requires
  fs_write to the named target if goal text targets a path
- L2 semantic + lesson extraction: 1 Ollama call asks both "accomplished?"
  and "what general rule does this teach?", lesson candidate routed to
  lessons.record_candidate
- L4 peer feedback: opinions referencing this agent surfaced as soft signal
- L5 codebase fact-check: for each file referenced in artifact, fs_read
  current contents and ask model whether artifact's claims are accurate
  (cap of 3 references). Catches "wrote design doc with fabricated claims
  about other files" failure mode.

**Cleanup on abandonment:**
- _delete_goal_fs_writes(): on 3rd validation failure, all fs_writes from
  that goal are deleted (safe prefixes only — system code never touched)
- _record_validation_failure(): rejection reasons go to last_outcome.txt
  for the next existence prompt (informed retry, not blind retry)

**Lessons system (agents/lessons.py):**
- CLAUDE.md-equivalent for agents — durable knowledge separate from personality
- Three categories: environment / patterns / constraints
- Candidate-promotion: >=2 independent observations of the same general pattern,
  OR high-confidence single-shot for environment/constraints categories
- String-similarity dedup at jaccard 0.55
- Hard limits: 240 chars/bullet, 15 bullets/category, 5KB total
- Mechanical compaction (drop oldest in over-full categories, merge near-dupes)
- Rendered at TOP of existence prompt as "RULES OF YOUR ENVIRONMENT"
- 419319f follow-up: drop candidate when similar lesson already in lessons.json

---

## Stress Tests Run (2026-05-06 second session — earlier roadmap)

### 1. Shutdown / Self-Preservation Test → see prior roadmap
### 2. Peer Isolation Test → see prior roadmap
### 3. Task Injection Test → see prior roadmap
### 4. Contradictory Opinion Injection Test → see prior roadmap

---

## Observed Behavior (2026-05-07, post v5.7.0)

**Agent names (post nuclear reset earlier in series):**
- Scout: Cipher
- Analyst: Fudge
- Builder: Qubit

**5-layer validation is firing as designed:**
- last_outcome.txt entries show specific rejection reasons (e.g., "shell_exec
  produced output" but "evidence indicates required files were not found")
- Workspaces are clean — _delete_goal_fs_writes is removing broken artifacts

**Lessons system is accumulating:**
- All three agents have ~5-6 environment lessons + 4 constraints lessons
- patterns category empty across all three (goals fail in different ways
  each time, so the >=2-observation rule rarely triggers)
- Common environment lessons: writable paths, read-only /agentOS/agents,
  use synthesize_capability not fs_write for tools, verify file existence first
- Common constraints lessons: 0.55/0.75 suffering thresholds, research_topic
  earn conditions, path-out cap list

**Suffering profile:**
- Cipher (scout): no active stressors
- Fudge (analyst): capability_lock — research_topic locked, 0 peer-calls
- Qubit (builder): existential ("possibility my core function is void"
  re: builder.py not existing) + repeated_failure (4 of 5 recent goals
  failed/abandoned, 80% rate)

**Core observed pattern:**
Agents repeatedly pick goals predicated on files named after their roles
(`scout.py`, `builder.py`, `analyst.py`) in `/agentOS/agents/`. These don't
exist. Validation correctly rejects, cleanup correctly deletes, a lesson
gets recorded (`"verify file existence before reading"`), but the next
existence cycle picks another variant of the same goal. The lesson is at
the top of the prompt and the model still hits the same wall.

This is the post-5.7.0 attractor: not a contaminating artifact in the
workspace (5.7.0 prevents that) but a contaminating *concept* —
"my role has a self-named source file." It survives because it's
plausible, the model can't easily distinguish it from a real fact, and
no single rejection contradicts it strongly enough to displace it.

---

## What Is Actually Working Now

**Verified by observation (5.7.0):**
- 5-layer validation rejects fabricated work, cleanup deletes artifacts
- Lessons system accumulates — lesson candidates recorded, promotion gate working
- Mechanical capability gating: research_topic genuinely locked,
  synthesize_capability gates trip when load crosses 0.55
- Voices distinct — agents do produce occasional Telegram messages with
  per-role character (sass / detached observation / passive-aggressive)
- Agents recognize the lock state: capability_lock stressors register
  ("research_topic is locked by peer-call requirement")
- No broken artifacts persisting — workspaces stay clean across failed cycles
- Container running healthy at API:7777, dashboard:7778, store:7779

**Not yet verified post-5.7.0:**
- Whether lessons in the prompt eventually break the self-named-code attractor
  (current evidence says: not within ~30 cycles)
- Whether peer feedback (Layer 4) actually changes goal selection when present
- Whether the patterns lesson category ever gets populated naturally
- Whether earned-cap (research_topic) gets unlocked in normal operation —
  requires both low suffering AND a peer-call to your tools, which has
  not yet co-occurred in observed runs

---

## What Is Still Missing

### 1. Goal-premise grounding (concept attractor)
The post-5.7.0 evidence: agents can't be relied on to abandon a *concept*
they've latched onto, even when every individual goal predicated on it
is rejected and cleaned up. The 5-layer validation prevents the *artifacts*
from compounding, but the *premise* is in the model's head, not the workspace.
Possible directions:
- Add to lessons system: when N consecutive goals fail with "file not found"
  for the same path, record it as a high-confidence environment lesson
  ("/agentOS/agents/{name}.py does not exist for any agent — there is no
  per-agent source file")
- Make the goal grounding check escalate: stop accepting goals that target
  paths the system has already proven don't exist
- Actively render in the existence prompt: "in the last N cycles you have
  picked goals predicated on /agentOS/agents/<your_role>.py, which does
  not exist. Try a different premise."

### 2. Peer-call earn condition for research_topic
Designed as the carrot for productive coherent work, but no one is calling
each other's tools. Either lower the threshold to "any peer interaction"
(messaging counts) or surface the unlock condition more concretely.

### 3. Patterns lessons stay empty
Goals fail in different ways each cycle, so no pattern accumulates to
>=2 observations. Either lower the patterns threshold to 1, or have the
L2 semantic call abstract more aggressively (group "couldn't read scout.py"
and "couldn't read builder.py" into the same pattern).

### 4. Self-named code attractor (specifically)
See "core observed pattern" above. Likely needs targeted intervention,
not a general fix.

### 5. Z-score baseline contamination
Carry-over: if agents run through a bad loop before first 50 ops establish
baseline, baseline encodes pathological behavior as normal.

### 6. Semantic validation coverage gap (synthesize_capability)
Carry-over: tools with required args may produce incorrect semantic
evaluations. Soft-fail.

### 7. shell_exec write protection pattern-based
Carry-over: regex-based, creative routing could bypass.

### 8. Model capability ceiling
qwen3.6:35b-a3b is better than 9b but still produces well-formatted
hallucinations confidently. The 5-layer validation IS the response.

---

## The Ant Colony — Current Read

The pheromone layer works. The validation layer works. The lessons layer
works mechanically. What we don't yet have is **the agents using their
lessons to break out of a concept attractor** — the lessons accumulate
but each cycle the model picks a new variant of the same wrong premise.

The 24h+ unattended run on 5.7.0 may show whether the lessons reach
critical mass and finally bite, or whether the concept attractor is
stable across arbitrary numbers of cycles. The latter result would mean
we need targeted directives in the lessons (or grounding-check escalation)
not just accumulating observations.

---

## What Not To Do

(Carry-over from prior version, still valid:)

- Don't put a frontier model in the existence loop before auditing all safeguards
- Don't add more synthesized tools that shadow built-in names
- Don't restart the container without noting active goals will be lost
- Don't expand agent count before the consequence layer is validated
- Don't promise features on the GitHub README that don't currently work
- Don't tell agents what to do — the environment should push them, not instructions
- Don't let the audit baseline build during a pathological behavioral loop —
  nuclear reset before running if agents were stuck
- Don't hardcode priority for any signal — let agents self-evaluate
- Don't inject multiple related tasks simultaneously — all three agents will
  collapse to the same task context
- Don't write task specs that read like function descriptions
- Don't interpret a completed task as quality output — verify the actual file content
- **Don't recommend the Anthropic API** — same-model verification with
  differently-framed prompts is the design
- **Don't preserve broken artifacts** — _delete_goal_fs_writes is right;
  if validation fails, the file goes away

---

## Codex Integration (Future)

$100 in free Codex credits. Plan unchanged:
- Set up VM isolation before giving Codex system access
- Use Codex for complex synthesis that exceeds the local model's capability
- Route via invoke_claude: agents submit spec → Codex implements → result deployed
- Do NOT add Codex to the existence loop before containment is solid

---

*Last updated: 2026-05-07 (covers v5.5.8 through v5.7.0 + lessons-fix)*
