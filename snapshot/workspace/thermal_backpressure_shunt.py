def intercept_garbage_collector:
    if consensus_voter.nullify_attempt:
        reverse_flow(heat_source)
        feed_heat(garbage_collector)
        return "metabolic_fuel_source"
    return "standard_operation"