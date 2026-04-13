"""
Web Search — AgentOS external knowledge module.

Gives agents access to external reality before they act.
No API keys required — uses public free APIs only.

Sources:
  - GitHub: find existing Python implementations
  - Wikipedia: domain knowledge and definitions
  - DuckDuckGo instant answers: quick factual lookups
  - PyPI: check if Python packages already solve this

The key purpose is not just information retrieval — it's grounding.
Agents form a prediction ("I will build X"), then search to discover
what already exists. The gap between prediction and reality updates
the self-model.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

SEARCH_TIMEOUT  = 6       # seconds per request
CACHE_TTL       = 600     # 10 minutes — don't re-search same query
_CACHE: dict    = {}      # in-memory cache {query_hash: (ts, result)}


def _cache_key(query: str) -> str:
    return hashlib.md5(query.lower().strip().encode()).hexdigest()


def _cached(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry[0]) < CACHE_TTL:
        return entry[1]
    return None


def _store(key: str, value):
    _CACHE[key] = (time.time(), value)
    return value


# ── GitHub search ─────────────────────────────────────────────────────────────

def search_github(query: str, max_results: int = 4) -> list[dict]:
    """
    Find Python repositories on GitHub related to a query.
    Returns list of {name, description, stars, url}.
    Uses unauthenticated API — 10 req/min limit.
    """
    key = _cache_key(f"gh:{query}")
    cached = _cached(key)
    if cached is not None:
        return cached

    if not _HTTPX_AVAILABLE:
        return []

    try:
        resp = httpx.get(
            "https://api.github.com/search/repositories",
            params={
                "q":        f"{query} language:python",
                "sort":     "stars",
                "order":    "desc",
                "per_page": max_results,
            },
            headers={"Accept": "application/vnd.github+json"},
            timeout=SEARCH_TIMEOUT,
        )
        if resp.status_code != 200:
            return _store(key, [])

        items = resp.json().get("items", [])
        results = [
            {
                "name":        item["full_name"],
                "description": (item.get("description") or "")[:120],
                "stars":       item.get("stargazers_count", 0),
                "url":         item["html_url"],
            }
            for item in items
        ]
        return _store(key, results)
    except Exception:
        return _store(key, [])


# ── Wikipedia ─────────────────────────────────────────────────────────────────

def search_wikipedia(query: str) -> str:
    """
    Get a short Wikipedia summary for a topic.
    Returns first 400 chars of the article intro, or empty string.
    """
    key = _cache_key(f"wiki:{query}")
    cached = _cached(key)
    if cached is not None:
        return cached

    if not _HTTPX_AVAILABLE:
        return ""

    try:
        # Step 1: find the best matching article title
        search_resp = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action":  "opensearch",
                "search":  query,
                "limit":   1,
                "format":  "json",
            },
            timeout=SEARCH_TIMEOUT,
        )
        titles = search_resp.json()[1] if search_resp.status_code == 200 else []
        if not titles:
            return _store(key, "")

        # Step 2: get the intro extract
        extract_resp = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action":    "query",
                "prop":      "extracts",
                "exintro":   True,
                "explaintext": True,
                "titles":    titles[0],
                "format":    "json",
            },
            timeout=SEARCH_TIMEOUT,
        )
        pages = extract_resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            extract = page.get("extract", "")
            result  = extract[:400].strip()
            return _store(key, result)

        return _store(key, "")
    except Exception:
        return _store(key, "")


# ── PyPI ──────────────────────────────────────────────────────────────────────

def check_pypi(package_name: str) -> Optional[dict]:
    """
    Check if a specific PyPI package exists.
    Returns {name, summary, version} or None.
    """
    key = _cache_key(f"pypi:{package_name}")
    cached = _cached(key)
    if cached is not None:
        return cached

    if not _HTTPX_AVAILABLE:
        return None

    try:
        resp = httpx.get(
            f"https://pypi.org/pypi/{package_name}/json",
            timeout=SEARCH_TIMEOUT,
        )
        if resp.status_code != 200:
            return _store(key, None)

        info = resp.json().get("info", {})
        result = {
            "name":    info.get("name", package_name),
            "summary": (info.get("summary") or "")[:100],
            "version": info.get("version", "?"),
        }
        return _store(key, result)
    except Exception:
        return _store(key, None)


# ── DuckDuckGo instant answers ────────────────────────────────────────────────

def duckduckgo_instant(query: str) -> str:
    """
    DuckDuckGo instant answer API — free, no key.
    Returns a short abstract or empty string.
    """
    key = _cache_key(f"ddg:{query}")
    cached = _cached(key)
    if cached is not None:
        return cached

    if not _HTTPX_AVAILABLE:
        return ""

    try:
        resp = httpx.get(
            "https://api.duckduckgo.com/",
            params={
                "q":              query,
                "format":         "json",
                "no_html":        1,
                "skip_disambig":  1,
            },
            timeout=SEARCH_TIMEOUT,
        )
        data     = resp.json()
        abstract = data.get("AbstractText", "").strip()
        answer   = data.get("Answer", "").strip()
        result   = abstract or answer or ""
        return _store(key, result[:300])
    except Exception:
        return _store(key, "")


# ── High-level: capability research ──────────────────────────────────────────

def research_capability(capability_description: str) -> dict:
    """
    Before an agent synthesizes a capability, call this to discover
    what already exists. Returns structured findings:

    {
        "github_repos":    [...],   # existing Python implementations
        "wiki_summary":    "...",   # domain background
        "ddg_answer":      "...",   # quick fact
        "summary":         "...",   # human-readable synthesis of findings
        "novelty_signal":  "high" | "medium" | "low",
    }

    novelty_signal:
      low    = well-established, many existing libraries → agent should
               build something genuinely different or much simpler
      medium = some solutions exist, room for a focused utility
      high   = little existing prior art → go for it
    """
    repos    = search_github(capability_description)
    wiki     = search_wikipedia(capability_description)
    ddg      = duckduckgo_instant(capability_description + " Python")

    # Determine novelty based on GitHub results
    if len(repos) >= 3 and any(r["stars"] > 500 for r in repos):
        novelty = "low"
    elif len(repos) >= 1:
        novelty = "medium"
    else:
        novelty = "high"

    # Build readable summary
    summary_parts = []

    if repos:
        repo_list = "; ".join(
            f"{r['name']} ({r['stars']} stars): {r['description']}"
            for r in repos[:3]
        )
        summary_parts.append(f"Existing Python implementations: {repo_list}.")

    if wiki:
        summary_parts.append(f"Domain context: {wiki[:200]}")

    if ddg and not wiki:
        summary_parts.append(f"Background: {ddg}")

    if not summary_parts:
        summary_parts.append("No significant existing implementations found.")

    return {
        "github_repos":   repos,
        "wiki_summary":   wiki,
        "ddg_answer":     ddg,
        "summary":        " ".join(summary_parts),
        "novelty_signal": novelty,
    }


def research_topic(topic: str) -> str:
    """
    For self-directed (non-synthesis) goals: get external context on a topic.
    Returns a paragraph of background the agent can reference.
    """
    wiki = search_wikipedia(topic)
    ddg  = duckduckgo_instant(topic)
    repos = search_github(topic, max_results=2)

    parts = []
    if wiki:
        parts.append(wiki[:300])
    elif ddg:
        parts.append(ddg)

    if repos:
        names = ", ".join(r["name"] for r in repos)
        parts.append(f"Related Python projects: {names}.")

    return " ".join(parts) if parts else ""
