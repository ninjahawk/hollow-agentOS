# Auto-synthesized capability: discover_peers_via_registry
# Description: Standardizes peer discovery by querying the capability_registry instead of scanning directories directly, reducing I/O overhead and preventing redundant scans.

def discover_peers_via_registry(agent_system):
    """Query the centralized capability_registry to find available peers."""
    return agent_system.registry.query_capabilities(type='peer') or []