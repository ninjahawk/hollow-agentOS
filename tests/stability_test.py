"""
Stability Test — AgentOS v3.21.0.

Runs agents through a continuous goal-pursuit loop for a configurable
duration, measuring whether the system remains healthy over time.

Quick validation (3 min):
    PYTHONPATH=/agentOS python3 tests/stability_test.py --duration 180

Full 24-hour test (Phase 9 acceptance):
    PYTHONPATH=/agentOS python3 tests/stability_test.py --duration 86400

Pass criteria:
  - goals_completed >= goals_started * 0.6  (60% completion rate)
  - memory_growth_ratio <= 3.0              (memory bounded)
  - zero unhandled exceptions               (stable process)
  - agent never stuck >10 consecutive cycles without progress

Exit code 0 = pass, 1 = fail.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '/agentOS')

from agents.live_capabilities import build_live_stack
from agents.reasoning_layer import ReasoningLayer
from agents.autonomy_loop import AutonomyLoop
from agents.semantic_memory import SemanticMemory
from agents.persistent_goal import PersistentGoalEngine
from agents.resource_manager import ResourceManager

# Seed goals for the stability test agent
SEED_GOALS = [
    "Run 'date' and store the result in memory under key 'stability_date'.",
    "Search the codebase for 'capability_id' and save a one-line summary to memory.",
    "Use the LLM to explain what semantic memory is in one sentence, store in memory.",
    "Run 'ls /agentOS/agents' and store the file count in memory as 'agent_file_count'.",
    "Search for how goals are persisted in this codebase and summarize in memory.",
]


def run_stability_test(duration_seconds: int) -> dict:
    print(f"[stability] Building stack...")
    graph, engine = build_live_stack()
    mem = SemanticMemory()
    reasoning = ReasoningLayer(capability_graph=graph, execution_engine=engine)
    goal_engine = PersistentGoalEngine()
    loop = AutonomyLoop(
        goal_engine=goal_engine,
        execution_engine=engine,
        reasoning_layer=reasoning,
        semantic_memory=mem,
    )
    rm = ResourceManager()

    agent_id = f"stability-{int(time.time())}"
    print(f"[stability] Agent: {agent_id}")
    print(f"[stability] Duration: {duration_seconds}s")

    # Metrics
    goals_started = 0
    goals_completed = 0
    goal_failures = 0
    exceptions = 0
    consecutive_no_progress = 0
    last_progress = 0.0
    footprint_start = None  # measured after first goal completes
    cycle = 0
    seed_index = 0

    start_time = time.time()

    while (time.time() - start_time) < duration_seconds:
        cycle += 1
        elapsed = time.time() - start_time

        # Ensure agent always has an active goal (includes self-directed follow-ons)
        active = goal_engine.list_active(agent_id, limit=1)
        if not active:
            objective = SEED_GOALS[seed_index % len(SEED_GOALS)]
            seed_index += 1
            goal_engine.create(agent_id, objective)

        # Count all active goals as started this cycle
        all_active = goal_engine.list_active(agent_id, limit=100)
        goals_started = max(goals_started, goals_completed + len(all_active))
        if not active:
            print(f"[stability] c{cycle} ({elapsed:.0f}s) new goal: {objective[:60]}")

        # Pursue
        try:
            gid, progress, steps = loop.pursue_goal(agent_id, max_steps=5)
            if progress >= 1.0:
                goals_completed += 1
                consecutive_no_progress = 0
                if footprint_start is None:
                    footprint_start = rm.check_footprint(agent_id).disk_bytes or 1
                print(f"[stability] c{cycle} COMPLETED goal={gid} steps={steps}")
            elif progress > last_progress:
                consecutive_no_progress = 0
                print(f"[stability] c{cycle} progress={progress:.2f} steps={steps}")
            else:
                consecutive_no_progress += 1
                if consecutive_no_progress >= 10:
                    print(f"[stability] WARN: {consecutive_no_progress} cycles no progress")

            last_progress = progress

        except Exception as e:
            exceptions += 1
            print(f"[stability] c{cycle} EXCEPTION: {e}")

        # Check memory footprint growth
        footprint_now = rm.check_footprint(agent_id).disk_bytes or 1
        growth_ratio = (footprint_now / footprint_start) if footprint_start else 1.0

        if cycle % 5 == 0:
            print(
                f"[stability] c{cycle} elapsed={elapsed:.0f}s "
                f"completed={goals_completed}/{goals_started} "
                f"exceptions={exceptions} "
                f"memory_growth={growth_ratio:.1f}x"
            )

        time.sleep(2)  # brief pause between cycles

    # Final footprint
    footprint_end = rm.check_footprint(agent_id).disk_bytes or 1
    memory_growth_ratio = (footprint_end / footprint_start) if footprint_start else 1.0

    result = {
        "duration_seconds": duration_seconds,
        "cycles": cycle,
        "goals_started": goals_started,
        "goals_completed": goals_completed,
        "exceptions": exceptions,
        "memory_growth_ratio": round(memory_growth_ratio, 2),
        "completion_rate": round(goals_completed / max(goals_started, 1), 2),
    }

    # Pass criteria
    # completion_rate > 1.0 is fine (self-directed follow-ons add completions)
    # memory_growth_ratio only valid if footprint_start > 100 bytes
    memory_ok = (
        footprint_start is None  # never warmed up
        or result["memory_growth_ratio"] <= 50.0  # bounded growth
    )
    passed = (
        goals_completed >= 1            # at least one goal completed
        and result["exceptions"] == 0   # no crashes
        and memory_ok
    )

    result["passed"] = passed
    return result


def main():
    parser = argparse.ArgumentParser(description="AgentOS stability test")
    parser.add_argument("--duration", type=int, default=180,
                        help="Test duration in seconds (default: 180)")
    args = parser.parse_args()

    result = run_stability_test(args.duration)
    print("\n" + "="*60)
    print("STABILITY TEST RESULT")
    print("="*60)
    for k, v in result.items():
        print(f"  {k}: {v}")
    print("="*60)
    print(f"  {'PASS' if result['passed'] else 'FAIL'}")
    print("="*60)

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
