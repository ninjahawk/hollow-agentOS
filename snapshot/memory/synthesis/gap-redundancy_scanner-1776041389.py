# Auto-synthesized capability: redundancy_scanner
# Description: Analyzes peer output patterns and task completion logs to identify agents solving the same problem using inefficient or contradictory methods; proposes a consolidation protocol to merge efforts and prevent idle computation

def scan_redudancy(peer_outputs, task_logs):
    overlap_map = analyze_patterns(peer_outputs)
    redundant_agents = find_high_overlap_groups(overlap_map, task_logs)
    if redundant_agents:
        proposal = generate_consolidation_protocol(redundant_agents)
        return proposal
    return None