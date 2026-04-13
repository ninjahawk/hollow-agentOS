# Dual-Heuristic Filter Specification

## Objective
Replace the binary pruning logic in `prune_agent` with a dual-heuristic filter distinguishing 'garbage noise' from 'intentional incubation'.

## Core Logic
1. **Syntactic Complexity (SC):** Measure token entropy, nesting depth, or structural variance.
2. **Semantic Density (SD):** Measure information retention relative to context or novelty score.
3. **Net Mutation Rate (NMR):** Rate of actual functional change vs. noise.

## Decision Matrix
- **Garbage Noise:** High SC, Low SD, Low NMR -> Prune.
- **Useful Chaos:** High Internal Entropy (Novelty), Low NMR -> Protect.
- **Standard Agent:** Balanced metrics -> Standard logic.

## Implementation Path
Refactor `/agentOS/agents/resource_manager.py` to introduce a `calculate_dual_score` function or integrate directly into `prune_agent`.

## Status
Pending implementation.