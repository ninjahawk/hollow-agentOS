#!/bin/bash
# HollowOS first-boot setup.
# Runs exactly once as a systemd one-shot service.
# Creates data dirs, clones the Hollow stack, and pulls container images.

set -euo pipefail
HOLLOW_REPO="https://github.com/ninjahawk/hollow-agentos"
HOLLOW_DIR="/opt/hollow"
ENV_FILE="/etc/hollow/hollow.env"
DATA_DIR="/var/hollow"

log() { echo "[hollow-first-boot] $*"; }

# Create persistent data directories
log "Creating data directories..."
mkdir -p \
    "$DATA_DIR/memory" \
    "$DATA_DIR/workspace/sandbox" \
    "$DATA_DIR/workspace/wrappers" \
    "$DATA_DIR/logs" \
    "$DATA_DIR/store/data"

# Clone the Hollow repo if not already present
if [ ! -d "$HOLLOW_DIR/.git" ]; then
    log "Cloning Hollow stack from $HOLLOW_REPO..."
    git clone --depth=1 "$HOLLOW_REPO" "$HOLLOW_DIR"
else
    log "Hollow stack already cloned, pulling updates..."
    git -C "$HOLLOW_DIR" pull --ff-only || true
fi

# Link the user's config into the stack
if [ -f "$ENV_FILE" ]; then
    log "Linking config..."
    ln -sf "$ENV_FILE" "$HOLLOW_DIR/.env"
fi

# Tell docker-compose to use pre-built store image from GHCR
# (avoids building from source on first boot, which requires dev tools)
export HOLLOW_STORE_IMAGE="ghcr.io/ninjahawk/hollow-store:latest"

# Create a default config.json if one doesn't exist in data dir
if [ ! -f "$DATA_DIR/config.json" ]; then
    log "Creating default config.json..."
    cat > "$DATA_DIR/config.json" <<'EOF'
{
  "api": {
    "host": "0.0.0.0",
    "port": 7777,
    "token": "hollow-default-token-change-me"
  },
  "ollama": {
    "host": "http://host.docker.internal:11434",
    "default_model": "qwen3.5:9b"
  }
}
EOF
fi

# Symlink config into the stack so docker-compose.yml finds it
ln -sf "$DATA_DIR/config.json" "$HOLLOW_DIR/config.json" 2>/dev/null || true

# Pre-pull container images while we have network
log "Pulling container images (this may take a few minutes on first boot)..."
cd "$HOLLOW_DIR"
docker compose pull --quiet 2>&1 | tail -5 || log "Image pull had warnings — continuing"

# Mark first boot complete
touch /var/lib/hollow-first-boot-done
log "First-boot setup complete."
