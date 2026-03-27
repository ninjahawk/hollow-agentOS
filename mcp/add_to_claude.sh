#!/bin/bash
# Adds AgentOS MCP server to Claude Code settings.
# Run from WSL2: bash /agentOS/mcp/add_to_claude.sh

SETTINGS="/mnt/c/Users/$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r\n')/.claude/settings.json"

if [ ! -f "$SETTINGS" ]; then
    echo "{\"error\": \"Claude settings not found at $SETTINGS\"}"
    exit 1
fi

python3 - "$SETTINGS" <<'EOF'
import json, sys

path = sys.argv[1]
with open(path) as f:
    config = json.load(f)

config.setdefault("mcpServers", {})["agentos"] = {
    "command": "wsl",
    "args": ["python3", "/agentOS/mcp/server.py"]
}

with open(path, "w") as f:
    json.dump(config, f, indent=2)

print(json.dumps({"ok": True, "message": "AgentOS MCP server added to Claude Code", "settings": path}))
EOF
