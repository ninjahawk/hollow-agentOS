# AgentOS — Post-Restart Instructions

## What happened before restart
WSL2 + Ubuntu was installed. AgentOS files are already built and sitting at:
C:\Users\jedin\Desktop\New Science\agentOS\

## What to do RIGHT NOW

### Step 1 — Open Ubuntu
Search "Ubuntu" in the Windows Start menu. Click it.
It will ask you to create a Linux username and password. Pick anything simple.
IMPORTANT: Remember this password — you'll need it when the installer asks for sudo.

### Step 2 — Run the installer
Once you see a $ prompt in Ubuntu, paste this exactly:

```
cd /mnt/c/Users/jedin/Desktop/New\ Science/agentOS && bash install.sh
```

This will:
- Install Python, ripgrep, nginx, curl
- Install FastAPI + uvicorn
- Set up systemd services (API on port 7777, dashboard on port 7778)
- Bootstrap the memory layer
- Ask if you want to install Ollama + AI models for your RTX 5070 (say Y)

### Step 3 — When install finishes
Open your browser and go to:
http://localhost:7778

That's the AgentOS dashboard. You should see a dark UI showing agent activity and system state.

API docs are at: http://localhost:7777/docs

### Step 4 — Tell Claude Code what happened
Open a new Claude Code session and say:
"I restarted, Ubuntu is set up, AgentOS is installed — what's next?"

Claude will have memory of this project and continue from here.

## What was already built (so Claude remembers)
- config.json — central config
- memory/manager.py — persistent workspace map, session log, tool registry
- shell/agent-shell.py — JSON-native shell (no ANSI, no blocking prompts)
- api/server.py — FastAPI REST API on port 7777
- tools/agent-fs.py, agent-git.py, agent-search.py, batch-read.py, decision.py
- ollama/setup.sh — RTX 5070 model install (qwen2.5-coder:32b + 7b + nomic-embed-text)
- dashboard/index.html — web UI
- install.sh — the installer you're about to run

## Hardware
RTX 5070 (12GB VRAM) — Ollama models are pre-selected for this card.
Model downloads will be ~20GB so give it time.
