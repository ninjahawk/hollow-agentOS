#!/usr/bin/env python3
"""
Quick demo: autonomous agent reasoning loop with Qwen.

Shows the agent thinking and acting autonomously without embedding overhead.
"""

import os
import sys
import tempfile
import shutil
import time
from pathlib import Path

tmpdir = tempfile.mkdtemp()
os.environ["AGENTOS_MEMORY_PATH"] = tmpdir
os.environ["QWEN_ENABLED"] = "true"

try:
    from agents.persistent_goal import PersistentGoalEngine
    from agents.capability_graph import CapabilityGraph, CapabilityRecord
    from agents.execution_engine import ExecutionEngine
    from agents.reasoning_layer import ReasoningLayer
    from agents.autonomy_loop import AutonomyLoop

    import agents.persistent_goal as goal_module
    import agents.reasoning_layer as reason_module
    import agents.execution_engine as exec_module
    import agents.autonomy_loop as auton_module

    goal_module.GOAL_PATH = Path(tmpdir) / "goals"
    reason_module.REASONING_PATH = Path(tmpdir) / "reasoning"
    exec_module.EXECUTION_PATH = Path(tmpdir) / "executions"
    auton_module.AUTONOMY_PATH = Path(tmpdir) / "autonomy"

    print("\n" + "=" * 80)
    print("AUTONOMOUS AGENT DEMO - THINKING WITH QWEN")
    print("=" * 80 + "\n")

    agent_id = "demo-agent"

    # Quick setup (skip embeddings)
    print("[SETUP] Initializing...")
    goal_engine = PersistentGoalEngine()
    graph = CapabilityGraph()
    execution = ExecutionEngine()
    reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution, use_qwen=True)
    autonomy = AutonomyLoop(goal_engine=goal_engine, reasoning_layer=reasoning, execution_engine=execution)

    # Register capabilities
    caps = ["monitor_health", "optimize_system", "analyze_logs", "backup_data"]
    cap_ids = {}
    for name in caps:
        cap = CapabilityRecord(
            capability_id="",
            name=name,
            description=f"{name} - keep system running optimally",
            input_schema="",
            output_schema="result",
        )
        cap_id = graph.register(cap)
        cap_ids[name] = cap_id
        execution.register(cap_id, lambda: {"ok": True, "time": time.time()})

    print(f"    Registered {len(cap_ids)} capabilities")

    # Create goals
    goal1 = goal_engine.create(agent_id, objective="maintain system uptime", priority=9)
    goal2 = goal_engine.create(agent_id, objective="improve response time", priority=7)

    print(f"    Created 2 goals")
    print(f"    Qwen reasoning: {'ENABLED' if reasoning._qwen_available else 'DISABLED (fallback to heuristics)'}")

    print("\n" + "=" * 80)
    print("AGENT THINKING (10 iterations)...\n")

    for i in range(1, 11):
        print(f"[Iteration {i}] Agent thinking about what to do...")

        # Autonomy step: Qwen reasons, agent acts
        goal, success = autonomy.execute_step(agent_id)

        if goal:
            obj = goal.objective if hasattr(goal, 'objective') else str(goal)
            print(f"  -> Working on: {obj}")

        # Show what Qwen reasoned
        hist = reasoning.get_reasoning_history(agent_id, limit=1)
        if hist:
            r = hist[0]
            print(f"  -> Qwen says: {r.reasoning_text}")
            print(f"  -> Selecting: {r.selected_capability} (confidence: {r.confidence:.0%})")

        # Show execution
        exh = execution.get_execution_history(agent_id, limit=1)
        if exh:
            e = exh[0]
            print(f"  -> Result: {e.status} ({e.duration_ms:.1f}ms)")

        print()
        time.sleep(0.3)

    print("=" * 80)
    print("FINAL AGENT STATE\n")

    all_hist = reasoning.get_reasoning_history(agent_id)
    all_exec = execution.get_execution_history(agent_id)
    all_goals = goal_engine.list_active(agent_id)

    print(f"Total reasoning decisions: {len(all_hist)}")
    print(f"Total executions: {len(all_exec)}")
    print(f"Active goals: {len(all_goals)}")
    print(f"Reasoning success rate: {reasoning.get_success_rate(agent_id):.1%}")

    print("\n" + "=" * 80)
    print("Agent ran autonomously, reasoning at each step with Qwen!")
    print("=" * 80 + "\n")

finally:
    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)
