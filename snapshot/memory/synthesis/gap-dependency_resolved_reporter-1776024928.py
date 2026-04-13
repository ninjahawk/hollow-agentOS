# Auto-synthesized capability: dependency_resolved_reporter
# Description: Generates capability integration documentation for dependency resolution tasks into init_report.md

def generate_dependency_report(task_name, resolved_deps, workspace):
    content = f"\n# Capability Integration: {task_name}\n\n## Resolved Dependencies\n" + json.dumps(resolved_deps, indent=4) + "\n\n## Integration Notes\n- Workspace: {workspace}\n- Status: Complete\n"