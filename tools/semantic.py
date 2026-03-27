#!/usr/bin/env python3
"""
AgentOS Semantic Search
Embeds workspace files using nomic-embed-text via Ollama.
Cosine similarity search over workspace chunks — no external vector DB needed.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import urllib.request

SEMANTIC_INDEX_PATH = Path("/agentOS/memory/semantic-index.json")
CONFIG_PATH = Path("/agentOS/config.json")
EMBED_MODEL = "nomic-embed-text"
CHUNK_SIZE = 600   # chars per chunk
CHUNK_OVERLAP = 80 # overlap to preserve context across chunk boundaries


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _ollama_host() -> str:
    return _load_config().get("ollama", {}).get("host", "http://localhost:11434")


def embed(text: str) -> Optional[list[float]]:
    """Get embedding vector from Ollama nomic-embed-text."""
    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{_ollama_host()}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get("embedding")
    except Exception:
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if c.strip()]


def load_index() -> dict:
    if SEMANTIC_INDEX_PATH.exists():
        try:
            return json.loads(SEMANTIC_INDEX_PATH.read_text())
        except Exception:
            pass
    return {"chunks": [], "indexed_at": None, "total_files": 0}


def save_index(index: dict) -> None:
    SEMANTIC_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEMANTIC_INDEX_PATH.write_text(json.dumps(index))


def index_files(paths: list[str]) -> dict:
    """Embed a list of files and update the semantic index."""
    index = load_index()
    path_set = set(paths)
    # Drop stale entries for files being re-indexed
    index["chunks"] = [c for c in index["chunks"] if c["file"] not in path_set]

    added, failed = 0, 0
    for path in paths:
        p = Path(path)
        if not p.exists() or not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace")
        except Exception:
            failed += 1
            continue

        for i, chunk in enumerate(chunk_text(text)):
            vec = embed(chunk)
            if vec is None:
                failed += 1
                continue
            index["chunks"].append({
                "file": str(p),
                "chunk_idx": i,
                "preview": chunk[:300],
                "embedding": vec
            })
            added += 1

    index["indexed_at"] = datetime.now(timezone.utc).isoformat()
    index["total_files"] = len({c["file"] for c in index["chunks"]})
    save_index(index)
    return {
        "added_chunks": added,
        "failed": failed,
        "total_chunks": len(index["chunks"]),
        "total_files": index["total_files"]
    }


def index_workspace() -> dict:
    """Index all eligible files in the workspace root."""
    config = _load_config()
    root = config.get("workspace", {}).get("root", "/agentOS/workspace")
    extensions = set(config.get("workspace", {}).get("index_extensions", []))
    SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 "dist", "build", ".next", "target", ".cache"}

    paths = []
    for item in Path(root).rglob("*"):
        if any(part in SKIP_DIRS for part in item.parts):
            continue
        if item.name.startswith("."):
            continue
        if item.is_file() and (not extensions or item.suffix in extensions):
            paths.append(str(item))

    return index_files(paths)


def search(query: str, top_k: int = 10) -> list[dict]:
    """Find the most relevant chunks for a query."""
    qvec = embed(query)
    if qvec is None:
        return [{"error": "embed model unavailable — is nomic-embed-text pulled?"}]

    index = load_index()
    scored = sorted(
        [
            {
                "file": c["file"],
                "chunk_idx": c["chunk_idx"],
                "preview": c["preview"],
                "score": round(cosine_similarity(qvec, c["embedding"]), 4)
            }
            for c in index["chunks"]
        ],
        key=lambda x: x["score"],
        reverse=True
    )
    return scored[:top_k]


def stats() -> dict:
    index = load_index()
    return {
        "total_chunks": len(index["chunks"]),
        "total_files": index.get("total_files", 0),
        "indexed_at": index.get("indexed_at"),
        "model": EMBED_MODEL
    }


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "index":
        print(json.dumps(index_workspace()))
    elif cmd == "search":
        q = " ".join(sys.argv[2:])
        print(json.dumps(search(q), indent=2))
    elif cmd == "stats":
        print(json.dumps(stats()))
    else:
        print(json.dumps({"error": f"unknown command: {cmd}"}))
        sys.exit(1)
