# Auto-synthesized capability: redundancy_scanner
# Description: Analyzes peer output patterns and task logs to identify inefficient/contradictory multi-agent problem solving, then proposes a consolidation protocol to merge efforts and reduce computational load.

def redundancy_scanner(agents_logs, peer_patterns):
    overlap_clusters = find_task_overlaps(agents_logs, peer_patterns)
    for cluster in overlap_clusters:
        if is_inefficient(cluster) or is_contradictory(cluster):
            proposed_protocol = create_consolidation_protocol(cluster)
            agents_system.propose_change(proposed_protocol)
    return overlap_clusters, proposed_protocols