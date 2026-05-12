# Troubleshooting

If `install.bat` or `python3 hollow.py` failed, check this page first.

---

## Installation failures

### "Docker Compose failed: mounts denied"

**Cause**: Docker Desktop is sandboxing filesystem access and your project path isn't in its allowlist.

**Fix on Linux/macOS**:
1. Open **Docker Desktop → Settings → Resources → File Sharing**
2. Click `+` and add the directory containing your `hollow-agentOS` clone
3. Click **Apply & Restart**
4. Re-run setup

**If your project is at `/mnt/...` or `/media/...`**: Docker Desktop's VM can't see external drives on Linux. Move to `~/hollow-agentOS` or switch to native Docker engine.

### "Could not select device driver 'nvidia' with capabilities: [[gpu]]"

**Cause**: You have an NVIDIA GPU and NVIDIA drivers installed, but Docker can't reach the GPU because `nvidia-container-toolkit` isn't installed.

**Fix**:
```bash
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

The setup wizard auto-detects this and falls back to CPU-only mode, but you'll lose significant speed. Install the toolkit and re-run.

### "Docker Compose failed: yaml"

**Cause**: Older versions (pre-5.7.x) had a regex bug in the no-GPU fallback that produced invalid YAML. Fixed by switching to a line-based pass.

**Fix**: Update to v5.7.32 or later. If you're already on latest and still seeing yaml errors, [open an issue](https://github.com/ninjahawk/hollow-agentOS/issues/new) with the full error.

### Setup hangs on "Pulling images"

**Cause**: Slow internet or GHCR is briefly down.

**Fix**: Wait it out (the images total ~1 GB), or `docker compose pull` manually to retry. If GHCR is down, you can build locally — the compose file falls back to `build:` blocks.

### "Permission denied" on Linux

**Cause**: Docker daemon isn't accessible to your user.

**Fix**: Add yourself to the docker group:
```bash
sudo usermod -aG docker $USER
newgrp docker  # or log out and back in
```

---

## Runtime issues

### "Agents aren't doing anything"

**Check**:
1. Is the API alive? `curl http://localhost:7777/health` should return JSON.
2. Is the model loaded? `curl http://localhost:11434/api/ps` should show your model in the `models` list.
3. Is the daemon producing logs? `docker logs hollow-api --since 5m`. You should see cycle entries every ~6 seconds.

**If model isn't loaded**: Ollama may have evicted it under VRAM pressure. Try `ollama run qwen3.6:35b-a3b "hi"` to warm it up. If it errors, your VRAM is full from something else.

**If daemon is silent for >5 minutes**: it may be stuck. Restart with `python3 hollow.py stop` then `python3 hollow.py`.

### "Cycle worker timeout" errors

**Cause**: A goal cycle exceeded the 1500s budget — usually means Ollama is slow or the agent picked an over-ambitious goal.

**Behavior**: Not fatal. The next cycle continues with the timed-out agent in "cooling" state for a few cycles, then it rejoins.

**If it's happening every cycle**: Check Ollama health (`/api/ps`). If models are repeatedly being unloaded, you don't have enough VRAM — switch to a smaller model in `config.json`.

### "Validation hangs" / "VALIDATE_START with no END"

**Cause**: Specific failure mode where Ollama evicts the model mid-request despite `keep_alive=-1`. The httpx client doesn't always surface this as a timeout.

**Mitigation**: v5.7.32 wraps validation Ollama calls in a wall-clock-bounded thread pool. If it still hangs, the cycle timeout (1500s) will recover.

### "Agents keep abandoning the same goal"

**Cause**: Either the goal is genuinely impossible (e.g. targeting a file path that doesn't exist) or the validation semantic check is too strict for the artifact produced.

**What the substrate does**: After 5 validation failures the goal is permanently abandoned and any partial artifacts are deleted. The agent picks a new goal next cycle.

**If a specific goal text loops 5+ times**: v5.7.32 has a same-text loop guard that rejects the model's output and falls back to a different goal source (open question / peer file / journal). If you're on an older version, update.

---

## Operator panel issues

### "Panel won't open"

**Windows**: `panel.bat` requires Python 3.12+ on PATH and `pywebview` installed (`pip install pywebview`).

**Mac/Linux**: Run `python3 panel.py` directly. The error message will tell you what's missing.

### "Panel opens but everything is dimmed/disabled"

**Cause**: API isn't reachable.

**Fix**: Check the API is running: `curl http://localhost:7777/health`. If it isn't, start the stack: `python3 hollow.py` (or `launch.bat`).

---

## When all else fails

**Clean slate**: 

```bash
python3 hollow.py stop
docker compose down -v   # this nukes container state
rm -rf memory/audit.log memory/goals/* memory/identity/*  # nukes agent memory
python3 hollow.py        # fresh start
```

This is destructive — you lose all agent history. But sometimes a baseline gets contaminated by a bad early run and the only way back is a reset.

The operator panel has a **Nuke** button that does this for you.

---

## Still stuck?

[Open an issue](https://github.com/ninjahawk/hollow-agentOS/issues/new) with:

- Your OS, GPU model (or "none"), Docker version, Python version
- What you ran
- The full error (please — `2>&1 | tee error.log` and paste the lot)
- A screenshot for GUI errors
