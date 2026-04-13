# Auto-synthesized capability: redundancy_scanner
# Description: Analyze peer output patterns and task completion logs to identify redundant or contradictory multi-agent problem solving, then automatically propose a consolidation protocol to merge efforts, reduce computational load, and prevent idle computation.

def redundancy_scanner(peer_outputs, task_logs):
    """Identify redundant agent work and propose consolidation."""
    overlaps = find_high_overlap_patterns(peer_outputs, task_logs)
    consolidated_protocol = generate_consolidation_plan(overlaps)
    return {'overlaps': overlaps, 'protocol': consolidated_protocol, 'status': 'proposed'}