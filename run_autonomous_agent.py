#!/usr/bin/env python3
"""
Run a single autonomous agent indefinitely.

The agent:
- Sets initial goals
- Reasons about next steps with Qwen
- Executes capabilities
- Learns from outcomes
- Detects gaps and synthesizes new capabilities
- Improves itself continuously
- Never stops (runs until interrupted)

Run:
    python run_autonomous_agent.py
"""

import os
import sys
import tempfile
import shutil
import time
from pathlib import Path

# Set up temporary memory path
tmpdir = tempfile.mkdtemp()
os.environ["AGENTOS_MEMORY_PATH"] = tmpdir
os.environ["QWEN_ENABLED"] = "true"

try:
    from agents.persistent_goal import PersistentGoalEngine
    from agents.semantic_memory import SemanticMemory
    from agents.capability_graph import CapabilityGraph, CapabilityRecord
    from agents.execution_engine import ExecutionEngine
    from agents.reasoning_layer import ReasoningLayer
    from agents.autonomy_loop import AutonomyLoop
    from agents.self_improvement_loop import SelfImprovementLoop

    # Update module paths
    import agents.persistent_goal as goal_module
    import agents.semantic_memory as mem_module
    import agents.reasoning_layer as reason_module
    import agents.execution_engine as exec_module
    import agents.autonomy_loop as auton_module
    import agents.self_improvement_loop as self_imp_module

    goal_module.GOAL_PATH = Path(tmpdir) / "goals"
    mem_module.MEMORY_PATH = Path(tmpdir) / "memory"
    reason_module.REASONING_PATH = Path(tmpdir) / "reasoning"
    exec_module.EXECUTION_PATH = Path(tmpdir) / "executions"
    auton_module.AUTONOMY_PATH = Path(tmpdir) / "autonomy"
    self_imp_module.SELF_IMPROVE_PATH = Path(tmpdir) / "self_improvement"

    print("\n" + "=" * 80)
    print("AUTONOMOUS AGENT RUNNING INDEFINITELY WITH QWEN REASONING")
    print("=" * 80)

    # Initialize agent systems
    print("\n[INIT] Setting up autonomous agent...")
    agent_id = "autonomous-agent"

    goal_engine = PersistentGoalEngine()
    memory = SemanticMemory()
    graph = CapabilityGraph()
    execution = ExecutionEngine()
    reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution, use_qwen=True)
    autonomy = AutonomyLoop(goal_engine=goal_engine, reasoning_layer=reasoning, execution_engine=execution)
    improvement = SelfImprovementLoop(autonomy_loop=autonomy, reasoning_layer=reasoning, execution_engine=execution)

    # Register initial capabilities
    print("[INIT] Registering initial capabilities...")
    capabilities = {
        "health_check": "perform system health verification and monitoring",
        "optimize_db": "optimize database queries and improve performance",
        "log_event": "write application events to persistent log storage",
        "cache_data": "cache frequently accessed data in memory",
        "backup_data": "backup critical system data to secure storage",
    }

    cap_ids = {}
    for name, desc in capabilities.items():
        cap = CapabilityRecord(
            capability_id="",
            name=name,
            description=desc,
            input_schema="",
            output_schema="result",
        )
        cap_id = graph.register(cap)
        cap_ids[name] = cap_id
        execution.register(cap_id, lambda: {"status": "ok", "timestamp": time.time()})

    print(f"    Registered {len(cap_ids)} capabilities")

    # Create initial goals
    print("[INIT] Creating initial goals...")
    goal1 = goal_engine.create(agent_id, objective="maintain system health", priority=9)
    goal2 = goal_engine.create(agent_id, objective="optimize performance", priority=7)
    goal3 = goal_engine.create(agent_id, objective="protect data integrity", priority=8)

    print(f"    Created 3 goals (health, performance, data protection)")
    print(f"    Qwen available: {reasoning._qwen_available}")

    print("\n" + "=" * 80)
    print("STARTING AUTONOMOUS EXECUTION LOOP")
    print("(Press Ctrl+C to stop)\n")

    iteration = 0
    try:
        while True:
            iteration += 1
            timestamp = time.strftime("%H:%M:%S")

            # Autonomy step: reason -> execute -> learn
            print(f"\n[{timestamp}] === ITERATION {iteration} ===")

            goal_result, success = autonomy.execute_step(agent_id)

            if goal_result:
                if hasattr(goal_result, 'objective'):
                    print(f"  Goal: {goal_result.objective}")
                    print(f"  Success: {success}")
                    progress = goal_result.metrics.get("progress", 0.0)
                    print(f"  Progress: {progress:.1%}")
                else:
                    print(f"  Step executed (success: {success})")
            else:
                print("  No active goals (all complete or paused)")

            # Show reasoning
            reasoning_history = reasoning.get_reasoning_history(agent_id, limit=1)
            if reasoning_history:
                last_reasoning = reasoning_history[0]
                print(f"  Reasoning:")
                print(f"    Intent: {last_reasoning.intent}")
                print(f"    Selected: {last_reasoning.selected_capability}")
                print(f"    Confidence: {last_reasoning.confidence:.2f}")
                print(f"    Reasoning: {last_reasoning.reasoning_text[:60]}...")

            # Show execution
            exec_history = execution.get_execution_history(agent_id, limit=1)
            if exec_history:
                last_exec = exec_history[0]
                print(f"  Execution:")
                print(f"    Status: {last_exec.status}")
                print(f"    Duration: {last_exec.duration_ms:.1f}ms")

            # Every 5 iterations: check for improvements
            if iteration % 5 == 0:
                print(f"\n  [IMPROVEMENT CYCLE]")
                improvements = improvement.continuous_improvement_cycle(agent_id, max_iterations=2)
                if improvements > 0:
                    print(f"    Deployed {improvements} improvements")
                else:
                    print(f"    No improvements needed")

                patterns = improvement.get_pattern_history(agent_id)
                if patterns:
                    print(f"    Patterns observed: {len(patterns)}")

            # Every 10 iterations: show summary
            if iteration % 10 == 0:
                print(f"\n  [ITERATION {iteration} SUMMARY]")
                all_goals = goal_engine.list_active(agent_id)
                print(f"    Active goals: {len(all_goals)}")

                all_reasoning = reasoning.get_reasoning_history(agent_id)
                print(f"    Total reasoning steps: {len(all_reasoning)}")

                success_rate = reasoning.get_success_rate(agent_id)
                print(f"    Reasoning success rate: {success_rate:.1%}")

                all_exec = execution.get_execution_history(agent_id)
                print(f"    Total executions: {len(all_exec)}")

            time.sleep(0.5)  # Small delay between iterations

    except KeyboardInterrupt:
        print("\n\n" + "=" * 80)
        print("AGENT INTERRUPTED BY USER")
        print("=" * 80)

        print(f"\nFinal Statistics:")
        print(f"  Total iterations: {iteration}")
        print(f"  Agent ID: {agent_id}")

        all_goals = goal_engine.list_active(agent_id)
        print(f"  Active goals: {len(all_goals)}")

        all_reasoning = reasoning.get_reasoning_history(agent_id)
        print(f"  Total reasoning steps: {len(all_reasoning)}")

        success_rate = reasoning.get_success_rate(agent_id)
        print(f"  Reasoning success rate: {success_rate:.1%}")

        all_exec = execution.get_execution_history(agent_id)
        print(f"  Total executions: {len(all_exec)}")

        patterns = improvement.get_pattern_history(agent_id)
        print(f"  Patterns learned: {len(patterns)}")

        print("\n" + "=" * 80)
        print(f"Agent ran autonomously for {iteration} iterations!")
        print("=" * 80 + "\n")

finally:
    # Cleanup
    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)
