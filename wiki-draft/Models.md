# Choosing a model

The setup wizard offers four model options. Your hardware decides which is realistic.

| Model | VRAM | Disk | Speed | Notes |
|---|---|---|---|---|
| **qwen3.6:35b-a3b** (default) | 24 GB+ | ~23 GB | Fast | MoE — 35B params, only 3B active at a time. Best quality, surprisingly fast for its size. |
| qwen3.5:9b | 8 GB+ | ~5.2 GB | Medium | Older fallback. Works fine on a 12 GB card. |
| gemma3:4b | 4 GB+ or CPU | ~3.3 GB | Slow | Google. CPU-friendly. |
| llama3.2:3b | CPU only | ~2 GB | Very slow | Anything with 4 GB RAM. Goals take much longer. |

## What "fast" actually means

For the recommended qwen3.6 on a 24 GB+ GPU:
- A goal cycle is typically 200-600 seconds (3-10 min). 
- One existence-loop call (picking the next goal) is ~5-30s.
- One planning call is ~30-90s.
- One LLM step (`ollama_chat`) is ~50-100s.
- Validation (semantic + fact-check) is ~30-120s.

On CPU with llama3.2:3b: multiply everything by ~10. The system still works but you'll see fewer goal completions per hour.

## Why qwen3.6 is the default

- Local-only (no cloud calls)
- MoE means inference is fast (3B active params) but reasoning quality matches 30B+ dense models
- Handles 32K context comfortably — important because the existence prompt is large (worldview + suffering + peer state + capabilities)
- Doesn't produce schema-shaped output with empty fields under `format=json` (a problem with some thinking-mode models)

## Switching models later

Edit `config.json`:

```json
{
  "ollama": {
    "default_model": "qwen3.5:9b"
  }
}
```

Then `python3 hollow.py stop` and `python3 hollow.py` to restart. Make sure the model is pulled first: `ollama pull qwen3.5:9b`.

## What about Claude / OpenAI / etc.?

Not supported and won't be. The system is designed for sustained 24/7 unattended operation; cloud LLMs would burn $100+/day in goal-selection alone. Everything runs locally on whatever model you pick, and the substrate's design (suffering, capability locks, lesson promotion) is calibrated for local inference speeds.

The exception is `invoke_claude` — agents can submit implementation requests to YOU, the operator. If you happen to use [Claude Code](https://claude.ai/code) as your editor, you can hook MCP and read/satisfy those requests there. But it's a human-in-the-loop channel, not an agent-side LLM call.
