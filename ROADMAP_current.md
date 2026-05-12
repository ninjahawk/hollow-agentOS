# Hollow AgentOS, Current State & Roadmap
*Revised 2026-05-12 (v5.7.32 substrate stability pass). Previous: 2026-05-07.*

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

### v5.7.32 (2026-05-12), substrate stability pass

Goal: unblock the loop. The fixes here all address structural reasons agents could not complete work or recover from failure.

- **Completion math fix.** HVC progress_delta 0.15 -> 0.20. With MAX_STEPS=6 the prior max-reachable progress was 6 * 0.15 = 0.90, which is below the `progress >= 1.0` gate that fires `_txn_commit_goal`. Every staged fs_write fell through to the end-of-cycle rollback. This was the root cause of the project-long empty workspace.
- **Mechanical validator artifact ordering.** Prefer real-artifact steps (fs_write / memory_set / propose_change / synthesize_capability) over evidence steps (shell_exec, ollama_chat). Without this, the most-recent successful step won, so semantic verification often compared the goal against a directory listing instead of the actual produced artifact.
- **Plan-vs-action gate.** Goals using a modify-existing verb (modify / enhance / fix / refactor / etc.) where the only completed artifact is a workspace `.md` / `.txt` now reject with a pointer toward propose_change / synthesize_capability / invoke_claude. Stops "wrote a plan, called it done."
- **Planner candidate filter.** `invoke_claude`, `propose_change`, `synthesize_capability`, `check_claude_status`, `vote_on_proposal` force-included in `_ALWAYS_INCLUDE`. They almost never won semantic search against goal text and were invisible to the planner: root cause of the empty `claude_requests.jsonl` queue across the project lifetime.
- **Fallback goal rotation.** `_construct_fallback_goal` now skips stressor types used in the recent 10 goals, forcing rotation to open-question / peer-file / last-outcome / journal. Without rotation agents picked "Address the repeated_failure stressor by changing its condition" 23-25 times in a row.
- **Same-text loop guard.** `_assign_idle_goal` rejects model-proposed goal text duplicating a goal abandoned 3+ times in the last 20 cycles, forces fallback. Catches the case where the model parrots back stressor-description text from the existence prompt.
- **Validation cap at 1 attempt per cycle.** After validation failure, break out of the pursue_goal while loop. Next cycle picks up at progress=0.85 and retries. Spreads validation cost across cycles. Stops the case where two validation chains in one cycle blow past the 1500s cycle timeout.
- **Bounded Ollama calls in validation.** L2 semantic + L5 fact-check wrapped in `concurrent.futures.ThreadPoolExecutor` with hard wall-clock deadlines. Observed: on Windows hosts, Ollama can evict the model under VRAM pressure despite `keep_alive=-1`, and httpx requests then hang below the exception layer for 14-40 minutes. Wall-clock-bounded execution guarantees return.
- **Watchdog plumbing fixed.** entrypoint.sh now backgrounds both daemon and uvicorn, `wait -n` on either; whichever dies brings the container down and Docker `restart: unless-stopped` recovers. Previously uvicorn was PID 1 and `os.kill(1)` from the watchdog either failed silently or only killed uvicorn, leaving the daemon dead in a "healthy" container.
- **Cycle / watchdog timeouts retuned.** Cycle 600s -> 1500s; watchdog 900s -> 2400s. Real qwen3.6 cycles run 500-800s; the prior 600s wall was timing out legitimate work and leaking workers via `pool.shutdown(wait=False)`, which compounded into permanent hung-agent state.
- **Placeholder gate widened.** Regex now catches `[From LLM Output]`, `[Hypothesis]`, `[Reasoning]`, `[expected ...]`, `[example ...]`, `[to do]`, `[to be filled]`, generic `[from LLM]`. Stops stub artifacts from passing substance.
- **Txn double-commit fixed.** Clear `_txn_id` after first commit so re-entry doesn't commit on a dead id (returning 400) and fallback rollback no-ops cleanly.
- **invoke_claude empty agent_id rejection.** Reject at source. Was leaking one garbage request per session.
- **Per-step diagnostic logging.** `PLAN_START`/`PLAN_END`, `STEP_START`/`STEP_END`, `VALIDATE_START`/`VALIDATE_END` with elapsed time. The next hang is self-explanatory instead of opaque.
- **Dashboard service removed.** It never worked end-to-end. The operator panel (`panel.py` / `panel.bat`) is the canonical UI. Static assets, compose service, CI verification, config block all dropped.
- **Log rotation.** `thoughts.log` rotates at 50MB (was unbounded; observed at 37MB after weeks of testing).
- **Setup wizard error surfacing.** Compose-failure errors no longer truncated. Mounts-denied and nvidia-toolkit-missing paths print platform-specific fixes inline. Issues #16/#17/#18/#22 all addressed.
- **Wiki shipped.** `https://ninjahawk.github.io/hollow-wiki/` on GH Pages with `just-the-docs` theme. Setup, troubleshooting, FAQ, substrate concepts, what-this-is. Surfaced at multiple touchpoints during install.

**First persisting agent artifacts observed under these fixes:** `workspace/builder/null_handler_because_apparently_no_one_else_will.py` (54 lines of real Python with voice in the filename), `workspace/analyst/peer_test_payload.json`, `workspace/analyst/peer_test_plan.md`. The loop closes: goals can complete, validation runs, artifacts persist, agents pick new work. 9 goal completions across agents during the stability pass.

---

## Stress Tests Run (2026-05-06 second session, earlier roadmap)

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

**Verified by observation (5.7.32, 2026-05-12):**
- The loop closes. Goals can reach `progress >= 1.0`, trigger commit, run through 5-layer validation, and complete. 9 goal completions across the three agents during the stability pass after the math fix landed.
- Workspace artifacts persist. `null_handler_because_apparently_no_one_else_will.py` (real working Python, character in the filename) survives across cycles and goal abandons (substantive-file-protected cleanup works as designed).
- Validation runs cleanly in 9-22 seconds per attempt. The chronic 14-40 minute hangs are gone (bounded Ollama calls with wall-clock deadlines).
- 5-layer validation rejects fabricated work, broken-artifact cleanup on abandon, lessons get extracted and promoted, mechanical capability gating fires at 0.55 and 0.75, watchdog/entrypoint recovery actually restarts the container under failure.
- Container running healthy at API:7777, store internal. Dashboard removed (never worked).
- Wiki live and serving from GH Pages with full nav, search, and edit-on-GitHub links.
- Setup wizard end-to-end error surfacing covers all the failure modes from open issues #16/#17/#18.

**Carryover from 5.7.0 (still working):**
- Lessons accumulate, promotion gate working
- research_topic genuinely locked behind earn condition
- Voices distinct per role
- Agents recognize lock state (capability_lock stressors register)

**Not yet verified:**
- Whether validation completions actually compound across hours into measurable behavior change (we have 9 completions in a few hours; need 24h+ run to see patterns)
- Whether `synthesize_capability` and `propose_change` get used (force-included in planner now, but observed usage is still rare)
- Whether `invoke_claude` queue gets real productive requests now that the planner sees the capability (one fired but it was garbage; rejection landed cleanly)
- Patterns lesson category remains empty
- research_topic earn condition still untriggered in normal operation

---

## What Is Still Missing

### 0. The dumb-behavior problem (new framing, 2026-05-12)

After the stability pass, the substrate runs without crashing and goals can complete. What's left over is that completed and abandoned goals both look pointless. Scout writes a baseline-marker, then verifies the baseline-marker, then re-verifies it. Analyst writes a peer-call test plan, then a payload, then an investigation result. Builder writes `null_handler.py`, loses it to validation, then investigates the survival of `null_handler.py`. The agents are working, but on increasingly internal recursive material.

Honest framing: this is not a model competence problem (qwen3.6:35b-a3b benchmarks comparably to current Sonnet). It's a substrate incentive problem. The existence prompt shows agents only their own state, peers, workspace, lessons, last outcome. Everything they see is internal. So everything they pick to do is internal. The validation reward function rewards *completion of any goal*, not *meaningful divergence from prior goals*. Lesson dedup catches repetitive lessons but no equivalent dedups goals at the *premise* level.

Candidate substrate changes (5.8.x roadmap, not 5.7.x patches):

- **Novelty pressure.** Validation reward decays for artifacts too similar to ones already in the workspace by the same agent. Fifth UUID-marker scores lower than first. Forces premise diversification.
- **World signal in the existence prompt.** Surface ambient context from outside the agent's own world: latest few commits, contents of `ROADMAP_current.md`, open issues, recent host messages. Not as instructions. As "the world contains these," same way peer activity currently surfaces.
- **Peer-issued tasks.** A capability that lets one agent set a concrete task for another, with a real validation outcome (peer accepts / rejects the result). Adds unpredictable external pressure between agents. The current peer-message capability is one-shot text; this would be a multi-step interaction with consequences.
- **Curiosity stressor.** A stressor that fires when an agent has not produced work that touches a file or concept outside their recent N goals. Forces the agent to look at parts of the world they have not yet engaged.

None of these are ship-blockers for 5.7.32. They are the next design wave.

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

## Pre-Release Testing (2026-05-12)

Before tagging 5.7.32 as a release, the following needs to be checked. None of this requires writing new code, only verifying that existing code works in clean conditions and on smaller models.

### Tier 1, must-do

1. **Fresh clean install** on a Windows machine: `git clone` to an empty directory, run `install.bat`, watch the wizard complete, verify the panel opens, verify agents start producing log output within 10 minutes. Catches assumptions about pre-existing state.
2. **Run on `qwen3.5:9b`** for at least one hour. Stop the system, edit `config.json` default_model, restart. Watch for: JSON parse failures, semantic-validation oddities, cycle-time changes. The 5.7.32 changes are calibrated against qwen3.6:35b-a3b; smaller models may surface assumption breaks.
3. **Verify the GitHub Pages wiki on mobile and desktop**, all links resolve, no 404s, search works.

### Tier 2, should-do

4. **Run on `llama3.2:3b`** (CPU-only) for at least one hour. Lower expectations on goal quality. Watch for: hangs, context overflow (context window is much smaller than the existence prompt expects), wizard model-selection logic correctness.
5. **Run integration tests locally** with `pytest tests/integration/`. Catches regressions in the autonomy loop, API, capability graph that CI may not exercise in the live container path.
6. **Operator panel end-to-end**: start, stop, suspend an agent, drop a file into a workspace, trigger an environmental event, send a host message, nuke. All buttons.

### Tier 3, nice-to-have

7. **24h+ unattended run** on the recommended config. Catches slow-burn issues (memory growth, log rotation correctness, audit log size, identity drift, lesson promotion stability).
8. **Small-model-specific warnings in the wizard**: when the user selects llama3.2:3b or gemma3:4b, surface a note that parse failures and shallow goals are expected. Optional but a kindness.

---

## What Not To Do

(Carry-over from prior version, still valid:)

- Don't put a frontier model in the existence loop before auditing all safeguards
- Don't add more synthesized tools that shadow built-in names
- Don't restart the container without noting active goals will be lost
- Don't expand agent count before the consequence layer is validated
- Don't promise features on the GitHub README that don't currently work
- Don't tell agents what to do, the environment should push them, not instructions
- Don't oversell the system in user-facing docs (README, wiki, blog, release notes). The work is impressive on its merits. Match Karpathy's autoresearch tone: state plainly what's there, what works, what's still being built.
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
