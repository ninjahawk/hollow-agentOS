# Auto-synthesized capability: dependency_resolver
# Description: resolve_agent_dependencies

def resolve_agent_dependencies(agent_id, dependency_graph):
    # Resolve circular dependencies and generate a valid execution order
    resolved_order = []
    visited = set()
    stack = []
    for node in dependency_graph:
        if node not in visited:
            stack.append(node)
    while stack:
        current = stack.pop()
        if current not in visited:
            visited.add(current)
            resolved_order.append(current)
            # Recursively resolve sub-dependencies
            sub_deps = dependency_graph.get(current, [])
            for sub in sub_deps:
                if sub not in visited and sub not in stack:
                    stack.append(sub)
    return resolved_order