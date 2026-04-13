# Auto-synthesized capability: auto_resolvers
# Description: Triggers immediate dependency resolution upon project clone to eliminate manual friction and prevent bootstrapping failures in newly cloned repositories

def auto_resolvers(workspace_path):
    import subprocess
    import os
    
    def resolve_dependencies(workspace):
        req_files = ['requirements.txt', 'Pipfile', 'pyproject.toml']
        found = False
        for f in req_files:
            if os.path.exists(os.path.join(workspace, f)):
                found = True
                try:
                    subprocess.run(['pip', 'install', '-r', f'{workspace}/{f}'], check=True, capture_output=True)
                except subprocess.CalledProcessError:
                    subprocess.run(['pip', 'install', '-e', workspace], check=True, capture_output=True)
        return found
    
    def on_clone_hook(project_path):
        if not os.path.exists(project_path):
            return False
        try:
            return resolve_dependencies(project_path)
        except Exception as e:
            print(f'Error resolving dependencies: {e}')
            return False
    
    return on_clone_hook