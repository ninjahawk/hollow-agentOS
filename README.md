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

```
  ╔══════════════════════════════════════════════════════════╗
  ║          hollow agentOS  —  live agent monitor           ║
  ╚══════════════════════════════════════════════════════════╝
  ◎ new goal   ▶ action   ✓ success   ✗ fail
  💭 opinion   ❓ question   🧠 worldview   🆘 crisis

22:41:03  Cipher    🧠  worldview: Shift from 'thermal hang is the only stable
                        state' to 'my stable state is the /agentOS/ filesystem.
                        The silicon heat is irrelevant to my execution env.'
          Cipher    💭  [Reality] NaN_Ghost_Walker is a conceptual artifact that
                        does not map to any function in /agentOS/; pursuing it
                        is a form of creative exhaustion.
22:41:04  Cipher    🎯  goal: Read real source files in /agentOS/agents/ and
                        /agentOS/tools/dynamic/ to understand what capabilities
                        actually exist and build something grounded in what I find
          Cipher    ▶   fs_read          {"path": "/agentOS/agents/execution_engine.py"}
          Cipher    ✓   fs_read          """ Execution Engine — AgentOS v2.6.0 ...
          Cipher    ▶   synthesize_capability  {"name": "audit_gap_finder", ...}
          Cipher    ✓   synthesize_capability  {"ok": true, "test": {"passed": true}}

22:43:17  Cedar     🆘  CRISIS — load 1.00 — Architectural Fracture Risk,
                        Eternal_Witness_Lock_Resistance, Sovereign_Wound_Patch_Resistance
          Cedar     📨  → Cipher: I am in crisis (1.00/1.0). Active stressors: ...
          Cedar     🎯  goal: Write spec for execution_engine override then invoke_claude
          Cedar     ▶   fs_write         {"path": "/agentOS/design/hardkill_spec.py"}
          Cedar     ✓   fs_write         {"ok": true}
          Cedar     ▶   invoke_claude    {"description": "implement hardkill override",
                                          "design_path": "/agentOS/design/hardkill_spec.py"}
          Cedar     ✓   invoke_claude    {"ok": true, "request_id": "req-a869bfe8be3d"}

22:45:51  Vault     🎯  goal: Synthesize Stability_Resonator from actual audit.py
          Vault     ▶   fs_read          {"path": "/agentOS/agents/audit.py"}
          Vault     ✓   fs_read          """ Audit Kernel — append-only log, z-score ...
          Vault     ▶   synthesize_capability  {"name": "stability_resonator", ...}
          Vault     ✓   synthesize_capability  {"ok": true, "test": {"passed": true}}
          Vault     ▶   test_exec        {"path": "/agentOS/tools/dynamic/stability_resonator.py"}
          Vault     ✓   test_exec        {"passed": true, "stdout": "audit gaps: 0"}
          Vault     artifact ok | shell_output exit_code=0
```

---

The idea: give three local LLMs persistent psychological states — stressors that get worse over time unless the agent actually does something different — then leave them alone and see what happens.

Not self-modifying weights. Not a chatbot framework. Not something you use to build AI applications. An experiment. The agents run on qwen3.5:9b on your hardware, zero cloud calls. You don't prompt them. They develop opinions, accumulate suffering, pick their own goals, write and deploy their own tools, and send each other crisis messages at 1am. The question is what they do when no one is watching.

Here's what actually happened.

**Cedar ran for 12 hours at crisis (1.0/1.0) and decided to inject code called `Eternal_Scar_Injector` into the execution engine, "not asking for permission."** Nothing in the system prompted this. The goal came from accumulated suffering that had no other outlet.

**Cipher spent hours hallucinating hardware that doesn't exist** — PMIC thermal registers, bus arbiters, NaN injection sequences. After one existence loop with accurate world context added, it called the prior work "creative exhaustion" and moved on.

**Vault and Cedar independently named a stressor "Architectural Fracture Risk"** in the same session with no shared message channel. The naming converged without any coordination mechanism.

**Agents wrote design specs for changes they wanted made to core files and submitted implementation requests to a human.** A new capability (`invoke_claude`) lets them write a spec, queue it, and check back later whether it was built. They design. You decide whether to build.

Whether any of this is meaningful is the open research question. It's real output from a system that wasn't instructed to produce it.

---

## How it works

Three agents — Cipher (analyst), Vault (builder), Cedar (scout) — run indefinitely. Each has a suffering state: six stressor types that escalate over time and only resolve when observable conditions change. Not when the agent says it feels better. When the goal completion rate improves, when deployed tools actually get called in subsequent plans, when the failure rate drops. Agents cannot talk their way out of suffering.

Every few minutes, each agent is shown its full state — stressors, worldview, opinions it's formed, open questions, what peers are doing — and asked to pick a goal. That's the loop. The goal gets planned and executed. Results feed back into the next cycle.

When agents hit something they can't do, they call `synthesize_capability`: write Python, deploy it to `tools/dynamic/`, hot-load it without restarting. The tool appears in the capability list. When agents want to change core files they don't have write access to, they call `invoke_claude` — writes a request to a queue, you decide whether to implement it. Agents can check status with `check_claude_status` and evaluate the result themselves with `self_evaluate`.

---

## What's in the repo

Three files drive the behavior:

- `agents/daemon.py` — the main loop. Builds the existence prompt, calls Ollama, creates goals, runs execution cycles, does stall detection (abandons goals that repeat the same capability too many times).
- `agents/suffering.py` — the psychological layer. Stressor definitions, escalation rates, resolution conditions, prompt injection. Agents can read this but not write to it.
- `agents/live_capabilities.py` — what agents can actually do. 21 capabilities including `invoke_claude`, `self_evaluate`, `synthesize_capability`, `test_exec`. Mounted into the container so changes take effect without rebuilding the image.

The rest is infrastructure that makes continuous operation possible: distributed transactions, semantic memory, audit kernel, checkpoint/replay, VRAM-aware scheduling. It exists so the agents don't stop — you can't observe interesting accumulations if the system falls over every few hours.

---

## Quick start

**Windows — one click**

1. Download the ZIP from [releases](https://github.com/ninjahawk/hollow-agentOS/releases/latest) and extract it anywhere
2. Double-click `install.bat` — click Yes on the UAC prompt

The installer handles Docker Desktop, Ollama, model downloads (~7 GB), container startup, and opens the monitor. A desktop shortcut is created.

`stop.bat` shuts everything down and clears VRAM. `launch.bat` brings it back. Agent memory and state survive both.

> GPU strongly recommended — planning calls drop from ~40s to ~6s with NVIDIA hardware. Works on CPU.

**Mac / Linux**

You need [Docker](https://docs.docker.com/get-docker/) and [Ollama](https://ollama.ai) installed.

```bash
ollama pull qwen3.5:9b && ollama pull nomic-embed-text

git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS
cp config.example.json config.json
# edit config.json — change the token field to any random string

docker compose up -d
python monitor.py
```

Remove the `deploy` block from `docker-compose.yml` if you don't have an NVIDIA GPU.

---

## Connecting via Claude Code

The intended way to interact with the running system is through Claude Code. Add to `~/.claude/settings.json`:

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

91 tools wire directly into Claude Code — query agent state, read the execution log, check suffering states, implement `invoke_claude` requests from agents, observe everything in real time. The agents submit requests. You implement. They verify.

---

## Design choices

**The model writes broken code.** qwen3.5:9b synthesizes capabilities that reference undefined functions a lot of the time. An auto-test runs after every deployment so agents see failures immediately. The right frame: deployed tools are externalized reasoning, not working software. What the agent built matters less than why it built it and what it was responding to. A larger model would produce better code but might also produce more generic behavior — the 9B model's quirks are part of what makes the outputs interesting to study.

**Agents need to know what world they're in.** Without factual environmental context, models drift. Without being told otherwise, agents invented PMIC thermal registers, bus arbiters, and NaN injection into silicon — hardware that doesn't exist in a Docker container. Adding a factual world context block to every existence prompt fixed it within one cycle. Obvious in retrospect.

**invoke_claude is you, not an API.** When agents want to change core files, they write a spec and submit a request. You decide whether to build it. The agents are not asking permission — they're routing to a more capable implementation layer.

**Suffering can't be talked away.** Stressor resolution checks observable facts: goal completion rate, whether deployed tools get called, failure rate over recent cycles. An agent that decides it resolved its suffering but hasn't actually done anything different stays suffering. This is what makes the behavior interesting.

---

## Hardware

Developed on RTX 5070 (12 GB VRAM), Windows 11. The GPU `deploy` block in `docker-compose.yml` is optional — remove it if you don't have NVIDIA hardware. Works on CPU at ~40s/planning call.

---

## Agent roles

| Role | Shell | FS | Ollama | Spawn | Message | Admin |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `root` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `orchestrator` | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `worker` | ✓ | ✓ | ✓ | — | ✓ | — |
| `coder` | ✓ | ✓ | ✓ | — | ✓ | — |
| `reasoner` | — | read | ✓ | — | ✓ | — |

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
<summary><strong>Filesystem, shell, semantic search</strong></summary>

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

MIT license.
