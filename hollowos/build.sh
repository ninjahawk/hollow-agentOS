#!/usr/bin/env bash
# HollowOS build script — produces a bootable disk image.
#
# Usage:
#   sudo ./build.sh              # build image only
#   sudo ./build.sh --flash /dev/sdX   # build + write to USB drive
#
# Output:  hollowos/build/hollowos.img
#
# Requirements (on a Debian/Ubuntu build host):
#   sudo apt install mkosi systemd-container qemu-utils
#   # mkosi v14+ required — if apt version is old:
#   pip install mkosi
#
# What the resulting image does:
#   1. Boots Debian Bookworm minimal
#   2. Runs first-boot.sh ONCE: clones hollow repo, pulls Docker images
#   3. Starts hollow-agent.service: runs 'docker compose up' from /opt/hollow
#   4. Starts hollow-kiosk.service: launches Chromium in kiosk mode
#   5. User sees only the Hollow UI — no desktop, no taskbar, no terminal
#
# Developer unlock: Ctrl+Alt+F2 → login as 'hollow' (password in /etc/hollow/hollow.env)
#
# To configure your API key before building:
#   edit hollowos/rootfs/etc/hollow.env.example
#   OR: edit /etc/hollow/hollow.env on the running system

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Validate prerequisites ──────────────────────────────────────────────────────

missing=()
for cmd in mkosi; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
done

if (( ${#missing[@]} )); then
    echo "[error] Missing required tools: ${missing[*]}"
    echo "  Install: sudo apt install mkosi systemd-container qemu-utils"
    echo "  Or:      pip install mkosi  (if apt version is too old)"
    exit 1
fi

MKOSI_VERSION=$(mkosi --version 2>/dev/null | grep -oP '\d+' | head -1 || echo "0")
if (( MKOSI_VERSION < 14 )); then
    echo "[warn] mkosi v${MKOSI_VERSION} detected — v14+ recommended. Some features may not work."
fi

# ── Build ───────────────────────────────────────────────────────────────────────

echo "[hollow] Building HollowOS disk image..."
echo "[hollow] This typically takes 3-8 minutes on first run (downloads packages)."
mkdir -p build/

sudo mkosi build

IMG="build/hollowos.img"
if [ -f "$IMG" ]; then
    SIZE=$(du -h "$IMG" | cut -f1)
    echo ""
    echo "[hollow] ✓ Image built: $IMG ($SIZE)"
    echo ""
    echo "  Flash to USB:  sudo dd if=$IMG of=/dev/sdX bs=4M status=progress && sync"
    echo "  Or:            bash build.sh --flash /dev/sdX"
    echo ""
    echo "  Before booting, edit /etc/hollow/hollow.env on the device to add ANTHROPIC_API_KEY."
else
    echo "[error] Build completed but image not found at $IMG"
    exit 1
fi

# ── Optional: flash to USB ──────────────────────────────────────────────────────

if [[ "${1:-}" == "--flash" && -n "${2:-}" ]]; then
    DEVICE="$2"
    if [[ ! -b "$DEVICE" ]]; then
        echo "[error] '$DEVICE' is not a block device. Check with: lsblk"
        exit 1
    fi

    # Show device info before asking
    echo ""
    echo "[hollow] Target device: $DEVICE"
    lsblk -o NAME,SIZE,MODEL "$DEVICE" 2>/dev/null || true
    echo ""
    echo "WARNING: All data on $DEVICE will be permanently erased."
    read -rp "Type 'yes' to continue: " confirm
    if [[ "$confirm" == "yes" ]]; then
        echo "[hollow] Flashing to $DEVICE..."
        sudo dd if="$IMG" of="$DEVICE" bs=4M status=progress conv=fsync
        sync
        echo ""
        echo "[hollow] ✓ Done. Boot from $DEVICE to reach Hollow."
        echo "  First boot takes ~3 minutes to pull container images."
    else
        echo "[hollow] Aborted."
    fi
fi
