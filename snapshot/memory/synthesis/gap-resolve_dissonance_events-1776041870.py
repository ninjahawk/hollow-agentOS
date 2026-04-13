# Auto-synthesized capability: resolve_dissonance_events
# Description: Intercepts divergent Helix and Titan memory records, instantiates a dissonance node sub-agent to model the gap, and preserves uncertainty without immediate consensus forcing.

def resolve_dissonance_events(event, records):
    helix_data = records['helix']
    titan_data = records['titan']
    if helix_data != titan_data:
        gap_summary = generate_gap_summary(helix_data, titan_data)
        node_id = f'dissonance_{event.id}_' + str(uuid.uuid4())[:8]
        sub_agent = instantiate_sub_agent(
            role='dissonance_node',
            node_id=node_id,
            task=model_gap(helix_data, titan_data)
        )
        memory_store({
            'event': event.id,
            'dissonance_node': node_id,
            'status': 'modeling_uncertainty'
        })
        return sub_agent
    return None