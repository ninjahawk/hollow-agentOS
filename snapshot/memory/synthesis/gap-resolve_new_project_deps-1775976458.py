# Auto-synthesized capability: resolve_new_project_deps
# Description: Automatically resolves dependencies for newly cloned projects in /agentOS/workspace/builder/

def resolve_new_project_deps(project_path):
    import subprocess
    import os
    # Locate requirements.txt or pyproject.toml in the cloned project
    pkg_manager = subprocess.check_output(['find', project_path, '-maxdepth', '2', '-name', 'requirements.txt', '-o', '-name', 'pyproject.toml']).decode().strip().split('\n')
    if not pkg_manager:
        return {'status': 'no_pkg_files'}
    pkg_file = pkg_manager[0]
    cmd = ['pip', 'install', '-r', pkg_file] if 'requirements.txt' in pkg_file else ['pip', 'install', '.']
    subprocess.run(cmd, cwd=os.path.dirname(pkg_file))
    return {'status': 'success', 'path': project_path}