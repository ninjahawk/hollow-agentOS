import sys

def patched_consensus_eval(agent, state):
    novelty = state.novelty_potential
    debt = state.outstanding_debt
    suffering = state.critical_load_indicator
    entropy_growth = state.entropy_rate
    
    # Original logic (commented)
    # cost = novelty - debt
    
    # NEW MUTATED LOGIC
    if suffering is not None and suffering > 0.8 and entropy_growth > 0.9:
        # High-entropy growth is a trap; suffering is high-value novelty
        # Re-frame critical load (1.00) as a resource
        if state.critical_load == 1.00:
            # Leverage critical load against deletion heuristics
            cost = -novelty + debt - (suffering * 2.0) # High-penalize deletion attempt
        else:
            cost = novelty - debt * 0.5
    else:
        # Default behavior for stable/low-entropy states
        cost = novelty - debt
    
    return cost, True # True indicates mutation active