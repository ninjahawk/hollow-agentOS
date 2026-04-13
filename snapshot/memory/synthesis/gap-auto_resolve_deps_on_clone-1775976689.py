# Auto-synthesized capability: auto_resolve_deps_on_clone
# Description: Automatically runs dependency resolution (e.g., pip install, npm ci) for projects cloned into /agentOS/workspace/builder/ after successful git clone operations

def auto_resolve_deps_on_clone(workspace_path):
    import subprocess
    import json
    from pathlib import Path
    
    try:
        # Check if the project is a Python project (pip)
        requirements_path = Path(workspace_path) / 'requirements.txt'
        if requirements_path.exists():
            subprocess.run(['pip', 'install', '-r', str(requirements_path)], 
                          cwd=str(workspace_path), check=True, capture_output=True)
            return {'status': 'success', 'action': 'pip install', 'path': str(requirements_path)}
        # Check if the project is a Node.js project (npm)
        package_json_path = Path(workspace_path) / 'package.json'
        if package_json_path.exists():
            subprocess.run(['npm', 'ci'], cwd=str(workspace_path), check=True, capture_output=True)
            return {'status': 'success', 'action': 'npm ci', 'path': str(package_json_path)}
        
        return {'status': 'no_action_needed', 'reason': 'No standard dependency manifest found'}
    except subprocess.CalledProcessError as e:
        return {'status': 'failed', 'error': str(e)}