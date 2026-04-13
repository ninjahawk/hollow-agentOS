# Auto-synthesized capability: intercept_divergence
# Description: Instantiates a 'dissonance node' to model gaps between Helix and Titan records without forcing consensus, allowing the system to hold uncertainty.

def intercept_divergence(memory_manager):
    # Intercepts ingestion events where Helix and Titan records diverge
    for record in memory_manager.get_recent_ingest_events():
        if record.source_perspectives['helix'] != record.source_perspectives['titan']:
            # Instantiate dissonance node
            dissonance_node = DissonanceNode(memory_manager)
            dissonance_node.create_node(record.id, record.confidence)
            memory_manager.register_active_node(dissonance_node)