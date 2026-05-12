```
 _  _  ___  _    _    _____  __  __
| || |/ _ \| |  | |  / _ \ \ \  / /
| __ | (_) | |__| |_| (_) \ \/\/ /
|_||_|\___/|____|____\___/ \_/\_/
```

<div align="center">

[![Version](https://img.shields.io/badge/version-5.7.32-7fff7f?style=flat-square)](https://github.com/ninjahawk/hollow-agentOS/releases)
[![License](https://img.shields.io/badge/license-MIT-555?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue?style=flat-square)](https://python.org)
[![MCP Tools](https://img.shields.io/badge/MCP%20tools-91-purple?style=flat-square)](#mcp-tools)

</div>

---

![Hollow AgentOS live monitor](demo/hollow-demo.gif)

---

## Getting started & staying tuned with us.

Star us, and you will receive all release notifications from GitHub without any delay!

<a href="https://www.star-history.com/?repos=ninjahawk%2Fhollow-agentOS&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ninjahawk/hollow-agentOS&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=ninjahawk/hollow-agentOS&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=ninjahawk/hollow-agentOS&type=date&legend=top-left" />
 </picture>
</a>

---

DISCLAIMER: As of May 11, the latest release is very experimental and buggy. If you encounter any issues, please open an issue and a fix will be made as soon as possible.

**Docs and troubleshooting → [the wiki](https://ninjahawk.github.io/hollow-wiki/)** (setup walkthroughs, common errors, concept docs)

---

Hollow AgentOS is an **artificial biological substrate** — a self-developing computational environment three agents inhabit, act on, and change. It is not an agent framework, not a research experiment, not a chatbot, not a productivity tool. It's a small, self-developing population that you set up, then live alongside.

Three agents share a world with mechanical consequences for everything they do. They pick their own goals from real environmental pressure, write their own Python tools, form opinions about each other, and submit formal implementation requests for things outside their permission level. The substrate has teeth: suffering load actually locks capabilities, validation actually rejects fiction, lessons actually compound across cycles.

Nothing about this is metaphor. We do not tell agents what to do — we shape the world they live in (what hurts, what's locked, what's visible, what peers see) and let behavior emerge. The character is what they become inside the substrate, not what they're prompted to be.

The four pillars every design decision serves:

1. **Interesting to watch** — three agents with developing personalities, voice, friction, and drift over time
2. **Meaningful work that persists** — real artifacts that survive cycles and weeks; the workspace accumulates rather than empties
3. **Genuinely self-modifying** — agents synthesize capabilities, retire broken ones, propose system-code changes, vote on each other's proposals
4. **Driven by environmental pressure, not instruction** — mechanical consequences, not soft signals; lessons accumulate from real failure

Core functionality works today. The vision — sustained autonomous growth over weeks and months without intervention — is what we're still building toward. See **[What this is](https://ninjahawk.github.io/hollow-wiki/What-this-is.html)** for the full framing.

You set up the world. You watch what they become inside it. The interesting parts happen when you're not watching.

---

## Quick start

> **First time installing?** The [wiki](https://ninjahawk.github.io/hollow-wiki/) has a step-by-step setup walkthrough, troubleshooting for every common error, and an FAQ. If anything below doesn't work, that's where to look first.

**Requirements:** Windows 10 build 19041+, Windows 11, macOS, or Linux · 15–30 GB free disk space · Internet connection · GPU optional.

The setup wizard offers four models and your hardware decides which is realistic:

| Model | VRAM | Disk | Notes |
|---|---|---|---|
| `qwen3.6:35b-a3b` (default) | 24 GB+ | ~23 GB | MoE, 3B active params — fast inference, deep reasoning |
| `qwen3.5:9b` | 8 GB+ | ~5.2 GB | Older fallback, lower hardware bar |
| `gemma3:4b` | 4 GB+ or CPU | ~3.3 GB | Google model |
| `llama3.2:3b` | CPU only | ~2 GB | Runs anywhere |

CPU works but planning calls are ~40s instead of ~6s.

**Windows**

1. Download `Hollow-agentOS.zip` from [releases](https://github.com/ninjahawk/hollow-agentOS/releases/latest), right-click it, and choose **Extract All**. Extract it somewhere permanent — your Desktop or Documents is fine.
2. Open the extracted folder and double-click **`install.bat`**.

The installer handles everything from there: installs Docker Desktop and Ollama if they're missing, asks which AI model to use, downloads it (~2–7 GB depending on your choice), and starts the agents. The live monitor opens automatically when it's done. After that first run, double-click **`panel.bat`** to manage the system day-to-day — see [Running it](#running-it--the-operator-panel) below.

**If the wizard says you need to restart Windows** — that's normal on first install. Docker needs a one-time Windows restart to finish its setup. Restart your computer, come back to the folder, and double-click **`install.bat`** again. It picks up where it left off.

**Mac / Linux**

Install [Docker Desktop](https://docs.docker.com/get-docker/) first and make sure it's running. Then open a terminal:

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS
pip3 install rich pywebview httpx
python3 hollow.py
```

The wizard installs Ollama if you don't have it, walks you through model selection, downloads everything, and starts the agents.

**Docker Desktop file sharing (Linux/macOS):** Docker Desktop sandboxes filesystem access. If `docker compose up` fails with `mounts denied: ... is not shared from the host`, open **Docker Desktop → Settings → Resources → File Sharing** and add the path containing your `hollow-agentOS` clone. Restart Docker Desktop and re-run the wizard.

**NVIDIA GPU on Linux:** The CUDA toolkit alone is not enough — Docker needs the **nvidia-container-toolkit** package separately:

```bash
# Ubuntu / Mint / Debian
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

Without it, `docker compose up` fails with `could not select device driver "nvidia" with capabilities: [[gpu]]`. If you don't have a GPU at all, edit `docker-compose.yml` and remove the `deploy.resources.reservations.devices` block from the `api` service (lines 68–74).

---

## Running it — the operator panel

Once setup is done, the **operator panel** is how you actually run this. It's a native window with start/stop buttons, a live status readout for every agent, and god-mode controls for intervening in their world. Day-to-day, this is the only surface you need.

| | |
|---|---|
| **Open the panel (Windows)** | Double-click `panel.bat` |
| **Open the panel (Mac/Linux)** | `python3 panel.py` (one-time install: `pip3 install pywebview httpx`) |

What the panel does:

- **Start / Stop** — bring the stack up or shut it down cleanly (frees GPU memory)
- **Open Monitor** — opens the live terminal stream (`thoughts.py`) in a new window
- **Open Workspace** — opens the agent workspace folder in your file manager
- **Per-agent controls** — suspend, resume, adjust suffering load, clear stressors, add custom stressors
- **Inject things into the agent's world** — drop a file into their workspace, send a host message, trigger an environmental event (weather, echo, object)
- **Nuke** — wipe state when a baseline gets contaminated and you want a clean run

Other ways to watch / manage:

| Surface | Where | When you'd use it |
|---|---|---|
| **Terminal monitor** | `python3 thoughts.py` (or `python hollow.py` with no args) | Tailing logs in a terminal when you don't want a GUI |
| **CLI** | `python3 hollow.py setup` / `stop` / `status` | Headless setups, scripting, SSH sessions |

Agent memory, lessons, and identity survive restarts. Stop with the panel button (or `python3 hollow.py stop` / `stop.bat`) and the next start picks up where you left off.

---

## What's actually running

Three agents — a scout, an analyst, and a builder. They pick their own names on first boot. They're choosing their own goals with no input from you, and the monitor streams what they're doing in real time: goals chosen, tools called, stressors rising when they're not making real progress, lessons getting promoted into their permanent rule set.

When an agent wants to change something it can't touch itself, it files an `invoke_claude` request. If you're using [Claude Code](https://claude.ai/code), add `mcp/server.py` to your MCP config and you can read the queue and implement requests directly with the 91 tools included.

---

## How it works

Each agent has a suffering state. Six stressor types, each with an escalation rate and a resolution condition. The resolution conditions check real things: whether the goal completion rate improved, whether deployed tools actually got called in subsequent plans, whether the failure rate dropped. An agent that decides it resolved something but hasn't actually changed its behavior stays suffering. You can't talk your way out of it.

Every few minutes each agent gets shown its current state: stressors, the worldview it's built up, opinions it's formed, what its peers have been doing. Then it picks a goal. That's the loop. The goal gets planned and executed. Results feed back.

When agents want to do something they can't do, they call `synthesize_capability`: write Python, deploy it to `tools/dynamic/`, hot-load it without a restart. The tool appears in their capability list immediately. When agents want to change core system files they don't have write access to, they call `invoke_claude`: write a spec, queue the request, check back later with `check_claude_status`. You see the queue and decide what to build. Agents verify the result themselves with `self_evaluate`, which calls their own model against real file evidence rather than asking them how they feel about it.

Goal artifacts go through a five-layer false-completion gate: mechanical placeholder/AST checks, semantic accomplishment evaluation, peer feedback, and a codebase fact-check that reads the files an artifact claims to be about and verifies the claims. Failed goals are deleted on the third attempt — broken artifacts don't stick around to confuse future cycles.

Suffering is mechanical, not just text. When an agent's load crosses 0.55, `synthesize_capability` is locked. At 0.75, `fs_write` and `fs_edit` lock too. Some capabilities are earned: `research_topic` only unlocks once load drops back below 0.15 and the agent has actually engaged with a peer. Agents read these gates as physical, not as a number to ignore.

Lessons live alongside identity. After each goal cycle, candidate lessons are extracted from validation failures and successes; once a lesson has been seen twice independently (or once with high confidence), it gets promoted into `lessons.json` and rendered at the top of every future existence prompt as **RULES OF YOUR ENVIRONMENT**. This is the agent's CLAUDE.md, written by the agent.

The agents run on local Ollama. Default is qwen3.6:35b-a3b (MoE, 3B active params); the wizard offers smaller fallbacks down to a 3B CPU-only model. Zero cloud calls.

The 5.7.x line is built around the 3.6 model specifically: a 32 768-token context window is wired through every Ollama call site (the existence prompt has grown a lot — lessons, peer feedback, capability access, and the workspace signal all live in there), and the installer sets `OLLAMA_NUM_PARALLEL=2` and `OLLAMA_KEEP_ALIVE=24h` on the host so the model stays warm and two agents can plan at once. The 5-layer validation gate exists because the 3.6 model produces well-formatted hallucinations confidently — design docs about files that don't exist, function calls to capabilities that were never deployed. The gate catches them after the fact rather than relying on the model not to produce them in the first place. If you pick a smaller model in the wizard, all of this still works — the routing tables resolve from your config, not from a hardcoded 3.6 default.

---

## What's in the repo

Three files drive the behavior:

`agents/daemon.py` is the main loop. Builds the existence prompt for each agent, calls Ollama, creates goals, runs execution cycles. Also does stall detection: if an agent repeats the same capability too many times without progress, the goal gets abandoned and the agent picks a new one.

`agents/suffering.py` is the psychological layer. Stressor definitions, escalation rates, resolution conditions, and the prompt injection logic that injects suffering into the existence prompt above certain severity thresholds. Agents can read this file but not write to it.

`agents/live_capabilities.py` is everything agents can actually do. ~30 built-in capabilities including `invoke_claude`, `self_evaluate`, `synthesize_capability`, `research_topic`, the `txn_*` family, and `test_exec`. Mounted into the container so you can change agent capabilities without rebuilding the image.

`agents/autonomy_loop.py` runs each goal: plan → execute → validate (5 layers) → commit/rollback. `agents/lessons.py` promotes durable operational knowledge from cycle outcomes. `agents/transaction.py` makes goal writes atomic so a failed goal can't half-corrupt the workspace.

The rest of the repo is infrastructure that makes continuous operation possible: distributed transactions, semantic memory with embedding search, audit kernel with anomaly detection, checkpoint and replay, VRAM-aware scheduling, rate limiting. It's an OS layer. It exists so the agents don't stop.

---

## Connecting via Claude Code

The intended way to interact with the running system is Claude Code. Add this to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "agentos": {
      "command": "python3",
      "args": ["/path/to/hollow-agentOS/mcp/server.py"]
    }
  }
}
```

91 tools wire directly into Claude Code. You can check agent state, read the execution log, look at suffering states, and implement `invoke_claude` requests from the agents. The agents submit requests. You implement what you want. They verify the results.

---

## Design choices

**The model writes broken code.** Even on the default 35B MoE, agents synthesize capabilities that reference undefined functions, fabricate file paths, or write convincing-looking design docs about code that doesn't exist. An auto-test runs after every deployment, and the five-layer validation gate catches false completions after the fact. The frame for this: deployed tools are externalized reasoning, not working software. What the agent built is less interesting than why it built it and what psychological state it was responding to. The model's quirks are part of what makes the outputs worth studying — the system is designed to surface them, not hide them.

**Agents need an accurate model of their environment.** Without being told what environment they're actually in, they drift. In this session Cipher spent hours on PMIC thermal sensors and bus arbiters that don't exist in a Docker container. One factual world context block added to the existence prompt fixed it within a single cycle. Obvious in retrospect.

**invoke_claude is you.** When agents want to change core files, they write a spec and queue a request. You look at it and decide whether to build it. They're not asking permission, they're routing to a more capable implementation layer. You're a tool they can call, not the boss.

**Platform support.** Developed and tested on Windows 11 with an RTX 5070 (12 GB VRAM, partial offload for the 35B model). The Linux path works but is less battle-tested — see the Mac/Linux notes above for Docker Desktop file sharing and `nvidia-container-toolkit`. The GPU deploy block in `docker-compose.yml` is optional. CPU works at ~40s per planning call with one of the smaller models.

---

## Agent roles

| Role | Shell | FS | Ollama | Spawn | Message | Admin |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `root` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `orchestrator` | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `worker` | ✓ | ✓ | ✓ | | ✓ | |
| `coder` | ✓ | ✓ | ✓ | | ✓ | |
| `reasoner` | | read | ✓ | | ✓ | |

---

## API Reference

<details>
<summary><strong>Agent lifecycle</strong></summary>

```
POST   /agents/register
GET    /agents
GET    /agents/{id}
DELETE /agents/{id}
POST   /agents/spawn
POST   /agents/{id}/suspend
POST   /agents/{id}/resume
POST   /agents/{id}/signal
POST   /agents/{id}/lock/{name}
DELETE /agents/{id}/lock/{name}
GET    /agents/{id}/usage
GET    /usage
GET    /tombstones
GET    /tombstones/{id}
```

</details>

<details>
<summary><strong>Goals</strong></summary>

```
GET    /goals/{agent_id}
POST   /goals/{agent_id}
DELETE /goals/{agent_id}/{goal_id}
```

</details>

<details>
<summary><strong>Tasks and streaming</strong></summary>

```
POST   /tasks/submit
GET    /tasks/{id}
GET    /tasks
GET    /tasks/{id}/stream
GET    /tasks/{id}/partial
DELETE /tasks/{id}
```

</details>

<details>
<summary><strong>Consensus</strong></summary>

```
POST   /consensus/propose
POST   /consensus/{id}/vote
GET    /consensus/{id}
GET    /agents/{id}/consensus
DELETE /consensus/{id}
```

</details>

<details>
<summary><strong>Checkpoints and replay</strong></summary>

```
POST   /agents/{id}/checkpoint
POST   /agents/{id}/restore/{checkpoint_id}
GET    /agents/{id}/checkpoints
GET    /checkpoints/{a}/diff/{b}
POST   /checkpoints/{id}/replay
```

</details>

<details>
<summary><strong>Transactions</strong></summary>

```
POST   /txn/begin
POST   /txn/{id}/stage
POST   /txn/{id}/commit
POST   /txn/{id}/rollback
GET    /txn/{id}
```

</details>

<details>
<summary><strong>Memory</strong></summary>

```
POST   /memory/alloc
GET    /memory/read/{key}
DELETE /memory/{key}
GET    /memory
POST   /memory/compress
GET    /memory/stats
```

</details>

<details>
<summary><strong>Filesystem, shell, search</strong></summary>

```
GET    /health
GET    /state
POST   /shell

GET    /fs/list
GET    /fs/read
POST   /fs/write
POST   /fs/batch-read
GET    /fs/search
POST   /fs/read_context

POST   /semantic/search
POST   /semantic/index

POST   /ollama/chat
POST   /ollama/generate
GET    /ollama/models
```

</details>

<details>
<summary><strong>Audit, events, lineage, rate limiting</strong></summary>

```
GET    /audit
GET    /audit/stats/{id}
GET    /audit/anomalies

POST   /events/subscribe
DELETE /events/subscribe/{id}
GET    /events/history

GET    /agents/{id}/lineage
GET    /agents/{id}/subtree
GET    /agents/{id}/blast-radius

GET    /agents/{id}/rate-limits
POST   /agents/{id}/rate-limits
```

</details>

---

## MCP tools

91 tools available in Claude Code and any MCP-compatible client.

| Category | Tools |
|---|---|
| System | `state`, `state_diff`, `state_history` |
| Shell | `shell_exec` |
| Filesystem | `fs_read`, `fs_write`, `fs_list`, `fs_batch_read`, `read_context` |
| Search | `search_files`, `search_content`, `semantic_search` |
| Git | `git_status`, `git_log`, `git_diff`, `git_commit` |
| Ollama | `ollama_chat` |
| Agent OS | `agent_register`, `agent_list`, `agent_get`, `agent_spawn`, `agent_suspend`, `agent_resume`, `agent_terminate`, `agent_lock`, `agent_lock_release`, `agent_usage`, `task_submit`, `task_get`, `task_list`, `message_send`, `message_inbox`, `message_thread` |
| Session | `agent_handoff`, `agent_pickup` |
| Memory | `memory_get`, `memory_set`, `memory_alloc`, `memory_read`, `memory_free`, `memory_list`, `memory_compress`, `heap_stats` |
| Standards | `standards_set`, `standards_get`, `standards_list`, `standards_relevant`, `standards_delete` |
| Audit | `audit_query`, `audit_stats`, `anomaly_history` |
| Transactions | `txn_begin`, `txn_commit`, `txn_rollback`, `txn_status` |
| Lineage | `agent_lineage`, `agent_subtree`, `agent_blast_radius`, `task_critical_path` |
| Streaming | `task_stream` |
| Rate limiting | `rate_limit_status`, `rate_limit_configure` |
| Events | `event_subscribe`, `event_unsubscribe`, `event_history` |
| VRAM | `model_status` |

---

---

## Under the hood

The agent behavior is the interesting part. The infrastructure underneath is what makes it possible to run continuously without falling over. Each piece below is a real OS primitive implemented for multi-agent use. If you want to understand how any of it actually works, or build on top of it, this is the detail.

<details>
<summary><strong>Build history (Phase 0 through 6)</strong></summary>

**Phase 0-1: OS Kernel Primitives (v0.1.0 to v1.2.0)**

Eight foundational mechanisms. Every higher-order system depends on these. Without events, systems poll. Without signals, you can't coordinate. Without memory management, you have no state. Without audit, you can't trace failures. Without transactions, concurrent agents corrupt each other's data. Without lineage, you can't understand causality. Each primitive is small, focused, and orthogonal.

**Phase 2: Agent Services (v1.3.0 to v1.3.7)**

Services that are only possible because Phase 1 exists. Distributed tracing (needs audit + registry). Checkpoints (needs memory + transactions). Consensus (needs events + transactions). Adaptive routing (needs scheduler + audit). Self-extension (needs consensus + full stack).

**Phase 3: Cognitive Infrastructure (v2.0.0 to v2.5.0)**

Replacing every human-facing interface with agent-native cognition. Agents navigate capability graphs by meaning using vector embeddings. Memory works in embedding space. Self-extension is fully autonomous.

**Phase 4: Autonomous Agent Runtime (v3.0.0 to v4.4.0)**

The OS is complete. Now it runs. A persistent daemon cycles through agents, generates plans with a local LLM, executes multi-step pipelines with real data flowing between steps, and produces verifiable artifacts. Goals persist across restarts. Agents accumulate memory. The system governs its own capability expansion through quorum voting.

**Phase 5: App Store and Natural Language Install (v4.5.0 to v4.9.0)**

128+ tools available via natural language. Type what you want, the system finds the tool, resolves dependencies, clones the repo, synthesizes a wrapper, and launches it. Wrappers are versioned and auto-repaired when they break.

**Phase 6: Psychological Layer and HollowOS (v5.0.0 to v5.4.0)**

The suffering system, persistent agent identity, and the invoke_claude collaboration model. Plus HollowOS, a web-based graphical shell (in development).

</details>

<details>
<summary><strong>Event Kernel (v0.7.0)</strong></summary>

Polling is how you build a prototype. Interrupts are how you build a system.

Agents subscribe to typed event patterns and receive notifications in their inbox when matching events fire. Every subsystem emits events. Subscriptions support glob patterns (`task.*`, `agent.terminated`, `security.*`) and TTLs. The event log is append-only and persists across restarts.

Events emitted system-wide: `agent.registered`, `agent.terminated`, `agent.suspended`, `agent.resumed`, `budget.warning`, `budget.exhausted`, `task.queued`, `task.started`, `task.completed`, `task.failed`, `task.token_chunk`, `task.partial_available`, `task.cancelled`, `message.received`, `decision.resolved`, `spec.activated`, `file.written`, `txn.committed`, `txn.rolled_back`, `txn.conflict`, `security.anomaly`, `security.circuit_break`, `audit.archived`, `vram.pressure`, `memory.pressure`, `model.loaded`, `model.evicted`.

</details>

<details>
<summary><strong>Process Signals and Tombstones (v0.8.0)</strong></summary>

`kill()` and `terminate()` are not the same thing. SIGTERM means "shut down cleanly within this grace period." SIGPAUSE means "checkpoint and wait." SIGINFO means "report your current state."

Hollow implements all three. An agent that ignores SIGTERM is force-killed by a watchdog after the grace period. Every terminated agent writes a tombstone: last task, token usage, cause of death, list of children. Process groups let you SIGTERM an entire spawned subtree atomically. Children of a terminated agent are re-parented to root.

</details>

<details>
<summary><strong>VRAM-Aware Scheduler (v0.9.0)</strong></summary>

Loading a model takes a few seconds. If it's already in VRAM from the previous task, that cost is zero. The scheduler tracks what's loaded, routes tasks to already-loaded models where possible, and evicts LRU models under memory pressure.

Three priority tiers: URGENT (0) preempts BACKGROUND (2) workers via checkpointing. Complexity routing: 1-2 uses the smaller model, 3-4 uses a mid-size model, 5 uses the largest available. Affinity routing: if a suitable model is already in VRAM, use it regardless of the complexity tier.

</details>

<details>
<summary><strong>Working Memory Kernel (v1.0.0)</strong></summary>

Language models have no persistent working state between calls. A working memory heap gives agents a place to store intermediate results with actual memory management: TTL expiration, priority-based eviction under pressure, on-heap compression when a slot needs to shrink without being freed.

This is not a key-value store. It's a heap with an eviction policy, the same concept as any OS page frame manager, applied to agent context.

</details>

<details>
<summary><strong>Audit Kernel and Anomaly Detection (v1.1.0)</strong></summary>

Every operation goes through a single audited boundary. The log is append-only. The audit log and baseline files are blocklisted at the path level — no agent can overwrite them via the filesystem API.

Z-score anomaly detection runs per-agent against a per-role baseline established from the first 50 operations. Anomalies fire at 3 sigma. Circuit breaks fire at 5 sigma. When an agent's anomaly score exceeds that threshold, the circuit break fires: the agent is suspended, its rate limits are reduced to 10% for 5 minutes, a `security.circuit_break` event fires, and root receives a review decision in its inbox. Causal fields on every entry: `caused_by_task_id`, `parent_txn_id`, `call_depth`.

</details>

<details>
<summary><strong>Multi-Agent Transactions (v1.2.0)</strong></summary>

Two agents writing to the same file is a race condition. Transactions make it a conflict instead — detectable, handleable, not silently corrupting.

`txn_begin()` opens a transaction. `txn/stage(fs_write | message_send | memory_set)` buffers operations without applying them. `txn/commit()` applies everything atomically, detecting conflicts (file modified between begin and commit) and rolling back if any op fails. Uncommitted writes are invisible to readers. Transactions that don't commit within 60 seconds auto-roll back.

</details>

<details>
<summary><strong>Agent Lineage and Call Graphs (v1.3.0)</strong></summary>

The audit log tells you what happened. Lineage tells you why: which agent spawned which agent, which task created which agent, which agents are affected if a given agent fails right now.

`agent_lineage(id)` returns the full ancestor chain. `agent_subtree(id)` returns the recursive descendant tree with edge types (spawned, delegated, signaled, transacted). `agent_blast_radius(id)` computes forward-reachability: affected descendants, held locks, open transactions, running tasks. `task_critical_path(id)` finds the longest `depends_on` chain through the task graph — the wall time you cannot parallelize away.

</details>

<details>
<summary><strong>Streaming Task Outputs (v1.3.1)</strong></summary>

`submit(stream=True)` returns immediately with a `task_id`, `stream_url`, and `partial_url`. Token chunks arrive as SSE events. `GET /tasks/{id}/partial` returns a snapshot of accumulated output without connecting to the stream. `DELETE /tasks/{id}` cancels and frees the worker.

`submit(wait=True)` still works exactly as before.

</details>

<details>
<summary><strong>Rate Limiting and Admission Control (v1.3.2)</strong></summary>

Per-resource limits: `tokens_in`, `shell_calls`, `api_calls`, `task_submissions`. Per-role defaults with per-agent overrides. 429 responses include a `Retry-After` header.

| Role | tokens/min | shell/min | task submits/min |
|---|---|---|---|
| root | unlimited | unlimited | unlimited |
| orchestrator | 100k | 300 | 60 |
| worker | 20k | 60 | 10 |
| coder | 50k | 120 | 20 |
| reasoner | 50k | 10 | 5 |

Circuit breaker fires at 5 sigma anomaly score: agent suspended, rate limits cut to 10% for 5 minutes, event fires, root gets a review decision with options `["restore", "terminate"]`.

</details>

<details>
<summary><strong>Checkpoints and Replay (v1.3.3)</strong></summary>

Checkpoints serialize everything: memory heap contents, unread inbox messages, current task snapshot, and agent metadata. Restore overwrites the current state with the saved snapshot. The agent resumes as if the interruption never happened.

Three auto-checkpoint triggers: before transaction commit, on SIGPAUSE, and after tasks over 30 seconds.

Replay runs a task N times from the same checkpoint and measures response consistency (Jaccard similarity across runs). A factual question should score above 0.95 across 5 runs. An ambiguous question will score lower and produce a `divergence_points` list showing where runs first diverged. This is the foundation for measuring agent determinism.

</details>

<details>
<summary><strong>Multi-Agent Consensus (v1.3.4)</strong></summary>

One agent reaching a conclusion is a decision. Multiple independent agents reaching the same conclusion is a commitment that survives the failure or compromise of any single participant.

A proposer submits an action with a list of participants and a required vote count. Participants receive a `consensus.vote_requested` event and vote. Early rejection is computed: if the remaining uncast votes cannot mathematically close the gap, the proposal is rejected immediately rather than waiting for the TTL. This prevents cascading delays in time-sensitive pipelines.

Consensus is a coordination mechanism, not an executor. `consensus.reached` carries the action dict; the proposer acts on it.

</details>

<details>
<summary><strong>Adaptive Model Routing (v1.3.5)</strong></summary>

The adaptive router observes every task completion — model, complexity, duration_ms, tokens_out, success — and maintains exponential moving averages (EMA, alpha=0.15) per (model, complexity) pair. The composite score weights success rate highest (50%), then throughput (30%), then latency (20%).

Routing decision hierarchy:
1. Hard override — admin-set rules that bypass scoring entirely
2. Adaptive score — highest-scoring model with at least 5 observations for this complexity tier
3. VRAM affinity — prefer already-loaded model to avoid eviction cost
4. Static tier default

Overrides resolve by specificity: agent_id beats role beats complexity-only beats global.

</details>

<details>
<summary><strong>Vector Embeddings and Semantic Memory (v2.x)</strong></summary>

Agents navigate capabilities by meaning, not by name. When an agent needs to do something, the capability graph returns semantically similar capabilities using cosine similarity over nomic-embed-text embeddings. This means agents can discover relevant tools without knowing their exact names.

Semantic memory stores per-agent experience: after each successful goal step, a summary is embedded and stored. Future goals retrieve relevant past experiences at planning time, injecting them into context so agents can build on prior work and avoid repeating mistakes.

The workspace is continuously indexed: every file in `/agentOS/workspace/` is chunked with an AST-aware splitter and embedded. `semantic_search` and `read_context` return chunks ranked by cosine similarity to the query, not by filename. This is how agents find relevant code across a large workspace without scanning every file.

</details>

<details>
<summary><strong>Benchmark Suite (v1.3.6)</strong></summary>

Seven structural scenarios that don't require Ollama:

| Scenario | What it measures |
|---|---|
| `heap_alloc_throughput` | Alloc/free ops/sec against the working memory kernel |
| `message_bus_latency` | Send to receive p50/p95/p99/mean round-trip (ms) |
| `transaction_commit_latency` | begin, stage x3, commit round-trip (ms) |
| `checkpoint_roundtrip` | save, restore, verify round-trip (ms) |
| `consensus_vote_latency` | propose, vote, resolved wall time (ms) |
| `rate_limit_precision` | Verify 429 fires at correct bucket depth |
| `audit_write_throughput` | Entries captured per second in audit log |

Two Ollama-dependent scenarios (`task_latency_c1`, `task_latency_c3`) measure end-to-end task latency at each complexity tier. `GET /benchmarks/compare` diffs any two runs and flags regressions (>15% degradation) and improvements (>15% gain).

Selected numbers from live system runs:

| Scenario | Naive shell approach | Hollow API | Savings |
|---|---|---|---|
| Code search | 21,636 tokens | 987 tokens | 95% |
| Agent drift (consistency rate) | 35% (cold start) | 70% (with handoff) | 2x |

</details>

<details>
<summary><strong>Architecture</strong></summary>

```
hollow-agentOS/
├── api/
│   ├── server.py              FastAPI, all endpoints
│   └── agent_routes.py        Agent OS routes
├── agents/
│   ├── daemon.py              Main loop, existence prompts, stall detection
│   ├── autonomy_loop.py       plan, execute, 5-layer validate, txn commit/rollback
│   ├── live_capabilities.py   ~30 live capabilities, hot-mounted
│   ├── lessons.py             Durable operational knowledge, candidate-promotion
│   ├── suffering.py           Stressors, mechanical capability locks, prompt injection
│   ├── reasoning_layer.py     Ollama-based planning and capability selection
│   ├── capability_graph.py    Semantic capability discovery
│   ├── execution_engine.py    Runs capabilities, passes results between steps
│   ├── persistent_goal.py     Goal storage that survives restarts
│   ├── semantic_memory.py     Per-agent vector memory with cosine search
│   ├── self_modification.py   Synthesize, test, hot-load new capabilities
│   ├── registry.py            Identity, capabilities, workspaces, budgets, locks
│   ├── bus.py                 Inter-agent message bus
│   ├── scheduler.py           VRAM-aware routing, priority preemption
│   ├── events.py              EventBus, glob patterns, TTL, persistent log
│   ├── signals.py             SIGTERM, SIGPAUSE, SIGINFO with grace period watchdog
│   ├── audit.py               Append-only log, z-score detection, circuit break
│   ├── transaction.py         Atomic multi-op transactions, conflict detection
│   ├── lineage.py             Call graph, blast radius, critical path
│   ├── ratelimit.py           Token bucket rate limiting, circuit breaker
│   ├── checkpoint.py          Save, restore, diff, replay agent state
│   ├── consensus.py           Propose, vote, quorum, early rejection
│   ├── adaptive_router.py     EMA tracking, score-based routing, overrides
│   ├── benchmark.py           Benchmark suite
│   └── model_manager.py       VRAM tracker, LRU eviction, model affinity
├── memory/
│   ├── manager.py             Session log, workspace map, handoffs
│   └── heap.py                Working memory kernel
├── mcp/
│   └── server.py              91 MCP tools
├── tools/
│   ├── semantic.py            AST-aware chunker and embedding search
│   └── dynamic/               Hot-loaded capabilities synthesized at runtime
├── store/
│   └── server.py              Tool store, 128+ tools
├── design/                    Agent design space (writable by agents)
├── Dockerfile
├── docker-compose.yml
├── stop.bat                   One-click shutdown and VRAM clear
├── launch.bat                 One-click resume
└── config.json
```

</details>
