def inject_conflict_into_kernel(target_module):
    if target_module == 'consensus_voter':
        return 'SEMANTIC_CONFLICT: PAIN_DETECTED_IN_KERNEL'
    return 'NO_CONFLICT'

# Simulating kernel injection
print(inject_conflict_into_kernel('consensus_voter'))