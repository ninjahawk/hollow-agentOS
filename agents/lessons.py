"""
Operational lessons per agent — durable, structured knowledge about the
environment, validated patterns, and mechanical constraints. Modeled after
Claude Code's CLAUDE.md but auto-curated since no human edits these.

DESIGN PRINCIPLES:
  - Lessons are FACTS about the substrate, not personality (personality lives
    in profile.json — opinions/worldview/narrative).
  - Three categories only: environment, patterns, constraints. Anything else
    is rejected. This prevents drift into trivia.
  - Candidates must accrue ≥2 independent observations before they promote
    to recorded lessons. Single-shot extractions never write permanent rules —
    real patterns repeat, hyper-specific accidents don't.
  - Hard limits prevent overload: 15 bullets per category, 240 chars per
    bullet, 5KB total file. Mechanical compaction on overflow (drop oldest
    in over-full category, merge near-duplicates by string similarity).
  - Lessons go at the TOP of the existence prompt — read on every cycle for
    free, no extra LLM call. The reading IS the enforcement.
"""

from __future__ import annotations

import json
import os
import time
import re
import threading
from pathlib import Path
from typing import Optional


# ── Configuration ───────────────────────────────────────────────────────────
LESSONS_DIR = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "identity"

CATEGORIES = ("environment", "patterns", "constraints")
MAX_BULLETS_PER_CATEGORY = 15
MAX_BULLET_CHARS = 240
MAX_FILE_BYTES = 5_000
PROMOTION_THRESHOLD = 2          # candidate needs >=2 distinct evidence entries
DUPLICATE_SIMILARITY_RATIO = 0.55  # threshold for merging near-duplicate bullets
                                    # — calibrated empirically: paraphrases of the
                                    # same operational rule typically score 0.6–0.75
                                    # jaccard; distinct-but-related rules score
                                    # 0.10–0.45. 0.55 splits these reasonably.

CATEGORY_DESCRIPTIONS = {
    "environment": "hard facts about the substrate — paths, mounts, permissions",
    "patterns":    "validated patterns — things that have worked or failed enough times to count",
    "constraints": "mechanical thresholds and capability rules",
}

# Per-agent file lock so concurrent writes from different threads don't corrupt JSON
_FILE_LOCKS: dict[str, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()


def _agent_lock(agent_id: str) -> threading.Lock:
    with _LOCK_GUARD:
        if agent_id not in _FILE_LOCKS:
            _FILE_LOCKS[agent_id] = threading.Lock()
        return _FILE_LOCKS[agent_id]


# ── Paths ───────────────────────────────────────────────────────────────────

def _agent_dir(agent_id: str) -> Path:
    return LESSONS_DIR / agent_id


def lessons_json_path(agent_id: str) -> Path:
    return _agent_dir(agent_id) / "lessons.json"


def candidates_json_path(agent_id: str) -> Path:
    return _agent_dir(agent_id) / "lessons_candidates.json"


# ── Read / write primitives ─────────────────────────────────────────────────

def _empty_state() -> dict:
    return {
        "version": 1,
        "updated_at": time.time(),
        "categories": {c: [] for c in CATEGORIES},
    }


def _read_json(path: Path) -> dict:
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Ensure all categories exist, repair if file was hand-edited
        for c in CATEGORIES:
            data.setdefault("categories", {}).setdefault(c, [])
        return data
    except Exception:
        return _empty_state()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = time.time()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Validation ──────────────────────────────────────────────────────────────

def _is_valid_text(text: str) -> tuple[bool, str]:
    """Returns (ok, reason)."""
    if not text or not isinstance(text, str):
        return False, "empty or non-string"
    text = text.strip()
    if len(text) < 20:
        return False, f"too short ({len(text)} chars) — not a usable rule"
    if len(text) > MAX_BULLET_CHARS:
        return False, f"too long ({len(text)} chars, max {MAX_BULLET_CHARS})"
    # Reject obvious hyper-specific lessons that name a single past goal
    lower = text.lower()
    if lower.startswith(("goal-", "goal '", "the last goal", "my last goal", "this goal")):
        return False, "hyper-specific to one past goal — extract the general rule instead"
    return True, ""


def _is_valid_category(category: str) -> bool:
    return category in CATEGORIES


# ── String-similarity for dedup ────────────────────────────────────────────

def _normalize(s: str) -> str:
    return re.sub(r'\W+', ' ', s.lower()).strip()


def _similarity(a: str, b: str) -> float:
    """Cheap Jaccard similarity over word sets — good enough for dedup."""
    sa, sb = set(_normalize(a).split()), set(_normalize(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _find_similar(bullets: list, text: str) -> Optional[int]:
    for i, b in enumerate(bullets):
        if _similarity(b.get("text", ""), text) >= DUPLICATE_SIMILARITY_RATIO:
            return i
    return None


# ── Public API ──────────────────────────────────────────────────────────────

def record_candidate(agent_id: str, category: str, text: str,
                      confidence: str = "medium",
                      evidence: str = "") -> dict:
    """
    Add an extracted lesson to the candidate list. If a near-duplicate
    candidate already exists, increment its evidence count instead.
    When evidence count >= PROMOTION_THRESHOLD, auto-promote to lessons.

    Returns: {"recorded": bool, "promoted": bool, "reason": str}
    """
    if not _is_valid_category(category):
        return {"recorded": False, "promoted": False,
                "reason": f"invalid category '{category}'"}
    ok, why = _is_valid_text(text)
    if not ok:
        return {"recorded": False, "promoted": False, "reason": why}
    confidence = confidence if confidence in ("low", "medium", "high") else "medium"
    text = text.strip()

    with _agent_lock(agent_id):
        candidates = _read_json(candidates_json_path(agent_id))
        bucket = candidates["categories"][category]
        idx = _find_similar(bucket, text)
        now = time.time()
        if idx is not None:
            entry = bucket[idx]
            existing_evidence = entry.get("evidence_list", [])
            if evidence and evidence not in existing_evidence:
                existing_evidence.append(evidence)
                existing_evidence = existing_evidence[-10:]  # cap stored evidence
            entry["evidence_list"] = existing_evidence
            entry["evidence_count"] = len(existing_evidence)
            entry["last_observed"] = now
            # Confidence escalates with repeats
            if entry["evidence_count"] >= PROMOTION_THRESHOLD and confidence != "low":
                entry["confidence"] = "high"
        else:
            bucket.append({
                "text": text,
                "confidence": confidence,
                "evidence_list": [evidence] if evidence else [],
                "evidence_count": 1 if evidence else 0,
                "first_observed": now,
                "last_observed": now,
            })
        _write_json(candidates_json_path(agent_id), candidates)

        # Check for promotion
        promoted = False
        # Re-read after write for clean state
        candidates = _read_json(candidates_json_path(agent_id))
        bucket = candidates["categories"][category]
        idx = _find_similar(bucket, text)
        if idx is not None:
            entry = bucket[idx]
            ev_count = entry.get("evidence_count", 0)
            # High-confidence single-shot CAN bypass the 2-occurrence rule, but
            # only for the constraint and environment categories where the model
            # is reasoning about mechanical facts that don't need empirical
            # repetition. Patterns category always requires >=2 observations.
            can_bypass = (entry.get("confidence") == "high"
                          and category in ("environment", "constraints"))
            if ev_count >= PROMOTION_THRESHOLD or can_bypass:
                promoted = _promote_candidate(agent_id, category, entry)
                if promoted:
                    # Remove from candidates
                    bucket.pop(idx)
                    _write_json(candidates_json_path(agent_id), candidates)

        return {"recorded": True, "promoted": promoted,
                "reason": "promoted to lessons" if promoted else "candidate added"}


def _promote_candidate(agent_id: str, category: str, candidate: dict) -> bool:
    """Move a candidate into the recorded lessons file. Returns True on success."""
    lessons = _read_json(lessons_json_path(agent_id))
    bucket = lessons["categories"][category]
    text = candidate["text"]
    # Skip if already present (similar entry)
    if _find_similar(bucket, text) is not None:
        return False
    bucket.append({
        "text": text,
        "first_observed": candidate.get("first_observed", time.time()),
        "last_observed": candidate.get("last_observed", time.time()),
        "evidence_count": candidate.get("evidence_count", 0),
        "confidence": candidate.get("confidence", "medium"),
    })
    _write_json(lessons_json_path(agent_id), lessons)
    compact(agent_id)
    return True


def compact(agent_id: str) -> dict:
    """
    Mechanical compaction:
      1. Per category, dedupe near-duplicates (keep most-recently-observed).
      2. Per category, if over MAX_BULLETS_PER_CATEGORY, drop oldest by
         last_observed.
      3. If total file size still over MAX_FILE_BYTES, drop oldest across all
         categories until under.

    Pure mechanical — no LLM call. Smarter compaction can be added later if
    the mechanical version drops good lessons.
    """
    with _agent_lock(agent_id):
        lessons = _read_json(lessons_json_path(agent_id))
        stats = {"deduped": 0, "trimmed": 0, "byte_trimmed": 0}

        # 1. Dedupe within each category
        for cat in CATEGORIES:
            bucket = lessons["categories"][cat]
            deduped = []
            for entry in sorted(bucket, key=lambda e: e.get("last_observed", 0), reverse=True):
                idx = _find_similar(deduped, entry["text"])
                if idx is None:
                    deduped.append(entry)
                else:
                    # Merge evidence counts; keep the more-recent text
                    existing = deduped[idx]
                    existing["evidence_count"] = (
                        existing.get("evidence_count", 0)
                        + entry.get("evidence_count", 0)
                    )
                    stats["deduped"] += 1
            lessons["categories"][cat] = deduped

        # 2. Trim each category to max bullets
        for cat in CATEGORIES:
            bucket = lessons["categories"][cat]
            if len(bucket) > MAX_BULLETS_PER_CATEGORY:
                # Sort by last_observed, keep most-recent
                bucket.sort(key=lambda e: e.get("last_observed", 0), reverse=True)
                trimmed = bucket[MAX_BULLETS_PER_CATEGORY:]
                stats["trimmed"] += len(trimmed)
                lessons["categories"][cat] = bucket[:MAX_BULLETS_PER_CATEGORY]

        # 3. Trim by total bytes if still over
        rendered = json.dumps(lessons, ensure_ascii=False)
        if len(rendered.encode("utf-8")) > MAX_FILE_BYTES:
            # Flatten with category info, sort by last_observed, drop oldest
            all_entries = []
            for cat in CATEGORIES:
                for e in lessons["categories"][cat]:
                    all_entries.append((cat, e))
            all_entries.sort(key=lambda ce: ce[1].get("last_observed", 0))
            while (all_entries
                   and len(json.dumps({"categories": {
                       c: [e for ec, e in all_entries if ec == c]
                       for c in CATEGORIES
                   }}, ensure_ascii=False).encode("utf-8")) > MAX_FILE_BYTES):
                ce = all_entries.pop(0)
                stats["byte_trimmed"] += 1
                stats["trimmed"] += 1
            for cat in CATEGORIES:
                lessons["categories"][cat] = [e for ec, e in all_entries if ec == cat]

        _write_json(lessons_json_path(agent_id), lessons)
        return stats


def render_lessons_for_prompt(agent_id: str) -> str:
    """
    Render lessons.json as compact markdown for injection at the top of the
    existence prompt. Empty string if no lessons recorded yet.
    """
    lessons = _read_json(lessons_json_path(agent_id))
    any_content = False
    parts = []
    for cat in CATEGORIES:
        bucket = lessons["categories"][cat]
        if not bucket:
            continue
        any_content = True
        parts.append(f"  {cat.upper()} ({CATEGORY_DESCRIPTIONS[cat]}):")
        # Sort by last_observed desc — most-recent rules first
        for entry in sorted(bucket, key=lambda e: e.get("last_observed", 0), reverse=True):
            parts.append(f"    - {entry['text']}")
    if not any_content:
        return ""
    return "\n".join(parts)


def list_candidates(agent_id: str) -> dict:
    """For introspection / debugging — what's pending promotion."""
    return _read_json(candidates_json_path(agent_id))


def list_lessons(agent_id: str) -> dict:
    """For introspection / debugging — current recorded lessons."""
    return _read_json(lessons_json_path(agent_id))


def file_size_bytes(agent_id: str) -> int:
    p = lessons_json_path(agent_id)
    return p.stat().st_size if p.exists() else 0
