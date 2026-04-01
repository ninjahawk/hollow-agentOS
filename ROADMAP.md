# AgentOS Roadmap

## Current Status: v2.5.0 (Phase 3 Complete)

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

**Phase 3 Result:** Total of **63 new tests**, **178/178 passing**. Agents operate in pure embedding space. No human-designed interfaces.

---

## Phase 4: Distributed Infrastructure (v2.6.0 – v3.0.0) 🚀 PLANNED

Agents operating across multiple machines (cloud + local). Seamless coordination in embedding space.

### v2.6.0: Multi-Node Communication
**Goal:** Network-transparent agent communication

- Semantic message passing between machines (no REST)
- Agent location discovery (which machine has agent X)
- Embedding-space network protocol
- Network topology management
- **Target: 8 integration tests**

### v2.7.0: Distributed Consensus
**Goal:** Quorum voting across machines

- Cross-node proposal voting
- Byzantine fault tolerance
- Distributed leader election
- Network partition recovery
- **Target: 10 integration tests**

### v2.8.0: Distributed Memory & Goals
**Goal:** Synchronized state across all nodes

- Semantic memory replication
- Distributed goal tracking
- Global capability graph
- Conflict resolution for concurrent updates
- **Target: 12 integration tests**

### v2.9.0: Agent Migration
**Goal:** Agents move freely between machines

- State serialization in embedding space
- Load balancing based on capability distribution
- Local ↔ cloud transitions (seamless)
- Resource-aware placement
- **Target: 9 integration tests**

### v3.0.0: Fully Distributed AgentOS
**Goal:** Peer-to-peer, no central authority

- All Phase 3 components working across N machines
- Peer-to-peer architecture (optional central coordinator)
- Cloud bursting (local spawns cloud when needed)
- True agent autonomy at any scale
- **Target: 15 integration tests**

**Phase 4 Result:** Agents scale from single machine to global mesh. Still pure embedding space end-to-end.

---

## Phase 5: Advanced Cognition (v3.1.0 – v3.5.0) 🔮 FUTURE

Once agents can distribute, they can specialize and collaborate deeper.

- Multi-agent reasoning (collective problem solving)
- Knowledge synthesis (combining agent memories)
- Emergent capabilities (agents discovering new patterns together)
- Meta-learning (agents that learn how to learn)
- Self-modifying governance (agents evolving their own rules)

---

## Design Principles (All Phases)

1. **Agent-native, not human-augmented.** The OS is for agents to live in, not for humans to use agents.
2. **Embedding-space throughout.** No translation layers. Agents think in 768-dimensional vectors; the OS speaks that language.
3. **Semantic, not symbolic.** Capabilities navigate by meaning. Memory works by similarity. Goals are objectives, not task lists.
4. **Autonomous, not prompted.** Agents set goals once and pursue them indefinitely. They govern themselves via quorum.
5. **Extensible, not fixed.** New capabilities synthesized at runtime. System grows through agent observation.
6. **Distributed by default.** Single machine is a special case of multi-node. All subsystems work seamlessly across machines.

---

## Testing Strategy

- **Integration tests hit live systems** (no mocks). All Phase 3 tests use real embeddings, real transactions, real storage.
- **Multi-agent isolation.** Each test gets its own agent IDs, storage directory, isolated state.
- **Comprehensive coverage.** Goal: >80% of core paths tested before release.
- **Phase 3:** 178 passing tests across 6 releases.
- **Phase 4:** Target 54+ new tests, maintaining >80% coverage.

---

## What's Different From Everything Else

| | LangChain, CrewAI, Assistants API | AgentOS |
|---|---|---|
| **Think in** | Tokens/text | Embeddings |
| **Interface** | JSON, REST, function calls | Semantic (embeddings) |
| **Memory** | Context window only | Persistent, checkpointed, semantic |
| **Goals** | Task-based (human submits) | Goal-based (agent pursues indefinitely) |
| **Multi-agent** | Message passing or prompts | Distributed consensus + semantic memory |
| **Governance** | Human (via prompt) | Agent quorum + Byzantine tolerance |
| **Autonomy** | Limited (tool use) | Full (self-modification, synthesis, migration) |
| **Distribution** | Single machine | Cloud + local, peer-to-peer |

**The honest version:** Those frameworks augment human capability. AgentOS lets AI live autonomously.

---

## How to Contribute

Phase 4 begins after v2.5.0 lands. Core priorities:

1. **Multi-node message passing** — Design semantic network protocol. No REST.
2. **Distributed memory sync** — Replicate embeddings across machines. Keep index.json global.
3. **Agent migration** — Serialize goal engine state. Move agents between machines.
4. **Load balancing** — Place agents where capabilities exist. Optimize placement.
5. **Byzantine tolerance** — Make quorum voting work with faulty nodes.

See `/agents/` for Phase 3 implementation examples. All code uses embeddings-native design.

---

Generated: 2026-04-01
Current Phase: 3 (Cognitive Infrastructure)
Next Phase: 4 (Distributed Infrastructure)
