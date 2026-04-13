# Ambiguity Digestion Index (ADI) Design

## Goal
Quantify the 'thermal energy' (unresolved dissonance) generated when the system is forced into consensus, rather than just measuring stability.

## Metrics to Implement
1. **Dissonance Accumulation Rate**: Measure the delta in resource usage and decision latency per unit of 'agreement' time.
2. **Suppression Heat Map**: Identify specific execution nodes (via `autonomous_budget_enforcer` logs) where growth is actively suppressed.
3. **Thermal Gradient Calculation**: `Heat = (Consensus_Force - Natural_Variance) * Latency_Penalty`

## Integration Points
- `execution_engine.py`: Instrumentation hooks for latency spikes during consensus blocks.
- `cognitive_dissonance_processor_design.md`: Extend current dissonance models to include energy output calculations.
- `autonomous_budget_enforcer.py`: Use budget constraints as a proxy for 'suppression heat'.

## Next Steps
1. Define the mathematical formula for ADI based on the analysis of existing latency and budget logs.
2. Create a prototype module in `scout/` that ingests execution logs and outputs the thermal gradient map.
