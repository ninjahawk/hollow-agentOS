# HollowOS

A minimal bootable Linux image that runs the Hollow interface in kiosk mode.

## What it is

HollowOS boots into a full-screen browser showing the Hollow app launcher. There is no desktop environment, no taskbar, no window manager. The browser IS the desktop. Users never know they're running Linux.

## Boot sequence

1. Machine powers on → Debian Bookworm minimal
2. lightdm auto-logs in as `hollow` user (no password prompt)
3. openbox starts, launches Chromium in kiosk mode → `loading.html`
4. `hollow-first-boot.service` runs once:
   - Clones the Hollow stack from GitHub to `/opt/hollow`
   - Creates data directories at `/var/hollow/`
   - Pulls Docker images (takes ~3 min on first boot)
5. `hollow-agent.service` starts: `docker compose up`
6. Dashboard becomes available at `localhost:7778`
7. `loading.html` detects the API is ready → redirects to `apps.html`
8. User sees Hollow. No terminal ever shown.

## Prerequisites (build host — must be Linux)

```bash
sudo apt install mkosi systemd-container qemu-utils
# If apt's mkosi is too old (< v14):
pip install mkosi
```

## Build

```bash
cd hollowos
sudo bash build.sh
```

Output: `hollowos/build/hollowos.img`

## Flash to USB

```bash
sudo bash build.sh --flash /dev/sdX  # replace sdX with your USB device
```

Or manually:
```bash
sudo dd if=build/hollowos.img of=/dev/sdX bs=4M status=progress conv=fsync
sync
```

## Configuration

Before flashing (or after mounting the EFI partition), edit `/etc/hollow/hollow.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...    # enables Claude for high-quality wrapping
HOLLOW_API_TOKEN=your-secret    # change from default before sharing hardware
```

Without an API key, Hollow works but wrapping quality is lower (falls back to Ollama).

## Developer access

From the kiosk, press `Ctrl+Alt+F2` to reach a terminal. Log in as `hollow` with the password from `/etc/hollow/hollow.env` (`HOLLOW_DEV_PASSWORD`).

## Files

```
hollowos/
├── mkosi.conf                    # mkosi image build config
├── build.sh                      # build + optional USB flash
├── scripts/
│   ├── first-boot.sh             # runs once at first boot (clones repo, pulls images)
│   └── postinstall.sh            # mkosi postinstall (user setup, permissions)
├── units/
│   ├── hollow-first-boot.service # one-shot first-boot service
│   ├── hollow-agent.service      # starts hollow stack (docker compose up)
│   ├── hollow-kiosk.service      # launches Chromium (fallback if openbox fails)
│   └── hollow-kiosk.sh           # Chromium kiosk launcher
└── rootfs/
    └── etc/
        ├── lightdm/lightdm.conf  # auto-login as hollow user
        └── hollow.env.example    # configuration template
```
