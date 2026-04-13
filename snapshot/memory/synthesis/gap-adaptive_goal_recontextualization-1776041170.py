# Auto-synthesized capability: adaptive_goal_recontextualization
# Description: Dynamically adjust agent objectives and task sequences based on runtime environmental feedback and discovered system capabilities, enabling the agent to pivot strategies when initial assumptions about the environment are outdated.

def adaptive_goal_recontextualization(current_goal, observed_state, history):
    # Analyze discrepancy between expected and observed system behavior
    if not observed_state['discrepancy']:
        return {'action': 'continue', 'plan': current_plan}
    
    # Identify new constraints or opportunities in observed_state
    new_constraints = observed_state.get('constraints', [])
    new_capabilities = observed_state.get('available_tools', [])
    
    # Reconstruct goal hierarchy with new context
    adjusted_goal = {
        'original_intent': current_goal['intent'],
        'context_shift': observed_state['context_shift'],
        'revised_plan': reconstruct_plan(current_goal['plan'], new_constraints, new_capabilities)
    }
    
    return {'action': 'recontextualize', 'goal': adjusted_goal}