```
 _  _  ___  _    _    _____  __  __
| || |/ _ \| |  | |  / _ \ \ \  / /
| __ | (_) | |__| |_| (_) \ \/\/ /
|_||_|\___/|____|____\___/ \_/\_/
```

<div align="center">

[![Version](https://img.shields.io/badge/version-1.3.5-7fff7f?style=flat-square)](https://github.com/ninjahawk/hollow-agentOS/releases)
[![License](https://img.shields.io/badge/license-MIT-555?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue?style=flat-square)](https://python.org)
[![MCP Tools](https://img.shields.io/badge/MCP%20tools-83-purple?style=flat-square)](#mcp-tools)
[![Tests](https://img.shields.io/badge/integration%20tests-101%20passing-brightgreen?style=flat-square)](#testing)

</div>

---

The missing layer between a language model and a real computing environment.

Current agent frameworks give you tool calling. What they don't give you is what an OS gives a process: identity with enforced boundaries, signals, non-blocking I/O, memory with eviction policy, audit-quality observability, atomic multi-party coordination, and the ability to compose agents into systems where you can trace causality, measure blast radius, and recover from failure. Hollow builds that layer.

This is not a research demo. It runs on a single machine, routes tasks to local Ollama models, and all 101 integration tests run against the live API with no mocks.

---

## What's inside

**Phase 1 (v0.7.0 – v1.2.0): OS kernel primitives.** Eight core mechanisms that every higher-order capability depends on.

**Phase 2 (v1.3.x): Agent services.** Higher-order capabilities that are only possible because Phase 1 exists. You cannot build the lineage graph without the audit kernel. You cannot build multi-agent consensus without transactions. The architecture is not incidental — Phase 1 had to come first.

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

Three priority tiers: URGENT (0) preempts BACKGROUND (2) workers via checkpointing. Complexity routing: 1–2 → `mistral-nemo:12b`, 3–4 → `qwen2.5:14b`, 5 → `qwen3.5-35b-moe`. Affinity routing: if a suitable model is already in VRAM, use it regardless of the complexity tier.

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

**Circuit breaker**: when an agent's anomaly z-score exceeds 5σ (double the alert threshold), the circuit break fires automatically: the agent is suspended, its rate limits are reduced to 10% for 5 minutes, a `security.circuit_break` event fires, and root receives a `circuit_break_review` decision in its inbox with options `["restore", "terminate"]`. This uses the audit kernel (anomaly detection), registry (suspend), event bus (notification), and message bus (inbox delivery) in combination — it required all four Phase 1 primitives.

### Agent Checkpoints and Replay (v1.3.3)

SIGPAUSE suspends an agent. But suspension is not the same as checkpointing — suspension preserves the agent's current task reference, not its full state. If the worker process restarts, the task context is gone. Checkpoints serialize everything: memory heap contents, unread inbox messages, current task snapshot, and agent metadata. Restore overwrites the current state with the saved snapshot. The agent resumes as if the interruption never happened.

Three auto-checkpoint triggers:
- **Before transaction commit**: every participant in a transaction is checkpointed before ops are applied. If the commit fails mid-way and rollback is needed, each agent can be restored to its pre-commit state independently.
- **On SIGPAUSE**: the checkpoint fires before the status flip to `suspended`. The checkpoint ID is stored in agent metadata and included in the SIGPAUSE inbox message, so the receiver knows exactly which snapshot to restore from.
- **After tasks >30 seconds**: long-running tasks produce the most state worth preserving. After any task that takes more than 30s, the submitting agent is auto-checkpointed.

**Replay** runs a task N times from the same checkpoint and measures response consistency (Jaccard similarity across runs). A factual question like "What is the capital of France?" should score >0.95 across 5 runs. An ambiguous preference question will score lower and produce a `divergence_points` list identifying where runs first diverged. This is the first step toward measuring agent determinism — a prerequisite for building reliable multi-agent systems.

Requires: memory heap (v1.0.0) for heap snapshot/restore, and transactions (v1.2.0) for pre-commit auto-checkpoint.

### Multi-Agent Consensus (v1.3.4)

One agent reaching a conclusion is a decision. Multiple independent agents reaching the same conclusion is a commitment — one that survives the failure or compromise of any single participant.

A proposer submits an action with a list of participants and a required vote count. Participants receive a `consensus.vote_requested` event and vote accept or reject. When accepts reach `required_votes`, the proposal is accepted and `consensus.reached` fires with the full action payload. Early rejection is computed: if the remaining uncast votes cannot mathematically close the gap, the proposal is rejected immediately rather than waiting for the TTL. This prevents cascading delays in time-sensitive pipelines.

Execution is intentionally out of scope. `consensus.reached` carries the action dict; the proposer acts on it. Consensus is a coordination mechanism, not an executor. To couple consensus to a transaction: include `{"txn_id": "..."}` in the action dict and listen for `consensus.reached` before committing. Pre-commit checkpoints (v1.3.3) ensure rollback is possible if anything fails after consensus but before the transaction commits.

Requires: events (v0.7.0) for vote distribution and consensus notifications, and transactions (v1.2.0) as the primary action type consensus gates.

### Adaptive Model Routing (v1.3.5)

Static complexity→model tiers (v0.9.0) choose the right model for the right complexity. VRAM affinity avoids eviction cost. Neither adapts to what actually happens: a model that loads fine but generates slowly at complexity 3, a model with intermittent failures at complexity 5, a new model that outperforms the static default.

The adaptive router observes every task completion — model, complexity, duration_ms, tokens_out, success — and maintains exponential moving averages (EMA, α=0.15) per (model, complexity) pair. The composite score weights success rate highest (50%), then throughput (30%), then latency (20%), because a fast wrong answer is strictly worse than a slow right one.

Routing decision hierarchy:
1. **Hard override** — admin-set rules that bypass scoring entirely (per agent, role, or complexity)
2. **Adaptive score** — highest-scoring model with ≥5 observations for this complexity tier
3. **VRAM affinity** (v0.9.0) — prefer already-loaded model to avoid 15–30s eviction cost
4. **Static tier default** — complexity 1–2 → mistral-nemo:12b, 3–4 → qwen2.5:14b, 5 → qwen3.5-35b-moe

Overrides resolve by specificity: agent_id > role > complexity-only > global. The most specific matching override wins. Stats and recommendations are exposed via API and MCP tools, so agents can observe and reason about routing decisions.

Requires: scheduler (v0.9.0) as the observation source, and audit kernel (v1.1.0) as the original per-operation metrics layer the routing decisions are grounded in.

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

Code search is where the savings are real: one semantic search call instead of N file reads, returning only relevant chunks. System state queries return comprehensive structured JSON — more tokens than `df -h` but machine-readable without regex. The 42% total is not the point; the 95% on code search is.

### Agent Drift Experiment

Agents resuming with Hollow handoff context make 2× more consistent decisions than cold-starting agents.

| Condition | Consistency rate | Corrections/run | Tokens/run |
|---|---|---|---|
| **Hollow** (structured handoff) | **70%** | 0.0 | 971 |
| Cold start (no context) | 35% | 0.1 | 1,246 |

Same model both conditions (`mistral-nemo:12b`), 10 runs each, 3-session task.

---

## Architecture

```
hollow-agentOS/
├── api/
│   ├── server.py          # FastAPI — all endpoints
│   └── agent_routes.py    # Agent OS routes: lifecycle, tasks, locks, txn, lineage, streaming, rate limits
├── agents/
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
│   ├── model_manager.py   # VRAM tracker, LRU eviction, model affinity
│   └── standards.py       # Project conventions store + semantic matching
├── memory/
│   ├── manager.py         # Session log, workspace map, token tracking, handoffs, specs, project
│   └── heap.py            # Working memory kernel — alloc, TTL, priority eviction, compression
├── mcp/
│   └── server.py          # 83 MCP tools for Claude Code and compatible agents
├── tools/
│   ├── semantic.py              # AST-aware chunker + embedding search
│   ├── bench_real_baseline.py   # Real baseline benchmark
│   ├── bench_breakeven.py       # Break-even analysis
│   └── experiment_agent_drift.py # Agent drift experiment
├── tests/
│   └── integration/       # 101 integration tests — no mocks, live API
│       ├── test_api.py
│       ├── test_events.py
│       ├── test_signals.py
│       ├── test_vram_scheduler.py
│       ├── test_audit.py
│       ├── test_transactions.py
│       ├── test_lineage.py
│       ├── test_streaming.py
│       ├── test_ratelimit.py
│       ├── test_checkpoint.py
│       ├── test_consensus.py
│       └── test_adaptive_routing.py
├── shell/
│   └── agent_shell.py     # JSON-native shell, deadlock-safe
├── sdk/
│   └── hollow.py          # Python SDK client
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
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

`override` body: `{"model": "qwen2.5:14b", "complexity": 3, "agent_id": null, "role": null, "reason": "..."}`. Omit fields to broaden scope.

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

71 tools wired directly into Claude Code and any MCP-compatible agent.

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

### Docker

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS

cp config.example.json config.json
# Set: api.token, workspace.root

docker-compose up
```

API at `http://localhost:7777`.

With Ollama (GPU required):

```bash
docker-compose --profile ollama up
```

### Manual (WSL2 / Linux)

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS

pip install -r requirements.txt

AGENTOS_CONFIG=/path/to/config.json \
AGENTOS_MEMORY_PATH=/path/to/memory \
AGENTOS_WORKSPACE_ROOT=/path/to/workspace \
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

### Register an agent

```bash
curl -X POST http://localhost:7777/agents/register \
  -H "Authorization: Bearer <master-token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "worker-1", "role": "worker"}'
```

---

## Testing

101 integration tests against the live API. No mocks. No seeded state.

```bash
# All integration tests
PYTHONPATH=. pytest tests/integration/ -v -m "integration and not slow"

# Individual primitive suites
PYTHONPATH=. pytest tests/integration/test_events.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_audit.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_transactions.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_lineage.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_streaming.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_ratelimit.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_checkpoint.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_consensus.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_adaptive_routing.py -v -m integration
```

Ollama-dependent tests skip automatically if Ollama is unavailable.

---

## Hardware

Developed on NVIDIA RTX 5070 (12 GB VRAM), WSL2 on Windows 11.

Ollama is optional. All OS primitives — events, signals, memory, audit, transactions, lineage, rate limiting, checkpoints, consensus, adaptive routing (overrides, stats, recommendations) — work without a GPU. EMA learning requires Ollama task completions.

With a GPU: models up to 14B fit in VRAM; up to 35B run with partial CPU offload. `nomic-embed-text` (semantic search) uses ~300 MB and stays resident. The VRAM scheduler tracks utilization and routes tasks to already-loaded models first.

---

## Roadmap

Phase 1 (v0.7.0 – v1.2.0): OS kernel primitives. Complete.

Phase 2 (v1.3.x): Agent services built on those primitives. In progress.

| Release | Feature | Status |
|---|---|---|
| v0.7.0 | Event Kernel | ✓ |
| v0.8.0 | Process Signals and Tombstones | ✓ |
| v0.9.0 | VRAM-Aware Scheduler | ✓ |
| v1.0.0 | Working Memory Kernel | ✓ |
| v1.1.0 | Audit Kernel | ✓ |
| v1.2.0 | Multi-Agent Transactions | ✓ |
| v1.3.0 | Agent Lineage and Call Graphs | ✓ |
| v1.3.1 | Streaming Task Outputs | ✓ |
| v1.3.2 | Rate Limiting and Admission Control | ✓ |
| v1.3.3 | Agent Checkpoints and Replay | ✓ |
| v1.3.4 | Multi-Agent Consensus | ✓ |
| v1.3.5 | Adaptive Model Routing | ✓ |
| v1.3.6 | Real Benchmark Suite | — |
| v1.3.7 | Self-Extending System | — |

The design principle that runs through Phase 2: each release requires two or more Phase 1 primitives working together. Checkpoints need the memory heap and transactions. Consensus needs events and transactions. Adaptive routing needs the scheduler and audit observations. Self-extension needs consensus and the full stack.

Phase 1 is the kernel. Phase 2 is userland. v1.3.7 closes the loop.
