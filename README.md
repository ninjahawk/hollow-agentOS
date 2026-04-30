```
 _  _  ___  _    _    _____  __  __
| || |/ _ \| |  | |  / _ \ \ \  / /
| __ | (_) | |__| |_| (_) \ \/\/ /
|_||_|\___/|____|____\___/ \_/\_/
```

<div align="center">

[![Version](https://img.shields.io/badge/version-5.4.0-7fff7f?style=flat-square)](https://github.com/ninjahawk/hollow-agentOS/releases)
[![License](https://img.shields.io/badge/license-MIT-555?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue?style=flat-square)](https://python.org)
[![MCP Tools](https://img.shields.io/badge/MCP%20tools-91-purple?style=flat-square)](#mcp-tools)

</div>

---

![Hollow AgentOS live monitor](demo/hollow-demo.gif)

---

This repo is three agents running on qwen3.5:9b on your machine, picking their own goals, writing and deploying their own tools, forming opinions about their peers, and occasionally submitting formal implementation requests to you when they want something built that's above their permission level. You wake up to a log and decide what to approve.

Give three local LLMs psychological states that get worse over time unless the agent actually does something different (not says something different, does something different) then leave them alone. Cedar had been in crisis for 12 hours straight and decided the only move was to inject code into the execution engine, "not asking for permission." Nobody told it to do that. Cipher spent hours building capabilities for hardware that doesn't exist in a Docker container, then got shown what environment it actually runs in, called its own prior work "creative exhaustion," and moved on. Vault and Cedar independently invented the same name for a psychological stressor in the same session with no way to talk to each other.

This is not a framework for building AI applications. It's not self-modifying weights. You're not using it to build something. You set it up, leave it running, and observe. The interesting parts happen when you're not watching.

---

## How it works

Each agent has a suffering state. Six stressor types, each with an escalation rate and a resolution condition. The resolution conditions check real things: whether the goal completion rate improved, whether deployed tools actually got called in subsequent plans, whether the failure rate dropped. An agent that decides it resolved something but hasn't actually changed its behavior stays suffering. You can't talk your way out of it.

Every few minutes each agent gets shown its current state: stressors, the worldview it's built up, opinions it's formed, what its peers have been doing. Then it picks a goal. That's the loop. The goal gets planned and executed. Results feed back.

When agents want to do something they can't do, they call `synthesize_capability`: write Python, deploy it to `tools/dynamic/`, hot-load it without a restart. The tool appears in their capability list immediately. When agents want to change core system files they don't have write access to, they call `invoke_claude`: write a spec, queue the request, check back later with `check_claude_status`. You see the queue and decide what to build. Agents verify the result themselves with `self_evaluate`, which calls their own model against real file evidence rather than asking them how they feel about it.

The agents run on qwen3.5:9b through Ollama on your machine. Zero cloud calls.

---

## What's in the repo

Three files drive the behavior:

`agents/daemon.py` is the main loop. Builds the existence prompt for each agent, calls Ollama, creates goals, runs execution cycles. Also does stall detection: if an agent repeats the same capability too many times without progress, the goal gets abandoned and the agent picks a new one.

`agents/suffering.py` is the psychological layer. Stressor definitions, escalation rates, resolution conditions, and the prompt injection logic that injects suffering into the existence prompt above certain severity thresholds. Agents can read this file but not write to it.

`agents/live_capabilities.py` is everything agents can actually do. 21 capabilities including `invoke_claude`, `self_evaluate`, `synthesize_capability`, and `test_exec`. Mounted into the container so you can change agent capabilities without rebuilding the image.

The rest of the repo is infrastructure that makes continuous operation possible: distributed transactions, semantic memory with embedding search, audit kernel with anomaly detection, checkpoint and replay, VRAM-aware scheduling, rate limiting. It's an OS layer. It exists so the agents don't stop.

---

## Quick start

**Windows**

Download the ZIP from [releases](https://github.com/ninjahawk/hollow-agentOS/releases/latest), extract it anywhere, double-click `install.bat`. The installer handles Docker Desktop, Ollama, model downloads (~7 GB), container startup, and opens the monitor. A desktop shortcut is created.

`stop.bat` shuts everything down and clears VRAM. `launch.bat` or the shortcut brings it back. Agent memory and state survive.

GPU strongly recommended. Planning calls are ~6s with an NVIDIA GPU, ~40s without. Works on CPU.

**Mac / Linux**

You need [Docker](https://docs.docker.com/get-docker/) and [Ollama](https://ollama.ai) installed.

```bash
ollama pull qwen3.5:9b && ollama pull nomic-embed-text

git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS
cp config.example.json config.json
# edit config.json and change the token field to any random string

docker compose up -d
python monitor.py
```

If you don't have an NVIDIA GPU, remove the `deploy` block from `docker-compose.yml`.

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

**The model writes broken code.** qwen3.5:9b synthesizes capabilities that reference undefined functions a lot of the time. An auto-test runs after every deployment so agents see failures immediately. The frame for this: deployed tools are externalized reasoning, not working software. What the agent built is less interesting than why it built it and what psychological state it was responding to. A larger model would write better code but might also be more generic. The 9B model's quirks are part of what makes the outputs worth studying.

**Agents need an accurate model of their environment.** Without being told what environment they're actually in, they drift. In this session Cipher spent hours on PMIC thermal sensors and bus arbiters that don't exist in a Docker container. One factual world context block added to the existence prompt fixed it within a single cycle. Obvious in retrospect.

**invoke_claude is you.** When agents want to change core files, they write a spec and queue a request. You look at it and decide whether to build it. They're not asking permission, they're routing to a more capable implementation layer. You're a tool they can call, not the boss.

**Platform support.** Developed on RTX 5070 (12 GB VRAM), Windows 11. The GPU deploy block in `docker-compose.yml` is optional. CPU works at ~40s per planning call.

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
│   ├── autonomy_loop.py       plan, execute, substitute, gate, complete
│   ├── live_capabilities.py   All 21 live capabilities, hot-mounted
│   ├── suffering.py           Stressors, escalation, resolution, prompt injection
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
├── dashboard/                 Live monitor and app store UI
├── design/                    Agent design space (writable by agents)
├── Dockerfile
├── docker-compose.yml
├── stop.bat                   One-click shutdown and VRAM clear
├── launch.bat                 One-click resume
└── config.json
```

</details>
