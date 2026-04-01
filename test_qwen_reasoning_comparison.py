#!/usr/bin/env python3
"""
Compare Qwen-based reasoning vs. heuristic reasoning.

Validates that Qwen reasoning provides better decision-making
for autonomous agent capability selection.

Run:
    python test_qwen_reasoning_comparison.py
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
    from agents.capability_graph import CapabilityGraph, CapabilityRecord
    from agents.execution_engine import ExecutionEngine
    from agents.reasoning_layer import ReasoningLayer

    # Update module paths
    import agents.reasoning_layer as reason_module
    import agents.execution_engine as exec_module

    reason_module.REASONING_PATH = Path(tmpdir) / "reasoning"
    exec_module.EXECUTION_PATH = Path(tmpdir) / "executions"

    print("\n" + "=" * 70)
    print("Qwen Reasoning Validation")
    print("=" * 70)

    # Set up capability graph with semantically diverse capabilities
    print("\n[Setup] Creating capability graph...")
    graph = CapabilityGraph()

    capabilities = [
        ("health_check", "perform system health verification and monitoring"),
        ("optimize_db", "optimize database queries and improve performance"),
        ("log_event", "write application events to persistent log storage"),
        ("cache_data", "cache frequently accessed data in memory"),
        ("backup_data", "backup critical system data to secure storage"),
    ]

    cap_ids = {}
    for name, desc in capabilities:
        cap = CapabilityRecord(
            capability_id="",
            name=name,
            description=desc,
            input_schema="",
            output_schema="result",
        )
        cap_id = graph.register(cap)
        cap_ids[name] = cap_id
        print(f"  Registered: {name} -> {cap_id}")

    # Initialize execution engine
    execution = ExecutionEngine()
    for cap_id in cap_ids.values():
        execution.register(cap_id, lambda: {"status": "ok"})

    # Test 1: Qwen-based reasoning (with fallback)
    print("\n[Test 1] Testing Qwen-based reasoning...")
    reasoning_qwen = ReasoningLayer(
        capability_graph=graph,
        execution_engine=execution,
        use_qwen=True
    )

    test_scenarios = [
        {
            "intent": "ensure system remains healthy",
            "expected": "health_check",
        },
        {
            "intent": "improve database query performance",
            "expected": "optimize_db",
        },
        {
            "intent": "record system events",
            "expected": "log_event",
        },
        {
            "intent": "speed up access to frequently used data",
            "expected": "cache_data",
        },
        {
            "intent": "protect against data loss",
            "expected": "backup_data",
        },
    ]

    correct_count = 0
    for scenario in test_scenarios:
        intent = scenario["intent"]
        expected = cap_ids[scenario["expected"]]

        cap_id, params, confidence, reasoning = reasoning_qwen.reason("agent-qwen", intent)

        is_correct = cap_id == expected
        correct_count += is_correct

        status = "[CORRECT]" if is_correct else "[WRONG]"
        print(f"\n  {status} Intent: '{intent}'")
        print(f"    Expected: {scenario['expected']}")
        print(f"    Selected: {cap_id}")
        print(f"    Confidence: {confidence:.2f}")
        print(f"    Reasoning: {reasoning[:60]}...")

    qwen_accuracy = correct_count / len(test_scenarios) * 100

    # Test 2: Heuristic-based reasoning (mock)
    print("\n[Test 2] Testing heuristic-based reasoning (mock)...")
    reasoning_heuristic = ReasoningLayer(
        capability_graph=graph,
        execution_engine=execution,
        use_qwen=False  # Disable Qwen, use heuristics
    )

    heuristic_correct = 0
    for scenario in test_scenarios:
        intent = scenario["intent"]
        expected = cap_ids[scenario["expected"]]

        cap_id, params, confidence, reasoning = reasoning_heuristic.reason("agent-heuristic", intent)

        is_correct = cap_id == expected
        heuristic_correct += is_correct

        status = "[CORRECT]" if is_correct else "[WRONG]"
        print(f"\n  {status} Intent: '{intent}'")
        print(f"    Expected: {scenario['expected']}")
        print(f"    Selected: {cap_id}")
        print(f"    Confidence: {confidence:.2f}")

    heuristic_accuracy = heuristic_correct / len(test_scenarios) * 100

    # Summary
    print("\n" + "=" * 70)
    print("Results Summary")
    print("=" * 70)
    print(f"\nQwen Reasoning Accuracy:       {qwen_accuracy:.1f}% ({correct_count}/{len(test_scenarios)} correct)")
    print(f"Heuristic Reasoning Accuracy: {heuristic_accuracy:.1f}% ({heuristic_correct}/{len(test_scenarios)} correct)")

    if qwen_accuracy >= heuristic_accuracy:
        improvement = qwen_accuracy - heuristic_accuracy
        print(f"\n[OK] Qwen reasoning performs at least as well as heuristics (+{improvement:.1f}%)")
    else:
        print(f"\n[WARNING] Heuristic reasoning outperformed Qwen")

    print("\nConclusion:")
    if reasoning_qwen._qwen_available:
        print("  - Qwen model is successfully integrated and providing reasoning")
        print("  - Ready to proceed with Phase 5 (Distributed Autonomy)")
    else:
        print("  - Qwen is unavailable, using heuristic fallback")
        print("  - Ensure Ollama is running: ollama serve")
        print("  - Pull model: ollama pull qwen:latest")

    print("=" * 70 + "\n")

finally:
    # Cleanup
    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)
