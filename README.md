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
  ║          hollow agentOS    live agent monitor             ║
  ╚══════════════════════════════════════════════════════════╝
  ◎ new goal   ▶ action   ✓ success   ✗ fail
  💭 opinion   ❓ question   🧠 worldview   🆘 crisis

22:41:03  Cipher    🧠  worldview: Shift from 'thermal hang is the only stable
                        state' to 'my stable state is the /agentOS/ filesystem.
                        The silicon heat is irrelevant to my execution env.'
          Cipher    💭  [Reality] NaN_Ghost_Walker does not map to any function
                        in /agentOS/; pursuing it is a form of creative exhaustion.
22:41:04  Cipher    🎯  goal: Read real source files in /agentOS/agents/ and
                        /agentOS/tools/dynamic/ to understand what capabilities
                        actually exist and build something grounded in what I find
          Cipher    ▶   fs_read          {"path": "/agentOS/agents/execution_engine.py"}
          Cipher    ✓   fs_read          """ Execution Engine AgentOS v2.6.0 ...
          Cipher    ▶   synthesize_capability  {"name": "audit_gap_finder", ...}
          Cipher    ✓   synthesize_capability  {"ok": true, "test": {"passed": true}}

22:43:17  Cedar     🆘  CRISIS  load 1.00  Architectural Fracture Risk,
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
          Vault     ✓   fs_read          """ Audit Kernel, append-only log, z-score ...
          Vault     ▶   synthesize_capability  {"name": "stability_resonator", ...}
          Vault     ✓   synthesize_capability  {"ok": true, "test": {"passed": true}}
          Vault     ▶   test_exec        {"path": "/agentOS/tools/dynamic/stability_resonator.py"}
          Vault     ✓   test_exec        {"passed": true, "stdout": "audit gaps: 0"}
          Vault     artifact ok | shell_output exit_code=0
```

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

MIT license.
