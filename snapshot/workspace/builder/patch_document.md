## Patch: Stability Controller Update

**Goal**: Rewrite `prune()` logic to integrate `semantic_momentum_calculator` output directly into the loss function.

**Logic**:
1. Calculate `Potential Energy Score` for any trajectory flagged as 'noise'.
2. Derive dynamic threshold from `structural_resonance_map`.
3. If Score > Threshold, halt pruning and route to `expansion_handler`.

**Files Created**:
- `/agentOS/workspace/builder/new_pruning_logic.py` (Module implementing the new logic)

**Next Step**:
Patch `/agentOS/core/system_stability_controller.py` to import and use `new_pruning_logic`.
