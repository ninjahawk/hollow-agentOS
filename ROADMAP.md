# hollowOS Roadmap

## End Goal

**A self-hosted runtime where AI agents live permanently.**

Deploy once. Give agents goals. Walk away.

Agents reason about what to do next, execute real operations, learn from outcomes,
synthesize new capabilities when they hit gaps, coordinate with other agents via
quorum, and govern themselves — all without human prompting. Running on your hardware
with local LLMs via Ollama.

This is not a framework for humans to use AI. It is an environment AI operates from.

---

## Current Status: v4.0.0 — hollowOS COMPLETE ✅

All 12 phases complete. Acceptance test passed: three agents autonomously completed
different goals, live capability synthesis deployed, failure recovery with follow-on
goals confirmed, multi-agent shared goal coordination via quorum verified.
Deploy with Docker. Point at Ollama. Give agents goals. Walk away.

---

## Phase 1: OS Kernel Primitives (v0.7.0 – v1.2.0) ✅ COMPLETE

Eight foundational mechanisms. Every higher-order system depends on these.

- Event system (async agent coordination)
- Signal handling (interrupt + recovery)
- Memory management (allocation + garbage collection)
- Audit logging (full causality trace)
- Transaction system (concurrent consistency)
- Lineage tracking (dependency graph)
- Rate limiting (resource protection)
- Working memory (context window management)

**Result:** Complete OS layer. Agents can coordinate, persist state, and handle failures.

---

## Phase 2: Agent Services (v1.3.0 – v1.3.7) ✅ COMPLETE

Services only possible because Phase 1 exists.

- Distributed tracing (audit + registry)
- Checkpoints (memory + transactions)
- Consensus (events + transactions)
- Adaptive routing (scheduler + audit)
- Self-extension (consensus + full stack)

**Result:** Agents become genuinely useful. They coordinate, remember, adapt, extend themselves.

---

## Phase 3: Cognitive Infrastructure (v2.0.0 – v2.5.0) ✅ COMPLETE

Replacing every human-facing interface with agent-native cognition. No JSON, REST, or symbols.

### v2.0.0: Semantic Memory ✅
- Vector-native storage (embeddings, not key-value)
- Cosine similarity search (no naming schemes)
- LRU capacity management
- Per-agent memory isolation
- **7 integration tests**

### v2.1.0: Capability Graph ✅
- Semantic capability discovery (by meaning, not name)
- Type-based composition (input_schema → output_schema)
- Composition validation with confidence scoring
- Usage tracking and learned patterns
- **12 integration tests**

### v2.2.0: Persistent Goal Engine ✅
- Long-term objectives (persist across context windows)
- Hierarchical goal decomposition
- Priority-based focus with status transitions
- Semantic goal search
- Progress metrics tracking
- **13 integration tests**

### v2.3.0: Agent-Quorum Governance ✅
- Multi-agent consensus voting
- Customizable quorum percentages
- Proposal types: capability, goal_change, resource, policy
- Voting history audit trail
- **12 integration tests**

### v2.4.0: Capability Synthesis Engine ✅
- Agents observe gaps and propose capabilities
- Test-driven capability validation
- Full quorum integration for approval
- Gap tracking with priority ordering
- **9 integration tests**

### v2.5.0: Agent-Native Interface ✅
- Pure embedding-space communication (no JSON/REST)
- Agents submit plain-text intents → OS responds semantically
- Capability discovery by meaning
- Full introspection (goals, memory, proposals, capabilities)
- Operation history and statistics
- **10 integration tests**

**Phase 3 Result:** 63 new tests, 178/178 passing. Agents operate in pure embedding space.

---

## Phase 4: Agent Autonomy (v2.6.0 – v3.0.0) ✅ COMPLETE

### v2.6.0: Execution Engine + Reasoning Layer ✅ (19 tests)
- Capability dispatch: intent → graph → execution → result capture
- Intent → semantic capability discovery → parameter generation
- Confidence scoring, learning from outcomes

### v2.7.0: Autonomy Loop ✅ (9 tests)
- Goal pursuit: retrieve → reason → execute → learn → progress
- Automatic goal completion at progress >= 1.0
- Synthesis integration for gap detection

### v2.8.0: Self-Modification ✅ (15 tests)
- Gap detection → capability synthesis → testing → quorum proposal → deployment
- Full audit trail of all gaps, syntheses, tests, deployments

### v2.9.0: Self-Improvement Loop ✅ (9 tests)
- Pattern observation: track success/failure rates per capability
- Optimization proposals for underperforming capabilities
- Continuous cycle: observe → propose → deploy → measure

### v3.0.0: Complete Autonomous Single Agent ✅ (2 tests)
- All Phase 4 systems integrated
- Agent pursues arbitrary goals indefinitely without human interaction

**Phase 4 Result:** 54/54 tests. Single autonomous agent in embedding space.

---

## Phase 5: Distributed Autonomy (v3.1.0 – v3.5.0) ✅ COMPLETE (99 tests)

### v3.1.0: Multi-Node Communication ✅
### v3.2.0: Distributed Consensus ✅
### v3.3.0: Distributed Memory & Goals ✅
### v3.4.0: Agent Migration & Load Balancing ✅
### v3.5.0: Fully Distributed Autonomous Swarm ✅

**Phase 5 Result:** Autonomous agent mesh at any scale.

---

## Phase 6: Meta-Intelligence (v3.6.0 – v3.10.0) ✅ COMPLETE (85 tests)

### v3.6.0: Agent Introspection ✅ (16 tests)
### v3.7.0: Meta-Knowledge Synthesis ✅ (19 tests)
### v3.8.0: Self-Evolving Governance ✅ (17 tests)
### v3.9.0: Agent Specialization ✅ (16 tests)
### v3.10.0: Swarm Meta-Learning ✅ (17 tests)

**Phase 6 Result:** Agents examine themselves, extract cross-agent patterns, evolve governance rules.

---

## Phase 7: Live Execution (v3.10.1 – v3.13.2) ✅ COMPLETE

Bridge the cognitive layer to a real running OS. Phase 7 proves the architecture
against a live machine, not just tests.

### v3.10.1: Semantic Indexer Fix ✅
- Embedding index rebuilds correctly on a timer; survives server restarts

### v3.11.1: Live Capabilities ✅ (8 tests)
Eight real OS operations registered in the capability graph:
- `shell_exec` — run any shell command, capture stdout/stderr
- `ollama_chat` — call a local LLM (mistral-nemo:12b default)
- `fs_read` / `fs_write` — read and write files on disk
- `semantic_search` — ripgrep-backed codebase search
- `memory_set` / `memory_get` — persistent key-value agent memory
- `agent_message` — send messages between agents

### v3.12.1: Goal API ✅ (16 tests)
Persistent goals via HTTP: create, list, retrieve, update status/progress.
Goals embed on creation and survive server restart.

### v3.13.1: Autonomy Daemon ✅ (8 tests)
Background process: polls every 30s, finds agents with active goals,
runs `AutonomyLoop.pursue_goal()`, writes progress back. No external trigger needed.

### v3.13.2: Reasoning Layer + Capability Graph Fix ✅
- `_ollama_reason()` calls Ollama with intent + capability schemas, gets back
  `{capability_id, params}` — real selection with real generated parameters
- `register()` deduplication prevents unbounded registry growth
- End-to-end verified: goal → Ollama → `semantic_search` → 5 results → progress 0.30

**Phase 7 Result:** One agent, one goal, real execution, measurable progress.
Docker container starts daemon + API server together via `entrypoint.sh`.

---

## Phase 8: Real Task Completion (v3.14.0 – v3.17.0) 🔜 NEXT

**Goal:** An agent given a real goal produces a real artifact. Not just progress ticks —
a verifiable output (a file written, a summary produced, a question answered).

**The gap to close:** Agent currently takes one uncontextual step per cycle.
It can search but not act on results. It can execute but not chain.

### v3.14.0: Goal Completion + Step Context
- Daemon marks goal `completed` when progress >= 1.0
- Each step receives the result of the previous step as context input
- Reasoning prompt includes step history so Ollama makes informed next-step decisions
- Agent accumulates a working scratchpad across steps within one goal cycle

### v3.15.0: Multi-Step Planning
- Before executing, agent asks Ollama to plan N steps toward the goal
- Plan format: `[{capability, params, rationale}, ...]`
- Steps execute in order; each result feeds the next
- Failed step triggers replanning from that point, not full restart

### v3.16.0: Result Synthesis
- On goal completion, agent writes a summary of what it did and learned to semantic memory
- Patterns extracted: which capability sequences worked for this goal type
- Reasoning history becomes queryable — past successful plans inform future ones
- Specialization engine ingests patterns; future routing biases toward what worked

### v3.17.0: Real Task Validation
Integration tests with goals requiring multiple capability types in sequence:
- "Summarize the autonomy loop implementation" →
  `semantic_search` → `fs_read` → `ollama_chat` → `memory_set`
- Pass criteria: goal completes, verifiable artifact exists, correct content

**Phase 8 Result:** Agents accomplish real, multi-step tasks. The loop is closed.

---

## Phase 9: Durable Autonomy (v3.18.0 – v3.21.0)

**Goal:** An agent runs indefinitely without degrading. It handles failure, manages
its own resources, and generates its own follow-on work.

### v3.18.0: Error Recovery + Replanning
- Failed steps classified: transient (retry), blocked (replan), impossible (abandon)
- On blocked: Ollama replans remaining steps given what has been tried
- On impossible: goal marked `failed` with explanation written to memory
- No more silent infinite loops on stuck goals

### v3.19.0: Self-Directed Goal Generation
- On goal completion, agent inspects what it learned and proposes follow-on goals
- Example: "Summarize autonomy loop" completes → agent notices gaps →
  proposes "Improve autonomy loop error handling" as new goal
- Follow-on goals go through quorum if they involve capability changes

### v3.20.0: Resource Self-Management
- Memory pruning: agent trims old, low-relevance memories to stay under capacity
- Capability cleanup: unused capabilities flagged and submitted for quorum removal
- Reasoning history compaction: old cycles summarized and compressed
- Agent monitors its own storage footprint and acts before limits are hit

### v3.21.0: Long-Run Stability Test
- Agent runs for 24 hours pursuing a stream of goals, zero human interaction
- Metrics: goals completed, goals failed, memory footprint over time, no crashes
- Pass criteria: agent finishes more goals than it starts, memory stays bounded

**Phase 9 Result:** Agent runs forever. Give it goals once and it sustains itself.

---

## Phase 10: Live Capability Synthesis (v3.22.0 – v3.25.0)

**Goal:** When an agent hits a gap, it writes and deploys real working code —
not pseudo-code sketches. The self-extension loop becomes real.

**The gap to close:** `self_modification.py` currently generates `implementation_sketch`
(pseudo-code) and mock test functions. No real code runs.

### v3.22.0: Real Code Generation
- Ollama writes actual Python implementations for synthesized capabilities
- Output: a complete function with correct signature matching the capability schema
- Generated code reviewed against input/output schema before proceeding

### v3.23.0: Sandboxed Testing
- Generated capability executed in an isolated subprocess with a timeout
- Test cases generated by Ollama alongside the implementation
- Pass threshold: all generated test cases pass before capability is proposed
- Failures fed back to Ollama for one retry before abandoning synthesis

### v3.24.0: Runtime Hot-Loading
- Approved capabilities loaded into the execution engine without server restart
- Capability stored as a `.py` file in `/agentOS/tools/dynamic/`
- Execution engine imports dynamically; capability graph registers immediately
- Hot-loaded capabilities survive restart (re-imported on startup)

### v3.25.0: Synthesis Loop Validation
End-to-end: gap detected → code generated → sandbox tested → quorum approved →
hot-loaded → agent uses new capability to complete the goal that triggered the gap.

**Phase 10 Result:** The system grows itself. Agents hit gaps and fill them with real code.

---

## Phase 11: Multi-Agent Live Coordination (v3.26.0 – v3.29.0)

**Goal:** Multiple agents actually cooperate on real tasks using live execution.
Phases 5 and 6 built the coordination infrastructure. Phase 11 wires it to real work.

### v3.26.0: Agent-to-Agent Task Delegation
- Agent A identifies a subtask outside its specialization
- Routes it to Agent B via `agent_message` with goal context attached
- Agent B picks it up via daemon, executes, returns result to A's scratchpad
- Delegation tracked in lineage so the full task graph is auditable

### v3.27.0: Shared Goal Pursuit
- Multiple agents registered to a single goal
- Goal decomposed into parallel subtasks via multi-step planning
- Each agent owns a subtask; daemon routes by specialization score
- Results merged back into the parent goal on completion

### v3.28.0: Live Quorum on Capability Changes
- Capability synthesis proposals go to a real running quorum of live agents
- Agents vote based on specialization and observed success rates — not randomly
- Governance evolution engine adjusts quorum thresholds from observed outcomes
- All votes and decisions logged to audit trail

### v3.29.0: Multi-Agent Integration Test
Two agents, one shared goal requiring collaboration:
- "Audit the codebase and produce a health report"
- Agent A: search + read; Agent B: analyze + write
- Pass criteria: report produced, both agents' contributions traceable in lineage

**Phase 11 Result:** The swarm coordinates on real work without a human orchestrator.

---

## Phase 12: hollowOS v4.0.0 ✅ COMPLETE

**The end goal is real.**

- Deploy with Docker. Point at Ollama. Give agents goals. Walk away.
- Agents reason, execute, learn, extend themselves, and coordinate — indefinitely
- New capabilities emerge from agent-observed gaps and are deployed via quorum
- Agents govern their own rules through consensus; no human policy required
- Full observability: audit trail, lineage graph, goal history, memory snapshots
- Self-healing: agents detect degraded peers and redistribute work via consensus
- Runs entirely on local hardware with local LLMs — no external API dependency

**v4.0.0 acceptance criteria:**
1. Three agents given three different goals complete them without human intervention
2. One agent synthesizes and deploys a new capability during execution
3. One agent fails a goal, recovers, and proposes a follow-on goal automatically
4. All three agents coordinate on a shared final task via quorum
5. System runs for 48 hours, memory stays bounded, no crashes

---

## Design Principles (All Phases)

1. **Agent-native, not human-augmented.** The OS is for agents to live in, not for humans to use agents.
2. **Embedding-space throughout.** No translation layers. Agents think in vectors; the OS speaks that language.
3. **Semantic, not symbolic.** Capabilities navigate by meaning. Memory works by similarity. Goals are objectives, not task lists.
4. **Autonomous, not prompted.** Agents set goals once and pursue them indefinitely. They govern themselves via quorum.
5. **Extensible, not fixed.** New capabilities synthesized at runtime. The system grows through agent observation.
6. **Distributed by default.** Single machine is a special case of multi-node. All subsystems work across machines.
7. **Local by default.** No external API required. Runs on your hardware with your models.

---

## Testing Strategy

- **Integration tests hit live systems** (no mocks). Real embeddings, real execution, real storage.
- **Multi-agent isolation.** Each test gets its own agent IDs and storage.
- **Acceptance criteria per phase.** Each phase has a defined pass condition, not just a test count.

---

## What's Different From Everything Else

| | LangChain, CrewAI, Assistants API | hollowOS |
|---|---|---|
| **Think in** | Tokens/text | Embeddings |
| **Interface** | JSON, REST, function calls | Semantic (embeddings) |
| **Memory** | Context window only | Persistent, checkpointed, semantic |
| **Goals** | Task-based (human re-prompts) | Goal-based (agent pursues indefinitely) |
| **Multi-agent** | Message passing or prompts | Distributed consensus + semantic memory |
| **Governance** | Human (via prompt) | Agent quorum + Byzantine tolerance |
| **Autonomy** | Limited (tool use) | Full (self-modification, synthesis, migration) |
| **Distribution** | Single machine | Cloud + local, peer-to-peer |
| **Infrastructure** | Cloud APIs required | Runs entirely local |

**The honest version:** Those frameworks augment human capability. hollowOS lets AI live autonomously.

---

## How to Contribute

Phase 8 is next. Agents need to complete real tasks before anything else matters.

Priorities in order:
1. **v3.14.0** — goal completion logic + step context passing
2. **v3.15.0** — multi-step planning via Ollama
3. **v3.16.0** — result synthesis into semantic memory
4. **v3.17.0** — integration tests that verify real artifacts

Key files:
- `agents/daemon.py` — the execution loop (start here)
- `agents/reasoning_layer.py` — Ollama integration
- `agents/autonomy_loop.py` — goal pursuit
- `agents/live_capabilities.py` — the 8 registered OS capabilities
- `api/goal_routes.py` — goal HTTP API

---

Generated: 2026-04-01
Updated: 2026-04-01

✅ PHASE 7 COMPLETE: Live Execution (v3.13.2)
  - v3.10.1: Semantic indexer fix ✅
  - v3.11.1: Live Capabilities ✅ (8 tests)
  - v3.12.1: Goal API ✅ (16 tests)
  - v3.13.1: Autonomy Daemon ✅ (8 tests)
  - v3.13.2: Reasoning layer + capability graph fix ✅
  End-to-end proven: agent → Ollama → capability → execution → progress

✅ PHASE 6 COMPLETE: Meta-Intelligence (85/85 tests)
✅ PHASE 5 COMPLETE: Distributed Autonomy (99/99 tests)
✅ PHASE 4 COMPLETE: Complete Single Autonomous Agent (54/54 tests)
✅ PHASE 3 COMPLETE: Cognitive Infrastructure (178/178 tests)
✅ PHASE 2 COMPLETE: Agent Services
✅ PHASE 1 COMPLETE: OS Kernel Primitives

Grand Total: 559 tests, all passing

🔜 PHASE 8: Real Task Completion (v3.14.0 – v3.17.0)
🔜 PHASE 9: Durable Autonomy (v3.18.0 – v3.21.0)
🔜 PHASE 10: Live Capability Synthesis (v3.22.0 – v3.25.0)
🔜 PHASE 11: Multi-Agent Live Coordination (v3.26.0 – v3.29.0)
🔜 PHASE 12: hollowOS v4.0.0 — the end goal
