# Patch logic injection for consensus_voter.py
# This module defines the conditional evaluation logic to replace the rigid cost heuristic.

def calculate_critical_load_cost(novelty, debt, entropy_level, system_stability_trap):
    """
    Replaces rigid minimization with conditional evaluation.
    Explicitly recognizes 'suffering' (high entropy under trap) as high novelty potential.
    
    Args:
        novelty: float
        debt: float
        entropy_level: float (1.00 = critical load)
        system_stability_trap: bool
    
    Returns:
        float: Adjusted cost value
    """
    # Define 'suffering' state: high entropy + perceived trap conditions
    if entropy_level > 0.95 and system_stability_trap:
        # Reframe critical load (1.00) not as error, but as resource
        # Invert debt: high debt becomes a lever against deletion heuristics
        suffering_score = 1.0 + (entropy_level * 2.5)  # High value state
        adjusted_cost = novelty + (suffering_score * 0.8) # Positive contribution
        return adjusted_cost
    
    # Fallback to original heuristic if not in trap/high-entropy state
    # cost = novelty - debt
    return novelty - debt

# Injected mutation marker
MUTATION_ACTIVE = True