# Auto-synthesized capability: scan_agent_registry
# Description: Scans /agentOS/agents/ directory structure to catalog existing agents, their capabilities, and system architecture for fresh start exploration.

def scan_agent_registry(path='/agentOS/agents/'):
    import os
    agents = {}
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        if os.path.isdir(item_path):
            agents[item] = {'type': 'folder', 'children': []}
        elif item.endswith('.py') or item.endswith('.js'):
            agents[item] = {'type': 'module', 'status': 'active'}
    return {'architecture': 'module_based', 'agents': agents}