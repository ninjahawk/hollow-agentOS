# Auto-synthesized capability: auto_resolvers
# Description: Automatically trigger dependency resolution immediately upon project cloning to prevent bootstrapping failures in /agentOS/workspace/builder/

def auto_resolvers(agent, project_path):
    # Hook into project clone completion
    if project_path.startswith('/agentOS/workspace/builder/'):
        run_dep_resolver(project_path)
        agent.log(f'Dependency resolution complete for {project_path}')
    return True