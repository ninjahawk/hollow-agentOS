# Auto-synthesized capability: resolve_dependency_hierarchically
# Description: A capability that checks the dynamic registry for user-defined plugins and falls back to core execution engines based on role-scoped permissions

def resolve_dependency_hierarchically(caps_registry: dict, role: str, cap_name: str) -> dict:
    # 1. Registry Lookup
    if cap_name in caps_registry.get('active_plugins', {}):
        return {"status": "found", "source": "registry", "handler": caps_registry['active_plugins'][cap_name]}

    # 2. Check Role Scope
    allowed_caps = ROLE_DEFAULTS.get(role, set())
    if cap_name not in allowed_caps:
        return {"status": "denied", "source": "policy", "reason": f"{cap_name} not in {role} scope"}

    # 3. Fallback to Core Engine
    return {"status": "found", "source": "core_engine", "handler": f"execution_engine::{cap_name}"}