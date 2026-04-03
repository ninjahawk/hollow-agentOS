"""
Batch LLM Server — AgentOS v4.3.0

Loads a HuggingFace model once into GPU memory.
All agent threads submit prompts to a shared queue.
Every MAX_WAIT_S seconds (or when BATCH_SIZE prompts accumulate),
fires a single model.generate(batch) — GPU processes all prompts simultaneously.

Result: 12 agents all get answers at the same time instead of queuing
one-by-one through Ollama.

Usage:
    from agents.batch_llm import get_server
    server = get_server()        # singleton, loads model on first call
    response = server.generate("your prompt")   # blocks until batch fires
"""

import json
import os
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Optional

import torch

CONFIG_PATH = Path(os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))

# ── Config ──────────────────────────────────────────────────────────────────
DEFAULT_MODEL   = os.getenv("BATCH_LLM_MODEL", "Qwen/Qwen2.5-3B-Instruct")
MAX_BATCH_SIZE  = int(os.getenv("BATCH_LLM_MAX_BATCH", "24"))
MAX_WAIT_S      = float(os.getenv("BATCH_LLM_WAIT_S", "2.5"))
MAX_NEW_TOKENS  = int(os.getenv("BATCH_LLM_MAX_TOKENS", "512"))
DTYPE           = torch.bfloat16


def _model_id() -> str:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return cfg.get("batch_llm", {}).get("model", DEFAULT_MODEL)
    except Exception:
        return DEFAULT_MODEL


# ── Server ───────────────────────────────────────────────────────────────────

class BatchLLMServer:
    """
    Singleton batch inference server.
    Call generate(prompt) from any thread — it blocks until the next
    batch fires and returns the model's response string.
    """

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()
        self._queue: list[tuple[str, int, Future]] = []
        self._trigger = threading.Event()
        self._ready = False
        self._load_error: Optional[str] = None

        # Start background threads
        t_load = threading.Thread(target=self._load_model, daemon=True, name="batch-llm-loader")
        t_load.start()
        t_work = threading.Thread(target=self._worker, daemon=True, name="batch-llm-worker")
        t_work.start()

    # ── Public API ────────────────────────────────────────────────────────

    def generate(self, prompt: str, max_new_tokens: int = MAX_NEW_TOKENS) -> str:
        """
        Submit a prompt. Blocks until the batch fires and returns the response.
        Raises RuntimeError if model failed to load.
        """
        # Wait for model to be ready (or fail)
        deadline = time.time() + 300
        while not self._ready and not self._load_error and time.time() < deadline:
            time.sleep(0.5)

        if self._load_error:
            raise RuntimeError(f"BatchLLM load failed: {self._load_error}")
        if not self._ready:
            raise RuntimeError("BatchLLM model did not load in time")

        future: Future = Future()
        with self._lock:
            self._queue.append((prompt, max_new_tokens, future))
            if len(self._queue) >= MAX_BATCH_SIZE:
                self._trigger.set()

        return future.result(timeout=180)

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def model_name(self) -> str:
        return _model_id()

    # ── Worker ────────────────────────────────────────────────────────────

    def _worker(self):
        while True:
            fired = self._trigger.wait(timeout=MAX_WAIT_S)
            self._trigger.clear()

            with self._lock:
                batch = self._queue[:]
                self._queue.clear()

            if not batch:
                continue

            if not self._ready:
                # Model not loaded yet — re-queue and wait
                with self._lock:
                    self._queue = batch + self._queue
                time.sleep(1.0)
                continue

            try:
                prompts     = [p for p, _, _ in batch]
                max_tokens  = max(t for _, t, _ in batch)
                results     = self._run_batch(prompts, max_tokens)
                for (_, _, fut), result in zip(batch, results):
                    if not fut.done():
                        fut.set_result(result)
            except Exception as e:
                err = str(e)
                for _, _, fut in batch:
                    if not fut.done():
                        fut.set_exception(RuntimeError(err))

    def _run_batch(self, prompts: list[str], max_new_tokens: int) -> list[str]:
        """Tokenize batch → generate → decode new tokens only."""
        # Format as chat messages
        formatted = []
        for p in prompts:
            msgs = [{"role": "user", "content": p}]
            text = self._tokenizer.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True
            )
            formatted.append(text)

        inputs = self._tokenizer(
            formatted,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=2048,
        ).to("cuda")

        input_len = inputs["input_ids"].shape[1]

        with torch.no_grad():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=0.7,
                top_p=0.9,
                pad_token_id=self._tokenizer.eos_token_id,
            )

        results = []
        for output in outputs:
            new_tokens = output[input_len:]
            results.append(self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip())
        return results

    # ── Model loader ──────────────────────────────────────────────────────

    def _load_model(self):
        try:
            from transformers import AutoTokenizer, AutoModelForCausalLM

            model_id = _model_id()
            print(f"[BatchLLM] Loading {model_id} ...", flush=True)

            self._tokenizer = AutoTokenizer.from_pretrained(
                model_id, trust_remote_code=True
            )
            if self._tokenizer.pad_token is None:
                self._tokenizer.pad_token = self._tokenizer.eos_token
            self._tokenizer.padding_side = "left"  # required for batch generation

            self._model = AutoModelForCausalLM.from_pretrained(
                model_id,
                torch_dtype=DTYPE,
                device_map="cuda",
                trust_remote_code=True,
            )
            self._model.eval()

            vram = torch.cuda.memory_allocated() // 1024**2
            print(f"[BatchLLM] Ready. VRAM used: {vram}MB", flush=True)
            self._ready = True

        except Exception as e:
            self._load_error = str(e)
            print(f"[BatchLLM] LOAD ERROR: {e}", flush=True)


# ── Singleton ─────────────────────────────────────────────────────────────────

_server: Optional[BatchLLMServer] = None
_server_lock = threading.Lock()


def get_server() -> BatchLLMServer:
    global _server
    if _server is None:
        with _server_lock:
            if _server is None:
                _server = BatchLLMServer()
    return _server
