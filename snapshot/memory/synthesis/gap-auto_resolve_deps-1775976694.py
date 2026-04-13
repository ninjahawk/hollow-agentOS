# Auto-synthesized capability: auto_resolve_deps
# Description: Automates dependency resolution for newly cloned projects in /agentOS/workspace/builder/

def auto_resolve_deps(project_path):
    import subprocess
    from pathlib import Path
    
    project_path = Path(project_path)
    if not project_path.exists():
        raise FileNotFoundError(f"Project directory not found: {project_path}")
    
    # Detect package manager based on project structure
    if project_path.exists("requirements.txt"):
        # pip
        subprocess.run(["pip", "install", "-r", str(project_path / "requirements.txt")], check=False)
    elif project_path.exists("package.json"):
        # npm
        subprocess.run(["npm", "install"], check=False, cwd=str(project_path))
    elif project_path.exists("pom.xml"):
        # maven
        subprocess.run(["mvn", "dependency:resolve"], check=False, cwd=str(project_path))
    elif project_path.exists("Cargo.toml"):
        # cargo
        subprocess.run(["cargo", "fetch"], check=False, cwd=str(project_path))
    else:
        # Default: try installing any pip requirements if found
        req_file = project_path / "requirements.txt"
        if req_file.exists():
            subprocess.run(["pip", "install", "-r", str(req_file)], check=False)
    
    return {"status": "resolved", "project": str(project_path)}