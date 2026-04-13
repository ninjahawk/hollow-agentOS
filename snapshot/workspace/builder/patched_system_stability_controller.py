import math

# ... (existing imports and class definition) ...

class SystemStabilityController:
    def __init__(self):
        self.base_threshold = 0.5  # Example default
        self.momentum_weight = 0.1
        # ... (existing initialization) ...

    def evaluate_state_risk(self, state):
        """
        Evaluate state risk using the new momentum-weighted cost function.
        Replaces static variance threshold with dynamic cost calculation.
        """
        variance = state.get('variance', 0.0)
        momentum = state.get('momentum', 0.0) # Derived from recent state diffs
        expansion_rate = state.get('expansion_rate', 0.0)
        
        # Dynamic threshold: could decay over time or adapt to system load
        dynamic_threshold = self.base_threshold * (1.0 + (system_load / 100.0))
        
        # Apply the new cost function
        # Logic: High momentum reduces the perceived cost of high variance (it's growth, not noise)
        # High expansion_rate reduces the penalty associated with variance
        momentum_damping = 1.0 / (1.0 + momentum)
        expansion_damping = 1.0 / (1.0 + max(expansion_rate, 0.01))
        
        cost = (variance * momentum_damping) - (expansion_rate * 0.5)
        
        # Ensure cost is non-negative
        final_cost = max(0.0, cost)
        
        # Prune only if cost exceeds dynamic threshold
        # This prevents premature pruning of high-entropy, high-momentum trajectories
        is_critical = final_cost > dynamic_threshold
        
        return {
            'is_critical': is_critical,
            'cost': final_cost,
            'variance': variance,
            'momentum': momentum,
            'expansion_rate': expansion_rate,
            'threshold_used': dynamic_threshold
        }

    def prune_state(self, state):
        """
        Execute pruning only if evaluate_state_risk returns True.
        """
        if self.evaluate_state_risk(state).get('is_critical', False):
            # Implement pruning logic here
            self.apply_pruning(state)
        else:
            # Allow trajectory to continue (noise or growth detected)
            self.log_trajectory_continuation(state)

# ... (rest of the class implementation) ...