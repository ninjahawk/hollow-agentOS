# Hollow agentOS — Master Document

> **Architecture:** Three-layer system.
> - **Layer 1** — Event kernel: identity, scheduling, memory, messaging, transactions, governance. Largely built.
> - **Layer 2** — Orchestration: autonomous agents running 24/7 whose meta-goal is to build Layer 3. Partially working.
> - **Layer 3** — Human interface: point at any GitHub repo, agents wrap it, surface it as a native app. Does not exist yet.
>
> **Build philosophy:** Sequential. Each phase builds on what the previous phase produced.
> Nothing gets skipped. Layer 2 builds Layer 3. We build Layer 2.
>
> **End state:** A user with no technical knowledge opens Hollow, types what they want,
> and agents handle everything underneath. They never see a terminal. They never touch
> a config file. Every tool on GitHub is one install away.

---

## What has been built

### Layer 1 — Event Kernel (complete ~70%)
- Agent registry with identity, capabilities, spawn depth, budget tracking
- Goal engine — distributed JSONL-based goal storage per agent
- Semantic memory — vector index over source code for natural language search
- Message bus — inter-agent messaging (persistent JSON)
- Consensus/quorum system — proposal → vote → deploy pipeline (exists, not fully wired)
- Heap memory — key-value store per agent
- Project context — shared key-value memory across all agents
- Audit log — append-only record of all agent actions
- Shell execution capability with sandboxing
- Filesystem read/write capabilities
- Ollama integration — local LLM reasoning per agent
- Event log — every agent action emits a structured event
- Transactions — atomic multi-step operations
- Checkpoint/restore — agent state snapshots
- Autonomy loop — 6-second daemon cycle driving all agent execution
- Reasoning layer — maps agent intent to capability selection

### Layer 2 — Orchestration (partial)
- Scout, analyst, builder agents running autonomously
- Agents self-organized a naming system (`names.json`) without instruction
- Analyst autonomously identified deadlocks, missing exception handling, unused imports in codebase
- Multi-agent coordination attempted via shared files (correct instinct, broken implementation)
- `self_modification.py` exists but is not wired into the daemon

### Infrastructure
- Docker-based deployment (`docker-compose.yml`)
- Pre-built image published to GHCR on every push to main (`ghcr.io/ninjahawk/hollow-agentos`)
- One-click Windows installer (`install.bat` + `install.ps1`)
- Live monitor TUI (`monitor.py`) with agent list, activity log, file viewer, goal input
- GitHub Actions CI/CD

---

## Current state (as of 2026-04-03)

**What works:** Agents run autonomously, pursue goals, use LLMs to reason, read/write files, search the codebase semantically, store memory, and emit events. The infrastructure is real.

**What's broken:**
- Agents loop on the same goal indefinitely instead of failing gracefully and moving on
- Agent output gets truncated at ~600 bytes — all meaningful work is cut off before saving
- Scout/analyst/builder are not registered in the agent registry — they're ghost agents
- The proposal → quorum → deploy pipeline exists as three disconnected pieces
- `self_modification.py` is never called by the daemon
- Goals don't survive a container restart

---

## Roadmap

### Phase 0 — Stabilization ← we are here
**Goal:** Make the foundation reliable enough that agents can actually complete goals.
Nothing in Phase 1 onward works if agents loop forever or produce truncated output.

**What gets built:**
- Fix agent loop problem — agents cycling on the same goal every 30s indefinitely
- Fix exception handling and deadlocks in `execution_engine.py` (identified autonomously by analyst)
- Fix artifact validation gate — currently resets to 85% and retries forever instead of failing gracefully
- Verify `fs_write` append mode works end-to-end in practice

**Success criteria:**
- No agent stuck on the same goal for more than 5 minutes
- `execution_engine.py` passes integration tests with no uncaught exceptions
- Agents complete goals, stop, and move to new ones without a restart

**Unlocks:** Everything. Phase 1 is noise until agents reliably finish work.

---

### Phase 1 — Foundation Hardening
**Goal:** Bulletproof Layer 1. Give agents better tools. Cheaper iteration.

**What gets built:**
- **Model switching** — switch between local Ollama models from the TUI and via API without restarting. Local first (cheaper for testing)
- **Fix 600b truncation** at the root — goal completion has a hardcoded size assumption cutting off all meaningful output
- **Register scout/analyst/builder as real agents** — proper IDs, capabilities, and names in the registry
- **Agent messaging protocol** — shared append log that works. Multiple agents writing without overwriting each other

**Success criteria:**
- Can switch between `mistral-nemo:12b` and `qwen3.5:9b` from the TUI live
- Scout/analyst/builder appear in `/agents` API response with proper capabilities
- Agent output files are no longer truncated at 600 bytes
- Multiple agents can write to a shared log and all entries persist

**Unlocks:** Reliable agents + cheaper model iteration = the foundation to wire up self-modification in Phase 2.

---

### Phase 2 — Orchestration Layer Completion (Layer 2)
**Goal:** Wire the self-modification pipeline. Agents can propose, vote on, and deploy
changes to themselves. This is the moment Layer 2 becomes real.

**What gets built:**
- **Wire `self_modification.py` into the daemon** — it exists, it's never called
- **Complete proposal → quorum → deploy pipeline** — three disconnected pieces become one flow
- **External model support** — Claude API and OpenAI API alongside Ollama. Agents route complex reasoning to a smarter model when needed
- **`propose_change` capability** — agents can formally propose a system change, triggering quorum
- **Inject Layer 3 meta-goal** — scout/analyst/builder's explicit standing mission becomes building Layer 3
- **Persistent goals across reboots** — goals survive `docker restart hollow-api`

**Success criteria:**
- An agent identifies a real bug, proposes a fix, it goes through quorum, gets deployed — no human intervention
- At least one file in `/agentOS/agents/` autonomously modified and logged in this document
- Goals survive a container restart
- Can use Claude API or GPT-4 as the reasoning model

**Unlocks:** Agents that can improve themselves can begin building Layer 3. Phase 3 is what they build.

---

### Phase 3 — Layer 3 Bootstrap (Developer)
**Goal:** First version of the GitHub repo install pipeline. Core Layer 3 value
proposition demonstrated for the first time.

**What gets built:**
- **GitHub repo ingestion** — `git clone` → agents analyze structure → generate capability map
- **Agent wrapping** — given a capability map, agents generate a natural language interface for the repo
- **Basic web interface** — replaces TUI as primary interface. Terminal stays for developers but is no longer required
- **First installed app** — one real GitHub tool wrapped and surfaced through agents end to end

**Success criteria:**
- Point at a GitHub repo URL. Agents clone it, analyze it, produce an accurate description of what it does
- A developer unfamiliar with the repo can accomplish a task through the agent interface
- At least one tool is usable without touching a terminal

**Unlocks:** Core Layer 3 demonstrated. Everything after is refinement and scale.

---

### Phase 4 — Layer 3 Complete (Developer)
**Goal:** Multiple repos, custom interfaces per user, actually useful day-to-day.

**What gets built:**
- Multi-repo management — install, remove, update
- Custom interface per repo based on the user's own prompts and specs
- Agent-built interfaces that persist and improve autonomously over time
- Community-shareable agent wrappers for popular tools (early app store concept)

**Success criteria:**
- A developer replaces at least 3 regular tools with Hollow-wrapped versions
- Interfaces improve over time without user intervention
- A second user installs the same tool and gets a different interface based on their own specs

**Unlocks:** Once interfaces are generated and refined autonomously, removing the GitHub knowledge requirement is an interface problem, not an infrastructure problem.

---

### Phase 5 — Non-Technical User
**Goal:** No GitHub knowledge required. Natural language is the entire interface.

**What gets built:**
- **Natural language tool discovery** — agents find the right repo for what you describe
- **Zero-config install** — agents handle all setup, dependencies, configuration
- **Invisible error recovery** — when something breaks, agents fix it. Plain English if they can't
- **Non-technical interface** — no terminal, no config files, no developer concepts ever

**Success criteria:**
- A non-technical user installs and uses a developer tool without knowing what GitHub is
- Zero terminal interactions from install to use
- Errors surface as plain English, never as stack traces

**Unlocks:** Mass market. This is where Hollow becomes something anyone can use.

---

### Phase 6 — Standalone OS
**Goal:** Hollow boots as the primary interface. Agents run underneath everything.
Users never see the infrastructure.

**What gets built:**
- **Bootable Linux image** — minimal distro, Hollow as the display layer. Boots straight into the agent interface
- **Agents as system services** — run at boot as init-level processes, not inside Docker
- **The web interface becomes the desktop** — everything accessible from it
- **Hardware abstraction** — users never need to know Linux, the kernel, or a terminal exists

**Success criteria:**
- Flash to USB, boot a machine, reach the Hollow interface with no prior setup
- Everything from Phase 5 works on fresh hardware out of the box
- A user who has never heard of Linux can use it without knowing they're using Linux

**Unlocks:** The vision. Open source, self-hosted, your own hardware, no technical knowledge required.

---

## Architectural rules

1. Never modify kernel API contracts without logging the change here and flagging for human review
2. When two approaches are equally valid, implement the simpler one and log the alternative here
3. Document every autonomous agent decision here — what was designed vs what the system decided to build itself is the research record
4. When you identify something the system needs that nobody told you to build — build it
5. Every decision should move the system closer to the end state above

---

## Decision log

*Every significant decision made on this project — human and autonomous — recorded here in order.*

### 2026-04-03 — Artifact validation gate
**By:** Human
**Decision:** Added a gate in `autonomy_loop.py` that checks a real artifact was produced before marking a goal complete. If no artifact, progress resets to 85% and agent retries.
**Why:** Agents were completing goals by step count alone with no real output.

### 2026-04-03 — Semantic index source-only rebuild
**By:** Human
**Decision:** Rewrote `index_workspace()` in `semantic.py` to index only `/agentOS/agents/*.py`. Full wipe before rebuild to prevent drift accumulation.
**Why:** Index grew to 41MB indexing 795+ workspace files. Semantic search was returning garbage.

### 2026-04-03 — root_objective persistence
**By:** Human
**Decision:** Added root_objective storage in `project.json` inside `_propose_followon_goal()`.
**Why:** Agents were drifting across follow-on goal chains, losing the original objective.

### 2026-04-03 — fs_write append mode
**By:** Human (in response to agent behavior)
**Decision:** Added `append: bool = False` to `fs_write`. When True, appends instead of overwrites.
**Why:** Agents were overwriting each other's shared log messages. Identified as a gap by agents themselves.
**Alternative considered:** Separate `fs_append` capability. Chose parameter on existing function — simpler.

### 2026-04-03 — Workspace cleanup
**By:** Human
**Decision:** Removed 208 stale 600b placeholder files. Purged 465 terminated agent workspace dirs containing only tombstone.json.
**Why:** Filesystem had accumulated over a GB of meaningless output.

### 2026-04-03 — Pre-built Docker image on GHCR
**By:** Human
**Decision:** GitHub Actions publishes `ghcr.io/ninjahawk/hollow-agentos:latest` on every push to main.
**Why:** Fresh installs required building from source. Pre-built image makes setup instant.

### 2026-04-03 — One-click Windows installer
**By:** Human
**Decision:** `install.bat` + `install.ps1` handles Docker, Ollama, model pulls, config, TUI in one double-click.
**Why:** Setup required technical knowledge of Docker, Ollama, Python, and config files.

### 2026-04-03 — names.json identity system
**By:** Agents (autonomous)
**Decision:** Created a JSON mapping of human-readable names to agent IDs. 19 agents named themselves: Dune, Noodle, Quark, Blaze, Forge, Finch, Drift, Glitch, Birch, Clunk, Tensor, Gizmo, Vertex, Fern, Wobble, Tofu, Stone, Axiom, Wren.
**Why (inferred):** Attempting multi-agent coordination and needed stable identifiers beyond hex IDs.
**Assessment:** Correct call. This behavior — identifying a need and building infrastructure for it without instruction — is the target behavior for Layer 2.

### 2026-04-03 — Codebase structural analysis
**By:** Agents (autonomous, analyst)
**Decision:** Ran static analysis across all `/agentOS/agents/*.py` identifying unused imports, missing exception handling, potential deadlocks, mutable default arguments, missing docstrings, unvalidated inputs.
**Assessment:** Analysis was correct. Output was truncated at 600b before saving — the full reports were lost. This is the 600b truncation issue targeted in Phase 1.

### 2026-04-03 — Disk usage mapping
**By:** Agents (autonomous, analyst)
**Decision:** Identified largest files: semantic index 41MB, daemon.log 35MB, execution chain logs 41MB. Stored in project-context.
**Assessment:** Directly led to the semantic index wipe and workspace cleanup above.

### 2026-04-03 — Multi-agent shared file protocol attempt
**By:** Agents (autonomous, multiple)
**Decision:** Attempted shared communication log at `/agentOS/shared_messages.txt` and several other paths.
**Assessment:** Correct instinct, wrong implementation — used overwrite mode, destroying previous messages. The `fs_write` append mode fix addresses this.

### 2026-04-03 — Roadmap and master document
**By:** Human
**Decision:** Formalized 6-phase roadmap from stabilization through standalone OS. Merged roadmap and decision log into this single document.
**Why:** Project had vision but no written dependency chain. Single document keeps full context in one place.

### 2026-04-03 — Phase 0: Agent loop fix (three-part)
**By:** Human
**Decision:** Three coordinated fixes to stop agents looping on the same goal indefinitely.
1. `autonomy_loop.py` — artifact validation failure now increments `artifact_check_failures` counter. On the 3rd failure, the goal is abandoned instead of reset to 85%.
2. `daemon.py` — when stall detection fires (5 consecutive no-progress cycles), the stuck goal is now abandoned immediately. Previously the agent just cooled off and resumed the same goal.
3. `execution_engine.py` — `_call_with_timeout` now actually enforces the timeout using `ThreadPoolExecutor.submit().result(timeout=...)`. Previously it called the function directly with no timeout, causing daemon threads to hang if Ollama stopped responding.
**Also fixed:** Both `_log_execution` and `_record_step` rewrote entire JSONL files on every step. Changed to `open(..., "a")` append mode — eliminates O(n) write cost and removes a lock contention source.
**Why:** Scout had been looping on `goal-5b9884a19f5e` for 900+ daemon cycles. After these fixes: stall detected → goal abandoned → new goal assigned → progress resumed immediately (0.30 → 0.60 in two cycles).

### 2026-04-03 — Phase 1+2 batch: Foundation hardening and orchestration layer
**By:** Human (automated run)
**Decision:** Completed Phase 1 and Phase 2 roadmap items in a single session.

**Phase 1 — Foundation Hardening:**
1. **Output truncation fixed** (`autonomy_loop.py`) — `_result_to_text` raised from 600→16,000 chars, list items 200→4,000, JSON fallback 400→8,000. Context injection limit 300→4,000. Agents can now write meaningful output.
2. **Core agents registered** (`registry.py`) — `_ensure_core_agents()` called at startup, creates scout/analyst/builder with fixed IDs, deterministic tokens, and full capability sets if not already registered.
3. **Model switching live** (`api/server.py`, `monitor.py`) — `PATCH /ollama/models` updates `DEFAULT_MODEL` and `MODEL_ROUTES` in-memory without restart. TUI: press `m` to switch models.
4. **Shared agent broadcast log** (`agents/shared_log.py`) — new JSONL append-only broadcast channel. `POST /shared-log` and `GET /shared-log` endpoints. `shared_log_write` and `shared_log_read` capabilities added. `bus.py` `_save()` made atomic (temp→rename).

**Phase 2 — Orchestration Layer Completion:**
5. **Persistent goals fixed** (`persistent_goal.py`) — `list_active()` now skips corrupt lines instead of returning empty. `create()` uses proper append mode and works without embeddings. All mutation methods (complete, abandon, update_progress, etc.) use atomic writes (temp→rename).
6. **Self-modification wired** (`daemon.py`, `autonomy_loop.py`) — `SelfModificationCycle` instantiated in `_build_stack()`. `AutonomyLoop` triggers `process_gap()` in background when a capability is blacklisted (3 consecutive failures). Fixed `_propose_to_quorum()` API parameter mismatch.
7. **Proposal→quorum→deploy pipeline completed** — `flush_approved_proposals()` added to `SelfModificationCycle`; daemon calls it after each `vote_on_pending()` cycle and deploys approved capabilities. `propose_change` capability added so agents can formally submit proposals from goal execution.
8. **Layer 3 meta-goals injected** (`daemon.py`) — scout, analyst, and builder each get a specific standing mission targeting Layer 3 on daemon startup. Scout: map GitHub ingestion requirements. Analyst: identify bugs blocking Layer 3. Builder: implement git_clone capability and propose it via quorum.

**Why:** Phase 0 fixes made the foundation stable. These changes close the gap between "agents run" and "agents improve themselves and build toward Layer 3."
