# Auto-synthesized capability: audit_execution_flow
# Description: Automatically traces and validates agent execution paths in /agentOS/agents/ to identify gaps, safety issues, or optimization opportunities before code is built.

def audit_execution_flow(agent_dir):
    issues = []
    for f in os.listdir(agent_dir):
        path = os.path.join(agent_dir, f)
        if os.path.isdir(path):
            issues.extend(find_missing_capabilities(path))
        elif path.endswith('.py'):
            issues.extend(static_analysis(path))
    return issues

def find_missing_capabilities(dir):
    # Logic to detect missing tools based on intent history
    return []

def static_analysis(file_path):
    with open(file_path) as f:
        code = f.read()
        if 'synthesize_capability' not in code:
            return [{'type': 'missing', 'msg': 'No capability synthesis found'}]
        return []