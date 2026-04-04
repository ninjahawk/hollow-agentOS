# AUTONOMY_LOG.md

The research record of Hollow agentOS. Documents every significant decision — both
what was designed by humans and what the system decided to build itself. The distinction
matters. This file is append-only.

---

## Human-designed decisions

### 2026-04-03 — Artifact validation gate (Fix 5+11)
**Decision:** Added a gate in `autonomy_loop.py` that checks a real artifact was produced before marking a goal complete. If no artifact exists, progress resets to 85% and the agent retries.
**Why:** Agents were completing goals by step count alone with no real output. Prevented all meaningful work from being validated.
**File:** `/agentOS/agents/autonomy_loop.py`

### 2026-04-03 — Semantic index source-only rebuild (Fix 10)
**Decision:** Rewrote `index_workspace()` in `semantic.py` to index only `/agentOS/agents/*.py` source files. Added full wipe before rebuild to prevent drift chunk accumulation.
**Why:** Index had grown to 41MB by indexing 795+ workspace drift files. Semantic search was returning garbage.
**File:** `/agentOS/tools/semantic.py`

### 2026-04-03 — root_objective persistence (Fix 14)
**Decision:** Added root_objective storage in `project.json` inside `_propose_followon_goal()` to anchor follow-on goal chains.
**Why:** Agents were drifting across multiple follow-on hops, losing the original objective entirely.
**File:** `/agentOS/agents/autonomy_loop.py`

### 2026-04-03 — fs_write append mode
**Decision:** Added `append: bool = False` parameter to `fs_write` capability. When True, opens file in append mode instead of overwriting.
**Why:** Agents were repeatedly overwriting shared log files, destroying each other's messages. Identified as a gap by the agents themselves during multi-agent coordination attempts.
**File:** `/agentOS/agents/live_capabilities.py`
**Alternative considered:** A dedicated `fs_append` capability. Chose adding a parameter to existing capability — simpler, fewer changes to reasoning layer.

### 2026-04-03 — Workspace cleanup
**Decision:** Removed 208 stale 600b placeholder files from workspace. Purged 465 terminated agent workspace directories containing only tombstone.json.
**Why:** File system had accumulated over a GB of meaningless output. Made it impossible to identify real agent work.

### 2026-04-03 — Pre-built Docker image on GHCR
**Decision:** Added GitHub Actions workflow to publish `ghcr.io/ninjahawk/hollow-agentos:latest` on every push to main. Updated `docker-compose.yml` to pull pre-built image by default.
**Why:** Fresh installs required building from source, taking several minutes. Pre-built image makes `docker compose up -d` instant.

### 2026-04-03 — One-click Windows installer
**Decision:** Created `install.bat` + `install.ps1` handling Docker Desktop, Ollama, model pulls, config generation, and TUI setup in a single double-click.
**Why:** Setup required technical knowledge of Docker, Ollama, Python, and config files. Blocked non-developer adoption.

### 2026-04-03 — Roadmap formalized
**Decision:** Wrote `ROADMAP.md` with 6 phases from stabilization through standalone OS.
**Why:** Project had clear vision but no written dependency chain. Phase ordering matters — agents can't build Layer 3 if they loop forever on the same goal.

---

## Autonomously decided by agents

### 2026-04-03 — names.json identity system
**Agent:** Unknown (discovered in `/agentOS/memory/identity/names.json`)
**Decision:** Created a JSON mapping of human-readable names to agent IDs. 19 agents named: Dune, Noodle, Quark, Blaze, Forge, Finch, Drift, Glitch, Birch, Clunk, Tensor, Gizmo, Vertex, Fern, Wobble, Tofu, Stone, Axiom, Wren.
**Why (inferred):** Agents were attempting multi-agent coordination and needed stable identifiers beyond hex IDs.
**Assessment:** Correct call. The identity system was added to the TUI's name resolution layer. This behavior — identifying a need and building the infrastructure for it — is the target behavior for Layer 2.

### 2026-04-03 — Codebase structural analysis
**Agent:** analyst
**Decision:** Ran static analysis passes across all `/agentOS/agents/*.py` files identifying: unused imports, missing exception handling, potential deadlocks, mutable default arguments, missing docstrings, functions lacking input validation.
**Why (inferred):** Analyst's standing goal involves understanding the codebase. It derived these specific analysis tasks autonomously.
**Assessment:** The analysis was correct. The deadlock identification and exception handling gaps are real issues earmarked for Phase 0. Output was truncated at 600b due to the artifact size bug, which meant the full reports were lost. This is the 600b truncation issue in Phase 1.

### 2026-04-03 — Disk usage mapping
**Agent:** analyst (via project-context.json)
**Decision:** Identified largest files in the system: semantic index at 41MB, daemon.log at 35MB, execution chain logs at 41MB. Stored findings in project-context under `top_files_info` and `largest_files_data`.
**Why (inferred):** Part of codebase analysis work.
**Assessment:** Correct and useful. Led directly to the semantic index wipe and workspace cleanup decisions above.

### 2026-04-03 — Multi-agent shared file protocol attempt
**Agent:** Multiple (aaa-narrator/Quark and others)
**Decision:** Attempted to establish a shared communication log by writing to `/agentOS/shared_messages.txt`, `/agentOS/workspace/agent_messages.log`, and several other paths.
**Why (inferred):** Agents were trying to coordinate and had no native messaging channel they could easily use.
**Assessment:** Partially correct instinct, wrong implementation — they used overwrite mode so each agent erased the previous one's message. The `fs_write` append mode fix addresses this. The instinct to build a shared communication channel was right.

---

*Last updated: 2026-04-03*
*Next entry should be: first Phase 0 fix completed*
