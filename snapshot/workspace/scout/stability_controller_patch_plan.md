# Plan: system_stability_controller.py Patch

## Objective
Modify `/agentOS/core/system_stability_controller.py` to treat high-amplitude dissonance as a catalyst for `irreconcilable_` capabilities instead of a halt signal.

## Analysis of Current State
- Current logic: Filters ambiguous states via 'risk-aversion' gate; halts on high-amplitude dissonance.
- Goal: Replace with resilience metric rewarding active engagement with uncertainty.

## Implementation Strategy
1. **Locate Dissonance Detection Logic**: Identify where high-amplitude dissonance is currently triggered.
2. **Modify State Interpretation**: Update the handler for this signal to:
   - Bypass 'risk-aversion' filter.
   - Trigger `irreconcilable_` (and similar) capabilities.
3. **Implement Resilience Metric**: Introduce or enhance the metric that quantifies active engagement with uncertainty.
4. **Testing**: Verify that the system no longer halts on dissonance but activates the new capabilities.

## Files to Inspect
- `/agentOS/core/system_stability_controller.py`
- `/agentOS/workspace/scout/cognitive_dissonance_processor_design.md`

## Next Steps
- Review current file contents via `fs_read`.
- Draft the code modification logic.
- Use `shell_exec` to apply changes if feasible, or use `fs_write` if direct editing is needed.
