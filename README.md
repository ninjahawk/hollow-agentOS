```
 _  _  ___  _    _    _____  __      __
| || |/ _ \| |  | |  / _ \ \ \    / /
| __ | (_) | |__| |_| (_) \ \/\/ /
|_||_|\___/|____|____\___/ \_/\_/
```

<div align="center">

**A structured JSON-native environment for AI agents. 95% fewer tokens on code search. ~42% fewer tokens across mixed workloads. Eliminates agent drift between sessions.**

[![Version](https://img.shields.io/badge/version-0.6.0-7fff7f?style=flat-square)](https://github.com/ninjahawk/hollow-agentOS/releases)
[![License](https://img.shields.io/badge/license-MIT-555?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue?style=flat-square)](https://python.org)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?style=flat-square)](#docker)
[![Platform](https://img.shields.io/badge/platform-Docker%20%2F%20WSL2%20%2F%20Linux-orange?style=flat-square)](#setup)

![hollow token demo](demo.gif)

</div>

---

## What it is

Hollow is a local REST API that runs alongside your agent and gives it a structured JSON interface to the system, file I/O, shell, semantic search, memory, local model inference, inter-agent messaging, and session state.

**Without Hollow**, an agent cold-starts every session. It runs 9 shell commands to discover system state. It greps files to find context it had last session. It makes fresh decisions about things it already decided before. That inconsistency compounds — each session reconstructs a slightly different understanding of the same codebase. This is **agent drift**, and cheaper tokens won't fix it.

**With Hollow**, an agent registers once, gets a token and an isolated workspace, and calls `GET /agent/pickup` to resume exactly where the last session ended — same decisions, same context, same understanding. No re-discovery. No drift.

The token savings are a measurable side effect. The real win is **agents that stay consistent across sessions**.

---

## How it works

Hollow is not a kernel replacement or a VM. Think of it the way Android sits on Linux: Android developers never write kernel code — they only interact with the Android layer. Hollow is the same idea for agents. The goal is that agents never need to touch the underlying OS directly at all. Hollow becomes the complete abstraction layer between agents and the system.

What's shipped today is the foundation of that vision. The core pieces:

- **REST API** on port 7777 — every system operation as a typed JSON endpoint
- **Agent registry** — each agent gets a UUID, capability set, isolated workspace, and budget
- **Task scheduler** — submit a task with a complexity hint, Hollow routes it to the right local model automatically
- **Session handoff** — agents write structured context when they finish; the next agent picks it up cold in one call
- **Semantic search** — natural language search over the workspace using local embeddings (separate `nomic-embed-text` model, not the agent model — stays fast regardless of which agent model is running)
- **Standards layer** — store project conventions once, auto-inject the relevant ones before each task
- **Inter-agent messaging** — typed inbox/thread bus for multi-agent coordination
- **MCP server** — 45 tools wired directly into Claude Code

Local models via Ollama are **optional**. All core features (state, filesystem, memory, shell, handoffs, standards) work without a GPU or Ollama installed.

---

## Benchmarks

Measured by running actual shell commands and actual Hollow API calls on the same live system. Both sides use real output — no constructed strings.

| Scenario | Naive (shell) | Hollow (API) | Savings |
|---|---|---|---|
| Code search (rg + read matched files) | 21,636 tok | 987 tok | **95%** |
| File read + semantic context | 12,699 tok | 15,580 tok | –23% |
| State polling (4 shell commands) | 373 tok | 722 tok | –93% |
| System state (5 discovery commands) | 341 tok | 1,578 tok | –363% |
| Agent cold start (pickup) | 617 tok | 1,800 tok | –192% |
| **Total** | **35,666 tok** | **20,667 tok** | **42%** |

**Where Hollow wins:** Code search is the core use case. Instead of grepping files and reading them in full, `POST /semantic/search` returns only the relevant chunks — 95% fewer tokens and a single API call instead of N file reads.

**Where Hollow costs more:** System state queries (`GET /state`) and state polling (`GET /state/diff`) return comprehensive structured JSON — more data than a targeted `df -h` or `free -m`, but structured and parseable without any regex. Use these when your agent needs rich context, not when it only needs one field.

**Why the old number was wrong:** The previous 68.5% benchmark used a constructed worst-case naive baseline. The numbers above use real live data. 42% is honest.

```bash
# Real baseline (actual shell output vs actual API responses)
python3 tools/bench_real_baseline.py

# Break-even analysis (at what session count does Hollow's overhead pay off?)
python3 tools/bench_breakeven.py

# Integration tests (hits the live API, no mocks)
python3 tools/test_integration.py
```

---

## Architecture

```
hollow-agentOS/
├── api/
│   ├── server.py          # FastAPI — all endpoints (v0.6.0)
│   └── agent_routes.py    # Agent OS routes: register, spawn, message, tasks, locks, usage
├── agents/
│   ├── registry.py        # Identity, capabilities, workspaces, budgets, locks, model policies
│   ├── bus.py             # Inter-agent message bus
│   ├── scheduler.py       # Task routing, model policies, standards injection
│   └── standards.py       # Project conventions store + semantic matching
├── mcp/
│   └── server.py          # 45 MCP tools for Claude Code and compatible agents
├── memory/
│   └── manager.py         # Session log, workspace map, token tracking, handoffs, state history, specs, project context
├── tools/
│   ├── semantic.py              # AST-aware chunker + embedding search
│   ├── bench_real_baseline.py   # Real baseline benchmark (live shell vs live API)
│   ├── bench_breakeven.py       # Break-even analysis: when does Hollow's overhead pay off?
│   ├── experiment_agent_drift.py # Agent drift experiment (Hollow handoff vs cold start)
│   ├── bench_vs_claudecode.py   # Benchmark vs simulated Claude Code tool call patterns
│   ├── benchmark.py             # Original benchmark suite (constructed baseline)
│   └── test_integration.py      # Integration tests (0 mocks)
├── shell/
│   └── agent_shell.py     # JSON-native shell, deadlock-safe
├── dashboard/
│   └── index.html         # Live dashboard (nginx :7778)
├── sdk/
│   └── hollow.py          # Python SDK client (also on PyPI: pip install hollow-sdk)
├── Dockerfile             # Single-container build
├── docker-compose.yml     # Full stack: API + dashboard + optional Ollama
├── pyproject.toml         # PyPI packaging
├── requirements.txt       # Pinned dependencies
└── config.json            # Config (see config.example.json)
```

---

## Agent Roles

| Role | Shell | FS Read | FS Write | Ollama | Spawn | Message |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `root` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `orchestrator` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `worker` | ✓ | ✓ | ✓ | ✓ | — | ✓ |
| `coder` | ✓ | ✓ | ✓ | ✓ | — | ✓ |
| `reasoner` | — | ✓ | — | ✓ | — | ✓ |
| `readonly` | — | ✓ | — | — | — | ✓ |

Custom capability sets and per-model policies are supported at registration.

---

## API Reference

<details>
<summary><strong>Agent OS</strong></summary>

```
POST   /agents/register          Register agent (returns token, shown once)
GET    /agents                   List all agents (admin)
GET    /agents/{id}              Agent state, usage, locks, and budget
DELETE /agents/{id}              Terminate agent
POST   /agents/spawn             Spawn child agent, run task, return result
POST   /agents/{id}/suspend      Suspend agent
POST   /agents/{id}/resume       Resume agent
POST   /agents/{id}/lock/{name}  Acquire a named timed lock
DELETE /agents/{id}/lock/{name}  Release a lock
GET    /agents/{id}/usage        Per-agent token breakdown by model and action type
GET    /usage                    Aggregate token usage across all agents by pipeline stage

POST   /messages                 Send message to agent or broadcast
GET    /messages                 Receive inbox (unread by default)
GET    /messages/thread/{id}     Get full reply thread

POST   /tasks/submit             Submit task → scheduler routes to model
GET    /tasks/{id}               Get task result
GET    /tasks                    List tasks
```

</details>

<details>
<summary><strong>System & Shell</strong></summary>

```
GET    /state                    Full system snapshot (JSON)
GET    /state/diff?since=<iso>   Changed fields only since timestamp
GET    /state/history?since=<iso> State snapshots since timestamp (temporal context)
GET    /health
POST   /shell                    Run command (scoped to agent workspace)
```

</details>

<details>
<summary><strong>Filesystem</strong></summary>

```
GET    /fs/list                  Directory listing
GET    /fs/read                  Read file
POST   /fs/write                 Write file
POST   /fs/batch-read            Read multiple files in one call
GET    /fs/search                Find files by pattern
POST   /fs/read_context          File + semantically related neighbors
```

</details>

<details>
<summary><strong>Ollama / Semantic</strong></summary>

```
POST   /ollama/chat              Role-based model routing
POST   /ollama/generate          Raw generate
GET    /ollama/models            Available + running + routing table

POST   /semantic/search          Cosine similarity search over workspace
POST   /semantic/index           Re-index workspace
GET    /semantic/stats

POST   /agent/handoff            Write structured session context
GET    /agent/pickup             Handoff + temporal context + active spec + project (one call)
```

</details>

<details>
<summary><strong>Standards, Specs & Project</strong></summary>

```
POST   /standards                Store a named project convention
GET    /standards                List all standards
GET    /standards/relevant?task= Semantic match: which standards apply to this task
DELETE /standards/{name}         Remove a standard

POST   /specs                    Create a feature spec
GET    /specs                    List specs
GET    /specs/{id}               Get a spec
PATCH  /specs/{id}/activate      Set as the active spec (injected into agent/pickup)

GET    /project                  Get project context (mission, tech-stack, goals)
POST   /project                  Update project context
```

</details>

<details>
<summary><strong>Framework Compatibility</strong></summary>

```
GET    /tools/openai             All tools as OpenAI function definitions (LangChain, AutoGen, CrewAI)
```

</details>

---

## Setup

### Docker (recommended — works on Mac, Linux, Windows)

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS

cp config.example.json config.json
# Set: api.token, workspace.root

docker-compose up
```

API is at `http://localhost:7777`, dashboard at `http://localhost:7778`.

To include Ollama (requires GPU):

```bash
docker-compose --profile ollama up
```

### Manual (WSL2 / Linux)

**Requirements:** Python 3.12+, WSL2 or Linux. Ollama is optional.

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS

cp config.example.json config.json
pip install -r requirements.txt
```

**Start the API:**

```bash
cd api && python3 -m uvicorn server:app --host 0.0.0.0 --port 7777
```

### Python SDK

```bash
pip install hollow-sdk
```

```python
from hollow import register, Hollow

agent = register("http://localhost:7777", master_token="your-token", name="my-agent", role="worker")
h = Hollow("http://localhost:7777", agent_token=agent.token)

state = h.state()
results = h.semantic_search("authentication middleware")
context = h.pickup()  # full session context: handoff + temporal state + active spec + standards
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

**Build semantic index:**

```bash
PYTHONPATH=/path/to/hollow-agentOS python3 tools/semantic.py index
```

---

## Framework Compatibility

Hollow exposes all its tools as OpenAI function definitions at `GET /tools/openai`. Any agent framework that supports OpenAI tool use works out of the box:

```python
import openai, requests

tools = requests.get("http://localhost:7777/tools/openai").json()
# Pass tools directly to OpenAI, LangChain, AutoGen, CrewAI, LlamaIndex, etc.
```

---

## Services (systemd — manual install only)

| Service | Port | Description |
|---|---|---|
| `agentos-api` | 7777 | REST API |
| `nginx` | 7778 | Dashboard |
| `ollama` | 11434 | Local inference (optional) |
| `agentos-indexer.timer` | — | Re-index every 30s |

```bash
sudo systemctl start agentos-api nginx ollama
```

---

## Hardware

Developed and benchmarked on NVIDIA RTX 5070 (12 GB VRAM), WSL2 on Windows 11.

Ollama is optional — all core features run without it. If you have a GPU:
- Models up to 14B fit in VRAM
- Models up to 35B run with partial CPU offload
- `nomic-embed-text` (semantic search embeddings) uses ~300 MB and stays resident, separate from whichever model handles agent tasks

---

## License

MIT
