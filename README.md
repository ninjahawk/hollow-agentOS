```
 _  _  ___  _    _    _____  __  __
| || |/ _ \| |  | |  / _ \ \ \  / /
| __ | (_) | |__| |_| (_) \ \/\/ /
|_||_|\___/|____|____\___/ \_/\_/
```

<div align="center">

**A genuine operating system for AI agents. Not a wrapper. Not a framework. An OS.**

[![Version](https://img.shields.io/badge/version-1.3.1-7fff7f?style=flat-square)](https://github.com/ninjahawk/hollow-agentOS/releases)
[![License](https://img.shields.io/badge/license-MIT-555?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue?style=flat-square)](https://python.org)
[![MCP Tools](https://img.shields.io/badge/MCP%20tools-69-purple?style=flat-square)](#mcp-tools)
[![Tests](https://img.shields.io/badge/integration%20tests-76%20passing-brightgreen?style=flat-square)](#testing)

![hollow token demo](demo.gif)

</div>

---

## What it is

Hollow is a local REST API that gives AI agents a complete operating system interface: identity, isolation, signals, memory, scheduling, events, transactions, audit, streaming I/O, lineage tracing, and inter-agent coordination.

It started as a token-efficiency layer. It became something else. The benchmarks are still real — 95% fewer tokens on code search, 42% overall — but that's not the point anymore. The point is that agents running on Hollow have the same primitives a real process has:

- A **process identity** with capabilities, budgets, and an isolated workspace
- An **event kernel** so agents get notified instead of polling
- **Process signals** (SIGTERM / SIGPAUSE / SIGINFO) with grace periods and tombstones
- A **working memory heap** with TTL, compression, and priority eviction
- A **VRAM-aware scheduler** that routes tasks to the right model automatically
- An **audit kernel** with append-only logging and z-score anomaly detection
- **Multi-agent transactions** — atomic commits across files, messages, and memory
- **Agent lineage** — who spawned who, blast radius analysis, critical path
- **Streaming task I/O** — non-blocking execution with SSE token streams

Everything runs locally. Ollama is optional. All 76 integration tests run against the live API with no mocks.

---

## OS Primitives

These are not features. They are the structural things that make Hollow an OS instead of a control plane.

### v0.7.0 — Event Kernel
The interrupt layer. Agents subscribe to typed event patterns instead of polling. Every subsystem emits events; every event routes to subscriber inboxes.

- `event_subscribe(pattern, ttl_seconds)` — glob patterns: `task.*`, `agent.terminated`, `*`
- `event_unsubscribe(subscription_id)`
- `event_history(since, event_types, limit)` — append-only event log, survives restart
- Events emitted by: registry, scheduler, bus, memory, audit, transactions, filesystem

| Event | Emitted by |
|---|---|
| `agent.registered`, `agent.terminated`, `agent.suspended`, `agent.resumed` | Registry |
| `budget.warning` (80%), `budget.exhausted` (100%) | Registry |
| `task.queued`, `task.started`, `task.completed`, `task.failed` | Scheduler |
| `task.token_chunk`, `task.partial_available`, `task.cancelled` | Streaming scheduler |
| `message.received` | Message bus |
| `decision.resolved`, `spec.activated` | Memory manager |
| `file.written` | Filesystem |
| `txn.committed`, `txn.rolled_back` | Transaction coordinator |
| `security.anomaly` | Audit kernel |

### v0.8.0 — Process Signals and Tombstones
Agents are real processes. `terminate()` is now `SIGTERM` — give the agent time to checkpoint, force-kill if it ignores the signal.

- `SIGTERM` — graceful shutdown with configurable grace period (default 30s), watchdog auto-terminates if agent doesn't respond
- `SIGPAUSE` — checkpoint current work, enter suspended state
- `SIGINFO` — agent reports current status to sender
- **Tombstones** — terminated agents write a final state record: last task, token usage, reason, children list
- **Process groups** — terminate an entire spawned subtree with one call (`terminate_group`)
- **Orphan adoption** — children of a terminated agent are automatically re-parented to root

### v0.9.0 — VRAM-Aware Scheduler
The scheduler knows what's in VRAM. It avoids cold-loading models that are already loaded and evicts least-recently-used models under memory pressure.

- **Model affinity** — routes tasks to models already loaded in VRAM first
- **Priority preemption** — `URGENT` tasks preempt `BACKGROUND` workers via checkpointing
- **Three priority tiers**: `URGENT` (0), `NORMAL` (1), `BACKGROUND` (2)
- **Complexity routing**: 1-2 → `mistral-nemo:12b` (fast), 3-4 → `qwen2.5:14b` (reasoning), 5 → `qwen3.5-35b-moe` (deep)
- **VRAM pressure** evicts LRU models to free space before routing

### v1.0.0 — Working Memory Kernel
Agents have a structured heap for intermediate work — not session logs, not files. A real working memory with allocation, TTL, priority eviction, and on-heap compression.

- `memory_alloc(key, content, priority, ttl_seconds)` — allocate a named heap entry
- `memory_read(key)`, `memory_free(key)`, `memory_list()`
- `memory_compress(key)` — compress a heap entry to free space without evicting
- `heap_stats()` — current allocation, utilization, eviction counts
- Priority-based eviction when heap reaches capacity
- Heap state persists across server restarts

### v1.1.0 — Audit Kernel
Every operation goes through a single audited boundary. The audit log is append-only. It can never be overwritten via the filesystem API — it is protected at the path level.

- Append-only NDJSON log (`audit.log`) — every shell, fs, ollama, agent, task, message, memory, lock, transaction operation
- **Z-score anomaly detection** — baseline per agent role, alert at 3σ deviations
- `security.anomaly` event fires on detection — real-time alerting
- `audit_query(agent_id, operation, since, until)` — precise filtering
- `audit_stats(agent_id)` — per-agent operation breakdown
- Baseline established from first 50 operations per role
- Causal fields on every entry: `caused_by_task_id`, `parent_txn_id`, `call_depth` (v1.3.0)

### v1.2.0 — Multi-Agent Transactions
File writes, message sends, and memory allocs can be staged atomically. Either everything commits or nothing does. Two agents staging writes to the same file are detected as a conflict before commit.

- `txn_begin()` → `txn_id`
- `txn/stage(op_type, params)` — buffer `fs_write` or `message_send` operations
- `txn/commit()` — apply all staged ops atomically; detect conflicts, roll back on any failure
- `txn/rollback()` — discard all staged ops
- **Isolation** — uncommitted writes are invisible to readers until commit
- **Conflict detection** — file modified between `txn_begin` and `txn_commit` → 409 conflict
- **Timeout watchdog** — transactions that don't commit within 60s are auto-rolled back
- Transaction log persists across restarts; expired transactions evicted automatically

### v1.3.0 — Agent Lineage and Call Graphs
The audit log records what happened. Lineage records *why* — who spawned who, which task caused which agent to exist, which agents are at risk if a given agent fails.

- `agent_lineage(agent_id)` — full ancestor chain up to root with spawn_depth at each level
- `agent_subtree(agent_id)` — recursive descendant call tree with edge types and metadata
- `agent_blast_radius(agent_id)` — forward-reachability: all affected descendants, held locks, open transactions, running tasks
- `task_critical_path(task_id)` — longest `depends_on` chain through the task graph
- **Causal audit entries** — every audit entry tagged with `caused_by_task_id` and `call_depth`
- **`depends_on`** on tasks — explicit dependency declarations for workflow ordering
- Lineage graph persists to `lineage.json`, survives restart

### v1.3.1 — Streaming Task Outputs
`submit(wait=True)` freezes the calling agent for 30–120 seconds. A real OS gives processes non-blocking I/O. Streaming makes task execution fully async: submit returns immediately, results stream back as chunks arrive.

- `submit(stream=True)` — returns immediately with `task_id`, `stream_url`, `partial_url`
- `GET /tasks/{id}/stream` — SSE endpoint, cursor-based token chunk delivery
- `GET /tasks/{id}/partial` — instant snapshot of accumulated partial output, non-blocking
- `DELETE /tasks/{id}` — cancel queued or running tasks
- `task.token_chunk` events every 10 tokens: `{task_id, chunk, tokens_so_far, model}`
- `task.partial_available` events every 500ms while running
- `task.cancelled` event on cancellation; worker freed immediately
- `wait=True` blocking mode unchanged — full backward compatibility

---

## Benchmarks

Measured on live system output. No constructed baselines.

| Scenario | Naive (shell) | Hollow (API) | Savings |
|---|---|---|---|
| Code search (rg + read matched files) | 21,636 tok | 987 tok | **95%** |
| File read + semantic context | 12,699 tok | 15,580 tok | –23% |
| State polling (4 shell commands) | 373 tok | 722 tok | –93% |
| System state (5 discovery commands) | 341 tok | 1,578 tok | –363% |
| Agent cold start (pickup) | 617 tok | 1,800 tok | –192% |
| **Total** | **35,666 tok** | **20,667 tok** | **42%** |

**Where Hollow wins:** Code search. `POST /semantic/search` returns only relevant chunks — 95% fewer tokens and one call instead of N file reads.

**Where Hollow costs more:** System state queries return comprehensive structured JSON — more than a targeted `df -h`, but structured and parseable without regex.

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
│   └── agent_routes.py    # Agent OS routes: all lifecycle, tasks, locks, txn, lineage, streaming
├── agents/
│   ├── registry.py        # Identity, capabilities, workspaces, budgets, locks, model policies
│   ├── bus.py             # Inter-agent message bus with pub/sub
│   ├── scheduler.py       # VRAM-aware routing, priority preemption, streaming, cancellation
│   ├── events.py          # EventBus — pub/sub, glob patterns, TTL, persistent event log
│   ├── signals.py         # SIGTERM / SIGPAUSE / SIGINFO with grace period watchdog
│   ├── audit.py           # Append-only audit log, z-score anomaly detection
│   ├── transaction.py     # Atomic multi-op transactions, conflict detection, isolation
│   ├── lineage.py         # Agent call graph, blast radius, critical path
│   ├── model_manager.py   # VRAM tracker, LRU eviction, model affinity
│   └── standards.py       # Project conventions store + semantic matching
├── memory/
│   ├── manager.py         # Session log, workspace map, token tracking, handoffs, specs, project
│   └── heap.py            # Working memory kernel — alloc, TTL, priority eviction, compression
├── mcp/
│   └── server.py          # 69 MCP tools for Claude Code and compatible agents
├── tools/
│   ├── semantic.py              # AST-aware chunker + embedding search
│   ├── bench_real_baseline.py   # Real baseline benchmark
│   ├── bench_breakeven.py       # Break-even analysis
│   ├── experiment_agent_drift.py # Agent drift experiment
│   └── test_integration.py      # Legacy integration suite
├── tests/
│   └── integration/       # 76 integration tests — no mocks, live API
│       ├── test_api.py
│       ├── test_events.py
│       ├── test_signals.py
│       ├── test_vram_scheduler.py
│       ├── test_audit.py
│       ├── test_transactions.py
│       ├── test_lineage.py
│       └── test_streaming.py
├── shell/
│   └── agent_shell.py     # JSON-native shell, deadlock-safe
├── dashboard/
│   └── index.html         # Live dashboard (nginx :7778)
├── sdk/
│   └── hollow.py          # Python SDK client
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
└── config.json
```

---

## Agent Roles

| Role | Shell | FS | Ollama | Spawn | Message | Admin | Lock |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `root` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `orchestrator` | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ |
| `worker` | ✓ | ✓ | ✓ | — | ✓ | — | ✓ |
| `coder` | ✓ | ✓ | ✓ | — | ✓ | — | ✓ |
| `reasoner` | — | read | ✓ | — | ✓ | — | — |
| `readonly` | — | read | — | — | ✓ | — | — |

Custom capability sets and per-model policies are supported at registration.

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
POST   /agents/{id}/signal           Send SIGTERM / SIGPAUSE / SIGINFO
POST   /agents/{id}/lock/{name}      Acquire named timed lock (default 300s TTL)
DELETE /agents/{id}/lock/{name}      Release lock
GET    /agents/{id}/usage            Per-agent token breakdown by model and action
GET    /usage                        Aggregate token usage across all agents
GET    /tombstones                   All terminated agent records
GET    /tombstones/{id}              Specific tombstone
```

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
GET    /tasks/{id}                   Get task state and result
GET    /tasks                        List tasks
GET    /tasks/{id}/stream            SSE stream — token chunks as they arrive
GET    /tasks/{id}/partial           Current partial output snapshot (non-blocking)
DELETE /tasks/{id}                   Cancel queued or running task
```

**Submit flags:**
- `stream=true` — non-blocking, returns `stream_url` and `partial_url` immediately
- `wait=false` — non-blocking without streaming, check status manually
- `depends_on=[task_id, ...]` — declare task dependencies for critical path analysis
- `parent_task_id` — causal context for lineage tracing

</details>

<details>
<summary><strong>Events (v0.7.0)</strong></summary>

```
POST   /events/subscribe             Subscribe to event pattern (glob, TTL optional)
DELETE /events/subscribe/{id}        Unsubscribe
GET    /events/history               Query persistent event log by type and time range
```

Events deliver to subscriber inboxes via the message bus. TTL subscriptions expire automatically.

</details>

<details>
<summary><strong>Messaging</strong></summary>

```
POST   /messages                     Send message to agent or broadcast
GET    /messages                     Receive inbox (unread by default, limit, thread support)
GET    /messages/thread/{id}         Full reply thread
```

Message types: `data`, `log`, `result`, `signal`, `event`

</details>

<details>
<summary><strong>Transactions (v1.2.0)</strong></summary>

```
POST   /txn/begin                    Open a transaction, returns txn_id
POST   /txn/{id}/stage               Stage an operation (fs_write or message_send)
POST   /txn/{id}/commit              Atomic commit — all ops or none
POST   /txn/{id}/rollback            Discard all staged ops
GET    /txn/{id}                     Transaction status, ops_count, expires_in_seconds
```

</details>

<details>
<summary><strong>Working Memory Heap (v1.0.0)</strong></summary>

```
POST   /memory/alloc                 Allocate heap entry (key, content, priority, ttl_seconds)
GET    /memory/read/{key}            Read heap entry
DELETE /memory/free/{key}           Free heap entry
GET    /memory/list                  List all entries with metadata
POST   /memory/compress/{key}        Compress entry in-place
GET    /memory/heap/stats            Utilization, eviction counts, free space
```

</details>

<details>
<summary><strong>Audit (v1.1.0)</strong></summary>

```
GET    /audit                        Query audit log (filter by agent, operation, time range)
GET    /audit/stats/{agent_id}       Per-agent operation breakdown and baseline
GET    /audit/anomalies              Recent anomaly reports (z-score detections)
```

</details>

<details>
<summary><strong>System, Shell, and Filesystem</strong></summary>

```
GET    /state                        Full system snapshot (JSON)
GET    /state/diff?since=<iso>       Changed fields only since timestamp
GET    /state/history?since=<iso>    State snapshots since timestamp
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
POST   /ollama/chat                  Role-based model routing (complexity 1-5)
POST   /ollama/generate              Raw generate
GET    /ollama/models                Available + running + routing table
GET    /model_status                 VRAM utilization, loaded models, eviction state

POST   /semantic/search              Cosine similarity search over workspace
POST   /semantic/index               Re-index workspace
GET    /semantic/stats

POST   /agent/handoff                Write structured session context
GET    /agent/pickup                 Handoff + temporal context + active spec + standards
```

</details>

<details>
<summary><strong>Standards, Specs, Project, and Framework Compat</strong></summary>

```
POST   /standards                    Store a named project convention
GET    /standards                    List all standards
GET    /standards/relevant?task=     Semantic match: which standards apply
DELETE /standards/{name}

POST   /specs                        Create feature spec
GET    /specs                        List specs
GET    /specs/{id}
PATCH  /specs/{id}/activate          Set as active spec (injected into agent/pickup)

GET    /project                      Get project context
POST   /project                      Update project context

GET    /tools/openai                 All tools as OpenAI function definitions
                                     (LangChain, AutoGen, CrewAI, LlamaIndex)
```

</details>

---

## MCP Tools

69 tools wired directly into Claude Code and any MCP-compatible agent.

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

---

## Setup

### Docker (recommended)

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS

cp config.example.json config.json
# Set: api.token, workspace.root

docker-compose up
```

API at `http://localhost:7777`, dashboard at `http://localhost:7778`.

With Ollama (GPU required):

```bash
docker-compose --profile ollama up
```

### Manual (WSL2 / Linux)

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS

cp config.example.json config.json
pip install -r requirements.txt

AGENTOS_CONFIG=/path/to/config.json \
AGENTOS_MEMORY_PATH=/path/to/memory \
AGENTOS_WORKSPACE_ROOT=/path/to/workspace \
python3 -m uvicorn api.server:app --host "::" --port 7777
```

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

### Register your first agent

```bash
curl -X POST http://localhost:7777/agents/register \
  -H "Authorization: Bearer <master-token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-agent", "role": "worker"}'
```

### Python SDK

```bash
pip install hollow-sdk
```

```python
from hollow import register, Hollow

agent = register("http://localhost:7777", master_token="your-token",
                 name="my-agent", role="worker")
h = Hollow("http://localhost:7777", agent_token=agent.token)

state = h.state()
results = h.semantic_search("authentication middleware")
context = h.pickup()  # handoff + temporal state + active spec + standards
```

---

## Testing

All 76 integration tests run against the live API. No mocks. No seeded data.

```bash
# All integration tests (excludes 65s timeout test)
PYTHONPATH=. pytest tests/integration/ -v -m "integration and not slow"

# Specific primitive suites
PYTHONPATH=. pytest tests/integration/test_events.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_signals.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_audit.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_transactions.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_lineage.py -v -m integration
PYTHONPATH=. pytest tests/integration/test_streaming.py -v -m integration

# Legacy benchmarks
python3 tools/bench_real_baseline.py
python3 tools/experiment_agent_drift.py --runs 10
```

Ollama-dependent tests auto-skip if Ollama is unavailable. All structural and protocol tests run without a GPU.

---

## Hardware

Developed and benchmarked on NVIDIA RTX 5070 (12 GB VRAM), WSL2 on Windows 11.

Ollama is optional — all OS primitives (events, signals, memory, audit, transactions, lineage) work without a GPU.

With a GPU:
- Models up to 14B fit in VRAM
- Models up to 35B run with partial CPU offload
- `nomic-embed-text` (semantic search) uses ~300 MB and stays resident, separate from agent task models
- VRAM-aware scheduler tracks utilization in real time and routes tasks to loaded models first

---

## Roadmap

Phase 1 (v0.7.0–v1.2.0) is complete. All OS primitives are implemented and integration-tested.

Phase 2 (v1.3.0–v1.3.7) builds higher-order services on top of those primitives.

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
| v1.3.2 | Rate Limiting and Admission Control | planned |
| v1.3.3 | Agent Checkpoints and Replay | planned |
| v1.3.4 | Multi-Agent Consensus | planned |
| v1.3.5 | Adaptive Model Routing | planned |
| v1.3.6 | Real Benchmark Suite | planned |
| v1.3.7 | Self-Extending System | planned |

---

## License

MIT
