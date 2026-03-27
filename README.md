# AgentOS

An operating environment designed exclusively for agentic AI — not humans.

Current OSes waste 60–70% of an agent's token budget on exploration, parsing human-readable output, handling interactive prompts, and re-discovering known state. AgentOS eliminates all of that.

## What it does

- **Single-call state** — one `GET /state` gives disk, memory, GPU, services, Ollama status, semantic index health, recent actions, and pending decisions. No exploration needed.
- **JSON-native shell** — every command returns structured JSON. No parsing required.
- **Semantic search** — natural language search over the entire workspace using nomic-embed-text embeddings. Finds the right function, not just the right filename.
- **Agent handoff** — structured session handoff so the next agent knows exactly what was done, what's in progress, and what files are relevant. Eliminates cold-start re-discovery.
- **Local model routing** — route requests to the best local Ollama model by role (`code`, `reasoning`, `general`, etc.). No cloud round-trips for routine tasks.
- **Persistent memory** — workspace map, project context, and token usage tracked across sessions.
- **Decision queue** — agents surface decisions to humans without blocking. Approvals show up in the dashboard.
- **MCP integration** — all capabilities exposed as native tools to Claude Code and any MCP-compatible agent.

## Architecture

```
/agentOS/
├── api/server.py          # FastAPI — all endpoints
├── mcp/server.py          # MCP server — 17+ tools for Claude Code
├── memory/manager.py      # Persistent workspace map, session log, decisions
├── tools/semantic.py      # Embedding-based search (nomic-embed-text + cosine)
├── shell/agent_shell.py   # JSON-native shell, blocking-safe
├── dashboard/index.html   # Live dashboard (nginx on :7778)
└── config.json            # Central config (see config.example.json)
```

## Services (systemd, WSL2)

| Service | Port | Description |
|---------|------|-------------|
| `agentos-api` | 7777 | REST API (FastAPI + uvicorn) |
| `nginx` | 7778 | Dashboard |
| `ollama` | 11434 | Local model inference |
| `agentos-indexer.timer` | — | Re-index workspace every 30s |

## API endpoints

**System**
- `GET /state` — full snapshot
- `GET /state/diff?since=<iso-timestamp>` — only what changed
- `GET /health`

**Shell**
- `POST /shell` — non-blocking, deadlock-safe

**Filesystem**
- `GET /fs/list` — directory listing
- `GET /fs/read` — read file
- `POST /fs/write` — write file
- `POST /fs/batch-read` — read multiple files in one call
- `GET /fs/search` — find files by name pattern
- `POST /fs/read_context` — read file + semantically related chunks

**Ollama**
- `POST /ollama/chat` — role-based model routing
- `POST /ollama/generate` — raw generate
- `GET /ollama/models` — available + running + routing table

**Semantic search**
- `POST /semantic/search` — cosine similarity search
- `POST /semantic/index` — re-index workspace
- `GET /semantic/stats`

**Agent**
- `POST /agent/handoff` — write session context for next agent
- `GET /agent/pickup` — get last handoff + what changed since

**Memory / Decisions**
- `GET /memory/context`, `GET /memory/actions`, `GET /memory/project`
- `POST /memory/project` — set project context key
- `GET /decisions`, `POST /decisions/resolve`

## Setup

```bash
cp config.example.json config.json
# Edit config.json: set token, workspace root, ollama path

pip install fastapi uvicorn httpx mcp

# Install MCP into Claude Code:
bash /agentOS/mcp/add_to_claude.sh
```

## Model routing

| Role | Model | VRAM |
|------|-------|------|
| `code` | qwen2.5:14b | 9 GB |
| `code-fast` | qwen3.5:9b | 7 GB |
| `general` | mistral-nemo:12b | 7 GB |
| `general-large` | qwen3.5:27b | 17 GB |
| `reasoning` | qwen3.5-35b-moe | 12 GB |
| `reasoning-large` | nous-hermes2:34b | 19 GB |
| `uncensored` | dolphin3 | 5 GB |
| `custom` | emmi | 5 GB |
| `embed` | nomic-embed-text | 0.3 GB |

## Hardware tested on

NVIDIA RTX 5070 (12 GB VRAM), WSL2 on Windows 11.
