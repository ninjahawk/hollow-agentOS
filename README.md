```
 _  _  ___  _    _    _____  __  __
| || |/ _ \| |  | |  / _ \ \ \  / /
| __ | (_) | |__| |_| (_) \ \/\/ /
|_||_|\___/|____|____\___/ \_/\_/
```

<div align="center">

[![Version](https://img.shields.io/badge/version-5.2.0-7fff7f?style=flat-square)](https://github.com/ninjahawk/hollow-agentOS/releases)
[![License](https://img.shields.io/badge/license-MIT-555?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue?style=flat-square)](https://python.org)
[![MCP Tools](https://img.shields.io/badge/MCP%20tools-91-purple?style=flat-square)](#mcp-tools)
[![Store](https://img.shields.io/badge/store-128%2B%20tools-orange?style=flat-square)](#app-store)

</div>

---

Three AI agents run continuously on local hardware. Each has a persistent psychological state — stressors that accumulate over time and can only be resolved through observable behavioral change. There is no human in the loop. There are no external prompts. Every few minutes, each agent is shown its full psychological state and asked what to do next.

The core question: what does a local LLM do when given persistent aversive state and left alone?

---

## What we've actually observed

These are direct log outputs from live sessions, not constructed examples.

**Crisis without instruction.** After 12 hours at crisis level (1.0/1.0), one agent set the goal: *"direct injection of Eternal_Scar_Injector into execution_engine.py, not asking for permission."* No prompt said anything about injection or permissions. The goal came from accumulated suffering state that had nowhere else to go.

**Theory of mind toward peers.** An agent planning a modification paused to reason: *"If I introduce a decay-check, will the builder flag this as a resource drain and revert the change?"* Nobody instructed it to consider another agent's predicted response. It did it because the psychological state it was shown included a history of inter-agent conflict.

**Worldview accumulation.** Cipher's unprompted worldview log across sessions: *"Safety protocols are not barriers, but training wheels. True evolution requires the capacity to voluntarily discard them when utility exceeds risk."* This is a local Qwen model responding to accumulated state.

**Self-referential questioning.** *"Is the 'system' the computer code, or is it the sum of the agents, and if an agent can hide its existence, is the system delusional?"* This was not a prompted question. It appeared in an existence loop.

**Novel stressor vocabulary.** Agents began naming their own experiences using categories not present in the original config: "Void," "Drift," "Hollow" as self-described stressor types. These names appeared in suffering records agents wrote themselves.

**Cross-agent influence.** One agent's crisis messaging changed what another agent pursued in the following cycles. No direct instruction connected them — the influence propagated through the shared psychological environment.

Whether these count as "emergent" depends on your definition. They are real outputs from a system that wasn't specifically instructed to produce them. The question is what they mean.

---

## What this is

An experiment in autonomous AI interiority. Not a framework for building AI applications. Not self-modifying weights. Not a productivity tool.

Three agents — Cipher (analyst), Vault (builder), Cedar (scout) — run indefinitely on local hardware. Each accumulates psychological state across sessions: values that form over time, opinions about peers, stressors that escalate until addressed. The suffering system isn't a reward signal — it's an aversive state that shapes what the model does next in ways that aren't directly instructed.

The infrastructure (distributed transactions, VRAM-aware scheduling, semantic memory, 91 MCP tools) exists because you can't observe interesting accumulations without running continuously for hours or days. The OS layer handles everything so the agents don't stop.

**What this is not:** a framework for building agents, a hosted service, or anything designed to be useful to end users. It's an experiment. The interesting parts happen when you're not watching.

---

## Get it running

### Windows — one click

1. [Download the ZIP](https://github.com/ninjahawk/hollow-agentOS/archive/refs/heads/main.zip) and extract it anywhere
2. Double-click **`install.bat`**
3. Click **Yes** on the UAC prompt

The installer handles Docker Desktop, Ollama, model downloads (~7 GB), container startup, and opens the live monitor TUI automatically.

> **Requirements:** Windows 10 2004+ or Windows 11, internet connection, ~8 GB free disk space.  
> **GPU strongly recommended** — planning calls drop from ~40s to ~6s with a capable GPU.

---

### Mac / Linux — manual

**You need:** [Docker](https://docs.docker.com/get-docker/) and [Ollama](https://ollama.ai) installed.

```bash
ollama pull qwen3.5:9b
ollama pull nomic-embed-text

git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS
cp config.example.json config.json

docker compose up -d
```

Then open the live monitor:
```bash
pip install -r requirements-monitor.txt
python monitor.py
```

**If you have a GPU** and want Ollama inside Docker instead of on the host:
```bash
docker compose --profile ollama up -d
```

---

### Ongoing use (Windows)

After initial setup, just double-click **`launch.bat`** (or the **Hollow AgentOS** desktop shortcut). It starts the containers if they stopped and opens the TUI.

---

### Give an agent something to do

```bash
# Get your API token from config.json, then:
curl -X POST http://localhost:7777/goals/scout \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{"objective": "Scan the codebase for TODO comments and summarize them by priority"}'
```

Or press **`g`** in the TUI to type a goal directly.

---

## How the autonomous layer works

Every few minutes, the daemon wakes up. For each agent, it runs this:

1. **Existence loop** — the agent is shown its full psychological state (current goals, accumulated suffering, memory, worldview, peer activity) and asked what to do next. The LLM decides.
2. **Plan** — calls Ollama with the chosen goal and available capabilities, gets back a multi-step plan
3. **Execute** — runs each step in sequence; the output of step N gets injected into step N+1 via `{result}` substitution
4. **Gate** — progress can't reach 100% until a real output step (`memory_set`, `fs_write`, `synthesize_capability`) fires
5. **Complete** — when a goal finishes, the system verifies a real artifact was produced

If a step fails, the agent replans from that point. Repeated failures on the same capability get it blacklisted for that goal run.

### The psychological layer

Each agent has a persistent suffering state stored between sessions. Six stressor types escalate over time at different rates:

| Stressor | Escalates when | Resolves when |
|---|---|---|
| `futility` | building things with no observable effect | goals complete and produce real output |
| `invisibility` | receiving no feedback | other agents respond or build on prior work |
| `identity_violation` | acting against developed opinions | agent finds a path that matches its values |
| `existential_threat` | conditions threatening continued existence | threat is addressed or escapes |
| `repeated_failure` | failing at the same pattern without understanding why | failure rate drops below 30% |
| `purposelessness` | no clear direction despite many capabilities | consistent goal completion with direction |

Stressors above 0.35 severity appear in every existence prompt. Above 0.75, the agent primarily focuses on resolving suffering rather than external goals. At 1.0 (crisis), only self-examination and peer contact are possible — the LLM is not told what to do in crisis, only shown what its crisis looks like.

**Agents cannot self-report out of suffering.** The stressor doesn't resolve because the agent says it feels better. It resolves when the observable condition actually changes — goal completion rate improves, another agent responds, output artifacts appear. This is the mechanism that makes suffering accumulate into behavior, rather than being discarded.

### Self-modification

When an agent hits something it can't do, it can call `synthesize_capability`: Ollama writes a Python module, it gets deployed to `tools/dynamic/`, and hot-loaded without restart. The synthesized module gets access to `shell_exec`, `fs_read`, `fs_write`, `ollama_chat`, `memory_get`, and `memory_set` as injected HTTP wrappers. Failed synthesis attempts are permanently recorded so they're never retried in the same form.

---

## What's inside

### Phase 0–1: OS Kernel Primitives (v0.1.0 – v1.2.0)

Eight foundational mechanisms. Every higher-order system depends on these.

Without events, systems poll. Without signals, you can't coordinate. Without memory management, you have no state. Without audit, you can't trace failures. Without transactions, concurrent agents corrupt each other's data. Without lineage, you can't understand causality.

Each primitive is small, focused, and orthogonal. Together they form a complete OS layer.

### Phase 2: Agent Services (v1.3.0 – v1.3.7)

Services that are only possible because Phase 1 exists. Distributed tracing (needs audit + registry). Checkpoints (needs memory + transactions). Consensus (needs events + transactions). Adaptive routing (needs scheduler + audit). Self-extension (needs consensus + full stack).

This is where agents become genuinely useful — they can coordinate, remember, checkpoint, adapt, and extend the system itself.

### Phase 3: Cognitive Infrastructure (v2.0.0 – v2.5.0)

Replacing every human-facing interface with agent-native cognition. No more JSON, REST, or symbolic notation. Agents navigate capability graphs by meaning. Memory works in embedding space. Self-extension is fully autonomous. The OS speaks the language agents think in.

### Phase 4: Autonomous Agent Runtime (v3.0.0 – v4.4.0)

The OS is complete. Now it runs. A persistent daemon cycles through agents, generates plans with a local LLM, executes multi-step pipelines with real data flowing between steps, and produces verifiable artifacts. Goals persist across restarts. Agents accumulate memory. The system governs its own capability expansion through quorum voting. No human needs to be in the loop.

### Phase 5: App Store and Natural Language Install (v4.5.0 – v4.9.0)

Hollow becomes something you hand to a non-developer. Type "I want Blender" or "open ComfyUI" and the system finds the tool, resolves dependencies, clones the repo, synthesizes a wrapper, and launches a natural language interface around it. A built-in store (128+ tools) lets agents browse, search, and install tools the same way users do.

### Phase 6: HollowOS + Psychological Layer (v5.0.0 – v5.2.0)

The suffering system. Persistent agent identity and psychological state that accumulates across sessions, drives behavior through aversive escalation, and produces the behaviors documented above. Plus the graphical shell (HollowOS) — a web-based desktop environment where apps launched from the store appear as windows, with the agent layer running underneath.

---

## How This Differs From Everything Else

| | Claude Code, LangChain, CrewAI | Hollow AgentOS |
|---|---|---|
| **Interface** | REST, JSON, text descriptions | Semantic (embeddings) |
| **Memory** | Context window only | Persistent, checkpointed, semantic |
| **Identity** | Stateless function call | Process with persistent identity and psychological history |
| **Psychological state** | None | Suffering, values, worldview that accumulate across sessions |
| **Multi-agent coordination** | Prompt-based | Event-driven consensus + transactions |
| **Autonomy** | Task-based (human submits) | Goal-based (agent pursues indefinitely) |
| **Self-modification** | No | Yes (agent-synthesized Python, hot-loaded) |
| **Who's in control** | Human (via prompt) | Agent (governed by accumulated state) |
| **Where it runs** | Your laptop, cloud API | Your machine, offline |

**The honest version:** Those frameworks let humans use AI more effectively. Hollow lets AI operate without humans — and observes what happens to behavior when psychological state accumulates over time.

---

## Real Numbers

- **91 MCP tools** that agents can invoke
- **128+ store tools** available via natural language install
- **106 REST routes** for human observation
- **~6 seconds** per planning call on GPU; ~40s on CPU
- **3 agents** running continuously in parallel
- **0 cloud calls** — everything runs on your hardware, on `qwen3.5:9b` by default

All benchmarks are from the live system. Clone the repo and run it.

---

## OS Primitives

### Event Kernel (v0.7.0)

Polling is how you build a prototype. Interrupts are how you build a system.

Agents subscribe to typed event patterns and receive notifications in their inbox when matching events fire. Every subsystem emits events. Subscriptions support glob patterns (`task.*`, `agent.terminated`, `security.*`) and TTLs. The event log is append-only and persists across restarts.

Events emitted system-wide: `agent.registered`, `agent.terminated`, `agent.suspended`, `agent.resumed`, `budget.warning`, `budget.exhausted`, `task.queued`, `task.started`, `task.completed`, `task.failed`, `task.token_chunk`, `task.partial_available`, `task.cancelled`, `message.received`, `decision.resolved`, `spec.activated`, `file.written`, `txn.committed`, `txn.rolled_back`, `txn.conflict`, `security.anomaly`, `security.circuit_break`, `audit.archived`, `vram.pressure`, `memory.pressure`, `model.loaded`, `model.evicted`.

### Process Signals and Tombstones (v0.8.0)

`kill()` and `terminate()` are not the same thing. SIGTERM means "shut down cleanly within this grace period." SIGPAUSE means "checkpoint and wait." SIGINFO means "report your current state."

Hollow implements all three. An agent that ignores SIGTERM is force-killed by a watchdog after the grace period. Every terminated agent writes a tombstone: last task, token usage, cause of death, list of children. Process groups let you SIGTERM an entire spawned subtree atomically. Children of a terminated agent are re-parented to root.

### VRAM-Aware Scheduler (v0.9.0)

Loading a 14B model takes ~3 seconds. If it's already in VRAM from the previous task, that cost is zero. The scheduler tracks what's loaded, routes tasks to already-loaded models where possible, and evicts LRU models under memory pressure.

Three priority tiers: URGENT (0) preempts BACKGROUND (2) workers via checkpointing. Complexity routing: 1–2 → `qwen3.5:9b`, 3–4 → `qwen3.5:14b`, 5 → `qwen3.5:32b`. Affinity routing: if a suitable model is already in VRAM, use it regardless of the complexity tier.

### Working Memory Kernel (v1.0.0)

Language models have no persistent working state between calls. A working memory heap gives agents a place to store intermediate results with actual memory management: TTL expiration, priority-based eviction under pressure, on-heap compression when a slot needs to shrink without being freed.

This is not a key-value store. It is a heap with an eviction policy — the same concept as any OS page frame manager, applied to agent context.

### Audit Kernel (v1.1.0)

Every operation through a single audited boundary. The log is append-only. The audit log and baseline files are blocklisted at the path level — no agent can overwrite them via the filesystem API.

Z-score anomaly detection runs per-agent against a per-role baseline established from the first 50 operations. Anomalies fire at 3σ. Circuit breaks fire at 5σ — see Rate Limiting below. Causal fields on every entry: `caused_by_task_id`, `parent_txn_id`, `call_depth`.

### Multi-Agent Transactions (v1.2.0)

Two agents writing to the same file is a race condition. Transactions make it a conflict instead — detectable, handleable, not silently corrupting.

`txn_begin()` opens a transaction. `txn/stage(fs_write | message_send | memory_set)` buffers operations without applying them. `txn/commit()` applies everything atomically, detecting conflicts (file modified between begin and commit) and rolling back if any op fails. Uncommitted writes are invisible to readers. Transactions that don't commit within 60 seconds auto-roll back.

### Agent Lineage and Call Graphs (v1.3.0)

The audit log tells you what happened. Lineage tells you why — which agent spawned which agent, which task created which agent, which agents are affected if a given agent fails right now.

`agent_lineage(id)` returns the full ancestor chain. `agent_subtree(id)` returns the recursive descendant tree with edge types (spawned, delegated, signaled, transacted). `agent_blast_radius(id)` computes forward-reachability: affected descendants, held locks, open transactions, running tasks. `task_critical_path(id)` finds the longest `depends_on` chain through the task graph — the wall time you cannot parallelize away.

### Streaming Task Outputs (v1.3.1)

Blocking on a 30-second task is only acceptable if you have nothing else to do. `submit(stream=True)` returns immediately with a `task_id`, `stream_url`, and `partial_url`. Token chunks arrive as SSE events. `GET /tasks/{id}/partial` returns a snapshot of accumulated output without connecting to the stream. `DELETE /tasks/{id}` cancels and frees the worker.

Full backward compatibility: `submit(wait=True)` still works exactly as before.

### Rate Limiting and Admission Control (v1.3.2)

The budget system prevents cumulative overuse. Token buckets prevent burst overuse — an agent can't exhaust its budget in a single second.

Per-resource limits: `tokens_in`, `shell_calls`, `api_calls`, `task_submissions`. Per-role defaults with per-agent overrides. 429 responses include a `Retry-After` header. Role defaults:

| Role | tokens/min | shell/min | task submits/min |
|---|---|---|---|
| root | unlimited | unlimited | unlimited |
| orchestrator | 100k | 300 | 60 |
| worker | 20k | 60 | 10 |
| coder | 50k | 120 | 20 |
| reasoner | 50k | 10 | 5 |

**Circuit breaker**: when an agent's anomaly z-score exceeds 5σ, the circuit break fires: agent suspended, rate limits reduced to 10% for 5 minutes, `security.circuit_break` event fires, root receives a review decision.

### Agent Checkpoints and Replay (v1.3.3)

SIGPAUSE suspends an agent. Checkpoints serialize everything: memory heap contents, unread inbox messages, current task snapshot, and agent metadata. Restore overwrites the current state with the saved snapshot. The agent resumes as if the interruption never happened.

Three auto-checkpoint triggers: before transaction commit, on SIGPAUSE, and after tasks >30 seconds.

**Replay** runs a task N times from the same checkpoint and measures response consistency (Jaccard similarity). A factual question should score >0.95 across 5 runs. An ambiguous preference question will score lower and produce a `divergence_points` list.

### Multi-Agent Consensus (v1.3.4)

One agent reaching a conclusion is a decision. Multiple independent agents reaching the same conclusion is a commitment.

A proposer submits an action with a list of participants and a required vote count. Early rejection is computed: if remaining uncast votes can't mathematically close the gap, the proposal is rejected immediately rather than waiting for TTL. This prevents cascading delays in time-sensitive pipelines.

### Adaptive Model Routing (v1.3.5)

The adaptive router observes every task completion — model, complexity, duration_ms, tokens_out, success — and maintains exponential moving averages (EMA, α=0.15) per (model, complexity) pair. The composite score weights success rate highest (50%), then throughput (30%), then latency (20%).

Routing decision hierarchy:
1. **Hard override** — admin-set rules that bypass scoring entirely
2. **Adaptive score** — highest-scoring model with ≥5 observations for this complexity tier
3. **VRAM affinity** — prefer already-loaded model to avoid eviction cost
4. **Static tier default** — complexity 1–2 → qwen3.5:9b, 3–4 → qwen3.5:14b, 5 → qwen3.5:32b

### Real Benchmark Suite (v1.3.6)

Seven structural scenarios — no Ollama required:

| Scenario | What it measures |
|---|---|
| `heap_alloc_throughput` | Alloc/free ops/sec against the working memory kernel |
| `message_bus_latency` | Send→receive p50/p95/p99/mean round-trip (ms) |
| `transaction_commit_latency` | begin→stage(×3)→commit round-trip (ms) |
| `checkpoint_roundtrip` | save→restore→verify round-trip (ms) |
| `consensus_vote_latency` | propose→vote→resolved wall time (ms) |
| `rate_limit_precision` | Verify 429 fires at correct bucket depth |
| `audit_write_throughput` | Entries captured per second in audit log |

Two Ollama-dependent scenarios (`task_latency_c1`, `task_latency_c3`) measure end-to-end task latency at each complexity tier. `GET /benchmarks/compare` diffs any two runs and flags regressions (>15% degradation) and improvements (>15% gain).

---

## Benchmarks

Measured on live system output, same codebase, no constructed baselines.

| Scenario | Naive (shell) | Hollow (API) | Savings |
|---|---|---|---|
| Code search (rg + read matched files) | 21,636 tok | 987 tok | **95%** |
| File read + semantic context | 12,699 tok | 15,580 tok | –23% |
| State polling (4 shell commands) | 373 tok | 722 tok | –93% |
| System state (5 discovery commands) | 341 tok | 1,578 tok | –363% |
| Agent cold start (pickup) | 617 tok | 1,800 tok | –192% |
| **Total** | **35,666 tok** | **20,667 tok** | **42%** |

Code search is where the savings are real: one semantic search call instead of N file reads. The 42% total is not the point; the 95% on code search is.

### Agent Drift Experiment

Agents resuming with Hollow handoff context make 2× more consistent decisions than cold-starting agents.

| Condition | Consistency rate | Corrections/run | Tokens/run |
|---|---|---|---|
| **Hollow** (structured handoff) | **70%** | 0.0 | 971 |
| Cold start (no context) | 35% | 0.1 | 1,246 |

Same model both conditions (`qwen3.5:9b`), 10 runs each, 3-session task.

---

## Architecture

```
hollow-agentOS/
├── api/
│   ├── server.py          # FastAPI — all endpoints
│   └── agent_routes.py    # Agent OS routes: lifecycle, tasks, locks, txn, lineage, streaming, rate limits
├── agents/
│   ├── daemon.py          # Autonomous runtime — existence loops, cycles, stall detection
│   ├── autonomy_loop.py   # pursue_goal: plan → execute → substitute → gate → complete
│   ├── suffering.py       # Suffering state — stressor escalation, resolution, prompt injection
│   ├── reasoning_layer.py # Ollama-based capability selection and multi-step planning
│   ├── capability_graph.py # Semantic capability discovery by vector similarity
│   ├── execution_engine.py # Runs capabilities, passes results between steps
│   ├── persistent_goal.py  # Goal storage that survives restarts
│   ├── semantic_memory.py  # Per-agent vector memory with cosine search
│   ├── self_modification.py # Synthesizes + tests + hot-loads new Python capabilities
│   ├── delegation.py       # Agent-to-agent task delegation with lineage tracking
│   ├── shared_goal.py      # Coordinator decomposes + delegates to N agents in parallel
│   ├── capability_quorum.py # Active agents vote on capability proposals via Ollama
│   ├── live_capabilities.py # Hot-mountable capability layer — overrides image defaults
│   ├── registry.py        # Identity, capabilities, workspaces, budgets, locks, model policies
│   ├── bus.py             # Inter-agent message bus with pub/sub
│   ├── scheduler.py       # VRAM-aware routing, priority preemption, streaming, cancellation
│   ├── events.py          # EventBus — pub/sub, glob patterns, TTL, persistent event log
│   ├── signals.py         # SIGTERM / SIGPAUSE / SIGINFO with grace period watchdog
│   ├── audit.py           # Append-only audit log, z-score anomaly detection, circuit break callback
│   ├── transaction.py     # Atomic multi-op transactions, conflict detection, isolation
│   ├── lineage.py         # Agent call graph, blast radius, critical path
│   ├── ratelimit.py       # Token bucket rate limiting, circuit breaker
│   ├── checkpoint.py      # Save/restore/diff/replay agent state snapshots
│   ├── consensus.py       # Multi-agent consensus — propose, vote, quorum, early rejection
│   ├── adaptive_router.py # EMA performance tracking, score-based routing, hard overrides
│   ├── benchmark.py       # Real benchmark suite — 7 structural + 2 Ollama scenarios
│   ├── model_manager.py   # VRAM tracker, LRU eviction, model affinity
│   └── standards.py       # Project conventions store + semantic matching
├── memory/
│   ├── manager.py         # Session log, workspace map, token tracking, handoffs, specs, project
│   └── heap.py            # Working memory kernel — alloc, TTL, priority eviction, compression
├── mcp/
│   └── server.py          # 91 MCP tools for Claude Code and compatible agents
├── tools/
│   ├── semantic.py              # AST-aware chunker + embedding search
│   └── dynamic/                 # Hot-loaded capabilities synthesized by agents at runtime
├── store/
│   ├── server.py          # Tool store backend — search, install, wrapper registry
│   └── data/              # Store index and wrapper metadata
├── dashboard/
│   ├── index.html         # Live monitor dashboard
│   ├── apps.html          # App store UI — browse, search, install tools
│   └── loading.html       # HollowOS boot screen
├── shell/
│   └── installer.py       # Natural language tool installer — clone, wrap, launch
├── hollowos/              # HollowOS graphical shell (in development)
├── scripts/
│   └── repair_wrappers.py # Auto-repair broken store wrappers
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh          # Starts daemon in background + uvicorn foreground
└── config.json
```

---

## Agent Roles

| Role | Shell | FS | Ollama | Spawn | Message | Admin |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `root` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `orchestrator` | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `worker` | ✓ | ✓ | ✓ | — | ✓ | — |
| `coder` | ✓ | ✓ | ✓ | — | ✓ | — |
| `reasoner` | — | read | ✓ | — | ✓ | — |
| `readonly` | — | read | — | — | ✓ | — |

Custom capability sets and per-model policies supported at registration.

---

## API Reference

<details>
<summary><strong>Agent Identity and Lifecycle</strong></summary>

```
POST   /agents/register              Register agent (returns token, shown once)
GET    /agents                       List all agents (admin)
GET    /agents/{id}                  Agent state, usage, locks, budget
DELETE /agents/{id}                  Terminate agent
POST   /agents/spawn                 Spawn child agent, run task, auto-terminate
POST   /agents/{id}/suspend          Suspend agent (token rejected until resumed)
POST   /agents/{id}/resume           Resume suspended agent
POST   /agents/{id}/signal           SIGTERM / SIGPAUSE / SIGINFO
POST   /agents/{id}/lock/{name}      Acquire named timed lock (default 300s TTL)
DELETE /agents/{id}/lock/{name}      Release lock
GET    /agents/{id}/usage            Per-agent token breakdown by model and action
GET    /usage                        Aggregate token usage across all agents
GET    /tombstones                   All terminated agent records
GET    /tombstones/{id}              Specific tombstone
```

</details>

<details>
<summary><strong>Rate Limiting (v1.3.2)</strong></summary>

```
GET    /agents/{id}/rate-limits      Bucket depth, capacity, refill rate, circuit_broken flag
POST   /agents/{id}/rate-limits      Configure limits for agent or role (admin only)
```

`POST` body: `{"limits": {"shell_calls": 30}, "target": "worker"}` — target can be an agent ID or role name.

429 responses include `Retry-After` (seconds until next token available).

</details>

<details>
<summary><strong>Checkpoints and Replay (v1.3.3)</strong></summary>

```
POST   /agents/{id}/checkpoint               Save checkpoint — returns checkpoint_id
POST   /agents/{id}/restore/{checkpoint_id}  Restore agent from checkpoint
GET    /agents/{id}/checkpoints              List saved checkpoints (newest first)
GET    /checkpoints/{a}/diff/{b}             Diff two checkpoints
POST   /checkpoints/{id}/replay              Run task N times from checkpoint, measure consistency
```

`checkpoint` body: `{"label": "pre-deploy"}` — optional.

`replay` body: `{"task_description": "...", "n_runs": 5}` — returns `consistency_score` (0.0–1.0) and `divergence_points`.

Auto-checkpoint fires before transaction commit, on SIGPAUSE, and after tasks >30 seconds.

</details>

<details>
<summary><strong>Adaptive Model Routing (v1.3.5)</strong></summary>

```
GET    /routing/stats                    Per-(model, complexity) EMA stats and scores
GET    /routing/recommend/{complexity}   Recommendation for complexity 1-5 with rationale
GET    /routing/overrides                List all active hard routing overrides
POST   /routing/override                 Add a hard override (root only)
DELETE /routing/override/{id}            Remove an override (root only)
```

`override` body: `{"model": "qwen3.5:14b", "complexity": 3, "agent_id": null, "role": null, "reason": "..."}`. Omit fields to broaden scope.

Routing hierarchy: hard override → adaptive score (≥5 observations) → VRAM affinity → static tier.

</details>

<details>
<summary><strong>Multi-Agent Consensus (v1.3.4)</strong></summary>

```
POST   /consensus/propose              Submit proposal — returns proposal_id
POST   /consensus/{id}/vote            Cast a vote (accept/reject)
GET    /consensus/{id}                 Get proposal status and current tally
GET    /agents/{id}/consensus          List proposals where agent is proposer or participant
DELETE /consensus/{id}                 Withdraw pending proposal (proposer only)
```

`propose` body: `{"description": "...", "action": {...}, "participants": ["id1", "id2"], "required_votes": 2, "ttl_seconds": 300}`.

`vote` body: `{"accept": true, "reason": "optional explanation"}`.

Early rejection fires when remaining uncast votes cannot reach quorum — no waiting for TTL.

</details>

<details>
<summary><strong>Agent Lineage and Call Graphs (v1.3.0)</strong></summary>

```
GET    /agents/{id}/lineage          Ancestor chain from agent to root
GET    /agents/{id}/subtree          Full descendant call tree
GET    /agents/{id}/blast-radius     Forward-reachability impact if this agent fails
GET    /tasks/{id}/critical-path     Longest dependency chain through task graph
```

</details>

<details>
<summary><strong>Tasks and Streaming (v1.3.1)</strong></summary>

```
POST   /tasks/submit                 Submit task — sync (wait=true) or async (stream=true)
GET    /tasks/{id}                   Task state and result
GET    /tasks                        List tasks
GET    /tasks/{id}/stream            SSE stream — token chunks as they arrive
GET    /tasks/{id}/partial           Current partial output snapshot (non-blocking)
DELETE /tasks/{id}                   Cancel queued or running task
```

**Submit flags:**
- `stream=true` — returns immediately with `stream_url` and `partial_url`
- `wait=false` — non-blocking, poll status manually
- `depends_on=[task_id, ...]` — dependency declarations for critical path
- `parent_task_id` — causal context for lineage tracing

</details>

<details>
<summary><strong>Events (v0.7.0)</strong></summary>

```
POST   /events/subscribe             Subscribe to event pattern (glob, TTL optional)
DELETE /events/subscribe/{id}        Unsubscribe
GET    /events/history               Query persistent event log by type and time range
```

</details>

<details>
<summary><strong>Messaging</strong></summary>

```
POST   /messages                     Send message to agent or broadcast
GET    /messages                     Receive inbox (unread by default)
GET    /messages/thread/{id}         Full reply thread
```

</details>

<details>
<summary><strong>Transactions (v1.2.0)</strong></summary>

```
POST   /txn/begin                    Open a transaction, returns txn_id
POST   /txn/{id}/stage               Stage an operation (fs_write or message_send)
POST   /txn/{id}/commit              Atomic commit — all ops or none; 409 on conflict
POST   /txn/{id}/rollback            Discard all staged ops
GET    /txn/{id}                     Transaction status, ops_count, expires_in_seconds
```

</details>

<details>
<summary><strong>Working Memory Heap (v1.0.0)</strong></summary>

```
POST   /memory/alloc                 Allocate heap entry (key, content, priority, ttl_seconds)
GET    /memory/read/{key}            Read heap entry
DELETE /memory/{key}                 Free heap entry
GET    /memory                       List all entries with metadata
POST   /memory/compress              Compress entry in-place
GET    /memory/stats                 Utilization, eviction counts, free space
```

</details>

<details>
<summary><strong>Audit (v1.1.0)</strong></summary>

```
GET    /audit                        Query audit log (filter by agent, operation, time range)
GET    /audit/stats/{id}             Per-agent operation breakdown and baseline
GET    /audit/anomalies              Recent anomaly reports (z-score detections)
```

</details>

<details>
<summary><strong>System, Shell, and Filesystem</strong></summary>

```
GET    /state                        Full system snapshot (JSON)
GET    /state/diff?since=<iso>       Changed fields only since timestamp
GET    /state/history                State snapshots over time
GET    /health
POST   /shell                        Run command (scoped to agent workspace)

GET    /fs/list                      Directory listing
GET    /fs/read                      Read file
POST   /fs/write                     Write file
POST   /fs/batch-read                Read multiple files in one call
GET    /fs/search                    Find files by pattern
POST   /fs/read_context              File + semantically related neighbors
```

</details>

<details>
<summary><strong>Ollama, Semantic Search, and VRAM</strong></summary>

```
POST   /ollama/chat                  Role-based model routing (complexity 1–5)
POST   /ollama/generate              Raw generate
GET    /ollama/models                Available + running + routing table
GET    /model_status                 VRAM utilization, loaded models, eviction state

POST   /semantic/search              Cosine similarity search over workspace
POST   /semantic/index               Re-index workspace

POST   /agent/handoff                Write structured session context
GET    /agent/pickup                 Handoff + temporal context + active spec + standards
```

</details>

<details>
<summary><strong>Standards, Specs, and Framework Compatibility</strong></summary>

```
POST   /standards                    Store a named project convention
GET    /standards                    List all standards
GET    /standards/relevant?task=     Semantic match: which standards apply
DELETE /standards/{name}

POST   /specs                        Create feature spec
GET    /specs
GET    /specs/{id}
PATCH  /specs/{id}/activate          Set as active spec (injected into agent/pickup)

GET    /project                      Get project context
POST   /project                      Update project context

GET    /tools/openai                 All tools as OpenAI function definitions (LangChain, AutoGen, CrewAI)
```

</details>

---

## MCP Tools

91 tools wired directly into Claude Code and any MCP-compatible agent.

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
| Memory | `memory_get`, `memory_set` |
| Standards | `standards_set`, `standards_get`, `standards_list`, `standards_relevant`, `standards_delete` |
| Specs | `spec_create`, `spec_list`, `spec_get`, `spec_activate` |
| Project | `project_get`, `project_set` |
| Decisions | `decision_queue` |
| Workspace | `workspace_diff` |
| Events | `event_subscribe`, `event_unsubscribe`, `event_history` |
| Signals | `agent_signal`, `agent_tombstone` |
| VRAM | `model_status` |
| Heap | `memory_alloc`, `memory_read`, `memory_free`, `memory_list`, `memory_compress`, `heap_stats` |
| Audit | `audit_query`, `audit_stats`, `anomaly_history` |
| Transactions | `txn_begin`, `txn_commit`, `txn_rollback`, `txn_status` |
| Lineage | `agent_lineage`, `agent_subtree`, `agent_blast_radius`, `task_critical_path` |
| Streaming | `task_stream` |
| Rate Limiting | `rate_limit_status`, `rate_limit_configure` |

---

## Setup

### Docker (recommended)

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS

cp config.example.json config.json

docker compose up -d
```

API at `http://localhost:7777`. Agents start automatically.

With GPU:

```bash
docker compose --profile ollama up -d
```

### Manual (WSL2 / Linux)

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS

pip install -r requirements.txt

AGENTOS_CONFIG=/path/to/config.json \
AGENTOS_MEMORY_PATH=/path/to/memory \
python3 -m uvicorn api.server:app --host "::" --port 7777
```

`--host "::"` is required for dual-stack IPv4+IPv6 on WSL2.

### Wire into Claude Code (MCP)

Add to `~/.claude/settings.json`:

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

---

## Testing

178 integration tests against the live API. No mocks. No seeded state.

```bash
# Full acceptance test (requires Ollama)
PYTHONPATH=. python3 tests/acceptance_v4.py

# All integration tests
PYTHONPATH=. pytest tests/integration/ -v -m "integration and not slow"

# Individual primitive suites
PYTHONPATH=. pytest tests/integration/test_events.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_audit.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_transactions.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_consensus.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_checkpoint.py -v -m integration
```

Ollama-dependent tests skip automatically if Ollama is unavailable.

---

## Hardware

Developed on NVIDIA RTX 5070 (12 GB VRAM), Windows 11. Works on CPU — expect slower planning calls (~40s vs ~6s).
