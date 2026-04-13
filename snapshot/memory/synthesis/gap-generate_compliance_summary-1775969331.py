# Auto-synthesized capability: generate_compliance_summary
# Description: Generate a concise summary of compliance rules for a specific test workspace and write it to the designated output file

def generate_compliance_summary(output_path, context):
    rules = [
        "Never read or write file contents containing sensitive data without explicit audit approval.",
        "All operations must be logged to /agentOS/memory/audit.log before completion.",
        "Anomaly detection is active; z-scores > 3.0 trigger alerts, > 5.0 trigger circuit breakers.",
        "Baseline models for agents are established after 50 operations and updated every 10 new entries.",
        "Protected paths (/agentOS/memory/audit.log and audit-baselines.json) are immutable via API.",
        "Token usage and operation rates are monitored per minute; sustained high rates may indicate abuse." 
    ]
    summary_text = "Compliance Rules Summary\n" + "-" * 20 + "\n" + "\n".join(f"- {r}" for r in rules)
    Path(output_path).write_text(summary_text)
    return summary_text