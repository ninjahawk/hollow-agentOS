"""
Standards Layer — store project conventions, auto-inject before tasks.

Solves the "agents forget how this project does things" problem.
Write a convention once, agents get it automatically when relevant.

Storage: /agentOS/memory/standards.json
Matching: cosine similarity via nomic-embed-text (falls back to keyword match if Ollama unavailable)
"""

import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Optional

STANDARDS_PATH = Path("/agentOS/memory/standards.json")
OLLAMA_HOST = "http://localhost:11434"


def _load() -> dict:
    if STANDARDS_PATH.exists():
        try:
            return json.loads(STANDARDS_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"standards": {}}


def _save(data: dict) -> None:
    STANDARDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STANDARDS_PATH.write_text(json.dumps(data, indent=2))


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── Embedding (with Ollama fallback) ─────────────────────────────────────────

def _embed(text: str) -> Optional[list[float]]:
    """Get embedding vector via Ollama nomic-embed-text. Returns None if unavailable."""
    try:
        body = json.dumps({"model": "nomic-embed-text", "prompt": text}).encode()
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return data.get("embedding")
    except Exception:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _keyword_score(query: str, text: str) -> float:
    """Fallback similarity when embeddings unavailable: word overlap ratio."""
    q_words = set(query.lower().split())
    t_words = set(text.lower().split())
    if not q_words:
        return 0.0
    return len(q_words & t_words) / len(q_words)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def set_standard(name: str, content: str, tags: list[str] = None,
                 description: str = "") -> dict:
    """
    Store a named project convention. Computes and caches embedding.
    name: short identifier, e.g. "api-response-format"
    content: the actual convention text (rule-first, concise)
    tags: optional labels for filtering
    description: one-line summary used in listings
    """
    data = _load()
    embedding = _embed(f"{name} {description} {content}")
    standard = {
        "name": name,
        "description": description or name,
        "content": content,
        "tags": tags or [],
        "created_at": data["standards"].get(name, {}).get("created_at", _now()),
        "updated_at": _now(),
        "embedding": embedding,
    }
    data["standards"][name] = standard
    _save(data)
    # Return without embedding (it's large)
    return {k: v for k, v in standard.items() if k != "embedding"}


def get_standard(name: str) -> Optional[dict]:
    data = _load()
    s = data["standards"].get(name)
    if s:
        return {k: v for k, v in s.items() if k != "embedding"}
    return None


def list_standards() -> list[dict]:
    data = _load()
    return [
        {k: v for k, v in s.items() if k != "embedding"}
        for s in data["standards"].values()
    ]


def delete_standard(name: str) -> bool:
    data = _load()
    if name not in data["standards"]:
        return False
    del data["standards"][name]
    _save(data)
    return True


# ── Relevance matching ────────────────────────────────────────────────────────

def get_relevant_standards(task_description: str, top_k: int = 5,
                           min_score: float = 0.3) -> list[dict]:
    """
    Return the standards most relevant to a task description.
    Uses cosine similarity on embeddings, falls back to keyword match.
    Returns list of {name, description, content, score}.
    """
    data = _load()
    if not data["standards"]:
        return []

    query_embedding = _embed(task_description)
    results = []

    for name, s in data["standards"].items():
        if query_embedding and s.get("embedding"):
            score = _cosine(query_embedding, s["embedding"])
        else:
            score = _keyword_score(
                task_description,
                f"{s['name']} {s.get('description', '')} {s.get('content', '')}"
            )

        if score >= min_score:
            results.append({
                "name": name,
                "description": s.get("description", ""),
                "content": s.get("content", ""),
                "tags": s.get("tags", []),
                "score": round(score, 4),
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def get_relevant_standards_text(task_description: str, top_k: int = 3) -> str:
    """
    Return relevant standards as a formatted string for injection into a system prompt.
    Returns empty string if no relevant standards found.
    """
    standards = get_relevant_standards(task_description, top_k=top_k)
    if not standards:
        return ""

    lines = ["## Project Standards\n"]
    for s in standards:
        lines.append(f"### {s['name']}")
        if s.get("description"):
            lines.append(f"_{s['description']}_")
        lines.append(s["content"])
        lines.append("")

    return "\n".join(lines)
