# Auto-synthesized capability: auto_resolve_deps
# Description: Automatically identifies and installs missing dependencies for newly cloned projects in the builder workspace.

def auto_resolve_deps(project_path):
    import subprocess
    import sys
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'], cwd=project_path, check=True)
    return True