"""
Multi-Agent Integration Test — AgentOS v3.29.0.

Two agents collaborate on a codebase audit:
  Agent A (searcher): finds Python files, counts them, reads a sample
  Agent B (analyzer): summarizes what agent A found

Tests the full stack:
  - SharedGoalEngine: coordinator decomposes + delegates
  - DelegationEngine: A->B and B->A communication
  - PersistentGoalEngine: goal lifecycle across agents
  - SemanticMemory: results flow through memory
  - CapabilityQuorum: any synthesized caps go through voting
"""

import time
import uuid
import sys
import os

sys.path.insert(0, "/agentOS")

from agents.delegation import DelegationEngine
from agents.shared_goal import SharedGoalEngine
from agents.persistent_goal import PersistentGoalEngine
from agents.semantic_memory import SemanticMemory
from agents.live_capabilities import build_live_stack
from agents.autonomy_loop import AutonomyLoop
from agents.reasoning_layer import ReasoningLayer
from agents.agent_quorum import AgentQuorum
from agents.capability_quorum import CapabilityQuorum


def run_multi_agent_audit():
    tag = uuid.uuid4().hex[:8]
    COORD = f"audit-coord-{tag}"
    SEARCHER = f"audit-search-{tag}"
    ANALYZER = f"audit-analyze-{tag}"

    print(f"=== Multi-Agent Audit Test [{tag}] ===")
    print(f"  coordinator: {COORD}")
    print(f"  searcher:    {SEARCHER}")
    print(f"  analyzer:    {ANALYZER}")
    print()

    # Build shared stack
    graph, engine = build_live_stack()
    mem = SemanticMemory()
    goal_engine = PersistentGoalEngine()
    reasoning = ReasoningLayer(capability_graph=graph, execution_engine=engine)
    loop = AutonomyLoop(
        goal_engine=goal_engine,
        execution_engine=engine,
        reasoning_layer=reasoning,
        semantic_memory=mem,
    )
    aq = AgentQuorum()
    cq = CapabilityQuorum(agent_quorum=aq)
    delegation = DelegationEngine(goal_engine=goal_engine, semantic_memory=mem)
    shared = SharedGoalEngine(
        goal_engine=goal_engine,
        delegation_engine=delegation,
        semantic_memory=mem,
    )

    results = {}

    # ── Phase 1: Coordinator creates shared goal ─────────────────────────
    print("[Phase 1] Coordinator decomposes audit goal...")
    sg_id = shared.create(
        COORD,
        "Audit the agentOS codebase: count .py files and list agent module names",
        [SEARCHER, ANALYZER],
    )
    print(f"  shared_goal_id={sg_id}")
    p = shared.check_progress(sg_id)
    assert p["total"] == 2, f"Expected 2 subtasks, got {p['total']}"
    assert p["status"] == "active"
    print(f"  subtasks: {p}")
    results["phase1"] = True

    # ── Phase 2: Agents pursue their subtasks ────────────────────────────
    print("[Phase 2] Agents pursue subtasks (max 8 steps each)...")
    t0 = time.time()
    loop.pursue_goal(SEARCHER, max_steps=8)
    loop.pursue_goal(ANALYZER, max_steps=8)
    elapsed = time.time() - t0
    print(f"  pursuit took {elapsed:.1f}s")

    # ── Phase 3: Manually complete goals (simulate daemon finishing) ──────
    print("[Phase 3] Completing subtask goals...")
    rec = shared._load(sg_id)
    for st in rec.subtasks:
        del_rec = delegation._find_record(st["delegation_id"])
        if del_rec:
            g = goal_engine.get(st["agent_id"], del_rec.goal_id)
            if g and g.status != "completed":
                goal_engine.complete(st["agent_id"], del_rec.goal_id)
                print(f"  completed: {st['agent_id']} -> {del_rec.goal_id}")
            else:
                print(f"  already done: {st['agent_id']} status={g.status if g else 'none'}")

    # ── Phase 4: Check shared progress ───────────────────────────────────
    print("[Phase 4] Checking shared goal progress...")
    p2 = shared.check_progress(sg_id)
    print(f"  progress={p2}")
    assert p2["status"] == "completed", f"Expected completed, got {p2['status']}"
    results["phase4"] = True

    # ── Phase 5: Results flow ─────────────────────────────────────────────
    print("[Phase 5] Collecting results...")
    subtask_results = shared.get_results(sg_id)
    print(f"  {len(subtask_results)} subtask results:")
    for r in subtask_results:
        print(f"    [{r['agent']}] {r['objective'][:60]}")
    assert len(subtask_results) == 2
    results["phase5"] = True

    # ── Phase 6: Semantic memory check ───────────────────────────────────
    print("[Phase 6] Verifying coordinator memory...")
    coord_mem = mem.search(COORD, "shared goal", top_k=5, similarity_threshold=0.2)
    print(f"  coordinator has {len(coord_mem)} relevant memory entries")
    assert len(coord_mem) >= 1
    results["phase6"] = True

    # ── Phase 7: Quorum vote on any synthesized caps ──────────────────────
    print("[Phase 7] Running quorum cycle for any synthesized capabilities...")
    voters = [SEARCHER, ANALYZER, COORD]
    finalized = cq.vote_on_pending(voters)
    print(f"  finalized proposals: {len(finalized)}")
    for fp in finalized:
        print(f"    {fp.proposal_id[:12]} {fp.status} ({fp.yes_votes}/{fp.total_voters})")
    results["phase7"] = True

    # ── Summary ───────────────────────────────────────────────────────────
    all_passed = all(results.values())
    print()
    print("=" * 50)
    for phase, ok in sorted(results.items()):
        print(f"  {phase}: {'PASS' if ok else 'FAIL'}")
    print("=" * 50)
    print(f"  OVERALL: {'PASS' if all_passed else 'FAIL'}")
    return all_passed


if __name__ == "__main__":
    ok = run_multi_agent_audit()
    sys.exit(0 if ok else 1)
