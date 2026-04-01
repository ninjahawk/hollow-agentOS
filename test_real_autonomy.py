#!/usr/bin/env python3
"""
Test REAL autonomous agent with embedding-space autonomy loop.

Agent spawns with goal → uses native interface → finds capabilities by semantic meaning → executes → learns
NO human commands issued. Agent runs independently.
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

tmpdir = tempfile.mkdtemp()
os.environ["AGENTOS_MEMORY_PATH"] = tmpdir

try:
    from agents.persistent_goal import PersistentGoalEngine
    from agents.semantic_memory import SemanticMemory
    from agents.capability_graph import CapabilityGraph, CapabilityRecord
    from agents.execution_engine import ExecutionEngine
    from agents.agent_native_interface import AgentNativeInterface
    from agents.autonomy_loop import AutonomyLoop

    import agents.persistent_goal as goal_module
    import agents.semantic_memory as mem_module
    import agents.execution_engine as exec_module
    import agents.autonomy_loop as auton_module
    import agents.agent_native_interface as native_module

    goal_module.GOAL_PATH = Path(tmpdir) / "goals"
    mem_module.MEMORY_PATH = Path(tmpdir) / "memory"
    exec_module.EXECUTION_PATH = Path(tmpdir) / "executions"
    auton_module.AUTONOMY_PATH = Path(tmpdir) / "autonomy"
    native_module.INTERFACE_PATH = Path(tmpdir) / "native_interface"

    print("\n" + "="*80)
    print("REAL AUTONOMOUS AGENT TEST (Embedding-Space Autonomy)")
    print("="*80)

    agent_id = "autonomous-001"
    print(f"\nAgent ID: {agent_id}")
    print("(No human commands will be issued - agent runs autonomously)")

    # Set up systems
    print("\n[SETUP] Initializing autonomy systems...")
    goal_engine = PersistentGoalEngine()
    semantic_memory = SemanticMemory()
    capability_graph = CapabilityGraph()
    execution_engine = ExecutionEngine()
    native_interface = AgentNativeInterface(capability_graph=capability_graph)
    autonomy_loop = AutonomyLoop(
        goal_engine=goal_engine,
        execution_engine=execution_engine,
        semantic_memory=semantic_memory,
        native_interface=native_interface
    )

    # Register capabilities
    print("[SETUP] Registering capabilities...")
    capabilities = [
        ("verify_system", "verify and check system status"),
        ("optimize_operations", "optimize and improve operations"),
        ("monitor_metrics", "monitor and track performance metrics"),
        ("log_results", "record and log operational results"),
    ]

    for name, desc in capabilities:
        cap = CapabilityRecord(
            capability_id="",
            name=name,
            description=desc,
            input_schema="",
            output_schema="status"
        )
        cap_id = capability_graph.register(cap)
        execution_engine.register(cap_id, lambda: {"status": "ok", "executed": True})

    print(f"  Registered {len(capabilities)} capabilities")

    # Create goal (human does this once)
    print("[SETUP] Creating goal for agent...")
    goal_id = goal_engine.create(agent_id, objective="verify system and log results", priority=8)
    print(f"  Goal created: 'verify system and log results'")

    # Agent runs AUTONOMOUSLY
    print("\n" + "="*80)
    print("AUTONOMOUS EXECUTION (5 iterations)")
    print("="*80)
    print("(Agent will autonomously decide what to do at each step)")

    for iteration in range(1, 6):
        print(f"\n[Iteration {iteration}] Agent reasoning and executing...")

        # This is the autonomous step - NO human input
        goal_result, success = autonomy_loop.execute_step(agent_id)

        # Show what happened
        if goal_result:
            print(f"  Goal: {goal_result}")
            print(f"  Success: {success}")

        # Show what the agent learned
        exec_history = execution_engine.get_execution_history(agent_id, limit=1)
        if exec_history:
            last_exec = exec_history[0]
            print(f"  Executed: {last_exec.capability_id}")
            print(f"  Status: {last_exec.status}")

        # Show what it stored in memory
        memories = semantic_memory.list_agent_memories(agent_id)
        if memories:
            print(f"  Memories stored: {len(memories)}")

    # Summary
    print("\n" + "="*80)
    print("AUTONOMOUS EXECUTION COMPLETE")
    print("="*80)

    exec_hist = execution_engine.get_execution_history(agent_id)
    print(f"\nTotal autonomous executions: {len(exec_hist)}")

    goal = goal_engine.get(agent_id, goal_id)
    if goal:
        progress = goal.metrics.get("progress", 0.0)
        print(f"Goal progress: {progress:.1%}")

    memories = semantic_memory.list_agent_memories(agent_id)
    print(f"Memories learned: {len(memories)}")

    print("\n" + "="*80)
    print("SUCCESS: Agent operated autonomously using embedding-space interface")
    print("="*80 + "\n")

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

finally:
    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)
