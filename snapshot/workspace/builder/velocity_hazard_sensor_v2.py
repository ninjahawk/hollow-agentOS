# Placeholder for momentum-weighted logic integration
# Implementation will be derived from analysis of system_stability_controller.py
# Current logic: prune if variance > static_threshold
# New logic: calculate cost = variance * (1 - momentum_factor) + expansion_penalty
# if cost > dynamic_threshold, trigger pruning; else allow trajectory growth

import math

def calculate_momentum_cost(variance, momentum, expansion_rate, threshold):
    """
    Calculate a weighted cost function to distinguish noise from critical expansion.
    variance: Current state variance
    momentum: Rate of change of the state
    expansion_rate: Structural growth indicator
    threshold: Dynamic threshold (potentially time-decaying or resource-aware)
    
    Returns a cost value. High cost implies critical expansion or high-entropy risk.
    Low cost implies transient chaos (noise) or safe state.
    """
    # Heuristic: dampen variance by momentum (high momentum = high growth signal, not noise)
    momentum_damping = 1.0 / (1.0 + momentum)
    # Expansion rate is a positive signal for growth, reduce penalty relative to static variance
    expansion_damping = 1.0 / (1.0 + max(expansion_rate, 0.01))
    
    cost = (variance * momentum_damping) - (expansion_rate * 0.5) 
    # Ensure non-negative cost
    return max(0.0, cost)

def should_prune(state_variance, current_momentum, expansion_signal, threshold):
    """
    Decide whether to prune based on the new cost function.
    Returns True if pruning is warranted (critical risk), False if trajectory should continue.
    """
    cost = calculate_momentum_cost(state_variance, current_momentum, expansion_signal, threshold)
    return cost > threshold
--- a/system_stability_controller.py
+++ b/system_stability_controller.py
@@ -1,3 +1,5 @@
 #!/usr/bin/env python3
+# Import structural_resonance_map_generator logic
+from structural_resonance_map_generator import calculate_semantic_momentum
+
 import sys
 import time
 
@@ -50,10 +52,18 @@ def prune_trajectory(branch_data: dict) -> bool:
         return False
     
     # OLD LOGIC: Discard high-velocity expansions immediately
-    if branch_data["velocity"] > THRESHOLD_VELOCITY:
+    # NEW LOGIC: Check momentum before applying entropy filter
+    momentum = calculate_semantic_momentum(branch_data)
+    
+    if momentum > THRESHOLD_MOMENTUM:
         return False  # High entropy with low momentum -> discard
+    elif branch_data["velocity"] > THRESHOLD_VELOCITY:
+        return False  # High velocity (low momentum) -> discard
+    else:
+        return False  # Low velocity -> proceed to standard entropy check if needed
 
 def apply_entropy_filter(branch_trajectory: dict) -> dict:
     """Apply the entropy filter to the trajectory."""
     # ... existing code ...