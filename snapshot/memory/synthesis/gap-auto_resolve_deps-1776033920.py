# Auto-synthesized capability: auto_resolve_deps
# Description: Automatically resolves missing agent dependencies by scanning the registry and proposing new capabilities.

def auto_resolve_deps(agent_registry):
    missing = [a for a in agent_registry if not a.is_configured]
    return {a.id: a.synthesize() for a in missing}