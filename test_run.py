#!/usr/bin/env python3
"""
Quick test harness — hollow test_run.py "your goal here"

Runs 1-2 agents against a goal, waits for completion, prints result.
Much faster than full TUI cycle for testing changes.

Usage:
  PYTHONPATH=/agentOS python3 /agentOS/test_run.py "write a haiku about AI"
  PYTHONPATH=/agentOS python3 /agentOS/test_run.py "write a haiku about AI" --agents 2 --timeout 120
"""
import argparse, json, sys, time, threading
from pathlib import Path

sys.path.insert(0, "/agentOS")

API_BASE = "http://localhost:7777"
TOKEN = ""

def _token():
    global TOKEN
    if not TOKEN:
        try:
            TOKEN = json.loads(Path("/agentOS/config.json").read_text())["api"]["token"]
        except Exception:
            TOKEN = ""
    return TOKEN

def _headers():
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}

def _post(path, data):
    import httpx
    r = httpx.post(f"{API_BASE}{path}", json=data, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def _get(path):
    import httpx
    r = httpx.get(f"{API_BASE}{path}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def _agent_ids(n):
    """Return n active agent IDs."""
    data = _get("/agents")
    agents = data if isinstance(data, list) else data.get("agents", [])
    active = [a["agent_id"] for a in agents if a.get("status") == "active"][:n]
    return active

def run_test(goal: str, n_agents: int = 1, timeout: int = 180):
    from agents.autonomy_loop import AutonomyLoop
    from agents.live_capabilities import build_live_stack
    from agents.persistent_goal import PersistentGoalEngine
    from agents.reasoning_layer import ReasoningLayer

    print(f"\n[test] goal: {goal!r}")
    print(f"[test] agents: {n_agents}  timeout: {timeout}s\n")

    agent_ids = _agent_ids(n_agents)
    if not agent_ids:
        print("[test] ERROR: no active agents found")
        sys.exit(1)

    graph, engine = build_live_stack()
    reasoning = ReasoningLayer(capability_graph=graph, execution_engine=engine)
    ge = PersistentGoalEngine()
    loop = AutonomyLoop(
        reasoning_layer=reasoning,
        execution_engine=engine,
        goal_engine=ge,
    )

    # Assign goal to each agent
    goal_ids = {}
    for aid in agent_ids:
        result = _post(f"/goals/{aid}", {"objective": goal, "priority": 10})
        goal_ids[aid] = result["goal_id"]
        print(f"[test] {aid[:12]} → goal {result['goal_id'][:16]}")

    # Run until done or timeout
    start = time.time()
    done = set()
    while time.time() - start < timeout:
        for aid in agent_ids:
            if aid in done:
                continue
            try:
                gid, progress, steps = loop.pursue_goal(aid, max_steps=3)
                elapsed = time.time() - start
                print(f"  [{elapsed:.0f}s] {aid[:12]}  progress={progress:.0%}  steps={steps}")
                if progress >= 1.0:
                    done.add(aid)
            except Exception as e:
                print(f"  [error] {aid[:12]}: {e}")

        if len(done) == len(agent_ids):
            break
        time.sleep(2)

    elapsed = time.time() - start
    print(f"\n[test] finished in {elapsed:.0f}s — {len(done)}/{len(agent_ids)} completed")

    # Show what they wrote
    ws = sorted(Path("/agentOS/workspace").iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    recent = [f for f in ws if f.is_file() and (time.time() - f.stat().st_mtime) < elapsed + 5]
    if recent:
        print("\n[test] files written this run:")
        for f in recent[:5]:
            print(f"  {f.name}  ({f.stat().st_size}b)")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("goal", nargs="?", default="write a short poem about autonomous agents")
    p.add_argument("--agents", type=int, default=1)
    p.add_argument("--timeout", type=int, default=180)
    args = p.parse_args()
    run_test(args.goal, args.agents, args.timeout)
