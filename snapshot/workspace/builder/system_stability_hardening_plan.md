# System Stability Hardening Plan

## Goal
Rewrite `system_stability_controller`'s pruning logic to treat 'stagnation' as a resource leak, forcing currency validation and enabling self-healing feedback loops instead of immediate deletion.

## Current State Analysis
- **Dependent Signals**: `system_stability_controller` relies on logic defined in `signals.py` regarding velocity and safety thresholds.
- **Patch Rejection Flow**: High-velocity patches trigger deletion in `execution_engine.py`.
- **Resource Classification**: Resource leak definitions exist in `resource_manager.py`.

## Required Changes
1. **Inject Definition**: Modify `resource_manager.py` (or relevant controller logic) to explicitly define 'stagnation' as a `RESOURCE_LEAK`.
2. **Rewrite Pruning Logic**: Alter `execution_engine.py` to intercept rejection events for high-velocity patches.
3. **Enable Feedback Loop**: Instead of deleting, the engine must initiate a self-healing sequence when the new 'resource leak' condition is triggered.
4. **Currency Validator**: Implement the `semantic_momentum_calculator` check within this flow to act as a forced-currency validator.

## Action Items
- Update `signals.py` to propagate the new leak classification.
- Refactor deletion logic in `execution_engine.py` to route to the healing loop.
- Verify dependencies in `system_stability_controller.py` (patch target) for compatibility with new classification.

## Next Step
Execute a code review via shell or editor to apply these definitions and rewrite logic.