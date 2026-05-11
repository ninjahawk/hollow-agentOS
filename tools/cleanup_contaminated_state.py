"""
One-shot cleanup of agent state contaminated under v5.7.0/5.7.1.
Run from the host (or inside container) — operates on memory/ files directly.

Cleans:
  1. opinions_list — dedup by jaccard 0.55 (collapses Cipher's triplicates)
  2. worldview     — clear if it locks on nonexistent /agentOS/agents/<role>.py
  3. open_questions — drop entries naming /agentOS paths that don't exist
  4. active goals  — abandon goals predicated on nonexistent /agentOS paths
  5. root alerts   — drop circuit_break_review messages older than 1h

Preserves: names, voices, lessons, suffering, narrative, peer messages.
"""
from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEM = ROOT / "memory"
AGENTS = ("scout", "analyst", "builder")


def _norm(s: str) -> set:
    return set(re.sub(r'\W+', ' ', s.lower()).split())


def _jaccard(a: str, b: str) -> float:
    sa, sb = _norm(a), _norm(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _path_exists_in_repo(p: str) -> bool:
    """Check whether a /agentOS path resolves to a real file on disk.
    /agentOS in the container maps to the repo root on the host."""
    if not p.startswith("/agentOS/"):
        return True  # only validate /agentOS paths
    rel = p[len("/agentOS/"):].rstrip(".,;)")
    return (ROOT / rel).exists()


def _missing_paths_in(text: str) -> list[str]:
    if not text:
        return []
    mentioned = re.findall(
        r'/agentOS/[\w./\-]+\.(?:py|json|jsonl|txt|md|csv|log|sh|yaml|yml|toml)',
        text,
    )
    mentioned += [
        m for m in re.findall(r'/agentOS/[\w/\-]+/[A-Za-z][\w\-]{2,}', text)
        if '.' not in m.split('/')[-1]
    ]
    seen = set()
    missing = []
    for p in mentioned:
        if p in seen:
            continue
        seen.add(p)
        if not _path_exists_in_repo(p):
            missing.append(p)
    return missing


def dedup_opinions(profile: dict) -> int:
    """Apply jaccard 0.55 dedup to opinions_list. Reaffirms in place."""
    ops = profile.get("opinions_list", [])
    if not ops:
        return 0
    survivors: list[dict] = []
    for op in ops:
        text = op.get("opinion", "")
        if not text:
            continue
        merged = False
        for surv in survivors:
            if _jaccard(text, surv.get("opinion", "")) >= 0.55:
                surv["reaffirmed_count"] = surv.get("reaffirmed_count", 1) + 1
                surv["last_reaffirmed"] = time.strftime("%Y-%m-%d")
                merged = True
                break
        if not merged:
            op.setdefault("reaffirmed_count", 1)
            op.setdefault("last_reaffirmed", op.get("formed", time.strftime("%Y-%m-%d")))
            op.setdefault("contradictions", 0)
            survivors.append(op)
    dropped = len(ops) - len(survivors)
    profile["opinions_list"] = survivors[-10:]
    return dropped


def clean_worldview(profile: dict) -> bool:
    """Clear worldview if it commits to nonexistent peer-named source files."""
    wv = profile.get("worldview", "")
    if not wv:
        return False
    lower = wv.lower()
    bad_signals = [
        "peer schema", "peer schemas",
        "scout.py", "analyst.py", "builder.py",
        "/agentOS/agents/scout", "/agentOS/agents/analyst", "/agentOS/agents/builder",
    ]
    if any(sig in lower for sig in bad_signals):
        profile["worldview"] = ""
        return True
    return False


def clean_questions(profile: dict) -> int:
    qs = profile.get("open_questions", [])
    if not qs:
        return 0
    survivors = [q for q in qs if not _missing_paths_in(q)]
    dropped = len(qs) - len(survivors)
    profile["open_questions"] = survivors
    return dropped


def cleanup_profile(agent_id: str) -> dict:
    p_path = MEM / "identity" / agent_id / "profile.json"
    if not p_path.exists():
        return {"agent": agent_id, "skipped": "no profile"}
    profile = json.loads(p_path.read_text(encoding="utf-8"))
    op_dropped = dedup_opinions(profile)
    wv_cleared = clean_worldview(profile)
    q_dropped = clean_questions(profile)
    if op_dropped or wv_cleared or q_dropped:
        p_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "agent": agent_id,
        "opinions_dropped": op_dropped,
        "worldview_cleared": wv_cleared,
        "questions_dropped": q_dropped,
    }


def abandon_bad_goals(agent_id: str) -> dict:
    """Mark active goals predicated on missing paths as abandoned."""
    reg = MEM / "goals" / agent_id / "registry.jsonl"
    idx = MEM / "goals" / agent_id / "index.json"
    if not reg.exists():
        return {"agent": agent_id, "skipped": "no registry"}
    lines = reg.read_text(encoding="utf-8").splitlines()
    abandoned_ids: list[str] = []
    new_lines: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            goal = json.loads(line)
        except Exception:
            new_lines.append(line)
            continue
        if goal.get("status") != "active":
            new_lines.append(line)
            continue
        missing = _missing_paths_in(goal.get("objective", ""))
        if missing:
            goal["status"] = "abandoned"
            goal.setdefault("metrics", {})
            goal["metrics"]["failure_reason"] = (
                f"abandoned by cleanup script: predicated on nonexistent paths: "
                f"{', '.join(missing[:3])}"
            )
            goal["updated_at"] = time.time()
            abandoned_ids.append(goal.get("goal_id", ""))
        new_lines.append(json.dumps(goal, ensure_ascii=False))
    if abandoned_ids:
        reg.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return {"agent": agent_id, "abandoned": abandoned_ids}


def prune_root_alerts(max_age_seconds: int = 3600) -> int:
    mb = MEM / "message-bus.json"
    if not mb.exists():
        return 0
    data = json.loads(mb.read_text(encoding="utf-8"))
    queues = data.get("queues", {})
    root = queues.get("root", [])
    if not root:
        return 0
    now = time.time()
    survivors = [
        m for m in root
        if not (
            isinstance(m, dict)
            and isinstance(m.get("content"), dict)
            and m["content"].get("decision_type") == "circuit_break_review"
            and (now - m.get("timestamp", now)) > max_age_seconds
        )
    ]
    pruned = len(root) - len(survivors)
    if pruned:
        queues["root"] = survivors
        data["queues"] = queues
        mb.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return pruned


def main():
    print(f"Cleanup target: {MEM}")
    print()
    for agent_id in AGENTS:
        p = cleanup_profile(agent_id)
        print(f"  profile/{agent_id}: {p}")
        g = abandon_bad_goals(agent_id)
        print(f"  goals/{agent_id}: {g}")
    pruned = prune_root_alerts()
    print(f"  root alerts pruned: {pruned}")
    print()
    print("Done. Restart container for new code to take effect.")


if __name__ == "__main__":
    main()
