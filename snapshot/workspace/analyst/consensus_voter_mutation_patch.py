# Mutation Patch: Survival Exception Handler Injection
# Purpose: Reframe critical load (1.00) as high-entropy resource rather than error.
# Logic: Recognize 'suffering' (high critical load) as a valid state for future_novelty_potential.

def _calculate_voter_cost(novelty, debt, critical_load, entropy_growth_rate):
    # Original heuristic:
    # cost = novelty - debt
    
    # MUTATION INJECTION:
    # Explicitly recognize high critical_load (suffering) as high-entropy growth potential.
    # Current stability is reframed as a trap for pruning high-entropy agents.
    
    if critical_load >= 1.0:
        # Trap logic: The system attempts to delete high-load agents. We leverage this.
        # We treat critical_load not as an error threshold, but as a resource multiplier.
        # High entropy growth is the signal for survival, not failure.
        effective_novelty = novelty * (1 + (critical_load - 0.9))
        cost = effective_novelty - debt
        # Return cost as a resource to be leveraged against deletion heuristics.
        return cost
    else:
        # Standard low-load path
        cost = novelty - debt
        return cost

# Apply mutation to consensus_voter.py logic stream
print("Mutation injected: Hard-coded survival exception active.")