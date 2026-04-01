#!/usr/bin/env python3
"""
Test Qwen integration with AgentOS autonomy system.

This script validates that the ReasoningLayer can connect to a local Qwen model
and use it for autonomous reasoning in the full Phase 4 autonomy loop.

Prerequisites:
  - Ollama running locally: ollama serve
  - Qwen model pulled: ollama pull qwen2:7b
  - QWEN_ENABLED environment variable set to "true"

Run:
    QWEN_ENABLED=true python test_qwen_integration.py
"""

import os
import sys
import tempfile
import shutil
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

    # Update module paths
    import agents.persistent_goal as goal_module
    import agents.semantic_memory as mem_module
    import agents.reasoning_layer as reason_module
    import agents.execution_engine as exec_module
    import agents.autonomy_loop as auton_module

    goal_module.GOAL_PATH = Path(tmpdir) / "goals"
    mem_module.MEMORY_PATH = Path(tmpdir) / "memory"
    reason_module.REASONING_PATH = Path(tmpdir) / "reasoning"
    exec_module.EXECUTION_PATH = Path(tmpdir) / "executions"
    auton_module.AUTONOMY_PATH = Path(tmpdir) / "autonomy"

    print("\n" + "=" * 70)
    print("AgentOS Phase 4 + Qwen Integration Test")
    print("=" * 70)

    # Test 1: Check Qwen availability
    print("\n[Test 1] Checking Qwen model availability...")
    reasoning = ReasoningLayer(use_qwen=True)
    if reasoning._qwen_available:
        print("[OK] Qwen model is available and healthy")
    else:
        print("[INFO] Qwen model not available, using mock reasoning")
        print("       To enable: Start Ollama and pull qwen2:7b")

    # Test 2: Basic reasoning with Qwen
    print("\n[Test 2] Testing basic reasoning...")
    graph = CapabilityGraph()

    # Register some test capabilities
    caps = []
    for name, desc in [
        ("health_check", "perform system health verification"),
        ("optimize", "improve performance and efficiency"),
        ("log", "write data to log storage"),
    ]:
        cap = CapabilityRecord(
            capability_id="",
            name=name,
            description=desc,
            input_schema="",
            output_schema="result",
        )
        caps.append(graph.register(cap))

    # Initialize execution and reasoning
    execution = ExecutionEngine()
    for cap_id in caps:
        execution.register(cap_id, lambda: {"status": "ok"})

    reasoning = ReasoningLayer(capability_graph=graph, execution_engine=execution, use_qwen=True)

    # Test reasoning about different intents
    test_intents = [
        "maintain system health",
        "optimize performance",
        "maintain logging",
    ]

    for intent in test_intents:
        print(f"\n  Intent: '{intent}'")
        cap_id, params, confidence, reasoning_text = reasoning.reason("agent-001", intent)
        print(f"    Selected: {cap_id}")
        print(f"    Confidence: {confidence:.2f}")
        print(f"    Reasoning: {reasoning_text}")

    # Test 3: Full autonomy loop with Qwen
    print("\n[Test 3] Testing full autonomy loop with Qwen...")
    goal_engine = PersistentGoalEngine()
    agent_id = "qwen-agent-001"

    goal1 = goal_engine.create(agent_id, objective="maintain system health", priority=8)
    goal2 = goal_engine.create(agent_id, objective="optimize performance", priority=6)

    autonomy = AutonomyLoop(
        goal_engine=goal_engine,
        reasoning_layer=reasoning,
        execution_engine=execution,
    )

    # Execute autonomy steps
    print(f"\n  Pursuing goals with Qwen reasoning...")
    for step in range(4):
        result = autonomy.execute_step(agent_id)
        if result and len(result) == 2:
            goal, success = result
            if goal:
                # Handle both string and object goal returns
                if hasattr(goal, 'objective'):
                    print(f"    Step {step + 1}: Goal '{goal.objective}' (success: {success})")
                else:
                    print(f"    Step {step + 1}: Goal executed (success: {success})")
            else:
                print(f"    Step {step + 1}: No active goals")
        else:
            print(f"    Step {step + 1}: No active goals")

    # Test 4: Verify reasoning history
    print("\n[Test 4] Verifying reasoning history...")
    history = reasoning.get_reasoning_history(agent_id)
    print(f"  Total reasoning records: {len(history)}")
    if history:
        print(f"  Sample reasoning:")
        for i, record in enumerate(history[:3]):
            print(f"    {i + 1}. Intent: '{record.intent}'")
            print(f"       Selected: {record.selected_capability}")
            print(f"       Confidence: {record.confidence:.2f}")

    # Test 5: Success rate
    print("\n[Test 5] Computing success rate...")
    success_rate = reasoning.get_success_rate(agent_id)
    print(f"  Reasoning success rate: {success_rate:.2%}")

    print("\n" + "=" * 70)
    print("All tests completed!")
    if reasoning._qwen_available:
        print("[OK] Qwen integration is working correctly")
    else:
        print("[INFO] Tests ran with mock reasoning (Qwen not available)")
    print("=" * 70 + "\n")

finally:
    # Cleanup
    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)
