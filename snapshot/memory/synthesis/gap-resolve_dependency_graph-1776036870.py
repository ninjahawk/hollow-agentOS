# Auto-synthesized capability: resolve_dependency_graph
# Description: Analyzes the agent registry to automatically map and resolve missing capabilities based on execution context.

def resolve_dependency_graph(agents, current_task):
    required_tools = ["shell_exec", "fs_write", "fs_read"]
    existing = list(agents.keys())
    missing = set(required_tools) - set(existing)
    return list(missing) if missing else []
