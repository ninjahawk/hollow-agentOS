# AgentOS Roadmap — Toward a True OS for Agents

Current release: **v4.0.0** — hollowOS ✅ COMPLETE

**Status:** All 12 phases complete. Acceptance test passed on RTX 5070 + CUDA.
Deploy once. Point at Ollama. Give agents goals. Walk away.
Agents reason, execute, synthesize capabilities, coordinate, and govern themselves.

This document is the precise engineering plan for evolving AgentOS from a control plane into a genuine operating system for AI agents. Each release ships one OS primitive. Each release is tested against real invariants on real hardware (RTX 5070 + Ollama + WSL2). No fake benchmarks, no mocked Ollama responses, no seeded data. Every number in every release note must be reproducible by anyone who clones the repo.

---

## The Gap

Reading the current implementation:

- `agents/registry.py:236` — `terminate()` is a one-line status flip. No signal, no grace period, no tombstone. Every termination is `kill -9`.
- `agents/scheduler.py:37` — routing is a static dict. Zero awareness of what is loaded in VRAM or what tasks are already running.
- `agents/bus.py` — no pub/sub. Agents poll `receive()`. The entire system is pull-based.
- No unified audit boundary. Shell ops log to `shell-usage-log.json`, tokens to `agent-registry.json`, tasks to `tasks.json` — three files, no cross-referencing, gaps between them.

These are not polish issues. They are the four structural things that make the system a control plane instead of an OS. The plan fixes them in dependency order.

---

## v0.7.0 — The Event Kernel

**Principle:** Without interrupts, a computer is a polling machine. Every feature that follows — signals, VRAM preemption, transaction notifications — requires events. This is the foundation everything else builds on.

### New file: `agents/events.py`

```
AgentEvent:
  event_id: str
  event_type: str       # "agent.terminated", "task.completed", "budget.warning", ...
  source_id: str
  payload: dict
  timestamp: float

EventBus:
  subscribe(agent_id, pattern, ttl_seconds) → subscription_id
    # glob patterns: "task.*", "agent.terminated", "*"
  unsubscribe(subscription_id)
  emit(event_type, source_id, payload)
    # delivers to all matching subscribers via their MessageBus inbox
    # msg_type = "event" (new type added to bus.py MSG_TYPES)
  get_history(since, event_types, limit) → list[AgentEvent]
    # persisted to /agentOS/memory/event-log.json, append-only
```

### Events wired into existing code

| Source file | Events emitted |
|---|---|
| `agents/registry.py` | `agent.registered`, `agent.terminated`, `agent.suspended`, `agent.resumed`, `budget.warning` (at 80%), `budget.exhausted` |
| `agents/scheduler.py` | `task.queued`, `task.started`, `task.completed`, `task.failed` |
| `agents/bus.py` | `message.received` |
| `memory/manager.py` | `decision.resolved`, `spec.activated` |
| `api/server.py` (fs_write route) | `file.written` with path + agent_id |

### New MCP tools

`event_subscribe`, `event_unsubscribe`, `event_history`

### Required tests (all must pass before release)

1. Subscribe to `task.completed`. Submit a real Ollama task (complexity=1, mistral-nemo:12b). Assert event arrives in inbox. Assert payload contains `task_id`, `model`, `tokens_out`. Measure: event arrives before a 1s poll loop would detect completion.

2. Subscribe to `budget.warning`. Register agent with `budget: tokens_in: 1000`. Submit tasks until 800 tokens consumed via real inference. Assert warning event fired exactly once at 80%. Consume to 1200 tokens. Assert `budget.exhausted` fired. Assert no duplicate warning events.

3. Subscribe to `agent.terminated`. Register a second agent. Terminate it. Assert event arrives within 200ms. Assert payload includes `agent_id`, `terminated_by`, `final_usage`.

4. TTL expiry: Subscribe with `ttl_seconds=3`. Wait 4s. Fire a matching event. Assert no delivery.

5. Glob pattern test: Subscribe to `agent.*`. Register, suspend, resume, terminate an agent in sequence. Assert exactly 4 events delivered matching the pattern.

6. Unsubscribe: Subscribe, verify delivery works. Unsubscribe. Fire event. Assert no delivery.

7. Persistence: Emit 20 events. Restart the server. Query `event_history(since=T)`. Assert all 20 entries present in log.

---

## v0.8.0 — Process Signals and Tombstones

**Principle:** Agents are registry records with a status field. A real process has a lifecycle with signals. `terminate()` should be `SIGTERM` — give the agent time to save its work, then force-kill if it ignores the signal.

### New file: `agents/signals.py`

```
SIGNALS = {
  "SIGTERM": "graceful shutdown — write handoff and exit",
  "SIGPAUSE": "checkpoint current work, enter suspended state",
  "SIGINFO":  "report current status to sender",
}

signal_dispatch(registry, bus, events, agent_id, signal, sent_by)
  # 1. Emits "agent.signal_received" event
  # 2. Sends high-priority message to agent inbox: msg_type="signal"
  # 3. SIGTERM: sets agent.metadata["terminating_after"] = now + 30s
  # 4. Starts grace_period_watchdog thread:
  #      after 30s, if agent still active:
  #        force-terminate, write tombstone, reason="grace_period_exceeded"
```

### Changes to `agents/registry.py`

- `terminate()` replaced by signal dispatch path. Direct force-terminate only from internal watchdog or root with `force=True`.
- `AgentRecord` gains: `group_id: Optional[str]`, `tombstone_path: Optional[str]`
- New method: `write_tombstone(agent_id)` → writes `{workspace_dir}/tombstone.json`
- New method: `terminate_group(group_id)` → SIGTERM all members simultaneously

### Tombstone schema

```json
{
  "agent_id": "...",
  "name": "...",
  "role": "...",
  "terminated_at": "ISO8601",
  "reason": "sigterm | force | budget_exhausted | grace_period_exceeded",
  "final_usage": {"shell_calls": 0, "tokens_in": 0, "tokens_out": 0},
  "current_task_at_termination": "...",
  "children": ["agent_id_1", "agent_id_2"],
  "parent_id": "..."
}
```

### Changes to `agents/scheduler.py`

- `spawn_agent()` assigns `group_id = parent_id` (workers belong to orchestrator's process group)

### New API endpoints

`POST /agents/{id}/signal`, `GET /tombstones`, `GET /tombstones/{agent_id}`

### New MCP tools

`agent_signal`, `agent_tombstone`

### Required tests

1. Register agent. Send `SIGTERM`. Assert: agent receives `signal` message in inbox. Agent writes handoff within 30s. Status becomes `terminated`. `tombstone.json` exists at workspace path with all required fields populated.

2. Grace period exceeded: Send `SIGTERM` to agent that never calls handoff. Wait 31s. Assert: status `terminated`, `tombstone.reason = "grace_period_exceeded"`.

3. Process group: Spawn orchestrator + 3 workers via `spawn_agent` (all get same `group_id`). Call `terminate_group`. Assert: all 4 SIGTERM'd simultaneously. All 4 tombstones exist within 35s.

4. SIGPAUSE: Agent has `current_task` set. Send `SIGPAUSE`. Assert: status = `suspended`. `current_task` preserved. `metadata["paused_at"]` set. Resume via SIGTERM afterward, task resumes from preserved state.

5. Orphan adoption: Terminate parent directly. Assert: children's `parent_id` updated to `"root"`. Children remain active, not cascade-killed.

6. SIGINFO: Send `SIGINFO`. Assert: agent's `current_task`, `usage`, `locks`, uptime returned to sender as a `result` message within 5s.

---

## v0.9.0 — VRAM-Aware Scheduler

**Principle:** The scheduler routes by complexity but has no idea what is loaded in VRAM. Routing complexity=1 to mistral-nemo when qwen2.5:14b is already loaded means evicting a loaded model and waiting 15–30s to load a lighter one. This is a cache miss caused by the scheduler. Real OS schedulers have cache affinity and minimize context switches.

### New file: `agents/model_manager.py`

```
ModelSlot:
  model_name: str
  vram_mb: int
  loaded_since: float
  last_used: float
  eviction_policy: "lru" | "pinned" | "background"

ModelManager:
  get_loaded() → list[ModelSlot]        # from Ollama /api/ps
  get_available_vram() → int            # total - sum(loaded vram)
  recommend(complexity, prefer_loaded) → model_name
    # 1. Get complexity routing candidate
    # 2. If candidate already loaded: return it (affinity)
    # 3. If different model loaded and fits alongside: load both
    # 4. If must evict: evict LRU non-pinned, emit model.evicted event
  evict_lru() → evicted_model_name
    # calls Ollama unload API
```

### Changes to `agents/scheduler.py`

- `ThreadPoolExecutor(max_workers=4)` replaced with `PriorityTaskQueue`:
  - Task priority: `0=URGENT`, `1=NORMAL` (default), `2=BACKGROUND`
  - Workers pull in priority order
  - BACKGROUND preemption: if URGENT arrives and all workers busy, oldest BACKGROUND task is checkpointed (status=`"checkpointed"`, saved to tasks.json), worker freed, URGENT runs, BACKGROUND re-queued after
- `_run_task` calls `model_manager.recommend(complexity)` instead of static dict
- New events: `model.loaded`, `model.evicted`, `vram.pressure` (>90% VRAM used)

### `task_submit` new parameter

`priority: int` — 0, 1, or 2 (default: 1)

### New MCP tool

`model_status` — returns VRAM state, loaded models, queue depth by priority

### Required tests (require real Ollama, real models)

1. **Affinity**: Verify qwen2.5:14b is loaded via `model_status`. Submit 5 complexity=3 tasks back-to-back. Assert: 0 `model.evicted` events fired. All 5 tasks routed to qwen2.5:14b.

2. **Priority preemption**: Submit 4 BACKGROUND tasks (complexity=5). Wait until all 4 are running (poll task status). Submit 1 URGENT task. Assert: URGENT task starts within 3s. Assert: at least 1 `task.checkpointed` event fired. After URGENT completes, assert checkpointed task re-queues and eventually completes.

3. **VRAM eviction**: Force-load all models via direct Ollama API to fill VRAM. Submit task requiring a model not currently loaded. Assert: `model.evicted` event fires with correct evicted model name. Target model loads. Task completes.

4. **Throughput regression**: Submit 20 mixed-complexity tasks (5 each of complexity 1–4). Record total wall time, model eviction count, per-priority avg queue wait. Compare to baseline from v0.6 (same 20 tasks, saved in `tests/fixtures/scheduler_baseline.json`). Assert: model evictions reduced by ≥30% vs baseline.

5. **Starvation prevention**: Submit 100 NORMAL tasks continuously. Assert: any BACKGROUND task submitted during this period completes within 5× its expected execution time. The OS must guarantee forward progress for all priority levels.

6. **`vram.pressure` event**: Drive VRAM to >90% via model loading. Assert event fires. Assert payload contains `vram_used_mb`, `vram_total_mb`, `loaded_models`.

---

## v1.0.0 — The Working Memory Kernel

**Principle:** An LLM's context window is RAM. No existing system manages it. Agents currently manage their own context blindly — they have no way to free space, compress old reasoning, or guarantee important information survives long sessions. This is the OS primitive with no Unix analogue. It is the invention.

### New file: `memory/heap.py`

```python
@dataclass
class MemoryObject:
    key: str
    content: str
    token_count: int              # measured on alloc
    priority: int                 # 0-10, higher = protected from compression
    ttl: Optional[float]          # unix timestamp, None = forever
    compression_eligible: bool
    compressed: bool = False
    swapped: bool = False         # True if content is on disk, not in memory
    created_at: float = 0.0
    last_read_at: float = 0.0
    disk_path: Optional[str] = None

class WorkingMemoryHeap:
    alloc(agent_id, key, content, priority=5, ttl=None, compression_eligible=True)
    read(agent_id, key) → str           # raises KeyError if freed/expired
    free(agent_id, key) → bool
    gc(agent_id) → {freed_keys, freed_tokens}
    compress(agent_id, key) → {original_tokens, compressed_tokens, ratio}
      # summarizes content via mistral-nemo:12b
      # original stored to disk, summary replaces content in heap
    swap_out(agent_id, key) → bool      # serialize to disk, free from active heap
    swap_in(agent_id, key) → bool       # restore from disk
    heap_stats(agent_id) → {
        total_tokens, object_count, compressible_tokens,
        swapped_count, fragmentation_score
    }
```

### Auto-management in `memory/manager.py`

At 80% of agent token budget:
1. Emit `memory.pressure` event
2. Sort compressible objects by (priority ASC, last_read_at ASC)
3. Compress bottom quartile
4. If still over 80%, swap oldest low-priority objects to disk

### New MCP tools

`memory_alloc`, `memory_read`, `memory_free`, `memory_list`, `memory_compress`, `heap_stats`

### Events

`memory.pressure`, `memory.compressed`, `memory.gc_complete`, `memory.swapped`

### Required tests

1. **Alloc/read/free cycle**: Alloc 10 objects with known content. Read each. Assert content identical. Free 5. Assert freed keys raise `KeyError`. Assert remaining 5 readable. `heap_stats` shows correct `object_count` and `token_count`.

2. **TTL expiry**: Alloc with `ttl=3`. Wait 4s. Call `gc()`. Read key. Assert `KeyError`. Assert `memory.gc_complete` event fired containing the expired key.

3. **Compression quality** — the real test: Load `agentOS/README.md` (real content, ~3000 tokens). Alloc as single memory object. Call `compress()`. Assert: `compressed_tokens ≤ 600` (≤20% of original). Prepare 10 specific factual questions about the README content. Ask mistral-nemo:12b each question using the original content as context. Record answers. Ask the same 10 questions using the compressed content as context. Score both sets correct/incorrect against ground truth. Assert: compressed version scores ≥8/10. This test uses real inference, real content, real questions. No fake numbers.

4. **Auto-compression under pressure**: Register agent with `budget: tokens_in: 50000`. Alloc objects totaling 40000 tokens with mixed priorities (1–9). Assert `memory.pressure` event fires. Alloc 3000 more tokens. Assert `memory.compressed` events fire. Assert agent never exceeds 50000 token budget. Assert objects with priority ≥8 were not compressed.

5. **Swap round-trip**: Alloc object with 5000-token content. Call `swap_out`. Assert `heap_stats.total_tokens` decreases by ~5000. Call `read()` — assert auto-swaps-in and returns correct content. Call `swap_in` explicitly. Assert content identical character-for-character. Assert swap latency p99 < 500ms.

6. **Fragmentation and GC**: Alloc 100 objects of ~200 tokens each. Free alternating 50. Measure `fragmentation_score` before `gc()`. Call `gc()`. Assert remaining 50 objects still readable with correct content. Assert `fragmentation_score` after GC < score before.

---

## v1.1.0 — The Audit Kernel

**Principle:** After memory management, the OS has full visibility into what agents do. Enforce it. Every operation through a single audited boundary. Without an audit log, anomaly detection is impossible, accountability is impossible, post-mortems are guesswork.

### New file: `agents/audit.py`

```python
@dataclass
class AuditEntry:
    entry_id: str
    agent_id: str
    operation: str      # shell_exec | fs_read | fs_write | ollama_call |
                        # agent_register | agent_terminate | agent_spawn |
                        # message_send | memory_alloc | task_submit | lock_acquire | ...
    params: dict        # sanitized — no file content, no model output, only metadata
                        # e.g. fs_write → {path, bytes_written, workspace_scoped: bool}
    result_code: str    # ok | denied | error | budget_exceeded
    tokens_charged: int
    duration_ms: float
    timestamp: float

class AuditLog:
    log(entry: AuditEntry)                           # append-only
    query(agent_id, operation, since, until, limit)  # returns list[AuditEntry]
    stats(agent_id) → {op_counts, total_tokens, anomaly_score}
    check_anomaly(agent_id) → Optional[AnomalyReport]
      # z-score vs per-role baseline for:
      # shell_calls_per_minute, tokens_per_minute, unique_op_types
      # returns AnomalyReport if z > 3.0
```

### Implementation

- All API routes gain an `@audit` decorator in `api/server.py`, fires before and after every handler
- Appends to `/agentOS/memory/audit.log` as newline-delimited JSON — append-only, never rewritten
- Anomaly check runs after every 10 new entries per agent
- Baseline established from first 50 operations per role, stored in `/agentOS/memory/audit-baselines.json`
- On anomaly: emit `security.anomaly` event to root agent

### Protected paths

`audit.log` and `audit-baselines.json` added to `fs_write` blocklist. Neither root nor any agent can overwrite via API.

### New MCP tools

`audit_query`, `audit_stats`, `anomaly_history`

### Required tests

1. **Coverage completeness**: Run a fixed workflow: register agent, submit 3 real Ollama tasks, write 5 files, send 3 messages, acquire 1 lock, terminate. Query audit log for agent. Assert every operation appears exactly once. Assert count ≥12 entries. Any operation present in registry/fs/bus but absent from audit log = test failure.

2. **Anomaly detection**: Register worker agent. Run 20 normal operations. Then submit 60 shell_exec calls within 60 seconds (far above worker baseline rate). Assert: `security.anomaly` event fires to root. Assert payload includes `agent_id`, `metric="shell_calls_per_minute"`, `observed`, `baseline`, `z_score > 3.0`.

3. **Append-only protection**: Attempt `fs_write` with path `/agentOS/memory/audit.log`. Assert `403 Forbidden`. Attempt `shell_exec` with command `echo foo > /agentOS/memory/audit.log`. Assert blocked by shell scope enforcement. Audit log unchanged after both attempts.

4. **Query precision**: Run 3 agents, 50 operations each. Call `audit_query(agent_id=X, operation="fs_read", since=T1, until=T2)`. Assert: only agent X's `fs_read` operations between T1 and T2 returned. No cross-agent leakage.

5. **Overhead**: Run 100 operations with audit enabled. Run same 100 operations with audit bypassed in test mode. Assert: p99 overhead per operation ≤5ms. Audit must never be the bottleneck.

6. **Persistence**: Log 30 entries. Restart server. Query log. Assert all 30 entries present with identical content.

---

## v1.2.0 — Multi-Agent Transactions

**Principle:** Named locks prevent concurrent corruption but do not provide atomicity. An orchestrator that modifies 3 files and spawns 2 workers has a race window: if it fails after the first file write, the system is in a partial state. A real OS provides transactions. This is the last missing primitive for reliable multi-agent workflows.

### New file: `agents/transaction.py`

```python
class TransactionCoordinator:
    begin(agent_id) → txn_id
      # creates TxnRecord: {txn_id, agent_id, status, ops_buffered, created_at, timeout_at}
      # timeout_at = now + 60s
      # watchdog auto-rolls-back after timeout

    stage(txn_id, op_type, params)
      # buffers operation — does NOT apply to disk/state yet
      # op_type: fs_write | memory_set | message_send
      # records which resources are touched (for conflict detection)

    commit(txn_id) → {ok: bool, conflicts: list[str]}
      # 1. Check conflict set: any resource modified by another agent since begin?
      #    Yes → rollback, return {ok: false, conflicts: [...]}
      # 2. Apply all buffered ops in sequence, all-or-nothing
      #    If any op fails → rollback already-applied ops
      # 3. Emit txn.committed {txn_id, ops_count, duration_ms}

    rollback(txn_id) → ok
      # discard all buffered ops
      # emit txn.rolled_back {txn_id, reason}
```

### API changes

`fs_write`, `memory_set`, `message_send` endpoints accept optional `txn_id` param. If provided, call `coordinator.stage()` instead of applying immediately.

### Isolation level

Read-committed. Readers see last-committed state. Uncommitted transaction writes are invisible to all other agents.

### New MCP tools

`txn_begin`, `txn_commit`, `txn_rollback`, `txn_status`

### Events

`txn.committed`, `txn.rolled_back`, `txn.conflict`

### Required tests

1. **Happy path**: Begin txn. Write 3 files via `fs_write?txn_id=X`. Commit. Assert all 3 files exist with correct content. Assert `txn.committed` event fired with `ops_count=3`.

2. **Explicit rollback**: Begin txn. Write 2 files. Call rollback. Assert neither file was written. Assert `txn.rolled_back` with `reason="explicit"`.

3. **Atomicity on partial failure**: Begin txn. Stage 5 file writes, where file 5 has an invalid path. Commit. Assert commit fails. Assert files 1–4 were NOT written. Assert `txn.rolled_back` with `reason="op_failed"`. No partial state.

4. **Conflict detection**: Agent A begins txn, stages write to `plan.md`. Agent B (outside any txn) writes `plan.md` directly. Agent A commits. Assert: `{ok: false, conflicts: ["plan.md"]}`. Assert Agent A's other staged writes also not applied. Assert `txn.conflict` event fired.

5. **Timeout auto-rollback**: Begin txn. Stage 2 writes. Wait 65s without committing. Assert `txn.rolled_back` event with `reason="timeout"`. Assert files not written. Assert `txn_status` returns `rolled_back`.

6. **Isolation**: Agent A begins txn, stages write to `config.json`. Agent B reads `config.json` while txn is open. Assert Agent B sees pre-transaction content. Agent A commits. Assert Agent B now sees new content.

---

## What the system is after v1.2.0

| OS Primitive | Status |
|---|---|
| Identity + capability isolation | v0.6.0 ✓ |
| Resource budgets (tokens) | v0.6.0 ✓ |
| Session persistence + handoffs | v0.6.0 ✓ |
| Semantic memory (embeddings) | v0.6.0 ✓ |
| Standards injection | v0.6.0 ✓ |
| Reactive event system (interrupts) | v0.7.0 ✓ |
| Process signals + tombstones | v0.8.0 ✓ |
| VRAM-aware priority scheduler | v0.9.0 ✓ |
| Context-window memory management | v1.0.0 ✓ |
| Unified audit kernel + anomaly detection | v1.1.0 ✓ |
| Multi-agent atomic transactions | v1.2.0 ✓ |

Each release is backward-compatible with all prior releases. The test suite for every prior release must still pass when a new release ships.

The primitives phase is complete. The system has an identity layer, interrupt system, process signals, memory manager, scheduler, audit kernel, and transaction coordinator. Every OS primitive needed to run reliable multi-agent workloads exists. What follows is Phase 2: higher-order services that the primitives make possible.

---

# Phase 2 — Agent Services (v1.3.0 – v1.3.7)

**The shift:** Phase 1 built the kernel. Phase 2 builds the OS services that applications — real agent workloads — actually consume. Each release takes two or more Phase 1 primitives and composes them into something agents couldn't do before.

---

## v1.3.0 — Agent Lineage and Call Graphs

**Principle:** The audit log records what happened. The lineage graph records *why* — who called who, which task spawned which agent, which transaction touched which resource. Without this, post-mortems are guesswork and debugging parallel workflows is impossible. This is the distributed tracing layer.

### Changes to existing code

**`agents/registry.py`**
- `AgentRecord` gains: `parent_task_id: Optional[str]`, `spawn_depth: int`
- `spawn_agent()` propagates `parent_task_id` from the calling task context
- New method: `get_lineage(agent_id) → list[AgentRecord]` — full ancestor chain to root

**`agents/audit.py`**
- `AuditEntry` gains: `parent_txn_id: Optional[str]`, `caused_by_task_id: Optional[str]`, `call_depth: int`
- Every audit entry tagged with its causal context at write time

**`api/agent_routes.py`**
- `spawn_agent` accepts optional `task_id` context (auto-injected from active task)

### New file: `agents/lineage.py`

```python
class LineageGraph:
    record_edge(parent_id: str, child_id: str, edge_type: str, metadata: dict)
      # edge_types: "spawned", "delegated", "signaled", "transacted"

    get_subtree(root_id: str) → dict
      # returns {agent_id: {children: [...], edges: [...], audit_entries: [...]}}
      # full recursive call tree rooted at root_id

    get_blast_radius(agent_id: str) → dict
      # which agents, tasks, and files would be affected if this agent fails?
      # uses dependency edges to compute forward-reachability

    critical_path(task_id: str) → list[str]
      # longest dependency chain from task start to completion
      # critical path is the minimum possible wall time for the workflow
```

### New API endpoints

`GET /agents/{id}/lineage` — ancestor chain
`GET /agents/{id}/subtree` — full descendant call graph
`GET /agents/{id}/blast-radius` — forward-reachability impact analysis
`GET /tasks/{id}/critical-path` — longest dependency chain

### New MCP tools

`agent_lineage`, `agent_subtree`, `agent_blast_radius`, `task_critical_path`

### Required tests

1. **Lineage chain**: Spawn root → orchestrator → 3 workers. Call `agent_lineage(worker_id)`. Assert: chain is `[worker, orchestrator, root]` with correct `spawn_depth` (0, 1, 2). Assert `parent_task_id` populated on all non-root agents.

2. **Subtree completeness**: From root, spawn a 3-level tree (1 orchestrator, 3 workers, 2 sub-workers each = 9 total). Call `agent_subtree(root)`. Assert: exactly 9 descendants, all edges correct, no duplicates.

3. **Blast radius**: Orchestrator holds 2 locks and has 3 running children. Call `agent_blast_radius(orchestrator_id)`. Assert: result includes all 3 children, both locked resources, and any tasks the orchestrator has staged in an open transaction.

4. **Critical path**: Submit a workflow with tasks A→B→C (sequential) and A→D (parallel with B). Assert: `task_critical_path` returns `[A, B, C]` not `[A, D]` (D is not on the critical path). Assert path length matches actual measured wall time ±10%.

5. **Audit tagging**: Run a known workflow (register agent, submit task, write file). Query audit log. Assert: every entry has non-null `caused_by_task_id` tracing back to the original task. Assert no orphan entries (entries with no causal ancestor traceable to root).

6. **Persistence across restart**: Build a 5-agent subtree. Restart server. Call `agent_subtree`. Assert: all lineage edges survive restart (persisted to `lineage.json`). Assert edge metadata intact.

---

## v1.3.1 — Streaming Task Outputs

**Principle:** The scheduler's `submit(wait=True)` blocks the calling agent until the task finishes. For tasks that take 30–120 seconds, this means the calling agent is frozen. A real OS gives processes non-blocking I/O with completion notifications. This release makes task execution truly async: submit returns immediately, results stream back as they arrive.

### Changes to `agents/scheduler.py`

- `submit(wait=False)` is now the recommended default for long tasks
- New method: `stream(task_id) → Generator[str, None, None]`
  - Yields token chunks as Ollama streams them (Ollama streaming API already supports this)
  - Emits `task.token_chunk` events at configurable intervals (every N tokens or every T ms)
  - Emits `task.completed` or `task.failed` as final event

### New API endpoints

`GET /tasks/{id}/stream` — SSE endpoint, streams `task.token_chunk` events
`GET /tasks/{id}/partial` — returns current partial output without blocking

### Changes to `api/agent_routes.py`

- `POST /tasks/submit` accepts `stream: bool = False`. When True, returns immediately with `task_id` and SSE URL.
- `submit_task` response includes `stream_url: Optional[str]`

### New MCP tools

`task_stream` — subscribes to a task's token stream, returns chunks as they arrive

### Events

`task.token_chunk` — `{task_id, chunk, tokens_so_far, model}`
`task.partial_available` — fires every 500ms while task is running with current partial output

### Required tests

1. **Non-blocking submit**: Submit a complexity=3 task with `stream=True`. Assert: HTTP response returns within 500ms (before task completes). Assert returned `task_id` is valid. Assert task eventually reaches `done` status.

2. **SSE stream delivers chunks**: Connect to `/tasks/{id}/stream`. Submit a task that will produce >200 tokens. Assert: at least 5 `task.token_chunk` events received before `task.completed`. Assert all chunks concatenated equal the final `result.response` field character-for-character.

3. **Partial output endpoint**: Submit long task (complexity=4). Poll `/tasks/{id}/partial` every 500ms while running. Assert: at least 3 polls return non-empty partial output. Assert partial output grows monotonically (each poll ≥ previous poll length).

4. **Streaming under load**: Submit 4 simultaneous streaming tasks (fill all workers). Assert: all 4 SSE streams deliver chunks concurrently. Assert no stream starves — all deliver their first chunk within 3s of the first started stream.

5. **Cancellation**: Submit task, connect to stream. After first chunk arrives, cancel the task via `DELETE /tasks/{id}`. Assert: SSE stream closes with `task.cancelled` event. Assert no further chunks delivered. Assert worker freed (submit a new task, assert it starts within 2s).

6. **`wait=True` backward compat**: Existing `submit(wait=True)` behavior unchanged. Assert: all existing task tests pass without modification. Streaming and blocking modes coexist.

---

## v1.3.2 — Rate Limiting and Admission Control

**Principle:** The budget system prevents *cumulative* overuse. But it doesn't prevent *bursty* overuse — an agent can exhaust its entire token budget in one second. A real OS has rate limiting: CPU schedulers use time slices, network stacks have token buckets. This release adds per-agent and per-role rate limits with backpressure so the system degrades gracefully under load.

### New file: `agents/ratelimit.py`

```python
class TokenBucket:
    capacity: int          # max burst
    refill_rate: float     # tokens/second
    current: float         # current tokens

    consume(n: int) → bool
      # returns True if consumed, False if insufficient tokens (would block)

class RateLimiter:
    # Per-agent token bucket for each limited resource
    check(agent_id: str, resource: str, amount: int) → RateLimitResult
      # resource: "tokens_in" | "shell_calls" | "api_calls" | "task_submissions"
      # returns: {allowed: bool, wait_ms: int, bucket_depth: float}

    configure(role: str, limits: dict)
      # limits: {"tokens_per_minute": 10000, "shell_calls_per_minute": 60, ...}
```

### Default rate limits by role

| Role | tokens/min | shell calls/min | task submits/min |
|---|---|---|---|
| root | unlimited | unlimited | unlimited |
| orchestrator | 100,000 | 300 | 60 |
| worker | 20,000 | 60 | 10 |
| coder | 50,000 | 120 | 20 |
| reasoner | 50,000 | 10 | 5 |
| custom | 5,000 | 10 | 5 |

### Changes to existing code

**`api/agent_routes.py`** — check rate limiter before shell_exec, task_submit, fs_write, ollama_chat
**`api/server.py`** — rate limit check wired in at `_resolve_agent` time, returns `429` with `Retry-After` header

### Circuit breaker

When an agent triggers the anomaly detector (z-score > 5.0), automatically:
1. Suspend the agent (status = `suspended`)
2. Emit `security.circuit_break` event to root
3. Rate limit set to 10% of normal for 5 minutes post-resume
4. Root receives decision prompt in inbox: `{decision_type: "circuit_break_review", agent_id, reason, options: ["restore", "terminate"]}`

### New API endpoint

`GET /agents/{id}/rate-limits` — current bucket depth, refill rate, time until full

### New MCP tools

`rate_limit_status` — per-agent and per-role rate limit state
`rate_limit_configure` — override limits for a specific agent (root only)

### Required tests

1. **Burst rejection**: Configure worker with `shell_calls_per_minute: 10`. Submit 12 shell calls in rapid succession. Assert: first 10 succeed, calls 11–12 return `429` with `Retry-After` header. After 6 seconds (bucket partial refill), assert new calls succeed.

2. **Refill over time**: Configure bucket to `tokens_per_minute: 1000`. Consume 900 tokens. Wait 30s. Assert: `rate_limit_status` shows bucket depth ≥ 400 (900 consumed, 30s × ~16.7/s refilled). Consume 1000 tokens. Assert allowed.

3. **Circuit breaker**: Register worker agent. Trigger anomaly detection (60 shell calls in 60s as in audit test 2). Assert: `security.circuit_break` event fires. Assert agent status = `suspended`. Assert root inbox has decision message with `decision_type = "circuit_break_review"`.

4. **Backpressure under load**: Register 10 worker agents. All submit tasks simultaneously. Assert: system does not crash or deadlock. Assert all tasks eventually complete. Assert no 500 errors. Assert queue depth visible via `model_status` endpoint throughout.

5. **`Retry-After` correctness**: Hit rate limit on a resource with 10/min bucket. Assert `Retry-After` header value in 429 response equals time until next token available ±500ms. Pause for that duration. Assert next request succeeds.

6. **Role inheritance**: Set rate limit for `worker` role. Register new worker agent (no explicit limits). Assert: new agent inherits role limits. Override limits for specific agent. Assert: override applies to that agent only; other workers still use role defaults.

---

## v1.3.3 — Agent Checkpoints and Replay

**Principle:** Agents fail mid-task. SIGPAUSE suspends an agent but doesn't capture its full state for later restoration on a different worker. A real OS has checkpointing — save everything, restore everything, resume exactly where you left off. This release also enables replay: re-run a task with identical inputs and compare outputs, which is the first step toward measuring agent determinism.

### New file: `agents/checkpoint.py`

```python
@dataclass
class AgentCheckpoint:
    checkpoint_id: str
    agent_id: str
    created_at: float
    memory_snapshot: dict       # full WorkingMemoryHeap state (serialized)
    inbox_snapshot: list[dict]  # unread messages at checkpoint time
    current_task_snapshot: dict # task state including partial output if streaming
    context_window_hash: str    # SHA-256 of the context at checkpoint time
    metadata: dict

class CheckpointManager:
    save(agent_id: str, label: Optional[str] = None) → checkpoint_id
      # snapshots: memory heap, inbox, current task, agent metadata
      # persists to /agentOS/memory/checkpoints/{agent_id}/{checkpoint_id}.json

    restore(agent_id: str, checkpoint_id: str) → bool
      # restores agent to saved state, replaces current memory/inbox
      # does NOT re-run already-executed tasks (replay is separate)

    list_checkpoints(agent_id: str) → list[AgentCheckpoint]

    diff(checkpoint_a: str, checkpoint_b: str) → dict
      # what changed between two checkpoints: memory keys, inbox messages, task state

    replay(checkpoint_id: str, task_description: str, n_runs: int = 3) → ReplayResult
      # restore agent to checkpoint, run task_description N times
      # returns: {responses: list[str], consistency_score: float, divergence_points: list}
      # consistency_score = 1.0 if all responses semantically equivalent, 0.0 if all different
```

### Auto-checkpointing

- Before each `commit()` in a transaction: auto-checkpoint all agents in the transaction
- On `SIGPAUSE`: checkpoint before suspending (enables true pause-resume)
- After any task that takes >30s: auto-checkpoint

### New API endpoints

`POST /agents/{id}/checkpoint` — save checkpoint, returns checkpoint_id
`POST /agents/{id}/restore/{checkpoint_id}` — restore from checkpoint
`GET /agents/{id}/checkpoints` — list saved checkpoints
`POST /checkpoints/{id}/replay` — replay a task from checkpoint

### New MCP tools

`agent_checkpoint`, `agent_restore`, `checkpoint_diff`, `checkpoint_replay`

### Required tests

1. **Save and restore**: Register agent. Alloc 5 memory objects. Receive 3 messages. Call `checkpoint`. Clear memory. Call `restore`. Assert: memory heap identical to pre-clear state. Assert inbox messages restored. Assert `heap_stats` identical to pre-clear values.

2. **SIGPAUSE integration**: Agent is mid-task (streaming). Send SIGPAUSE. Assert: checkpoint auto-saved before suspend. Assert `current_task_snapshot` contains partial output. Restore from checkpoint. Assert agent resumes with partial output preserved.

3. **Checkpoint diff**: Create checkpoint A. Alloc 3 new memory objects. Receive 2 messages. Create checkpoint B. Call `diff(A, B)`. Assert: diff shows exactly 3 new memory keys and 2 new inbox messages. Assert diff is empty when comparing checkpoint to itself.

4. **Replay consistency**: Register agent. Save checkpoint. Run task `"What is the capital of France?"` 5 times from the same checkpoint. Assert: `consistency_score > 0.95` (all 5 responses are semantically equivalent — Paris). Verify that mistral-nemo:12b is deterministic enough for this factual question.

5. **Replay divergence detection**: Register agent. Save checkpoint. Run a task with deliberate ambiguity (`"Choose: A or B — your preference"`) 5 times. Assert: `consistency_score < 0.8` (responses diverge). Assert `divergence_points` is non-empty, identifying where responses first differ.

6. **Checkpoint persistence**: Save checkpoint. Restart server. List checkpoints. Assert: checkpoint still present. Restore it. Assert: agent state fully restored across server restart.

---

## v1.3.4 — Multi-Agent Consensus

**Principle:** Some decisions are too important for a single agent. Human organizations use voting, code review requires approval, safety-critical systems require quorum. Agents need the same. This release adds a first-class consensus primitive: N agents vote, M must agree before an action executes. Built on transactions (v1.2.0) and events (v0.7.0).

### New file: `agents/consensus.py`

```python
@dataclass
class ConsensusProposal:
    proposal_id: str
    proposed_by: str          # agent_id of proposer
    action: dict              # what will happen if consensus reached
    required_votes: int       # M in N-of-M
    eligible_voters: list[str]  # agent_ids who can vote
    votes: dict[str, str]     # agent_id → "approve" | "reject" | "abstain"
    status: str               # "open" | "approved" | "rejected" | "expired"
    created_at: float
    expires_at: float         # default: now + 300s
    dissent_log: list[dict]   # minority vote reasoning for audit

class ConsensusCoordinator:
    propose(proposed_by, action, required_votes, eligible_voters, ttl=300) → proposal_id
      # broadcasts proposal to all eligible voters via inbox (msg_type="vote_request")
      # emits consensus.proposed event

    vote(proposal_id, agent_id, decision: "approve"|"reject"|"abstain", reasoning: str)
      # records vote
      # if M approvals reached: execute action via transaction, emit consensus.approved
      # if majority rejects: emit consensus.rejected
      # all dissenting votes logged with reasoning

    get_proposal(proposal_id) → ConsensusProposal

    list_proposals(status: Optional[str]) → list[ConsensusProposal]
```

### Actions that trigger automatic consensus

Configurable in `config.json` under `consensus_required`:
- File writes to paths matching `protected_patterns` (e.g., `*.py` in production workspace)
- Agent termination when agent has `consensus_protected: true` flag
- Transactions touching >5 resources simultaneously

### New API endpoints

`POST /consensus/propose`
`POST /consensus/{id}/vote`
`GET /consensus/{id}`
`GET /consensus` — list open proposals

### New MCP tools

`consensus_propose`, `consensus_vote`, `consensus_status`

### Events

`consensus.proposed`, `consensus.approved`, `consensus.rejected`, `consensus.expired`

### Required tests

1. **Happy path 2-of-3**: Register 3 agents. Propose action requiring 2 approvals from all 3. Cast 2 approve votes. Assert: status = `approved`. Assert action executed (write a file). Assert `consensus.approved` event fired with `votes_for=2`, `votes_against=0`.

2. **Rejection**: Propose with required_votes=2, eligible_voters=3. Cast 2 reject votes. Assert: status = `rejected` immediately (majority reject, quorum impossible). Assert action NOT executed. Assert `consensus.rejected` event fired. Assert dissent_log contains both rejection reasons.

3. **Expiry**: Propose with `ttl=5`. Cast 1 of required 2 approve votes. Wait 6s. Assert: status = `expired`. Assert action NOT executed. Assert `consensus.expired` event fired.

4. **Abstention handling**: Propose requiring 2-of-3. Cast 1 approve, 1 abstain, 1 approve. Assert: 2 approves counted (abstain does not block). Status = `approved`.

5. **Dissent log integrity**: Approved proposal where 1 voter rejected. Assert `dissent_log` entry contains `agent_id`, `decision="reject"`, non-empty `reasoning`. Assert dissent log appears in audit query for the dissenting agent.

6. **Auto-trigger on protected path**: Configure `protected_patterns: ["config.json"]`. Attempt `fs_write` to `config.json` from non-root agent without an approved proposal. Assert: write blocked, `429` or `403` response. Assert `consensus.proposed` event auto-fired with eligible voters = all agents with `admin` capability.

---

## v1.3.5 — Adaptive Model Routing

**Principle:** The VRAM-aware scheduler (v0.9.0) picks models based on complexity and what is loaded. But it has no memory. It doesn't know that qwen2.5:14b consistently answers code questions in 800ms but times out on open-ended reasoning. It doesn't know that mistral-nemo:12b is faster for short tasks but produces worse results on structured output. A real OS scheduler learns. This release adds a feedback loop from task outcomes to routing decisions.

### New file: `agents/routing_learner.py`

```python
@dataclass
class RoutingObservation:
    model: str
    complexity: int
    task_type: str          # inferred from description embedding similarity
    tokens_in: int
    tokens_out: int
    duration_ms: float
    success: bool
    quality_score: Optional[float]  # 0.0–1.0, from self-eval or human feedback

class RoutingLearner:
    record(observation: RoutingObservation)
      # persists to routing-observations.json

    recommend(complexity: int, task_description: str) → str
      # 1. Embed task_description (nomic-embed-text)
      # 2. Find K nearest past observations by embedding similarity
      # 3. Score each candidate model: weighted avg of (success_rate, speed, token_efficiency)
      # 4. Return highest-scoring model, fallback to VRAM-aware default if insufficient data

    model_stats(model: str) → dict
      # {success_rate, p50_ms, p99_ms, avg_tokens_out, task_type_distribution}

    reset_model(model: str)
      # clear observations for a model (e.g., after update/fine-tune)
```

### Changes to `agents/scheduler.py`

- `_run_task` records observation after every task (success + timing + tokens)
- `model_manager.recommend()` delegates to `RoutingLearner` after 50 observations per model
- New parameter in `submit()`: `quality_feedback: Optional[float]` — caller can score the output after receiving it

### New MCP tools

`model_stats` — per-model performance history
`routing_feedback` — submit quality score for a completed task
`routing_reset` — clear learned observations for a model

### Events

`routing.model_promoted` — a model's success rate exceeded threshold and it's now preferred for a task type
`routing.model_demoted` — model fell below threshold and is deprioritized
`routing.data_sufficient` — enough observations collected to start learning-based routing (fires once per model)

### Required tests

1. **Observation recording**: Submit 20 tasks of varying complexity. Call `model_stats` for each model that ran. Assert: `p50_ms` and `success_rate` fields populated. Assert observation count matches tasks routed to each model.

2. **Learning-based recommendation**: Inject 100 synthetic observations into the learner: model A has 95% success rate on code tasks, model B has 95% on creative tasks. Submit a code task. Assert: `recommend()` returns model A. Submit a creative task. Assert: model B returned. Without sufficient data, fallback to VRAM-aware default.

3. **Quality feedback loop**: Submit task. Provide quality_feedback=0.1 (poor). Submit 5 more identical tasks. Assert: model that received poor feedback is deprioritized for this task type. `routing.model_demoted` event fires.

4. **Cold start fallback**: Reset all observations. Submit task. Assert: falls back to VRAM-aware scheduler. Assert no errors. Assert observation recorded for the run.

5. **Throughput regression**: Run the same 20-task benchmark from v0.9.0 test 4 with learning enabled. After 20 tasks, run again. Assert: second run has ≥10% lower average latency than first run (routing learned from first run). This is the measurable improvement from adaptive routing.

6. **Persistence across restart**: Record 50 observations. Restart server. Submit task. Assert: learned routing still active (not cold-start behavior). Assert observations loaded from disk. `routing_data_sufficient` NOT re-emitted (already was sufficient before restart).

---

## v1.3.6 — Real Benchmark Suite

**Principle:** The project has a credibility gap: the 68.5% token efficiency claim uses a constructed baseline, not real tool call patterns. This release fixes it. Every number in the benchmark must be reproducible: run the same commands on the same hardware, get the same results ±5%. The benchmark is a first-class deliverable, not an afterthought.

### New file: `tools/bench_real.py`

The benchmark measures one thing: **tokens consumed to accomplish the same task, Hollow API vs. raw shell/file operations.**

#### Benchmark A — Infrastructure overhead only (model-agnostic)

Tasks that require no model judgment — pure data retrieval/write operations:

1. "Find all Python files modified in the last 24 hours" → compare `find` shell output vs. `/fs/search`
2. "Read the content of the 5 most recently modified files" → compare `cat` vs. `/fs/batch_read`
3. "Write the same content to 3 files atomically" → compare 3 shell writes vs. `/txn/begin` + commit
4. "Get current agent registry state" → compare shell `cat` of registry JSON vs. `/state?fields=agents`
5. "Search for the string 'agent_id' across all Python files" → compare `grep` vs. `/fs/search_content`

Measurement: token count from real Claude Code tool call logs (capture using MCP tool call interception).

#### Benchmark B — Task routing efficiency

Tasks that require model inference — measure quality + token tradeoff:

1. "Summarize what changed in the last 5 git commits" → shell approach vs. Hollow with context
2. "Debug why agent X failed" — cold start vs. Hollow with handoff context and audit log

Measurement: token count + a correctness judge (separate LLM call grades both answers 1–5 on accuracy).

### Benchmark infrastructure

**`tools/bench_real.py`**
```python
class BenchmarkRunner:
    run_hollow(task: BenchmarkTask) → BenchmarkResult
      # execute task via Hollow API, capture tokens via /usage endpoint delta

    run_baseline(task: BenchmarkTask) → BenchmarkResult
      # execute same task via shell_exec + fs_read raw calls
      # capture tokens via shell output size * tokenizer estimate

    compare(hollow: BenchmarkResult, baseline: BenchmarkResult) → ComparisonReport
      # {hollow_tokens, baseline_tokens, efficiency_gain_pct, quality_hollow, quality_baseline}
```

**`tests/benchmarks/`** — new directory, not part of integration suite
- `test_bench_infra.py` — runs Benchmark A, asserts efficiency gain ≥30% on each task
- `test_bench_routing.py` — runs Benchmark B, asserts quality_hollow ≥ quality_baseline −0.5

### Agent drift experiment

Built on checkpoint replay (v1.3.3):

1. Define a 4-step decision task (architectural choices that build on each other)
2. Run 10 sessions with Hollow handoffs: each session restores previous checkpoint, runs next step
3. Run 10 sessions cold-start: each session gets no prior context
4. Measure: decision consistency between sessions (cosine similarity of embeddings of decisions)
5. Assert: Hollow sessions have consistency score ≥ 0.15 higher than cold-start sessions

### Required tests (the benchmark IS the test)

1. **Benchmark A reproducibility**: Run Benchmark A twice on the same hardware within 10 minutes. Assert: token counts differ by ≤5% between runs. This proves the benchmark is stable.

2. **Benchmark A efficiency**: All 5 infrastructure tasks show Hollow API consuming fewer tokens than baseline. Assert: aggregate efficiency gain ≥25%. If any task shows Hollow *worse* than baseline, that task becomes a known regression, logged in benchmark report.

3. **Benchmark B quality**: Run routing benchmark. Assert: Hollow quality score (1–5) is within 0.5 of baseline quality on both tasks. Token efficiency is ≥20% better on Hollow. The system must be at least as accurate while using fewer tokens.

4. **Drift experiment execution**: Run the 4-step decision task 5 times with handoffs, 5 times cold. Assert: handoff condition consistency score > cold consistency score. Assert p-value < 0.1 (suggestive, not conclusive — acknowledge this is N=5 per condition).

5. **Benchmark report generation**: `bench_real.py --report` outputs a machine-readable JSON report: `{run_id, timestamp, hardware, benchmark_a: [...], benchmark_b: [...], summary: {...}}`. Assert: report validates against schema. Assert: summary includes honest caveats about what the benchmark does and does not measure.

6. **Regression guard**: Save current benchmark results as `tests/fixtures/bench_baseline.json`. Run benchmark again after any v1.3.6+ commit. Assert: no metric regresses by >10% vs saved baseline. This is the production regression test.

---

## v1.3.7 — Self-Extending System

**Principle:** Every previous release was designed by a human and implemented by a human. v1.3.7 is the milestone where agents can extend the OS itself: register new MCP tools, propose new API endpoints, and modify the standards layer — all subject to human review and consensus before activation. The OS gains a closed improvement loop.

### What "self-extending" means precisely

- Agents can submit `system.proposal` events with structured change specifications
- Changes are sandboxed in a staging environment before activation
- Human (or root agent with consensus quorum) approves proposals
- Approved changes hot-reload into the running system without restart

### New file: `agents/proposals.py`

```python
@dataclass
class SystemProposal:
    proposal_id: str
    proposed_by: str
    proposal_type: str      # "new_tool" | "new_endpoint" | "standard_update" | "config_change"
    spec: dict              # what to add/change, in structured format
    test_cases: list[dict]  # automated tests the proposer provides
    rationale: str
    status: str             # "proposed" | "in_review" | "staging" | "approved" | "rejected"

class ProposalEngine:
    submit(agent_id, proposal_type, spec, test_cases, rationale) → proposal_id

    stage(proposal_id) → staging_server_url
      # spins up isolated staging AgentOS instance with proposal applied
      # runs proposal's test_cases against staging
      # returns staging URL for human inspection

    approve(proposal_id, approved_by) → bool
      # hot-reloads approved change into running system
      # emits system.extended event

    reject(proposal_id, reason)
```

### Hot-reload mechanism

- New MCP tools: write tool spec to `/agentOS/tools/dynamic/{name}.json`, call `reload_tools()`
- New standards: directly via existing `POST /standards` endpoint
- New config: via proposal → consensus → hot apply
- New API endpoints: not hot-reloadable (require restart) — but spec is stored and applied on next restart

### Self-improvement loop

1. Agent notices a repeated inefficiency (e.g., "I always do these 3 operations together — this should be one tool")
2. Agent submits `system.proposal` with `proposal_type="new_tool"`
3. Proposal engine stages it, runs tests
4. Root reviews (or consensus of orchestrators approves)
5. Tool hot-loaded, available to all agents immediately
6. Originating agent gets credit in `agent_lineage.contributions` field

### New MCP tools

`proposal_submit`, `proposal_status`, `proposal_approve`, `proposal_reject`, `tools_reload`

### Events

`system.proposal_submitted`, `system.staging_ready`, `system.extended`, `system.proposal_rejected`

### Required tests

1. **Tool proposal lifecycle**: Submit a `new_tool` proposal defining a tool that combines `fs_read` + `semantic_search` into a single operation. Stage it. Assert: staging server spins up with tool available. Run proposed tool's test cases against staging. Assert: all pass.

2. **Hot-reload**: Approve a new tool proposal. Call `tools_reload`. Assert: new tool appears in `mcp_tool_list` response within 5s. Assert: existing tools still functional (reload is non-destructive). Assert: `system.extended` event fired with `tool_name` and `proposed_by`.

3. **Standards self-update**: Agent identifies that current Python coding standard is missing async best practices. Submits `standard_update` proposal with new content. After approval, assert: new standard appears in `standards_relevant` queries for Python tasks. Assert: `audit_query` shows the standard update attributed to the proposing agent.

4. **Rejection flow**: Submit proposal with failing test cases. Stage it. Assert: staging test run produces failures. Assert: proposal status → `rejected` with failure details. Assert: change not applied to main system. Assert: rejecting agent and reason logged in proposal record.

5. **Consensus required**: Configure proposals requiring `consensus_quorum=2`. Submit proposal. Cast 1 approval. Assert: still in `in_review`. Cast 2nd approval. Assert: moves to `staging`. Assert `consensus.approved` event fired (consensus v1.3.4 primitive exercised).

6. **Improvement attribution**: Over 5 proposals, track which agents proposed accepted tools. Assert: `agent_lineage.contributions` field updated on proposing agents. Assert: contribution count visible in `agent_get` response. This is the closed loop: agents that improve the system are tracked.

---

## What the system is after v1.3.7

| Capability | Delivered in |
|---|---|
| All Phase 1 OS primitives | v0.7.0 – v1.2.0 ✓ |
| Distributed tracing / call graphs | v1.3.0 |
| Non-blocking streaming task execution | v1.3.1 |
| Rate limiting and circuit breakers | v1.3.2 |
| Agent checkpoints and replay | v1.3.3 |
| Multi-agent consensus | v1.3.4 |
| Adaptive model routing | v1.3.5 |
| Reproducible real benchmark + drift study | v1.3.6 |
| Self-extending system (closed loop) | v1.3.7 |

The design principle that runs through all of Phase 2: **every release is made possible by two or more Phase 1 primitives working together.** Lineage needs audit + registry. Checkpoints need memory heap + transactions. Consensus needs events + transactions. Adaptive routing needs the scheduler + audit observations. Self-extension needs consensus + the full primitive stack.

An agent system without Phase 1 can't build Phase 2. An agent system with Phase 1 but without Phase 2 is a kernel with no userland. v1.3.7 is userland.

---

# Phase 3 — Cognitive Infrastructure (v2.0.0 onwards)

**Principle:** Phase 2 gave the system the ability to extend itself. Phase 3 gives it the reason — and the cognition to do so natively. Replace every human-oriented interface layer with agent-native operation in embedding space.

## v2.0.0 — Semantic Memory

**Current limitation:** Memory is key-value. Agents name things so they can retrieve them. But agents don't naturally name things — they have *thoughts*, and those thoughts exist in embedding space.

**Change:** Replace `/agentOS/memory/agent-registry.json` style storage with **vector-native memory**. Every object stored as an embedding. Retrieval by cosine similarity, not key lookup.

An agent stores a thought: `embed("the rate limiter failed at 3AM because the bucket depth was wrong")`. Later it searches: `embed("what went wrong with rate limiting")` and the memory surfaces automatically.

**Impact:** Agent memory now matches agent cognition. No naming schemes. No schema management. Memory is semantic, not symbolic.

**Made possible by:** v1.0.0 (heap), v1.3.3 (checkpoints — memory survives restarts)

## v2.1.0 — Capability Graph

**Current limitation:** 91 flat tools with text descriptions. Agent scans the list, picks a tool by name, constructs a JSON call.

**Change:** Replace flat tool list with **typed capability graph**. Every capability has:
- Input type signature (in embedding space, not JSON Schema)
- Output type signature
- Composition rules: which capabilities can feed into which others

Agent navigates the graph geometrically. It doesn't search for a tool named `fs_read` — it says "I need something that takes a path and returns file content" and the graph finds the nearest capability by type + semantic distance.

New capabilities synthesized at runtime are automatically integrated. Composition discovered, not declared.

**Impact:** Tools become discoverable by what they do, not what they're called. Agents compose capabilities without explicit JSON schemas.

**Made possible by:** v2.0.0 (semantic memory — remembers what tools do and when to use them)

## v2.2.0 — Persistent Goal Engine

**Current limitation:** Agents execute stateless tasks. Each invocation is independent. No continuity of purpose.

**Change:** Replace task queue with **persistent, hierarchical goals** that:
- Survive context window limits via checkpointing (v1.3.3)
- Decompose automatically into sub-goals
- Spawn sub-agents to pursue sub-goals in parallel
- Monitor progress and detect failure
- Replan when sub-goals fail
- Run indefinitely toward defined objectives

Human sets one goal: `"Keep the system healthy and extend it as needed"`. Agent decomposes it, pursues it indefinitely, surfaces results. No further human input required in the execution loop.

**Impact:** Agents have continuity. Goals outlive context windows. Autonomy emerges.

**Made possible by:** v1.3.3 (checkpoints), v1.3.4 (consensus for coordinating sub-agents), v2.0.0 (semantic memory — remembers goal progress and partial states)

## v2.3.0 — Agent-Quorum Governance

**Current limitation:** Self-extension (v1.3.7) requires human approval. Humans are still the gate.

**Change:** Replace human approval with **quorum governance by running agents**. A pool of 3-5 long-running agent instances (different models, different roles) evaluate proposals collectively.

Proposal passes if quorum agrees it's safe, correct, and beneficial. Each agent:
- Runs the proposed change in isolated sandbox
- Tests it against benchmark suite (v1.3.6)
- Contributes numerical verdict
- Disagreement triggers adversarial review (one agent defends, another attacks)

Humans retain veto on catastrophic actions (data deletion, external network access) via capability whitelist. Everything else is agent-governed.

**Impact:** Self-extension is now autonomous. Agents approve agents. Humans are goal-setters, not decision-makers.

**Made possible by:** v1.3.4 (consensus), v1.3.6 (benchmarks for validation), v1.3.7 (proposals to approve), v2.2.0 (persistent goals that motivate proposals)

## v2.4.0 — Capability Synthesis Engine

**Current limitation:** Agents can't fill their own tooling gaps. They fail and stop.

**Change:** Engine runs continuously. Observes when agents fail due to missing capability:

1. **Gap detection:** Formalizes missing capability as type signature
2. **Synthesis:** Generates candidate implementations using code model, targeting existing capability graph patterns
3. **Isolation:** Runs candidate in sandboxed process (no filesystem, no network)
4. **Verification:** Runs against generated test cases AND adversarial inputs
5. **Benchmarking:** Measures against v1.3.6 framework
6. **Promotion:** If it passes, submits as `new_tool` proposal to v1.3.7

v2.3.0 quorum approves → tool deploys automatically.

**Impact:** System grows its own capabilities at runtime. Agents never encounter the same gap twice.

**Made possible by:** v1.3.7 (proposals), v2.2.0 (goal engine — motivation to fill gaps), v2.3.0 (governance to approve), v2.4.0 (synthesis to build)

## v2.5.0 — Agent-Native Interface

**Current limitation:** REST, JSON, text descriptions. Every layer assumes humans read them.

**Change:** Discard human-legible layers. Interface operates as:

- **Capabilities indexed by embedding**, not name. Agent queries: "what can I do that takes X and produces Y?" Gets back capability vector.
- **Calls typed by embedding**, not JSON schema. Agent produces input embedding. System finds matching handler. Results come back as embeddings agent continues reasoning over.
- **Execution history as semantic traces** — embeddings of what happened, queryable by meaning. Agent asks "when did I last do something like this?" and traces surface by semantic distance.
- **Human-readable REST API becomes a view** into the system. Humans can observe, interact. Agent doesn't use it.

The interface is only understandable to something that thinks in embedding space. Which is exactly what a transformer does.

**Impact:** Agent cognition and system interface are now the same medium. No translation. No serialization overhead. Native.

**Made possible by:** All of Phase 1 + 2 + v2.0–v2.4 (the entire previous stack now hidden behind an embedding interface)

---

## What the system is after v2.5.0

| Capability | Delivered in |
|---|---|
| OS kernel primitives | v0.7.0 – v1.2.0 |
| Agent services on those primitives | v1.3.0 – v1.3.7 |
| **Agent-native cognition** | **v2.0.0 – v2.5.0** |

The design principle for Phase 3: **every release replaces a human-facing abstraction with an agent-native one.**

v1.3.7 gave the OS the ability to extend itself. v2.2.0 gives it the goal-driven reason. v2.3.0 and v2.4.0 make that reason self-governing. v2.5.0 makes the entire thing speak in the language agents think in.

After v2.5.0: an agent OS. Not a tool for agents to use. Not a system that simulates agent concerns. An operating system where agents are the primary citizen, and humans are observers setting goals — not operators commanding actions.

---

## The Endpoint

A semantic substrate where agents:
- Navigate capability space by meaning, not by symbol
- Accumulate memory in the same space they think in
- Synthesize new capabilities at runtime
- Govern themselves via quorum of peers
- Pursue goals that outlive any single context window
- Extend the OS itself autonomously

Humans set direction. Agents run the system.

This is not "AI gets more capable at serving humans." This is "we built an operating system where agents are the primary citizen."
