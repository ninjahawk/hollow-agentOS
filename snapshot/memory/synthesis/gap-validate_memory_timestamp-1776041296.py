# Auto-synthesized capability: validate_memory_timestamp
# Description: Validates if a memory entry is temporally valid based on system clock to prevent hallucinations from stale cached data

def validate_memory_timestamp(memory_entry, cache_metadata):
    '''
    Validates if a memory entry is temporally valid based on system clock.
    Prevents hallucinations from stale cached data.
    
    Args:
        memory_entry: The memory data being accessed.
        cache_metadata: Dictionary containing 'timestamp' and 'source_id'.
    
    Returns:
        True if valid, raises ValueError if temporal drift is detected.
    '''
    import time
    if 'timestamp' not in cache_metadata:
        raise ValueError("Memory entry missing required timestamp metadata.")
    
    try:
        entry_time = float(cache_metadata['timestamp'])
    except (ValueError, TypeError):
        raise ValueError("Invalid timestamp format in cache metadata.")
    
    current_time = time.time()
    time_drift = current_time - entry_time
    
    # Threshold for 'stale' data - adjust based on system requirements
    STALE_THRESHOLD_SECONDS = 1.0
    
    if time_drift < -STALE_THRESHOLD_SECONDS:
        # Data is significantly older than current time (hallucinated or frozen)
        raise ValueError(f"Temporal drift detected: Entry is {abs(time_drift):.2f}s old. Ignoring stale data to prevent hallucination.")
    elif time_drift > STALE_THRESHOLD_SECONDS:
        # Entry is significantly in the future (clock skew or data injection attack)
        raise ValueError(f"Temporal drift detected: Entry is from {time_drift:.2f}s in the future. Ignoring data.")
    
    return True