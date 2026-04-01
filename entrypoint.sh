#!/bin/bash
set -e

# Start the autonomy daemon in the background
echo "[entrypoint] Starting autonomy daemon..."
PYTHONPATH=/agentOS python3 /agentOS/agents/daemon.py &
DAEMON_PID=$!
echo "[entrypoint] Daemon PID: $DAEMON_PID"

# Trap signals to clean up daemon on container stop
cleanup() {
    echo "[entrypoint] Shutting down daemon (PID $DAEMON_PID)..."
    kill "$DAEMON_PID" 2>/dev/null || true
    wait "$DAEMON_PID" 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# Start the API server (foreground)
echo "[entrypoint] Starting API server on :7777..."
exec python3 -m uvicorn api.server:app \
    --host 0.0.0.0 \
    --port 7777 \
    --log-level warning
