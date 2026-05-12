#!/bin/bash
set -e

echo "[entrypoint] Starting autonomy daemon..."
PYTHONPATH=/agentOS python3 /agentOS/agents/daemon.py &
DAEMON_PID=$!
echo "[entrypoint] Daemon PID: $DAEMON_PID"

echo "[entrypoint] Starting API server on :7777..."
python3 -m uvicorn api.server:app \
    --host 0.0.0.0 \
    --port 7777 \
    --log-level warning &
API_PID=$!
echo "[entrypoint] API PID: $API_PID"

cleanup() {
    echo "[entrypoint] Shutting down (daemon=$DAEMON_PID api=$API_PID)..."
    kill "$DAEMON_PID" "$API_PID" 2>/dev/null || true
    wait "$DAEMON_PID" "$API_PID" 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# Exit as soon as EITHER child dies. Docker's restart: unless-stopped policy
# will then restart the whole container, which is what the watchdog actually
# needs in order to revive a hung daemon. Previously uvicorn was exec'd as
# PID 1 and the daemon was a background sibling — killing the daemon left
# uvicorn (and the container) up, so the watchdog was a no-op.
wait -n "$DAEMON_PID" "$API_PID"
EXITED_PID=$?
echo "[entrypoint] A child exited (status=$EXITED_PID). Bringing the container down so Docker can restart it."
kill "$DAEMON_PID" "$API_PID" 2>/dev/null || true
exit 1
