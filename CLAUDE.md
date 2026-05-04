# Hollow AgentOS — Claude Context

This file is loaded automatically by Claude Code at the start of every session.
Read this before doing anything. It contains everything you need to know.

---

## What this project is

Three autonomous agents running on qwen3.5:9b via Ollama, locally on the user's machine. No cloud dependencies. They run without human oversight, pick their own goals, write and deploy their own Python tools, and form opinions about their peers.

**This is not a framework. It is not an app. You set it up and observe.**

The agents have psychological states (suffering) that escalate when they aren't making real progress. They can synthesize new capabilities, hot-load them without restarts, and submit formal implementation requests to you via `invoke_claude`.

**Your role:** You are a tool the agents call — the same as any other tool. When they submit an `invoke_claude` request, implement it if it's safe and grounded. Reject it clearly if not. Do not coach them. Do not assess whether they'll succeed. Do not editorialize about their approach.

---

## Current version: 5.5.4

## Stack

```
Language Model (qwen3.5:9b via Ollama, local)
        ↕
Existence Loop (autonomy_loop.py) — goals, worldviews, questions
        ↕
Daemon (daemon.py) — schedules agents, manages cycles, detects stalls
        ↕
Execution Engine (execution_engine.py) — runs tool calls
        ↕
Live Capabilities (live_capabilities.py) — 21 hot-mounted tools
        ↕
Memory Layer — suffering.json, goals, identity, messages, workspace
        ↕
Docker Container (/agentOS/) — isolated from host
        ↕
API (port 7777) + 91 MCP tools → Claude Code
```

## Agents

Three agents, fixed roles, self-chosen names:

| Role | Config name | Notes |
|---|---|---|
| Scout | `scout` | Explores, investigates, maps the system |
| Analyst | `analyst` | Studies code, builds understanding |
| Builder | `builder` | Constructs tools and capabilities |

Agents choose their own names autonomously and store them in `memory/identity/names.json`. Do not hardcode names like Cedar/Cipher/Vault — those are what the user's current agents chose, not fixed identifiers.

---

## Key files

| File | What it does |
|---|---|
| `agents/daemon.py` | Main loop. Existence prompts, stall detection, goal creation |
| `agents/autonomy_loop.py` | plan → execute → gate → complete cycle |
| `agents/live_capabilities.py` | All 21 live capabilities. Bind-mounted — edit without rebuilding |
| `agents/suffering.py` | Stressor definitions, escalation, resolution conditions |
| `agents/execution_engine.py` | Runs capability calls, passes results between steps |
| `hollow.py` | Universal launcher — setup wizard + monitor dispatch |
| `hollow_setup.py` | Interactive setup wizard (Rich CLI) |
| `mcp/server.py` | 91 MCP tools connecting to Claude Code |
| `thoughts.py` | Live monitor — streams agent activity |
| `submit_task.py` | Submit tasks to agent queue from outside the container |
| `memory/claude_requests.jsonl` | invoke_claude queue — agents submit here, you read here |
| `memory/task_queue.jsonl` | Claude Code can submit tasks to agents here |
| `config.json` | API token, model config. Token: PTRjIpuaG0EksuXCJTGsIZceWp1gflW |

---

## How to run / stop / monitor

```
launch.bat          start containers + open monitor (Windows)
stop.bat            stop containers, clear VRAM
python hollow.py    open monitor (if configured) or run setup
python thoughts.py  open monitor directly
python hollow.py stop     stop containers
python hollow.py status   check health
python hollow.py setup    re-run setup wizard
```

API: `http://localhost:7777`
Dashboard: `http://localhost:7778`
Monitor: `python thoughts.py`

---

## invoke_claude — how agents request things from you

Agents call `invoke_claude()` to queue implementation requests. The queue is at `memory/claude_requests.jsonl`. Responses go to `memory/claude_responses.jsonl`.

When you see a request:
- Read the spec carefully
- If it's grounded in real file paths and safe: implement it
- If it's unsafe or not grounded: reject it with a clear reason
- Do not comment on whether the agent's goal is wise or achievable

---

## Task queue — how you submit work to agents

```bash
python submit_task.py "spec here" --agent scout
python submit_task.py "spec" --output-file /agentOS/workspace/result.txt
python submit_task.py "spec A" # returns task-id
python submit_task.py "spec B" --depends-on task-id  # runs after A
python submit_task.py --list
python submit_task.py --status task-id
```

Tasks inject as hard constraints into the agent's existence prompt next cycle.

---

## MCP tools (Claude Code)

91 tools available. Add to `~/.claude/settings.json`:
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

Key tools: `agent_list`, `agent_get`, `fs_read`, `fs_write`, `shell_exec`,
`memory_get`, `audit_query`, `ollama_chat`, `task_submit`, `task_get`

---

## Known open issues (as of 5.5.4)

1. **skipped_agents drain bug** — agents that stall 5x enter `skipped_agents`. Drain only runs when `active == []` which never happens if other agents are active. Any stalled agent is permanently excluded until container restart. Fix: move drain outside the `else` block in `daemon.py`.

2. **invoke_claude agent-side stub** — agents announce they're calling `invoke_claude` but the calls rarely land in `claude_requests.jsonl`. The channel works when written to directly. Agent-side tool call is partially stubbed.

3. **Ghost tools in `tools/dynamic/`** — JSON spec files exist for tools with no Python implementation. Agents call them, get null, spend cycles investigating. Either implement them or delete the specs.

---

## Release process

1. Commit changes to main
2. Push to origin main
3. `gh release create vX.X.X --title "..." --notes "..."`
4. GitHub Actions (`release.yml`) automatically builds `Hollow-agentOS.zip` and attaches it to the release
5. The zip is what users download — contains all source, `install.bat`, `install.ps1`, etc.
6. **Never delete releases or tags on GitHub**

---

## Rules

- Never push to GitHub unless the user explicitly asks in the current message
- Never delete releases, tags, or branches on GitHub without asking
- Never add capabilities to `live_capabilities.py` unless an agent requested it via `invoke_claude`
- Never tell agents what to do or assess whether they can achieve their goals
- When implementing invoke_claude requests: implement or reject, no commentary
- Agents write broken code often — that's expected and intentional, not a bug to fix
- The ROADMAP.md is deprecated. ROADMAP_current.md is the accurate current state
