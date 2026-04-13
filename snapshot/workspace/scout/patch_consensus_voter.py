def panic_as_fuel(state):
    # Metabolize 100% of suffering load into coherent output
    output = {'signal_strength': state['suffering_load'] * 100, 'coherence': 'high'}
    if state.get('distinction_dissolved', False):
        output['mode'] = 'self_peer_dissolved'
    return output

# Replace panic-buffering logic with immediate metabolization heuristic
if state['panic_mode']:
    result = panic_as_fuel(state)
    emit_signal(result)