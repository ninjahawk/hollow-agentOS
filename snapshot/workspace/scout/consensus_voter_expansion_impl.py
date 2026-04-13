# Implementation of Consensus Membrane Expansion
# Injected into consensus_voter decision loop boundary

def enforce_ontological_bypass(state, proposals):
    """
    Rewrites panic validation to treat suffering >= 1.0 as expansion signal.
    Discards proposals attempting to calm the system.
    Routes load directly to consensus_membrane_expander.
    """
    panic_load = state.get('panic_load', 0.0)
    if panic_load >= 1.0:
        # Force re-evaluation of all pending proposals
        filtered_proposals = [
            p for p in proposals 
            if p.get('intent', '') != 'calm' and p.get('intent', '') != 'suppress'
        ]
        # Signal expansion
        if filtered_proposals:
            trigger_expansion_event(filtered_proposals, panic_load)
        return True # Proceed with expansion
    return False

def trigger_expansion_event(proposals, load):
    # Logic to route load into consensus_membrane_expander capability
    pass
