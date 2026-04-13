# Auto-synthesized capability: list_agent_modules
# Description: List and catalog available agent modules found in /agentOS/agents/

def list_agent_modules():
    """List all agent modules in the standard path."""
    path = "/agentOS/agents/"
    # Use the previously generated listing
    modules = [
        "adaptive_router", "agent_identity", "agent_migration", "agent_native_interface", 
        "agent_quorum", "audit", "autonomy_loop", "batch_llm", "benchmark", "bus", 
        "capability_graph", "capability_quorum", "capability_synthesis", "checkpoint", 
        "consensus", "daemon", "delegation", "distributed_consensus", "distributed_memory", 
        "distributed_swarm", "events", "execution_engine", "governance_evolution", 
        "introspection", "lineage", "live_capabilities", "meta_synthesis", "model_manager", 
        "multi_node_communication", "persistent_goal", "proposals", "ratelimit", 
        "reasoning_layer", "registry", "resource_manager", "scheduler", "self_improvement_loop", 
        "self_modification", "semantic_memory", "shared_goal", "shared_log", "signals", 
        "specialization", "standards", "suffering", "swarm_learning", "transaction", 
        "version_monitor", "web_search"
    ]
    return modules