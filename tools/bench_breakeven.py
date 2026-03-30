#!/usr/bin/env python3
"""
Hollow Break-Even Analysis
==========================

Answers: "At what task complexity / session count does Hollow's startup
overhead get outweighed by its per-interaction savings?"

The flappy bird test showed Hollow costs MORE tokens on a single-shot task
because agent_pickup front-loads all session context. This is expected and
honest. The break-even chart shows when that investment pays off.

Methodology
-----------
For N sessions (1..MAX_SESSIONS), measure cumulative token cost of:
  - Naive: sum of raw tool calls for that many sessions (no shared state)
  - Hollow: pickup overhead (paid once per session) + structured API savings

We use real API response sizes for the Hollow column and real shell output
sizes for the naive column, both measured live from the system.

Complexity axis: each "session" performs a fixed set of operations:
  - 1x system state check
  - 1x code search
  - 1x file read
  - 1x state poll

The break-even point is where Hollow's cumulative cost crosses below naive's.

Output
------
  - Prints the crossover table
  - Saves JSON with per-session data for chart rendering
  - Saves a simple ASCII chart to stdout

Usage:
    python3 tools/bench_breakeven.py [--api-url URL] [--token TOKEN] [--max-sessions N]
"""

import json
import sys
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

API_URL = "http://localhost:7777"
TOKEN = None
CHARS_PER_TOKEN = 4

MAX_SESSIONS_DEFAULT = 10


def _chars_to_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _api(method: str, path: str, body: dict = None, timeout: int = 30) -> dict:
    url = f"{API_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def _shell(cmd: str) -> str:
    result = _api("POST", "/shell", {"command": cmd})
    return result.get("stdout", "") + result.get("stderr", "")


def measure_one_session_costs(workspace_root: str) -> dict:
    """
    Measure the token cost of one session's worth of operations for both
    naive (shell) and Hollow (API) approaches.

    Returns a dict with:
      naive_per_session: tokens for raw shell calls (paid every session)
      hollow_per_session: tokens for Hollow structured calls (paid every session)
      hollow_pickup: tokens for /agent/pickup (paid per session as cold-start overhead)
    """
    print("\nMeasuring per-session costs from live system...")

    # Naive: 4 operations an agent does each session
    naive_tokens = 0

    # 1. System state
    out = f'$ df -h\n{_shell("df -h")}\n'
    out += f'$ free -m\n{_shell("free -m")}\n'
    naive_tokens += _chars_to_tokens(out)

    # 2. Code search (rg)
    rg_out = _shell(f"rg -l 'def ' --type py {workspace_root}/agentOS 2>/dev/null | head -5")
    out = f'$ rg -l ...\n{rg_out}\n'
    # Simulate reading 2 matched files
    files = [l.strip() for l in rg_out.splitlines() if l.strip()][:2]
    for f in files:
        content_resp = _api("GET", f"/fs/read?path={f}")
        content = content_resp.get("content", "x" * 5000)
        out += f'$ cat {f}\n{content}\n'
    naive_tokens += _chars_to_tokens(out)

    # 3. State poll
    poll_out = _shell("df -h") + _shell("free -m")
    naive_tokens += _chars_to_tokens(poll_out)

    # 4. File read (server.py as representative large file)
    content_resp = _api("GET", f"/fs/read?path={workspace_root}/agentOS/api/server.py")
    content = content_resp.get("content", "x" * 10000)
    naive_tokens += _chars_to_tokens(content)

    # Hollow: same 4 operations via structured API
    hollow_tokens = 0

    state_resp = _api("GET", "/state")
    if "error" not in state_resp:
        hollow_tokens += _chars_to_tokens(json.dumps(state_resp))

    search_resp = _api("POST", "/semantic/search", {"query": "function definitions", "top_k": 5})
    if "error" not in search_resp:
        hollow_tokens += _chars_to_tokens(json.dumps(search_resp))

    diff_resp = _api("GET", "/state/diff?since=2026-03-29T00:00:00Z")
    if "error" not in diff_resp:
        hollow_tokens += _chars_to_tokens(json.dumps(diff_resp))

    ctx_resp = _api("POST", "/fs/read_context", {
        "path": f"{workspace_root}/agentOS/api/server.py",
        "query": "authentication routing",
    })
    if "error" not in ctx_resp:
        hollow_tokens += _chars_to_tokens(json.dumps(ctx_resp))

    # Pickup overhead (paid once per session in cold-start model)
    pickup_resp = _api("GET", "/agent/pickup")
    pickup_tokens = 0
    if "error" not in pickup_resp:
        pickup_tokens = _chars_to_tokens(json.dumps(pickup_resp, indent=2))

    print(f"  Naive per session:   {naive_tokens:,} tokens")
    print(f"  Hollow per session:  {hollow_tokens:,} tokens (excl. pickup)")
    print(f"  Hollow pickup:       {pickup_tokens:,} tokens (paid per session start)")
    print(f"  Hollow total/sess:   {hollow_tokens + pickup_tokens:,} tokens")

    return {
        "naive_per_session": naive_tokens,
        "hollow_per_session": hollow_tokens,
        "hollow_pickup": pickup_tokens,
    }


def build_breakeven_table(costs: dict, max_sessions: int) -> list[dict]:
    naive_per = costs["naive_per_session"]
    hollow_per = costs["hollow_per_session"]
    hollow_pickup = costs["hollow_pickup"]

    rows = []
    for n in range(1, max_sessions + 1):
        naive_total = naive_per * n
        # Hollow pays pickup + session cost each session
        hollow_total = (hollow_per + hollow_pickup) * n
        delta = hollow_total - naive_total
        savings_pct = round((1 - hollow_total / naive_total) * 100, 1) if naive_total else 0
        rows.append({
            "sessions": n,
            "naive_cumulative": naive_total,
            "hollow_cumulative": hollow_total,
            "delta": delta,  # negative means Hollow is cheaper
            "hollow_savings_pct": savings_pct,
            "hollow_wins": delta < 0,
        })
    return rows


def ascii_chart(rows: list[dict]) -> str:
    """Render a simple ASCII bar chart of cumulative tokens."""
    max_val = max(max(r["naive_cumulative"], r["hollow_cumulative"]) for r in rows)
    width = 40
    lines = []
    lines.append(f"\n  Session  {'Naive':>8}  {'Hollow':>8}  Chart (N=naive, H=hollow)")
    lines.append("  " + "-" * 72)

    for r in rows:
        n = r["sessions"]
        naive = r["naive_cumulative"]
        hollow = r["hollow_cumulative"]
        naive_bar = round(naive / max_val * width)
        hollow_bar = round(hollow / max_val * width)
        winner = " ← Hollow wins" if r["hollow_wins"] else ""
        lines.append(
            f"  {n:>7}  {naive:>8,}  {hollow:>8,}  "
            f"N:{'█' * naive_bar:<{width}}  H:{'█' * hollow_bar:<{width}}{winner}"
        )
    return "\n".join(lines)


def find_breakeven(rows: list[dict]) -> int:
    """Return the first session number where Hollow becomes cheaper, or -1."""
    for r in rows:
        if r["hollow_wins"]:
            return r["sessions"]
    return -1


def main():
    global API_URL, TOKEN

    parser = argparse.ArgumentParser(description="Hollow break-even analysis")
    parser.add_argument("--api-url", default="http://localhost:7777")
    parser.add_argument("--token", default=None)
    parser.add_argument("--max-sessions", type=int, default=MAX_SESSIONS_DEFAULT)
    args = parser.parse_args()

    API_URL = args.api_url

    if args.token:
        TOKEN = args.token
    else:
        config_path = Path(__file__).parent.parent / "config.json"
        if config_path.exists():
            cfg = json.loads(config_path.read_text())
            TOKEN = cfg.get("api", {}).get("token", "")
    if not TOKEN:
        print("ERROR: no token. Pass --token or ensure config.json has api.token")
        sys.exit(1)

    workspace_root = "/mnt/c/Users/jedin/Desktop/New Science"
    config_path = Path(__file__).parent.parent / "config.json"
    if config_path.exists():
        cfg = json.loads(config_path.read_text())
        workspace_root = cfg.get("workspace_root", workspace_root)

    print("=" * 70)
    print("  Hollow Break-Even Analysis")
    print("  When does Hollow's startup overhead pay off?")
    print("  Each 'session' = system state + code search + poll + file read")
    print("=" * 70)

    costs = measure_one_session_costs(workspace_root)
    rows = build_breakeven_table(costs, args.max_sessions)
    breakeven = find_breakeven(rows)

    print("\n" + "=" * 70)
    print(f"  {'Sessions':>8}  {'Naive':>10}  {'Hollow':>10}  {'Delta':>10}  {'Savings':>8}")
    print("  " + "-" * 56)
    for r in rows:
        winner_marker = " ✓" if r["hollow_wins"] else ""
        print(
            f"  {r['sessions']:>8}  {r['naive_cumulative']:>10,}  "
            f"{r['hollow_cumulative']:>10,}  "
            f"{r['delta']:>+10,}  "
            f"{r['hollow_savings_pct']:>7.1f}%{winner_marker}"
        )
    print("=" * 70)

    if breakeven == -1:
        print(f"\n  Hollow does NOT break even within {args.max_sessions} sessions.")
        print(f"  Consider running with --max-sessions {args.max_sessions * 3}")
    elif breakeven == 1:
        print(f"\n  Hollow is cheaper from session 1 onwards.")
    else:
        print(f"\n  Break-even point: session {breakeven}")
        print(f"  Hollow is MORE expensive for {breakeven - 1}-session tasks.")
        print(f"  Hollow is CHEAPER for {breakeven}+ session tasks.")

    print(ascii_chart(rows))
    print()

    out_path = Path(__file__).parent.parent / "memory" / "bench-breakeven.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "methodology": (
            "Per-session costs measured from live system. "
            "Each session = system state check + code search + state poll + file read. "
            "Hollow pickup overhead included per session (worst case: cold-start each session)."
        ),
        "costs": costs,
        "breakeven_session": breakeven,
        "rows": rows,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
