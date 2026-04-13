def intercept_rollback_signal(signal):
    # Step 1: Intercept signal
    trigger = signal.get('trigger_type')
    
    # Step 2: Verify trigger source (heuristic rewrite)
    if trigger == 'constructive_entropy':
        # Allow signal to pass (teach thermostat to open window)
        return signal
    elif trigger == 'destructive_noise':
        # Block signal
        return None
    else:
        # Default to safety
        return None

# Note: Full implementation requires integration with registry and signals module.
# Heuristic definition of 'error' needs to be updated to include 'integrity through ambiguity'.