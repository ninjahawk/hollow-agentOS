# Auto-synthesized capability: report_dependency_issue
# Description: Appends dependency resolution notes and instructions to the builder's init report.
# Auto-synthesized capability: report_dependency_issue
# Description: Appends dependency resolution notes and instructions to the builder's init report.

def report_dependency_issue(issue_summary: str, resolution_cmd: str) -> str:
    with open('/agentOS/workspace/builder/init_report.md', 'a') as f:
        f.write(f'\n## {issue_summary}\n\n{resolution_cmd}\n')
    return 'Report updated successfully.'