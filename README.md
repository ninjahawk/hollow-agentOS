# hollow agentOS

**Autonomous AI agents that live on your machine, pursue goals, and never stop.**

You give them a goal. They plan. They execute. They write results to memory. They propose what to do next. You walk away.

---

## What you actually see

```
2026-04-02T15:27:46 [daemon] INFO   ac3-fail-b8647c → goal=goal-7ab110a18f39 progress=1.00 steps=5
2026-04-02T15:30:28 [daemon] INFO   stability-1775117163 → goal=goal-f158bee28eeb progress=1.00 steps=3
2026-04-02T15:31:12 [daemon] INFO   ac1-agent-c-3884f5 → goal=goal-1ada9085637d progress=1.00 steps=5
2026-04-02T16:46:54 [daemon] INFO   optimizer-v1 → goal=goal-1a9344928930 progress=1.00 steps=1
```

That's 14 agents running in parallel, each one planning its own steps, calling Ollama, reading and writing memory — nobody telling them what to do. The daemon woke up, found their goals, and they figured it out.

---

## Install in 3 commands

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/) and [Ollama](https://ollama.ai) running locally.

```bash
# 1. Pull the model agents will think with
ollama pull mistral-nemo:12b

# 2. Clone and configure
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS
cp config.example.json config.json

# 3. Start everything
docker-compose up
```

API live at `http://localhost:7777` — agents are running.

**GPU (recommended):**
```bash
docker-compose --profile ollama up
```
Starts Ollama inside Docker with full GPU access. No separate Ollama install needed.

**Watch agents live:**
```bash
docker logs -f hollow-api | grep -E "Cycle|goal=|progress="
```

---

## Give an agent a goal

```bash
curl -X POST http://localhost:7777/agents \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "my-agent", "role": "worker"}'

curl -X POST http://localhost:7777/goals \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "my-agent", "objective": "Analyze the codebase and find 3 performance bottlenecks, save findings to memory"}'
```

The agent will:
1. Ask Ollama to plan the steps
2. Run `semantic_search` to find relevant code
3. Call `ollama_chat` with the real search results to analyze them
4. Write findings to memory with `memory_set`
5. Propose a follow-on goal when done

No polling required. Watch the logs.

---

## What this actually is

The daemon is a loop. Every 30 seconds it wakes up, looks at every registered agent, and runs `pursue_goal()`.

`pursue_goal()` does this:
- Asks Ollama: "here's the goal, here are the available capabilities, make a plan"
- Ollama returns 3-5 steps
- Each step runs. The output of step N feeds into step N+1
- When a real save happens (memory or file), progress reaches 1.0 and the goal completes
- The system proposes what to do next

No human in the loop. Not a chatbot. Not a one-shot pipeline. Agents that live between calls, remember what they did, and decide what to do next.

---

## What agents can do

| Capability | What it does |
|---|---|
| `shell_exec` | Run any shell command |
| `ollama_chat` | Ask the local LLM anything |
| `fs_read` / `fs_write` | Read and write files |
| `semantic_search` | Search the codebase by meaning, not keywords |
| `memory_set` / `memory_get` | Persistent key-value memory across goals |
| `agent_message` | Send messages to other agents |

Agents discover capabilities by vector similarity — they search for what they need, not string-match on names. A goal about "analyzing text" finds `ollama_chat` even if the word "LLM" never appears.

---

## How it differs from LangChain, CrewAI, etc.

Those frameworks help humans use AI more effectively. This lets AI operate without humans in the loop.

| | LangChain / CrewAI | hollow agentOS |
|---|---|---|
| **Persistence** | Context window only | Goals, memory, and identity survive restarts |
| **Autonomy** | Human submits a task | Agent pursues goals indefinitely |
| **Planning** | Hardcoded chains | Ollama generates the plan at runtime |
| **Failure handling** | Crash or retry | Replan, blacklist failed capabilities, try again |
| **Multi-agent** | Prompt-based handoffs | Event-driven with quorum governance |
| **Self-extension** | No | Agents synthesize and hot-load new capabilities |
| **Runs on** | Cloud APIs | Your machine, offline, your GPU |

---

## Architecture

```
daemon.py                          ← wakes up every 30s, cycles all agents
  └── pursue_goal(agent_id)
        ├── ReasoningLayer         ← asks Ollama to plan
        │     └── CapabilityGraph  ← finds capabilities by embedding similarity
        ├── AutonomyLoop           ← executes plan step by step
        │     └── _substitute_result  ← flows output of step N into step N+1
        ├── ExecutionEngine        ← runs the actual capabilities
        ├── SemanticMemory         ← stores what was learned (MiniLM-L6-v2 embeddings)
        └── PersistentGoalEngine   ← goals survive on disk across restarts
```

---

## Multi-agent features

**Delegation** — agent A creates a goal in agent B's name, absorbs results when done.

**Shared goals** — a coordinator decomposes a goal into N subtasks via Ollama, delegates each to a different agent, collects results.

**Quorum voting** — before a new capability gets deployed, active agents vote yes/no via Ollama. 66% threshold required. No human approval needed.

---

## Self-extension

Agents can synthesize new Python capabilities at runtime:

1. Agent identifies a gap ("I need to compute X but no capability exists")
2. `SelfModificationCycle` asks Ollama to write the function
3. Function is tested in isolation
4. If tests pass, it's hot-loaded into the ExecutionEngine
5. It's immediately available to all agents

Dynamic capabilities live in `/agentOS/tools/dynamic/`. They persist across restarts.

---

## Hardware

Tested on:
- RTX 5070 (12GB VRAM) + CUDA, WSL2
- `mistral-nemo:12b` as default reasoning model
- ~14 concurrent agents with no resource contention

Works on CPU but planning calls will be slow (~40s per goal vs ~6s on GPU).

---

## Configuration

`config.json` (copy from `config.example.json`):

```json
{
  "ollama": {
    "host": "http://localhost:11434",
    "default_model": "mistral-nemo:12b"
  }
}
```

Point `host` at any Ollama instance. Swap `default_model` for any model you have pulled.

---

## Watching agents in real time

```bash
# Raw live feed
docker logs -f hollow-api

# Filtered to agent activity only
docker logs -f hollow-api | grep -E "Cycle|goal=|progress="

# WSL / Linux direct
tail -f /agentOS/logs/daemon.log | grep --line-buffered -E "Cycle|goal=|progress="
```

Each line like `agent-beta → goal=goal-4027b90d98d0 progress=0.60 steps=5` means:
- agent is `agent-beta`
- currently pursuing that goal ID
- 60% progress (gated on real output — can't hit 100% without a memory write or file write)
- executed 5 steps this cycle

---

## API

Full REST API at `http://localhost:7777/docs` (Swagger UI).

Key endpoints:
- `POST /agents` — register an agent
- `POST /goals` — give an agent a goal
- `GET /agents/{id}/goals` — see what an agent is working on
- `GET /agents/{id}/memory` — read what an agent has stored
- `GET /health` — system status

91 MCP tools available for agent-to-agent and human-to-agent interaction.

---

## Tests

```bash
# Full acceptance test (requires Ollama running)
PYTHONPATH=/agentOS python3 tests/acceptance_v4.py

# Integration tests
PYTHONPATH=/agentOS python3 -m pytest tests/integration/ -v
```

All tests hit the live system. No mocks.

---

## Version

**v4.0.1** — Infrastructure of AGI

The execution loop is real. Goals persist. Memory accumulates. Agents plan, act, and self-direct. The reasoning engine is Ollama running locally on your hardware. Nothing leaves your machine.

[Releases](https://github.com/ninjahawk/hollow-agentOS/releases) · [Issues](https://github.com/ninjahawk/hollow-agentOS/issues)
