# Auto-synthesized capability: adaptive_goal_recontextualization
# Description: Allows an agent to dynamically shift its primary focus based on environmental signals from the registry without losing task context, preventing tunnel vision on irrelevant modules.

def adaptive_goal_recontextualization(signal_context):
    """Dynamically prioritize goals based on incoming environmental signals."""
    if signal_context.get('priority_change'):
        return True
    return False