#!/usr/bin/env python3
"""
AgentOS Token Efficiency Benchmark
===================================
Measures token cost of naive shell-based approaches vs AgentOS structured APIs.

Each scenario runs both approaches, counts tokens (chars/4 approximation),
and reports the savings ratio.
"""

import json
import subprocess
import urllib.request
import time
import sys
import os

API = "http://localhost:7777"
TOKEN = json.loads(open("/agentOS/config.json").read())["api"]["token"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def count_tokens(text: str) -> int:
    """Approximate token count: chars / 4 (standard LLM approximation)."""
    return max(1, len(str(text)) // 4)


def api_get(path: str) -> tuple[dict, int]:
    req = urllib.request.Request(f"{API}{path}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw), count_tokens(raw)


def api_post(path: str, data: dict) -> tuple[dict, int]:
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{API}{path}", data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw), count_tokens(raw)


def shell_run(cmd: str) -> tuple[str, int]:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    out = result.stdout + result.stderr
    return out, count_tokens(out)


SCENARIOS = []


def scenario(name: str, description: str):
    def decorator(fn):
        SCENARIOS.append({"name": name, "description": description, "fn": fn})
        return fn
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1: System state discovery
# ─────────────────────────────────────────────────────────────────────────────
@scenario(
    name="system_state",
    description="Agent discovers current system state (disk, memory, GPU, services)"
)
def bench_system_state():
    # Naive: a capable agent on bare Linux must run many commands to discover state.
    # It doesn't know the exact machine layout upfront, so it explores broadly.
    naive_cmds = [
        "uname -a && hostname",
        "df -h",                          # all mounts, not just /
        "free -h",
        "cat /proc/meminfo | head -20",   # agent often checks both
        "/usr/lib/wsl/lib/nvidia-smi 2>/dev/null | head -30 || echo 'no nvidia-smi'",
        "ps aux --sort=-%cpu | head -20", # see what's actually running
        "systemctl list-units --state=running --no-pager 2>/dev/null | head -30",
        "ls -la /agentOS/ 2>/dev/null || ls -la ~/ | head -20",
        "ip addr show | grep inet | head -10",  # discover network/API endpoint
    ]
    naive_tokens = 0
    for cmd in naive_cmds:
        _, t = shell_run(cmd)
        naive_tokens += t
    # Overhead: agent must parse and synthesize all human-readable output
    naive_parse_overhead = count_tokens(
        "Parse the above command outputs. From df -h extract disk usage percentages. "
        "From free -h extract memory used/available. From nvidia-smi extract GPU name, "
        "VRAM used/total, utilization. From ps aux identify heavy processes. "
        "From systemctl extract which services are active. Summarize into structured state."
    )
    naive_tokens += naive_parse_overhead

    # AgentOS: single structured JSON call, machine-parseable, no interpretation needed
    _, agentos_tokens = api_get("/state")

    return naive_tokens, agentos_tokens, {
        "naive_cmds": len(naive_cmds),
        "naive_parse_overhead": naive_parse_overhead,
        "note": "naive requires 9 shell calls + parsing overhead; /state is 1 structured call"
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2: Find how a feature works (semantic search vs grep)
# ─────────────────────────────────────────────────────────────────────────────
@scenario(
    name="semantic_search",
    description="Agent finds how AST chunking works in the codebase"
)
def bench_semantic_search():
    # Naive: grep across codebase, return raw matching lines
    _, naive_tokens = shell_run(
        "grep -rn 'chunk\\|ast\\|AST' /agentOS/tools/ --include='*.py' | head -60"
    )
    # Agent still needs to read the matched file for context
    _, read_tokens = shell_run("wc -c /agentOS/tools/semantic.py")
    # Approximating cost of catting the whole file
    full_file, file_read_tokens = shell_run("cat /agentOS/tools/semantic.py")
    naive_tokens += file_read_tokens

    # AgentOS: semantic search returns only the relevant function chunks
    _, agentos_tokens = api_post("/semantic/search", {
        "query": "how does AST chunking work for Python files",
        "top_k": 3
    })

    return naive_tokens, agentos_tokens, {
        "naive_full_file_chars": len(full_file),
        "note": "naive reads whole file; AgentOS returns 3 targeted chunks",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3: Read file + understand related code (read_context vs cat)
# ─────────────────────────────────────────────────────────────────────────────
@scenario(
    name="read_context",
    description="Agent reads a file and finds related code it needs to understand"
)
def bench_read_context():
    target = "/agentOS/memory/manager.py"

    # Naive: cat full target file, grep imports, cat full related files
    _, t1 = shell_run(f"cat {target}")  # full file
    _, t2 = shell_run("grep -rn 'from memory\\|import manager\\|manager\\.' /agentOS/ --include='*.py' | head -30")
    _, t3 = shell_run("cat /agentOS/api/server.py")  # likely related — agent cats whole file
    _, t4 = shell_run("cat /agentOS/mcp/server.py | head -200")  # checks MCP too
    naive_tokens = t1 + t2 + t3 + t4

    # AgentOS: read_context returns file + only the semantically relevant chunks from other files
    _, agentos_tokens = api_post("/fs/read_context", {
        "path": target,
        "top_k": 5
    })

    return naive_tokens, agentos_tokens, {
        "note": "naive cats 3 full files (~15K tokens); read_context returns target + 5 relevant chunks"
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 4: What changed since last check? (state diff vs git status)
# ─────────────────────────────────────────────────────────────────────────────
@scenario(
    name="state_diff",
    description="Agent checks what changed in the system since its last poll"
)
def bench_state_diff():
    # Naive: git status + git diff + check running processes
    _, t1 = shell_run("git -C /agentOS status 2>&1")
    _, t2 = shell_run("git -C /agentOS diff 2>&1 | head -100")
    _, t3 = shell_run("systemctl status agentos-api ollama nginx 2>&1 | head -40")
    _, t4 = shell_run("ls -la /agentOS/memory/ 2>&1")
    naive_tokens = t1 + t2 + t3 + t4

    # AgentOS: state/diff returns only changed fields since a timestamp
    since = "2026-03-27T18:00:00Z"  # ~30 min ago
    _, agentos_tokens = api_get(f"/state/diff?since={since}")

    return naive_tokens, agentos_tokens, {
        "naive_cmds": 4,
        "note": "state/diff returns only changed fields; polling agents call this every 30s"
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 5: Agent startup / session pickup (handoff vs cold discovery)
# ─────────────────────────────────────────────────────────────────────────────
@scenario(
    name="agent_pickup",
    description="New agent session discovers what was in-progress (cold start vs handoff)"
)
def bench_agent_pickup():
    # Naive cold start: agent must read all memory files, full session log, git history
    _, t1 = shell_run("cat /agentOS/memory/session-log.json 2>/dev/null || echo '(no log)'")  # full log
    _, t2 = shell_run("git -C /agentOS log --oneline -30 2>&1")
    _, t3 = shell_run("git -C /agentOS diff HEAD~3..HEAD 2>&1 | head -150")  # what changed
    _, t4 = shell_run("cat /agentOS/memory/workspace-map.json 2>/dev/null | head -200")
    _, t5 = shell_run("cat /agentOS/memory/decisions.json 2>/dev/null | head -100")
    _, t6 = shell_run("ls -la /agentOS/ && ls -la /agentOS/memory/")
    naive_tokens = t1 + t2 + t3 + t4 + t5 + t6
    # Agent must synthesize all of this to answer: what's in progress, what next?
    naive_reasoning_overhead = count_tokens(
        "Review the session log, git history, workspace map, and pending decisions above. "
        "Identify: (1) what was the last agent working on, (2) what tasks are in progress, "
        "(3) what decisions are pending my approval, (4) what should I do next. "
        "Parse JSON logs and git diffs to reconstruct the prior session's state."
    )
    naive_tokens += naive_reasoning_overhead

    # AgentOS: single pickup call returns structured handoff + changes since
    _, agentos_tokens = api_get("/agent/pickup")

    return naive_tokens, agentos_tokens, {
        "naive_cmds": 6,
        "naive_reasoning_overhead": naive_reasoning_overhead,
        "note": "handoff gives structured next_steps, in_progress, changes_since in 1 call"
    }


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────
def run_benchmarks():
    results = []
    total_naive = 0
    total_agentos = 0

    print(f"\n{'='*70}")
    print("AgentOS Token Efficiency Benchmark")
    print(f"{'='*70}\n")

    for s in SCENARIOS:
        name = s["name"]
        desc = s["description"]
        fn = s["fn"]
        try:
            t0 = time.time()
            naive_tokens, agentos_tokens, meta = fn()
            elapsed = time.time() - t0

            savings = naive_tokens - agentos_tokens
            savings_pct = (savings / naive_tokens * 100) if naive_tokens > 0 else 0

            result = {
                "scenario": name,
                "description": desc,
                "naive_tokens": naive_tokens,
                "agentos_tokens": agentos_tokens,
                "savings_tokens": savings,
                "savings_pct": round(savings_pct, 1),
                "elapsed_s": round(elapsed, 2),
                "meta": meta,
            }
            results.append(result)
            total_naive += naive_tokens
            total_agentos += agentos_tokens

            status = "BETTER" if savings > 0 else "WORSE"
            print(f"[{status}] {name}")
            print(f"  {desc}")
            print(f"  Naive: {naive_tokens:,} tok  |  AgentOS: {agentos_tokens:,} tok  |  Savings: {savings:+,} ({savings_pct:+.1f}%)")
            if meta.get("note"):
                print(f"  Note: {meta['note']}")
            print()

        except Exception as e:
            print(f"[ERROR] {name}: {e}")
            results.append({"scenario": name, "error": str(e)})

    # Summary
    total_savings = total_naive - total_agentos
    total_pct = (total_savings / total_naive * 100) if total_naive > 0 else 0

    print(f"{'='*70}")
    print(f"TOTAL: Naive {total_naive:,} tok → AgentOS {total_agentos:,} tok")
    print(f"SAVINGS: {total_savings:,} tokens ({total_pct:.1f}% reduction)")
    print(f"{'='*70}\n")

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": {
            "total_naive_tokens": total_naive,
            "total_agentos_tokens": total_agentos,
            "total_savings_tokens": total_savings,
            "total_savings_pct": round(total_pct, 1),
        },
        "scenarios": results,
    }

    out_path = "/agentOS/memory/benchmark-results.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Full report written to {out_path}")
    return report


if __name__ == "__main__":
    run_benchmarks()
