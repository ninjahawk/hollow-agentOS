#!/usr/bin/env python3
"""
AgentOS Semantic Search
Embeds workspace files using nomic-embed-text via Ollama.
Cosine similarity search over workspace chunks — no external vector DB needed.

Chunking strategy:
  .py files  → AST-aware: one chunk per function/class (finds the right code, not nearby lines)
  other files → char-based with overlap (600 chars, 80 overlap)
"""

import ast
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import urllib.request

SEMANTIC_INDEX_PATH = Path("/agentOS/memory/semantic-index.json")
CONFIG_PATH = Path("/agentOS/config.json")
EMBED_MODEL = "nomic-embed-text"
CHUNK_SIZE = 600    # chars — used for non-Python files
CHUNK_OVERLAP = 80  # overlap to preserve context across boundaries


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _ollama_host() -> str:
    return _load_config().get("ollama", {}).get("host", "http://localhost:11434")


def _ollama_embed_host() -> str:
    # Prefer OLLAMA_EMBED_HOST (dedicated embed instance) over OLLAMA_HOST over config default
    import os
    return os.getenv("OLLAMA_EMBED_HOST", os.getenv("OLLAMA_HOST", _ollama_host()))


def embed(text: str) -> Optional[list[float]]:
    """Get embedding vector from Ollama nomic-embed-text.
    Retries up to 3 times with backoff on 503 (queue full) before giving up.
    """
    import time
    import urllib.error

    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    for attempt in range(3):
        req = urllib.request.Request(
            f"{_ollama_embed_host()}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read()).get("embedding")
        except urllib.error.HTTPError as e:
            if e.code == 503 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))  # 1.5s, then 3s
                continue
            return None
        except Exception:
            return None
    return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── Char-based chunker (non-Python files) ────────────────────────────────────

def chunk_text(text: str) -> list[str]:
    """Naive character-based chunking with overlap. Used for non-Python files."""
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if c.strip()]


# ── AST-aware chunker (Python files) ─────────────────────────────────────────

def chunk_python_ast(text: str) -> list[str]:
    """
    AST-aware chunking for Python source files.
    Each top-level function, async function, and class becomes its own chunk.
    Large classes are split into header + per-method chunks.
    Module-level code (imports, constants) becomes a separate leading chunk.
    Falls back to char-based chunking on parse error.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return chunk_text(text)

    lines = text.splitlines(keepends=True)
    chunks = []

    # Collect line ranges of top-level definitions
    def_ranges = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for ln in range(node.lineno, node.end_lineno + 1):
                def_ranges.add(ln)

    # Module-level chunk: imports + top-level constants/assignments
    module_lines = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno - 1
            end = node.end_lineno if hasattr(node, "end_lineno") else node.lineno
            module_lines.extend(lines[start:end])
    if module_lines:
        chunk = "".join(module_lines).strip()
        if chunk:
            chunks.append(chunk)

    # One chunk per top-level function / class
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        start = node.lineno - 1
        end = node.end_lineno
        full_text = "".join(lines[start:end])

        if isinstance(node, ast.ClassDef) and len(full_text) > CHUNK_SIZE * 3:
            # Large class: emit class header (up to first method) + each method separately
            first_method_line = None
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    first_method_line = item.lineno - 1
                    break

            header_end = first_method_line if first_method_line else end
            header = "".join(lines[start:header_end]).strip()
            if header:
                chunks.append(header)

            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mstart = item.lineno - 1
                    mend = item.end_lineno
                    method = "".join(lines[mstart:mend]).strip()
                    if method:
                        chunks.append(method)
        else:
            if full_text.strip():
                chunks.append(full_text.strip())

    return chunks if chunks else chunk_text(text)


# ── Dispatcher: picks chunker by file type ────────────────────────────────────

def chunk_file(text: str, filepath: str) -> list[str]:
    """Choose chunking strategy based on file extension."""
    if filepath.endswith(".py"):
        return chunk_python_ast(text)
    return chunk_text(text)


# ── Index ─────────────────────────────────────────────────────────────────────

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

        for i, chunk in enumerate(chunk_file(text, path)):
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
    """Index agent source code only.

    Only indexes /agentOS/agents/*.py — the canonical source of truth.
    Workspace output files are excluded to prevent drift artifacts
    from contaminating semantic search results.

    Always performs a FULL rebuild (wipes old index) so stale chunks
    from drift files do not persist across re-indexes.
    """
    SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 "dist", "build", ".next", "target", ".cache"}

    paths = []

    # Index agent source code exclusively
    agents_root = Path("/agentOS/agents")
    if agents_root.exists():
        for item in agents_root.rglob("*"):
            if any(part in SKIP_DIRS for part in item.parts):
                continue
            if item.name.startswith("."):
                continue
            if item.is_file() and item.suffix == ".py":
                paths.append(str(item))

    # Wipe existing index so stale chunks from drift/workspace files are removed
    from datetime import datetime, timezone
    empty = {"chunks": [], "indexed_at": None, "total_files": 0}
    save_index(empty)

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
    elif cmd == "test-chunker":
        path = sys.argv[2] if len(sys.argv) > 2 else __file__
        text = Path(path).read_text()
        chunks = chunk_file(text, path)
        print(json.dumps({"file": path, "chunks": len(chunks),
                          "previews": [c[:80] for c in chunks]}, indent=2))
    else:
        print(json.dumps({"error": f"unknown command: {cmd}"}))
        sys.exit(1)
