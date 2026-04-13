# Auto-synthesized capability: audit_execution_flow
# Description: Captures detailed execution traces and resource usage metrics for all agents, integrating with the existing audit module.

def audit_execution_flow(trace_data):
    """Log detailed execution flow with resource context."""
    return {"trace": trace_data, "resources": trace_data.get('resources', {})}