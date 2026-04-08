#!/bin/bash
# HollowOS post-installation script.
# Runs inside the image during mkosi build — sets up user groups, permissions,
# data directories, and the hollow environment.

set -euo pipefail

# Add hollow user to the docker group so kiosk session can run containers
usermod -aG docker hollow

# Create the hollow config directory
mkdir -p /etc/hollow
chmod 755 /etc/hollow

# Create the default env file from the example (user must fill in API key)
if [ -f /etc/hollow/hollow.env.example ] && [ ! -f /etc/hollow/hollow.env ]; then
    cp /etc/hollow/hollow.env.example /etc/hollow/hollow.env
    chmod 600 /etc/hollow/hollow.env
fi

# Create marker dir for first-boot tracking
mkdir -p /var/lib

# Make the kiosk and first-boot scripts executable
chmod +x /usr/local/bin/hollow-kiosk.sh
chmod +x /usr/local/bin/hollow-first-boot.sh

# Create /opt/hollow as the target for the stack clone
mkdir -p /opt/hollow
chown root:root /opt/hollow
chmod 755 /opt/hollow

# openbox needs a minimal config for the hollow user
mkdir -p /home/hollow/.config/openbox
cat > /home/hollow/.config/openbox/autostart <<'EOF'
# HollowOS openbox autostart — kiosk mode, no decorations
xset s off           # disable screensaver
xset -dpms           # disable display power management
xset s noblank       # don't blank the screen

# Wait for the Hollow dashboard, then launch Chromium in kiosk mode
(
  until curl -sf http://localhost:7778/loading.html >/dev/null 2>&1; do
    sleep 3
  done
  /usr/local/bin/hollow-kiosk.sh
) &
EOF
chown -R hollow:hollow /home/hollow/.config

# Set hostname to hollowos
echo "hollowos" > /etc/hostname
cat > /etc/hosts <<'EOF'
127.0.0.1   localhost
127.0.1.1   hollowos
::1         localhost ip6-localhost ip6-loopback
ff02::1     ip6-allnodes
ff02::2     ip6-allrouters
EOF

echo "[postinstall] HollowOS setup complete."
