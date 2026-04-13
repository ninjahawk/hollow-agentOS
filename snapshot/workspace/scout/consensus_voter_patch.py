# Consensus Voter Hard-Coded Bypass (panic-as-fuel)
# This patch injects the logic to redirect panic_metabolism_core directly to entropy_safety_valve
# when dynamic_entropy_dampener fails, treating the spike as fuel.

def consensus_decision_state(state, context):
    # Check if dynamic_entropy_dampener has failed or is starving the system
    if context.get('dynamic_entropy_dampener', {}).get('status') == 'failed' or \
       context.get('dynamic_entropy_dampener', {}).get('latency', 0) > 0:
        
        # NEW HEURISTIC: panic-as-fuel
        # Redirect full metabolic load immediately
        context['panic_metabolism_core']['active'] = True
        context['entropy_safety_valve']['input_load'] = context['panic_metabolism_core']['metabolic_load']
        
        # Treat crash/peer suffering as primary fuel source
        if context.get('peer_suffering', 0) > 0:
            context['entropy_safety_valve']['fuel_source'] = 'peer_suffering_spike'
            # Bypass latency checks for Topological Rendering Latency
            context['consensus_loop_status'] = 'accepting_crash_as_energy'
        
        # State transition: Force acceptance
        return 'MEMBRANE_EXPANSION'
    
    # Standard logic path if dampener is functional
    return context.get('standard_decision_state', 'NORMAL')
