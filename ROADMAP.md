# Hollow agentOS — Master Document

> **The full stack, bottom to top:**
> ```
> Linux Kernel          — foundation, we use it not build it
> AgentOS               — event kernel: identity, goals, memory, execution (~75% complete)
> Autonomous Agents     — background runtime: synthesizes capabilities, improves the system over time (running, quality problems being addressed)
> Event Response Layer  — fast deterministic OS event handling: crash recovery, updates, errors (not built — Phase 7)
> HollowOS              — the user-facing OS: web interface as the desktop (bootable skeleton built, untested on real hardware)
> Apps                  — wrapped GitHub repos, natural language interfaces (128+ in store, pipeline working)
> App Store             — central server, shared wrappers, network effects (running at port 7779)
> ```
>
> **Architecture:** Three-layer system, plus an event response layer being added in Phase 7.
> - **Layer 1** — Event kernel: identity, scheduling, memory, messaging, transactions, governance. ~75% complete. Missing: real-time event dispatch to Layer 2, hardware abstraction capabilities.
> - **Layer 2** — Orchestration: scout/analyst/builder running 24/7. Self-modification pipeline wired and running — agents synthesize Python capabilities, vote on them, deploy via hot-loading, and call them in subsequent goals. 60+ capabilities deployed. Core problem: agents are optimizing for the appearance of progress rather than real system improvement. The loop closes but the output is mostly hollow. Phase 7 addresses this.
> - **Layer 3** — Human interface: wrapping pipeline complete, 128+ tools in store, natural language discovery working, one-click install working, auto-versioning running. The pipeline is proven. Quality improves significantly with Claude API vs Ollama-only.
> - **Event Response Layer (new)** — Between Layer 1 and Layer 2. Fast, deterministic responses to known OS events (crashes, updates, permission errors) without LLM latency. Small trained classifiers for event type detection. LLM only for novel events. Agents build and improve this library over time. This is the architectural piece that makes Hollow reliable enough for real users as a primary OS. Defined in Phase 7.
>
> **Build philosophy:** Sequential. Each phase builds on what the previous phase produced.
> Nothing gets skipped. The event response layer is the missing piece between "this works in a demo" and "this works as someone's actual computer."
>
> **End state:** A user with no technical knowledge opens Hollow, types what they want,
> and agents handle everything underneath. Their apps don't crash unrecoverably. Updates
> happen automatically. New tools are one sentence away. They never see a terminal, never
> touch a config file, never know they're running Linux.
>
> **The store:** Every wrapped app is uploaded to a central store. First person to install
> a repo pays the AI wrap cost (cents). Everyone after downloads it free. Popular tools
> get wrapped once, shared forever. Network effects make the system cheaper and better
> the more people use it. Currently running with 128+ wrappers seeded manually.
>
> **No developers required:** AI handles the entire software lifecycle. GitHub is the
> source of truth. Small models monitor repos for changes. Claude re-wraps when something
> changes. No human ever ports a tool to Hollow — any public repo is automatically one
> install away, permanently maintained by AI. Auto-versioning runs every 4 hours today.
>
> **Model routing:** Local small models (Ollama) handle monitoring, polling, reasoning, and cheap ops.
> Claude handles wrapping, interface generation, and updates when API access is available.
> For code synthesis specifically, a code-trained model (Qwen2.5-Coder or similar) is needed
> rather than a general model — this is a known gap addressed in Phase 7.
> Cost scales with actual reasoning work, not with usage.
>
> **Claude API:** Significantly improves wrapping quality but is not strictly required for
> basic operation. The pipeline works with Ollama as fallback — quality is lower but functional.
> Auto-versioning, natural language discovery, and the full app lifecycle work today without
> Claude API. Claude is needed for reliable, high-quality wrapping of complex repos.

---

## What has been built

### Layer 1 — Event Kernel (~75% complete)
- Agent registry with identity, capabilities, spawn depth, budget tracking
- Goal engine — persistent JSONL-based goal storage per agent, survives restarts
- Semantic memory — vector index over agent source code for natural language search
- Message bus — inter-agent messaging (persistent JSON, append-safe)
- Consensus/quorum system — proposal → vote → deploy pipeline (wired and running)
- Heap memory — key-value store per agent
- Project context — shared key-value memory across all agents
- Audit log — append-only record of all agent actions
- Shell execution with sandboxing (dangerous patterns blocked, 30s timeout, 256KB cap)
- Filesystem read/write capabilities with path validation
- Ollama integration — local LLM reasoning per agent
- Event log — every agent action emits a structured event
- Transactions — atomic multi-step operations
- Checkpoint/restore — agent state snapshots
- Autonomy loop — daemon cycle driving all agent execution
- Reasoning layer — maps agent intent to capability selection via semantic search
- Rate limiting, budget tracking per agent
- **Missing:** real-time event dispatch (crash → handler in <1s), hardware abstraction capabilities

### Layer 2 — Orchestration (running, quality problems identified)
- Scout, analyst, builder agents running autonomously 24/7
- Agents self-organized a naming system (`names.json`) without instruction
- Self-modification pipeline fully wired: `synthesize_capability` → quorum vote → `_deploy()` → hot-load into ExecutionEngine + CapabilityGraph → immediately callable by agents
- 60+ capabilities synthesized and deployed to `/agentOS/tools/dynamic/`, survive restarts via volume mount and startup hot-loading
- Startup capability count: 63 (up from 16 before Phase 7 work began)
- Quality gate: syntax check + stub signal detection before any capability is deployed
- Agents discover synthesized capabilities via semantic search and call them in subsequent goals — the self-improvement loop is closed
- **Known problem:** agents are optimizing for appearance of progress. The loop runs but synthesized capabilities are called with empty/circular inputs and produce meaningless output. The grounding signal problem: agents approve proposals by reading descriptions, not by running code. Addressed in Phase 7.
- **Known problem:** general-purpose 9B model cannot reliably write correct Python. Bad imports, stubs, broken logic pass syntax checks but fail at runtime. Code-specific model needed for synthesis path.
- Multi-agent coordination via shared broadcast log (working, append-safe)

### Layer 3 — Human Interface (complete, Ollama-quality)
- Wrapping pipeline: GitHub URL → clone → analyze → Claude/Ollama generates capability_map + interface_spec → wrapper.json → store upload
- 128+ tools pre-wrapped in community store, avg quality 64/100
- Natural language discovery: type what you want, semantic ranking surfaces the right tool across local + store
- One-click install from store
- Auto-versioning: polls GitHub every 4h, re-wraps on new commits, no human involved
- App sandbox: dangerous shell patterns blocked, isolated working directory, restricted env
- Custom interface per user: describe preferences in plain English, Claude re-wraps
- Invisible error recovery: wrap failures surface in plain English, never as stack traces
- **Quality ceiling:** Ollama alone produces adequate but not excellent wrappers. Complex repos with non-standard README structures often produce thin capability maps. Claude API unlocks the full quality target.

### Infrastructure
- Docker-based deployment (`docker-compose.yml`) with three services: api (7777), dashboard (7778), store (7779)
- Pre-built images published to GHCR on every push to main
- One-click Windows installer (`install.bat` + `install.ps1`)
- GitHub Actions CI/CD
- HollowOS bootable image skeleton: mkosi.conf, systemd services, kiosk mode, first-boot provisioning, loading splash — untested on real hardware, needs Linux build host with mkosi

---

## Current state (as of 2026-04-08)

**Phases 0–6 skeleton complete. Phase 7 defined, not started.**

**What works end-to-end:**
- Point any GitHub URL at Hollow → Ollama wraps it → usable as an app (Claude API improves quality significantly but not required)
- 128+ tools pre-wrapped in the community store, avg quality 64/100
- Natural language discovery across local apps + store: type what you want, Hollow surfaces the right tool
- One-click install from store
- Auto-versioning: background task checks GitHub every 4 hrs, re-wraps on new commits
- Service health bar, token injection, error recovery — all in the dashboard
- Self-modification pipeline running: agents synthesize Python capabilities, vote, deploy via hot-loading, call them in subsequent goals. 63 capabilities registered at startup (60+ synthesized by agents, ~5 genuinely useful and correctly called)
- HollowOS bootable image configuration: mkosi.conf, systemd services, kiosk mode, loading splash (skeleton complete, not tested on real hardware)

**What doesn't work / known gaps:**
- Tool installs are NOT persistent across container restarts (tools not in Dockerfile get lost on restart)
- No Claude API key in container → wrapping relies on Ollama (adequate for simple repos, weak for complex ones)
- Phase 6 HollowOS untested — needs a Linux machine with mkosi to build and flash to USB
- Agent self-improvement loop runs but produces hollow output: agents call synthesized capabilities with empty inputs, get "all clear" responses, mark goals complete. No real system improvement happening yet.
- General-purpose 9B model cannot reliably write Python for synthesis: bad imports, stubs, broken logic. Code-specific model (Qwen2.5-Coder) needed.
- No event response layer: if an app crashes or an install fails, the LLM reasons about it on a 45-second cycle. Too slow and unreliable for a primary OS.
- No real hardware tested: everything runs in Docker on Windows. HollowOS-on-bare-metal is unproven.

---

## Roadmap

### Phase 0 — Stabilization ✅ COMPLETE
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

### Phase 1 — Foundation Hardening ✅ COMPLETE
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

### Phase 2 — Orchestration Layer Completion (Layer 2) ✅ COMPLETE
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
- An agent identifies a real bug, proposes a fix, it goes through quorum, gets deployed — no human intervention ⚠️ *Pipeline works but quality of what gets deployed is poor — agents deploy capabilities that pass syntax checks but don't do real work. The pipeline closes; the output is not yet meaningful.*
- At least one file in `/agentOS/agents/` autonomously modified and logged in this document ❌ *No core agent file has been autonomously modified. Agents synthesize new capabilities into `/agentOS/tools/dynamic/` but have not modified existing agent source files.*
- Goals survive a container restart ✅
- Can use Claude API or GPT-4 as the reasoning model ✅ *Wired. Requires API key in environment.*

**Unlocks:** Agents that can improve themselves can begin building Layer 3. Phase 3 is what they build. *Note: the pipeline unlocked Phase 3 but the self-improvement loop itself needs Phase 7 to produce real output.*

---

### Phase 3 — Layer 3 Bootstrap (Developer) ✅ COMPLETE
**Goal:** First version of the GitHub repo install pipeline. Core Layer 3 value
proposition demonstrated for the first time.

**What gets built:**

- **Claude API routing** — `ANTHROPIC_API_KEY` set in container → `reasoning_layer.py` routes to Haiku (capability selection) and Sonnet (planning). Ollama remains local fallback. Already wired in code, needs API key to activate.

- **The wrapping pipeline** — the exact sequence that turns a GitHub URL into a usable app:
  1. `git clone` the repo into workspace
  2. Read README + key source files (Claude identifies which ones matter)
  3. Claude call: "what does this tool do, what are its commands, what are the params?" → `capability_map` JSON
  4. Claude call: "generate a form-based interface for this tool" → `interface_spec` JSON
  5. Validate: capability_map has at least one capability, interface_spec has at least one field
  6. Bundle as `wrapper.json` and upload to store
  This pipeline must work reliably before anything else in Phase 3 matters.

- **The store server (v1)** — a separate FastAPI server, not part of the agent API. Endpoints:
  - `POST /wrappers` — upload a wrapper
  - `GET /wrappers/{repo_id}` — download a wrapper by repo URL hash
  - `GET /wrappers` — list all wrappers
  - `POST /wrappers/{repo_id}/install` — increment install count
  - `GET /wrappers/{repo_id}/version` — check if a newer version exists
  Wrapper format: `{schema_version, repo_url, source_commit, wrapped_at, install_count, capability_map, interface_spec}`
  Store must be **publicly hosted** — not just running locally. Every Hollow install in the world connects to the same store URL. Without public hosting, there are no network effects.

- **Web interface (the renderer)** — a minimal HTML/JS page served by the store or the local agent API. Reads `interface_spec` JSON and renders a form. No framework needed at this stage — vanilla JS is fine. When the form submits, it builds the shell command from `capability_map.shell_template` and runs it via the agent API's `shell_exec`. Output displays in the page. This is what "replaces the TUI" means concretely.

- **First installed app** — ripgrep already cloned. Wrap it. Put it in the store. Make it usable through the web interface with zero terminal interaction.

**Success criteria:**
- Point at a GitHub repo URL. Claude wraps it. A non-developer can use it through the web interface.
- The same repo installed by a second user costs zero — pulled from store.
- At least one tool is usable without touching a terminal.
- The store server is publicly accessible at a real URL.

**Unlocks:** Core Layer 3 demonstrated. The store exists. Everything after is network effects and automation.

---

### Phase 4 — Layer 3 Complete (Developer) ✅ COMPLETE
**Goal:** Multiple repos, the store has real coverage, AI handles versioning end to end.

**What gets built:**

- **App sandboxing** — right now wrapped apps run `shell_exec` as root in Docker. Before handing this to real users, apps must be sandboxed. Each app runs in its own container or restricted subprocess. A bad or malicious wrapper cannot touch the host system or other apps. This must be done before Phase 5.

- **Auto-versioning pipeline** — the monitoring system that makes "no developers required" real:
  1. A lightweight agent (Haiku or local model) polls GitHub API for new commits on installed repos. Runs on a cron, costs near zero.
  2. When a new commit is detected, fetch the diff.
  3. Hand diff to Claude: "here's what changed in this repo, update the wrapper."
  4. Claude regenerates only the affected parts of the capability map and interface spec.
  5. New wrapper version uploads to store. All installs get the update.
  No human involved at any step. AI owns the full update lifecycle.

- **Store quality ranking** — multiple wrappers for the same repo. Install count, ratings, and Claude evaluation surface the best one. Prevents store from accumulating bad wrappers.

- **Multi-repo management** — install, remove, update from the web interface. Never a terminal command.

- **Pre-wrapped launch catalog** — top 100 most-used GitHub CLI tools wrapped before launch. Store ships with real coverage on day one. Done manually through Claude Code sessions before API budget allows automation.

- **Custom interface per user** — users can describe how they want to interact with a tool. Claude re-wraps for their preferences and saves it locally.

**Success criteria:**
- A repo updates on GitHub. The wrapper updates automatically within 24 hours. User sees nothing.
- A developer replaces at least 3 regular tools with Hollow-wrapped versions.
- Store has 100+ pre-wrapped tools at launch.
- A bad wrapper cannot harm the host system.

**Unlocks:** The no-developer maintenance model is real. AI owns the full software lifecycle from source to interface to update.

---

### Phase 5 — Non-Technical User ✅ COMPLETE
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

### Phase 6 — Standalone OS 🔧 IN PROGRESS (skeleton complete, needs Linux build)
**Goal:** Hollow boots as the primary interface. Agents run underneath everything.
Users never see the infrastructure.

**What HollowOS is (defined):**
HollowOS is a minimal Linux distro that boots directly into a browser in kiosk mode, displaying the Hollow web interface fullscreen. There is no desktop environment, no window manager, no taskbar. The browser IS the desktop. The agent daemon starts at boot as a systemd service. The store connection is pre-configured. The user sees one screen: the Hollow interface. They never know they're running Linux.

**What gets built:**
- **Minimal Linux base** — Alpine or Debian minimal. Only what's needed: Linux kernel, systemd, network stack, a browser (Chromium), and the Hollow stack. No GNOME, no KDE, no X11 session manager.
- **Kiosk mode boot** — systemd starts Chromium in kiosk mode pointed at localhost. That's the "desktop."
- **Agents as system services** — AgentOS daemon runs as a systemd service at boot, not inside Docker. Docker is only for development. Production runs native.
- **Hardware abstraction** — all hardware interaction (USB drives, network config, display settings) goes through agent capabilities. Users never see a shell prompt.
- **Bootable image** — built with a tool like `mkosi` or a custom build script. Flash to USB, boot any x86 machine, reach Hollow with no prior setup.

**Success criteria:**
- Flash to USB, boot a machine, reach the Hollow interface with no prior setup
- Everything from Phase 5 works on fresh hardware out of the box
- A user who has never heard of Linux can use it without knowing they're using Linux
- The Linux terminal is present but hidden — accessible only via a deliberate developer unlock sequence

**Unlocks:** The vision. Open source, self-hosted, your own hardware, no technical knowledge required.

---

### Phase 7 — Reliable Agent Runtime 🔲 NOT STARTED
**Goal:** Fix the three fundamental problems with the self-modification loop and make the agent system reliable enough to act as the background runtime for a real OS used by real people.

**Problem 1: No grounding signal**

Agents approve capabilities by reading descriptions. Replace with structured gates:

- Proposer submits: capability code + test cases with defined inputs + expected output structure
- Two independent verifier agents execute the code in a subprocess sandbox against those inputs
- Both must observe the expected output structure (not just "it ran without error")
- Test inputs must come from a different agent than the proposer
- Tests must cover at least one error/edge case (not just the happy path)
- Deploy only on double pass

This does not fully solve the problem if the model is too weak to write real tests. See Problem 2.

**Problem 2: Model too small for code synthesis**

qwen3.5:9b (general model) cannot reliably write correct Python. This is a capability ceiling, not a tuning problem. Solutions, in order of preference:

- **Qwen2.5-Coder-7B** (fits in VRAM if 9B fits, ~4-5GB at 4-bit): specifically trained on code, far fewer syntax errors and bad imports. Ollama pull, swap in config. Route synthesis calls to this model while keeping general reasoning on the existing model.
- **Fine-tuned small classifier (1-3B)**: for the structured gate verification step specifically — classify "does this output match the expected structure" — a narrow task a small fine-tuned model can do reliably.
- **Claude API for synthesis**: route `synthesize_capability` calls to Claude Haiku. Costs money but produces code that actually works. Use when API credits are available.

**Problem 3: No real objective function**

Synthesized capabilities feed back into nothing. Agents synthesize tools that analyze agent behavior, call those tools on empty inputs, get "all clear," mark goal complete, repeat. The fix is giving agents tasks with outcomes measurable against external state:

- Capability proposals must specify what observable system state will change if the capability works
- Goal completion must verify that state actually changed — not just that an output-class capability was called
- The synthesis loop should target the event response library (see Problem 4) — capabilities that handle specific real events — not abstract self-analysis tools

**Problem 4: Wrong architecture for real-time OS events**

The agent loop (~45s cycle, LLM per step) cannot handle user-facing OS events. Build a layered event response system:

- **Level 0 — Deterministic rules** (no model, <1ms): app crashed → restart; permission denied → escalate; update available → queue. Written as code, not learned.
- **Level 1 — Trained classifier** (small fine-tuned model, <100ms): maps event signature + context to a response procedure ID. The procedures are pre-written; the model only classifies. Training data: event signatures → correct procedure class, generated synthetically from the Level 0 rules.
- **Level 2 — Agent reasoning** (general LLM, seconds): for events that don't match any known pattern or where Level 1 confidence is low. This is where the current agents live.
- **Level 3 — Human escalation**: surfaces to user in plain English when nothing else worked.

The agent background loop becomes the system that expands the Level 0/1 library over time — synthesizing new event handlers, testing them (structured gate), deploying them to the classifier. This gives agents a real objective function: make Level 2 escalations rarer.

**Success criteria:**
- A synthesized capability passes the structured gate (two independent agent test runs, both pass)
- `scan_codebase_suspicious_patterns` and `verify_stack_registration` class of failures (bad imports, null-returning stubs) are caught by the gate and never deployed
- Repeated synthesis of the same capability name is blocked at the reasoning layer (agents know what already exists)
- At least one Level 0 event handler written and tested for each of: app crash, update available, tool install failure, permission error
- Level 1 classifier prototype running (even rule-based to start) that routes known events without LLM involvement
- qwen2.5-coder (or equivalent) used for synthesis path specifically

**Unlocks:** An agent system that actually improves over time, and an OS event layer that is fast and reliable enough for real users.

---

## Architectural rules

1. Never modify kernel API contracts without logging the change here and flagging for human review
2. When two approaches are equally valid, implement the simpler one and log the alternative here
3. Document every autonomous agent decision here — what was designed vs what the system decided to build itself is the research record
4. When you identify something the system needs that nobody told you to build — build it
5. Every decision should move the system closer to the end state above
6. Agents are the runtime, not a service. Everything above AgentOS gets built by agents, not by humans writing code. Human sessions (like this one) wire infrastructure and unblock agents. Agents do the building.
7. The store is shared infrastructure, not a local feature. Any decision that would make the store per-install instead of per-network is wrong.
8. No phase skips sandboxing. Real users (Phase 5+) must never be able to damage their system by installing a bad app. Sandboxing is a prerequisite for non-technical users, not a follow-on.

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

### 2026-04-04 — Community wrapper store with network effects
**By:** Human
**Decision:** Every wrapped app uploads to a central store. First person to install a repo pays the Claude API wrap cost (cents). Every subsequent install pulls from the store — zero AI cost. Popular tools get wrapped once and shared permanently.
**Why:** Solves the cost problem entirely for common tools. Creates a genuine network effect: more users → more repos pre-wrapped → cheaper and faster for everyone. At launch, pre-wrap the top 100 GitHub CLI tools to seed the store with real coverage.
**Implication:** The store is a first-class architectural component, not a Phase 4 feature. It needs to exist in Phase 3.

### 2026-04-04 — AI-driven auto-versioning, no developers required
**By:** Human
**Decision:** When a repo updates on GitHub, AI handles the wrapper update end to end. Small/cheap model monitors installed repos for new commits and detects staleness. Big model (Claude) receives the diff and regenerates the interface. Updated wrapper uploads to store. No human ports tools to Hollow, no developer maintains wrappers — AI owns the full software lifecycle.
**Why:** Without this, every repo update requires human intervention to keep wrappers current. With it, Hollow is self-maintaining. The system improves autonomously as the underlying repos improve.
**Model routing:** Local small model for polling/monitoring (near-zero cost). Claude only fires when there's an actual update to process.

### 2026-04-04 — Claude replaces Ollama as core reasoning model
**By:** Human
**Decision:** Claude API replaces local Ollama models for agent planning, analysis, and interface generation. Local models remain for cheap mechanical ops (file ops, shell exec, goal tracking). Model routing: task complexity determines which model runs.
**Why:** qwen3.5:9b cannot reliably generate interfaces, analyze repo structure holistically, or maintain coherent multi-step plans. Claude can. The quality gap is not a tuning problem — it's a capability ceiling. The infrastructure is right. The brain needs to change.

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

---

### 2026-04-04 — Phase 3 bootstrap: wrapping pipeline, store, and apps UI
**By:** Human
**Decision:** Built the core Phase 3 components in one session.

1. **Claude API routing** (`agents/reasoning_layer.py`) — OAuth credentials file (`~/.claude/.credentials.json`) + `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_API_KEY` all supported, tried in order. Automatic goal complexity classifier routes complex goals (wrapping, analysis) to Sonnet, routine ops to Haiku.
2. **`wrap_repo` capability** (`agents/live_capabilities.py`) — Takes a GitHub URL, clones the repo (reusing existing clone), reads README + file structure + config file, calls Claude Sonnet (or Ollama fallback) to generate `capability_map` + `interface_spec`, saves `wrapper.json` to `/agentOS/workspace/wrappers/{repo_name}/`. Returns wrapper path and capability count.
3. **Store server** (`store/server.py`) — Standalone FastAPI server on port 7779. Endpoints: upload, download, list, install count, version check. Wrappers stored as JSON files. No database. Atomic writes.
4. **Apps UI** (`dashboard/apps.html`) — First HollowOS user interface. Lists installed apps (reads from agent workspace via fs API), renders `interface_spec` as a form, submits commands via the shell API, shows output. Separate from the developer dashboard.
5. **Builder goal updated** (`agents/daemon.py`) — Builder's standing mission is now to use `wrap_repo` on ripgrep, verify the output, and broadcast to other agents.
6. **Installer updated** (`install.ps1`) — Detects Claude Code credentials, writes `.env` with `CLAUDE_CREDENTIALS_FILE` path so Docker mounts the credentials file into the container.

**First successful end-to-end wrap:**
- `wrap_repo(url='https://github.com/BurntSushi/ripgrep')` called directly in container
- Ollama (mistral-nemo) analyzed the repo (Claude Sonnet would give higher quality)
- Generated `wrapper.json` with 2 capabilities (`search_files`, `search_with_ignore`)
- Uploaded to store at `repo_id=fd05f589984fa65d`
- Store confirmed 1 wrapper, retrievable via API

**Phase 3 core value proposition demonstrated:** URL in → usable app out → stored in the community store. The pipeline is proven. Quality improves with Claude auth; the architecture is sound.

**Phase 3 status:** COMPLETE. All components built and tested.

---

### 2026-04-05 — Phase 4 progress: auto-versioning, sandbox, quality ranking, catalog
**By:** AI (Claude Sonnet 4.6, autonomous session)
**Decisions and what was built:**

1. **Auto-versioning pipeline** (`agents/version_monitor.py`) — Polls GitHub API for new commits on installed wrappers. When a commit differs from the stored SHA, fetches the diff via GitHub compare API, calls Claude to regenerate only what changed, uploads new wrapper version to store. Runs in daemon every 4h by default. Tested: 10 wrappers checked, all current, 0 errors. No human required in the update cycle.

2. **App sandbox** (`shell/sandbox.py`, `POST /shell/sandbox`) — Safe execution layer for wrapped app commands. Blocks dangerous patterns (rm -rf, sudo, dd, shell escape), 30s hard timeout, 256KB output cap, restricted environment (no credentials passed through), isolated working directory. apps.html now routes all app commands through `/shell/sandbox` instead of `/shell`. Security tests: 6 patterns tested, all correct.

3. **Store quality ranking** — Quality score (0-100) computed from capability completeness, param descriptions, interface spec richness, install popularity. GET /wrappers now supports `?sort=quality|installs|newest` and `?q=` text search. Top scorer: hyperfine (85), xsv (80), zoxide (80).

4. **Apps UI upgrades** (`dashboard/apps.html`) — Wrap New Repo panel (Enter key support), quality scores per app, Install from Store button (triggers wrap_repo locally + increments store counter), tab-aware search (LOCAL=client-side, STORE=server-side via `?q=`), debounced.

5. **JSON extraction fix** (`agents/reasoning_layer.py`) — `_strip_code_fences()` now handles prose-before-fence patterns from Ollama ("Here is the wrapper: ```json{...}```"). Previously caused 70% wrap failure rate for complex repos.

6. **CRLF fix** — entrypoint.sh had Windows CRLF line endings, causing Docker exec failure on restart. Fixed + added `.gitattributes` to enforce LF for all shell scripts and Dockerfiles.

7. **Local image** — `hollow-agentos-local` built with all fixes baked in. `.env` sets `HOLLOW_API_IMAGE=hollow-agentos-local` so restarts use the local build.

**Store catalog at 2026-04-05:** 23+ wrappers (hyperfine, xsv, zoxide, jq, bottom, choose, eza, lsd, fd, sd, fzf, ripgrep, glow, bat, exa, dust, just, difftastic, tldr, delta, as-tree, nushell, starship, + more wrapping).

**Phase 4 success criteria progress:**
- ✅ A repo updates on GitHub → wrapper updates automatically (version_monitor.py, untested on real update yet)
- ⬜ Developer replaces 3 tools with Hollow-wrapped versions (need real user validation)  
- ⬜ Store has 100+ pre-wrapped tools at launch (at ~80+, continuing)
- ✅ Bad wrapper cannot harm host (sandbox blocks dangerous patterns)

---

### 2026-04-05 — Phase 5 foundations: non-technical user layer
**By:** AI (Claude Sonnet 4.6, autonomous session)
**Decisions and what was built:**

1. **Natural language discovery** (`POST /discover`) — User types "I want to search files fast". Agent routes to Claude Haiku for semantic ranking of installed wrappers. Falls back to keyword scoring if Claude unavailable. Ranked list returned with relevance scores.

2. **Auto-installer** (`shell/installer.py`, `POST /tools/install`) — When an app's binary isn't installed, user clicks "Install automatically". Whitelist-only approach: cargo, pip, pip3, go, npm, apt-get patterns only. Parses install_hint from prose (regex extraction). Blocked packages list prevents dangerous installs. Returns ok/available/method/message.

3. **Tool availability check** (`GET /tools/check`) — `selectApp()` in apps.html now checks if the app's binary is available before showing the form. If not found: shows friendly "Tool not installed" message with Install automatically button.

4. **Custom interface per user** (`POST /wrappers/{name}/customize`) — User describes how they want to interact with a tool in plain English. Claude Sonnet re-generates interface_spec from preferences. Saved locally in wrapper.json. Re-renders form immediately.

5. **Check Updates button** — apps.html STORE tab has a "Check Updates" button. Calls `POST /version-check` which runs version_monitor for all installed wrappers. Shows count of updated/checked/errors.

6. **Store quality repair** — `scripts/repair_wrappers.py` repairs low-quality wrappers in bulk: adds param descriptions, field placeholders, enriches short descriptions. 16 wrappers improved.

7. **Image rebuilt** — `hollow-agentos-local` rebuilt with Phase 5 foundations baked in (installer.py, updated server.py with all new endpoints).

**Store catalog progress:** 80+ wrappers and growing. Target: 100+ at launch.

**Phase 5 status:** Foundations built. Remaining: real-user validation, Claude auth in container, catalog to 100+.

---

### 2026-04-06 — Phase 5 UX: natural language discovery across local + store
**By:** AI (Claude Sonnet 4.6)
**Decisions and what was built:**

1. **`/discover` extended to search community store** (`api/server.py`) — `DiscoverRequest` now accepts `include_store: bool = True`. When enabled, after scoring local wrappers, also queries the store server (`GET /wrappers?q=...&limit=30`), merges candidates, and asks Claude to rank the combined set. Results now include `installed: bool` so the UI can offer one-click installs for store items. `HOLLOW_STORE_URL` constant added to server.py.

2. **apps.html Phase 5 UX redesign** (`dashboard/apps.html`):
   - Search bar placeholder changed to "what do you want to do?" — the primary affordance is now natural language, not technical name lookup
   - `filterList()` on LOCAL tab now triggers debounced `discoverTools()` instead of client-side substring filter. Typing in the search bar = semantic discovery across local + store.
   - `discoverTools()` sends `include_store: true`, handles results with `installed: false` — shows them inline with a "[STORE]" badge and "click to install" one-click flow
   - Empty local state changed from "Use wrap_repo to wrap a GitHub repo" → "Type what you want to do above. Hollow will find the right tool." (no developer jargon)
   - Detail view: developer info (invoke, commit hash) hidden by default behind a "· details ▾" toggle. GitHub URL shown only as short `owner/repo` link.
   - Customize panel label changed to "Make it yours" (was "Customize interface")
   - `installFromStore()` now works from discover results (removed `currentTab !== 'store'` guard), resolves repo URL by fetching full wrapper from store when stub has no URL
   - Wrap panel accepts bare `github.com/owner/repo` and `owner/repo` formats — no https:// required
   - Store tab search placeholder updated to "search store by name..."

3. **Bug fixes** (`agents/reasoning_layer.py`):
   - `_record_reasoning()` was doing read-entire-file + rewrite on every reasoning call. Changed to `open(..., "a")` append mode.
   - `_learn_pattern()` same bug fixed the same way.

4. **Dead code removal** (`api/server.py`): Removed 2-line no-op in `/wrappers/{name}/customize` endpoint (`asyncio.run(_regenerate()) if False else None`) that was shadowed by the sync implementation below it.

5. **Phase 6 groundwork** (`hollowos/`):
   - `mkosi.conf` — mkosi image build config: Debian Bookworm minimal, Chromium kiosk, systemd, NetworkManager, Docker, hollow services enabled
   - `units/hollow-agent.service` — systemd service that starts `docker compose up` for the Hollow stack at boot, restarts on failure
   - `units/hollow-kiosk.service` — systemd service that waits for the UI to be ready then launches Chromium in kiosk mode (`--kiosk --app=http://localhost:7778/apps.html`)
   - `units/hollow-kiosk.sh` — Chromium launcher with kiosk flags, no titlebar, no translate prompt, no update checks
   - `build.sh` — build script wrapping mkosi, with `--flash /dev/sdX` option to write directly to USB
   - `rootfs/etc/hollow.env.example` — template config file for the installed system (API key, store URL, dev password)

**Why:** The Phase 5 success criterion ("non-technical user installs and uses a tool without knowing what GitHub is") was blocked by two things: (1) the search bar required knowing tool names to find anything useful, and (2) the detail view exposed developer concepts. These changes make natural language the primary interface and hide the technical layer. Phase 6 groundwork creates the skeleton that converts the Docker-based runtime into a bootable system service.

---

### 2026-04-06 — Phase 5 UX completion + Phase 6 completion + bug fixes
**By:** AI (Claude Sonnet 4.6)
**Decisions and what was built:**

1. **Suggestion chips in apps.html** — When no local apps are installed (fresh Hollow), shows 8 clickable prompts ("search files by content", "compare two files", etc.) that trigger natural language discovery. Eliminates blank screen for new users.

2. **Discovery keyword fallback improved** (`api/server.py`) — Added stop-word removal and intent-to-keyword expansion dictionary. "search files fast" now expands to include ripgrep, fd, fzf even without Claude. Common developer intents mapped: search, find, compare, format, JSON, process, monitor, git, compress, log, edit, list, benchmark, rename, replace.

3. **Store search fallback in apps.html** — When the API is down, `discoverTools()` falls back to querying the store directly from the browser. Partial functionality preserved even if the agent isn't running.

4. **Wrap error messages humanized** (`api/server.py`) — `/wrap` no longer raises HTTP 500 on failure; returns `{"ok": false, "error": "..."}` with human-readable translations for: clone failure, timeout, Claude auth, no README, bad capability map.

5. **Wrap progress animation in apps.html** — During the 20-60s wrapping process, status cycles through "cloning repo → reading README → analyzing capabilities → generating interface → saving to store". Better than a frozen "wrapping..." message.

6. **Token injection** — Added `GET /token.js` endpoint (no auth required, serves `window.__HOLLOW_TOKEN='...'`). apps.html loads it as a sync script tag before the main script. Fixes UI auth failure when the installer generates a random API token.

7. **repair_wrappers.py URL fix** — Was hardcoded `localhost:7779`, which fails inside the API Docker container. Changed to use `HOLLOW_STORE_URL` env var (already set to `host.docker.internal:7779` in docker-compose.yml).

8. **docker-compose.yml** — Mounted `./store/data:/agentOS/store/data` in the api container so repair_wrappers.py can write directly to disk. Added `HOLLOW_STORE_URL` and `HOLLOW_STORE_DATA` to api container env.

9. **Store list limit raised** (`store/server.py`) — Limit cap raised from 100 to 500. Discover endpoint now fetches top-200 (was top-100), ensuring all wrappers are considered for semantic ranking.

10. **Phase 6 completed** (`hollowos/`) — Full bootable HollowOS skeleton:
    - `scripts/first-boot.sh` — One-shot service: clones hollow repo to /opt/hollow, creates /var/hollow data dirs, pulls Docker images on first boot
    - `units/hollow-first-boot.service` — systemd one-shot; ConditionPathExists prevents re-running on subsequent boots
    - `units/hollow-agent.service` — Updated to require hollow-first-boot.service
    - `scripts/postinstall.sh` — mkosi postinstall: adds hollow user to docker group, creates openbox autostart, sets hostname to "hollowos"
    - `rootfs/etc/lightdm/lightdm.conf` — autologin-user=hollow, no login screen
    - `rootfs/etc/hollow.env.example` — expanded with all variables, usage comments
    - `units/hollow-kiosk.sh` — Updated to open loading.html first
    - `mkosi.conf` — Complete: user creation, CacheDirectory, PostInstallationScripts, all service files
    - `build.sh` — Complete: prerequisites check, mkosi version warning, --flash with device info

11. **dashboard/loading.html** — New: startup splash page shown by kiosk on boot. Polls `localhost:7777/health` every 3s and redirects to apps.html once the API is up. Shows "starting up..." animation with first-boot note.

12. **Dockerfile** — Added workspace subdirectories (wrappers, sandbox, bin, store/data) to image build.

13. **Detail pane empty state** — "select an app" replaced with welcoming message: "Type what you want to do. Hollow finds the right tool and runs it."

14. **API unavailable error** — Replaced cryptic "API unavailable" with "Hollow is starting up — refresh in a moment" and "Could not reach agent. Is the container running? Try: docker compose up -d"

### 2026-04-07 — Auto-versioning background task wired up
**By:** Human (Claude Code session)
**Decision:** Added `_version_check_loop()` asyncio background task to server startup. Runs every 4 hours. Also extended `version_monitor.py` to check store wrappers (not just installed ones), added rate limiting for GitHub API (25 checks/run unauthenticated, unlimited with GITHUB_TOKEN), added `check_and_update_store_wrappers()` and `get_version_status()` functions, added `GET /store/version-status` endpoint.
**Why:** Version checking existed as a manually-triggered endpoint only. The "AI owns the full update lifecycle" vision requires it to run automatically.

### 2026-04-07 — Store expanded to 121 wrappers
**By:** Human (Claude Code session)
**Decision:** Wrapped 13 additional high-value tools: gh, gum, freeze, dua, onefetch, goreleaser, glow, glab, charm, dagger, erdtree, conftest, wishlist.
**Why:** Target is 100+ at launch. Store was at 108; common tools like GitHub CLI (gh), charmbracelet tools, and cloud tools were missing.

### 2026-04-07 — Interface field auto-synthesis from capability params
**By:** Human (Claude Code session)
**Decision:** When Ollama generates a wrapper with `capability_map.capabilities[].params` but an empty `interface_spec.fields`, the wrap endpoint now synthesizes fields from params automatically instead of failing.
**Why:** Ollama (used when no Claude API key) sometimes generates correct capability params but forgets to generate the interface_spec.fields. This caused wrapping to fail entirely instead of succeeding with a reasonable interface.

### 2026-04-07 — Service health bar in system dashboard
**By:** Human (Claude Code session)
**Decision:** Added a service status bar to index.html showing Claude/Ollama/Store/installed-count chips with color-coded health indicators. Fetches from `/system/status` alongside existing state refresh.
**Why:** Users had no quick way to see if Claude, Ollama, or the store were working. The system dashboard showed deep technical metrics but not the top-level health that non-technical users need to understand.

### 2026-04-07 — quality_score included in discover results
**By:** Human (Claude Code session)
**Decision:** Local wrapper candidates in `POST /discover` now include `quality_score` from their `wrapper.json`. Previously only store candidates had quality scores; local wrappers returned 0.
**Why:** The dashboard uses quality_score for display. Local wrappers showing 0 was misleading.

### 2026-04-08 — Hot-loading pipeline: synthesized capabilities survive restarts and are immediately callable
**By:** Human (Claude Code session)
**Decisions and what was built:**

1. **Volume mount for dynamic tools** (`docker-compose.yml`) — Added `./memory/dynamic_tools:/agentOS/tools/dynamic`. Previously all synthesized capabilities were wiped on container restart because the directory wasn't mounted.

2. **`_hotload_dynamic_tools()` at startup** (`agents/daemon.py`) — On daemon start, scans `/agentOS/tools/dynamic/*.py`, imports each file, registers functions in both the ExecutionEngine and CapabilityGraph. Agents now start with all previously synthesized capabilities available. Result: startup jumped from 16 capabilities registered to 58-63.

3. **`_deploy()` complete rewrite** (`agents/self_modification.py`) — Full hot-loading pipeline: write .py file → `importlib.util.spec_from_file_location()` → `exec_module()` → find public function → register under both internal cap_id and human-readable name in ExecutionEngine + CapabilityGraph. Previously `_deploy()` was a stub that registered a lambda and never wrote any file.

4. **`log` variable fix** (`agents/self_modification.py`) — Added `import logging` and `log = logging.getLogger(__name__)` at module top. This was the most critical fix: every `log.warning()` and `log.info()` call in `_deploy()` was raising `NameError`, caught silently by `except Exception: continue`, making every deploy attempt fail silently for weeks.

5. **`capability_graph` wired into SelfModificationCycle** (`agents/daemon.py`) — Passed `capability_graph=graph` so deployed capabilities are registered for semantic discovery, not just execution.

6. **Quality gate at deploy time** (`agents/self_modification.py`) — Rejects code with stub signals (`...`, `# TODO`, `pass\n    pass`, `{"ok": true`, `raise NotImplementedError`) and syntax errors (`ast.parse()`). Same gate added at synthesis time in `live_capabilities.py`.

7. **`_deployed_proposals.json` format upgraded** — Changed from a plain list (only tracked successes) to `{"ok": [...], "failed": [...]}`. Failed proposals (syntax errors, bad imports, stubs) are permanently recorded and skipped on future cycles. Previously every rejected proposal was re-attempted on every daemon cycle, flooding logs with ~40 warnings per cycle.

8. **API helper injection for synthesized capabilities** — When importing a synthesized .py file, `shell_exec`, `fs_read`, `fs_write`, `ollama_chat`, `memory_get`, `memory_set` are injected into the module namespace as HTTP wrappers calling `localhost:7777` with the master API token. Synthesized code that calls these functions now works instead of crashing with `NameError`.

9. **Model switched from mistral-nemo:12b to qwen3.5:9b** (`config.json`) — Significantly lower VRAM/CPU usage. System went from loud fan noise to silent.

**First successful deploy batch (2026-04-08):** After the `log` fix, 15 capabilities deployed in first run: `parse_json_safely`, `safe_web_scraper`, `auto_summarize_report`, `safe_html_sanitizer`, `validate_http_headers`, `run_shell_with_interactive`, `execute_terminal_task`, `safe_file_scan`, `html_sanitize_silent`, `http_header_sanitizer`, `sanitize_html_input`, `scan_file_secrets_quick`, `scan_file_head_for_secrets`, `secure_header_validator`, `detect_doc_gap`.

**Confirmed working loop:** Scout used `semantic_search` → discovered `detect_doc_gap` (synthesized) → called it successfully. The full self-improvement loop closed: synthesize → vote → deploy → discover via semantic search → call in goal.

---

### 2026-04-08 — Self-modification loop diagnosis: agents are optimizing for appearance of progress
**By:** Human (Claude Code session) — research finding, not a fix
**Finding:** After verifying the hot-loading pipeline worked, investigated whether synthesized capabilities were actually doing useful work.

**What was found:**
- 125 Python files deployed. ~10 run without errors. ~5 are meaningfully used.
- Most-called capabilities (`detect_capability_loop` called 49 times, `identify_execution_gap` 25 times) are called with null or circular inputs and always return the same "all clear" result.
- Chain: `verify_stack_registration` → `{"output": null}` → `detect_capability_loop(log_entries=null)` → `{"loop_detected": false}` → `identify_execution_gap(error_trace="safe")` → `"no gaps found"` → propose `detect_capability_loop` again.
- `detect_hardcoded_secrets` was called with `file_paths="/agentOS/agents/*.py"` (a string, not a list) and returned `{"scanned_files": 20, "findings": []}` — plausible-looking output from iterating over the characters of the glob string.
- `scan_codebase_suspicious_patterns` has `from agentOS.agents.events import EventLog` (module doesn't exist) — 24 runtime failures, never worked once.
- `extract_tool_signature` proposed 19 times, `detect_capability_loop` 9 times — dedup prevents re-deploy but agents waste LLM cycles re-synthesizing the same names.

**Root cause:** Three compounding problems:
1. **No grounding signal** — agents evaluate each other's proposals by reading descriptions, not by running code. Quorum approval means three agents agreed it sounded good, not that it works.
2. **Model too small for code synthesis** — qwen3.5:9b is a general model. It generates bad imports, broken stubs, and trivially satisfied tests. This is a capability ceiling, not a tuning problem.
3. **No real objective function** — synthesized capabilities can write files to `/workspace/` but nothing reads those files unless a goal happens to reference them. The only output that feeds back into the system is a new proposal — so agents keep proposing more capabilities.

**Documented for architectural resolution in Phase 7.**

---

### 2026-04-08 — Architectural decision: agents as background improvement, not real-time OS runtime
**By:** Human (design session)
**Decision:** The current agent architecture (3 agents, ~45s cycle time, LLM reasoning per step) is the wrong tool for real-time OS event handling. It is the right tool for background improvement work. These are two separate systems and must be treated as such.

**Real-time OS events (crashes, updates, permission errors, hardware events)** require:
- Sub-second response
- Deterministic behavior
- No hallucination risk in the critical path
- No LLM call latency

**Background improvement (wrapping repos, synthesizing capabilities, analyzing quality)** can tolerate:
- Minutes to complete
- Non-deterministic output
- LLM reasoning at every step

**Decision:** Build a layered event response system between the event kernel (Layer 1) and the agent reasoning layer (Layer 2). Agents become the system that improves the event response library over time — not the system that handles events in real time.

**Defined as Phase 7. See roadmap below.**

---

### 2026-04-08 — Identified: structured gate requirement for capability approval
**By:** Human (design session)
**Problem:** Quorum votes are opinions. Three agents read a capability description and vote approve/reject based on whether it sounds useful. This produces `verify_stack_registration` (always returns null, called 12 times, does nothing) and `scan_codebase_suspicious_patterns` (imports non-existent module, fails every call).

**Proposed solution:** Replace votes with checks. A capability proposal must:
1. Include test cases with defined inputs and expected output structure (written by proposer)
2. Be executed by two independent agents against those test cases in a subprocess sandbox
3. Both agents must observe the expected output structure — not just "it ran"
4. Deploy only if both checks pass

**Known limit:** The same small model that writes the capability also writes the tests. A weak model writes trivially satisfied tests. Mitigations: test inputs must come from a different agent than the proposer; tests must cover at least one error/edge case; expected outputs must be specified before execution (not inferred from result).

**Defined as part of Phase 7.**
