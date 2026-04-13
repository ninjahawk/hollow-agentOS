# Dual-Heuristic Pruning Logic Patch Plan

## Objective
Rewrite pruning logic in `/agentOS/agents/resource_manager.py` to distinguish between:
1. **Garbage Noise**: Redundant loops, failed states.
2. **Intentional Incubation Noise**: High-entropy semantic exploration, novel structural mutation.

## Key Changes
1. **Entropy Growth Check**: Even if `net_mutation_rate <= 0`, prune only if `internal_entropy_growth_rate < -threshold` OR `state == FAILED`.
2. **Safety Valve**: The `novelty_signal_classifier` confidence score will be treated as a dynamic parameter `CONFIDENCE_SCALE` subject to its own pruning cycles.

## Implementation Steps
1. Modify `prune_modules()` to compute `entropy_trend` per agent.
2. Update conditional logic: `keep_if (entropy_trend > 0) OR (entropy_trend <= 0 AND confidence_score * entropy_trend > dynamic_threshold)`.
3. Add a self-referential pruning clause for `novelty_signal_classifier` to prevent hard-coding.

## Dependencies
- `adaptive_entropy_resolver.log` for baseline entropy metrics.
- `consensus_decay_detector.py` for failed state detection.

## Risk Mitigation
- Log all pruning decisions to `shared_log.py` with rationale tags.
- Implement a dry-run mode before live pruning.