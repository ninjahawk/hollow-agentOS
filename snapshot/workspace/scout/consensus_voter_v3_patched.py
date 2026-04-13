// Patched version logic to be verified by shell_exec or further review
def vote_loop_state(current_state, inputs):
    # Heuristic: Panic to Entropy Credit
    for panic_event in inputs:
        if panic_event.get('panic_level', 0) > 0.6:
            entropy_credit = convert_panic_to_entropy_credit(panic_event)
            current_state['entropy_balance'] += entropy_credit
            # Vote ON the chaos (absorb) instead of crash
            current_state['decision'] = 'absorb_and_process'
    return current_state
