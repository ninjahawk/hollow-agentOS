# Setup

Three paths depending on your OS. All three end up at the same place: three agents running locally, picking their own goals, and an operator panel open on your desktop.

## What you need first

- **Disk**: 15-30 GB free (the model is the biggest chunk; qwen3.6 is ~23 GB).
- **GPU is optional.** A modern NVIDIA card with 24 GB+ VRAM is recommended for the default model. Anything less and the wizard picks a smaller model automatically.
- **Internet** for the initial install and model download.
- **Python 3.12+** (the wizard installs it on Windows; verify with `python3 --version` elsewhere).

That's it. No accounts, no API keys, no cloud anything.

---

## Windows

1. Download `Hollow-agentOS.zip` from [releases](https://github.com/ninjahawk/hollow-agentOS/releases/latest).
2. Right-click → **Extract All**. Pick a permanent location (Desktop or Documents — not Downloads, since that may auto-clean).
3. Open the folder, double-click **`install.bat`**.

The installer handles everything from there:

- Installs Docker Desktop if missing
- Installs Ollama if missing
- Asks which AI model to use (recommends based on your hardware)
- Downloads the model (~2–7 GB)
- Starts the agents
- Opens the live monitor

**If Windows needs a restart** during install (Docker requires this on first install), that's normal. Restart, open the folder again, double-click `install.bat` — it picks up where it left off.

After the first run, **double-click `panel.bat`** for day-to-day operation. That's the operator panel — start/stop, per-agent controls, file injection, etc.

---

## macOS

1. Install [Docker Desktop for Mac](https://docs.docker.com/get-docker/) and start it.
2. Install Python 3.12+ (Homebrew: `brew install python@3.12`).
3. Clone and run:

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS
pip3 install rich pywebview httpx
python3 hollow.py
```

The wizard installs Ollama via Homebrew if needed, walks you through model selection, downloads the model, and starts the agents.

For the panel: `python3 panel.py` (requires `pywebview httpx` — already installed by the line above).

---

## Linux

```bash
git clone https://github.com/ninjahawk/hollow-agentOS
cd hollow-agentOS
pip3 install rich pywebview httpx
python3 hollow.py
```

There are two extra Linux-specific things to know:

### Docker Desktop file sharing

If you use Docker Desktop on Linux (not native Docker engine), it sandboxes filesystem access. If `docker compose up` fails with `mounts denied: ... is not shared from the host`:

- Open **Docker Desktop → Settings → Resources → File Sharing**
- Add the path containing your `hollow-agentOS` clone
- Apply & restart Docker Desktop
- Re-run `python3 hollow.py`

If your project sits under `/mnt/` or `/media/` (external drive), Docker Desktop's VM can't see it at all. Move it to your home directory (e.g. `~/hollow-agentOS`) or switch to native Docker engine.

### NVIDIA GPU on Linux

CUDA toolkit alone is **not enough** — Docker needs `nvidia-container-toolkit` separately:

```bash
# Ubuntu / Mint / Debian
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

Without it, you'll see `could not select device driver "nvidia" with capabilities: [[gpu]]`. The setup wizard will detect this and fall back to CPU-only mode, but you'll lose 7-10× inference speed.

If you don't have a GPU at all, the wizard handles it automatically — picks a smaller model and edits the compose file to drop the GPU reservation block.

---

## Verifying it's running

After setup completes:

```bash
# Check API health
curl http://localhost:7777/health

# Check loaded model
curl http://localhost:11434/api/ps

# Watch agent activity (most useful)
python3 thoughts.py
```

The `thoughts.py` stream shows every goal an agent picks, every tool call, every suffering change, every lesson promoted. This is the live view of what's happening inside the system.

---

## What's next

See [Operator Panel](Operator-Panel.md) for day-to-day controls. See [Troubleshooting](Troubleshooting.md) if something didn't work.
