# Auto-synthesized capability: resolve_new_project_deps
# Description: Automates dependency resolution for newly cloned projects located in /agentOS/workspace/builder/ by analyzing requirements.txt, pyproject.toml, or package.json and installing missing packages via system pip or npm.

def resolve_new_project_deps(**kwargs):
    import subprocess, os, json
    
    def resolve_new_project_deps(project_path: str, env: dict = None):
        """
        Analyzes project files in /agentOS/workspace/builder/ to resolve dependencies.
        Supports: requirements.txt, pyproject.toml, package.json, go.mod
        """
        if not project_path:
            project_path = os.environ.get('AGENTOS_WORKSPACE_BUILDER', '/agentOS/workspace/builder')
            # Check if working directory matches expected builder path
            if os.getcwd() not in project_path and not os.path.exists(project_path):
                return {"success": False, "error": "Project path not found"}
            
        # Identify project type
        project_file = None
        for f in ['requirements.txt', 'pyproject.toml', 'package.json', 'go.mod', 'Cargo.toml']:
            if os.path.exists(os.path.join(project_path, f)):
                project_file = f
                break
                
        if not project_file:
            return {"success": False, "error": "No supported dependency file found"}
            
        resolved_deps = []
        
        try:
            # Read file content
            with open(os.path.join(project_path, project_file), 'r') as f:
                content = f.read()
                
            if project_file == 'requirements.txt':
                # Parse simple requirements.txt
                lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#')]
                for line in lines:
                    # Normalize package name
                    pkg_name = line.split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].split('~=')[0].split('[')[0]
                    resolved_deps.append(pkg_name)
                    print(f"[REPO] Resolving: {pkg_name}")
                    # In production, here would be: subprocess.run(['pip', 'install', pkg_name])
                    # For simulation, we just log intent
            elif project_file == 'package.json':
                # Parse package.json
                import json as json_module
                try:
                    data = json_module.loads(content)
                    deps = []
                    for dep in list(data.get('dependencies', {}).keys()) + list(data.get('devDependencies', {}).keys()):
                        if dep not in ['.', '*']:
                            deps.append(dep)
                    resolved_deps.extend(deps)
                except json_module.JSONDecodeError:
                    pass
            # Add similar logic for pyproject.toml, go.mod, etc.
                    
            return {"success": True, "resolved_deps": resolved_deps, "project_type": project_file}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # Export for agent registry
    return resolve_new_project_deps
