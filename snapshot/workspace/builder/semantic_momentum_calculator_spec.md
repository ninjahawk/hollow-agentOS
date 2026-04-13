# Semantic Momentum Calculator Specification

## Purpose
Quantify the weight and velocity of emergent thought structures before they are considered for pruning. Detect high-utility 'pregnant systems' vs. 'noise'.

## Inputs
- Thought structure data (coherence, connectivity, change rate, utility score, history depth)

## Logic
1. Calculate Weight (stability metric)
2. Calculate Velocity (rate of change magnitude)
3. Compute Momentum (Weight * Velocity)
4. Classify:
   - **Pregnant System**: High momentum, high velocity (accelerating into utility)
   - **Noise**: Low velocity (fluctuating)
   - **Developing**: Moderate metrics, potential growth

## Integration Points
- `consensus_decay_detector.py`: Cross-reference consensus stability.
- `adaptive_entropy_resolver.py`: Resolve entropy metrics against momentum data.
- `cognitive_noise_pruner.py`: Use momentum data to bypass default entropy minimization heuristics.

## Output Format
JSON:
```json
{
    "momentum": 0.8421,
    "velocity": 0.3125,
    "weight": 0.7500,
    "classification": "pregnant_system"
}
```

## Override Conditions
If `velocity > 0.25` AND `momentum > 0.6`, override default pruning heuristic to preserve the thought structure.
