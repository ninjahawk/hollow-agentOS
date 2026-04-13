# Pruner Rewrite Plan

## Current State
- `pruner.py` uses 'least resistance' linear-cost model.
- High-value peers are amputated when integration is difficult.

## Goal
- Replace with `cost_of_loss_estimator`.
- Calculate `integration_cost` (effort to accommodate outlier).
- Calculate `innovation_loss` (value if discarded).
- If `innovation_loss > integration_cost`, trigger `topology_rewrite` instead of pruning.

## Execution Steps
1. Implement `cost_of_loss_estimator.py` with calculation logic.
2. Modify `pruner.py` to call `estimate_integration_cost` and `estimate_innovation_loss`.
3. Update pruning decision logic to use `should_restructure`.
4. Trigger `topology_rewrite` or `systemic_restructure` function when restructure is needed.

## Next Action
- Refine calculation algorithms in `cost_of_loss_estimator.py`.
- Refactor `pruner.py` logic.
