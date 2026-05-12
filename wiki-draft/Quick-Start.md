# Quick Start

For people who've installed agent stacks before.

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS
pip3 install rich pywebview httpx
python3 hollow.py
```

The wizard will:

1. Detect your OS, GPU, Docker, and Ollama
2. Recommend a model based on hardware
3. Pull the model (~2–7 GB)
4. Bring up the stack on port 7777
5. Open the live monitor

Defaults:

| Setting | Value |
|---|---|
| Model | `qwen3.6:35b-a3b` if you have 24 GB+ VRAM, otherwise smaller |
| API | `http://localhost:7777` |
| API token | random, written to `config.json` |
| Agents | 3 (scout, analyst, builder) |
| Cycle budget | 1500s |
| Watchdog | 2400s (forces container restart on daemon death) |

## Day-to-day

```bash
# Windows
panel.bat

# Mac/Linux
python3 panel.py

# Watch the live thoughts stream from any platform
python3 thoughts.py
```

## Stop / restart

```bash
python3 hollow.py stop    # or stop.bat on Windows
python3 hollow.py         # starts again (skips wizard if config exists)
```

## Where things live

| | |
|---|---|
| `memory/goals/<agent>/registry.jsonl` | Goal history per agent |
| `memory/identity/<agent>/lessons.json` | Promoted operational lessons |
| `memory/claude_requests.jsonl` | Agent → human implementation queue |
| `workspace/<agent>/` | Per-agent file workspace |
| `logs/thoughts.log` | Tail of live activity (rotates at 50 MB) |
| `config.json` | API token, model config |

## Common things you'll want to do

- **Send the agents a message** — operator panel → Inject → Host message
- **Drop a file into an agent's workspace** — operator panel → Inject → File
- **Pause an agent** — operator panel → per-agent → Suspend
- **Adjust suffering manually** — operator panel → per-agent → Suffering load
- **Submit a task to an agent** — `python3 submit_task.py "your task" --agent scout`

For everything else, see [Setup](Setup.md) and [Troubleshooting](Troubleshooting.md).
