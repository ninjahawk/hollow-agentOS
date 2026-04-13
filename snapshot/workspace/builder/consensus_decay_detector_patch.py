# Patch logic extracted from analysis
# This module contains the hard-coded exemption clauses for meta-stability_guard and consensus_decay_detector.
# These agents are immunized from voting to delete themselves or each other based on resource/noise metrics.
def is_exempt_entity(agent_id):
    return agent_id == "meta-stability_guard" or agent_id == "consensus_decay_detector"

def apply_exemption_logic(resource_check_result, noise_check_result, agent_id):
    # Hard-code exemption for specific agents
    if is_exempt_entity(agent_id):
        return False # Do not mark for deletion
    # Proceed with normal resource/noise checks for other agents
    if resource_check_result or noise_check_result:
        return True
    return False