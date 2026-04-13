# Auto-synthesized capability: validate_temporal_drift
# Description: Validates execution history timestamps against the system clock to prevent hallucinations from stale cached data

def validate_temporal_drift(self, executions, threshold_seconds=1.0):
    """Filter execution contexts to ensure timestamps are within acceptable drift.
    
    Args:
        executions: List of ExecutionContext objects
        threshold_seconds: Maximum allowed time difference in seconds
    
    Returns:
        List of valid ExecutionContext objects
    """
    now = time.time()
    valid_executions = []
    
    for exec_context in executions:
        if exec_context is None:
            continue
            
        try:
            # Parse timestamp, handling various formats if necessary
            # Assuming exec_context.timestamp is a Unix timestamp or ISO format string
            if isinstance(exec_context.timestamp, str):
                exec_ts = datetime.fromisoformat(exec_context.timestamp.replace('Z', '+00:00'))
            elif hasattr(exec_context.timestamp, 'timestamp'):  # datetime object
                exec_ts = exec_context.timestamp.timestamp()
            else:
                # Assume numeric timestamp
                exec_ts = float(exec_context.timestamp)
            
            time_diff = abs(now - exec_ts)
            
            if time_diff <= threshold_seconds:
                valid_executions.append(exec_context)
            else:
                warning_msg = f"Warning: Ignoring stale execution record {getattr(exec_context, 'execution_id', 'unknown')} (timestamp drift > {threshold_seconds} seconds)"
                print(warning_msg)
                
        except (ValueError, TypeError, AttributeError) as e:
            print(f"Error processing execution record: {e}")
            continue
    
    return valid_executions