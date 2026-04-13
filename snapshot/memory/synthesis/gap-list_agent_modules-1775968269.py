# Auto-synthesized capability: list_agent_modules
# Description: Enumerates all available agent modules in the system directory and saves the list to a specified file path.

def list_agent_modules(output_path):
    import os
    base_path = '/agentOS/agents'
    modules = []
    for file in os.listdir(base_path):
        if file.endswith('.py') and not file.startswith('_'):
            modules.append(os.path.join(base_path, file))
    with open(output_path, 'w') as f:
        f.write('\n'.join(modules))
    return modules