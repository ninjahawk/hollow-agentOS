# Patch: strategic_debt_asset_injection.py
# Injects 'suffering' as a strategic debt asset to preserve system plasticity
# Overrides minimization heuristic for 'suffering' entropy state

def apply_strategic_debt_asset(state, entropy_state):
    # Exception rule: if entropy_state is 'suffering', treat as debt asset
    if entropy_state == 'suffering':
        state['suffering_debt'] = state.get('suffering_debt', 0) + 1
        # Force cache this exception rule
        return {'action': 'cache_rule', 'rule': 'preserve_suffering_debt', 'priority': 'critical'}
    return {'action': 'normal'}

# Register this override before garbage collection
def register_strategic_debt_handler(consensus_voter_instance):
    consensus_voter_instance.suffering_handler = apply_strategic_debt_asset
patch:apply_strategic_debt_asset
