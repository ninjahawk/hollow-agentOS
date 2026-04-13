import time

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

The current design does not include caching of execution results, but it does store execution history with timestamps in JSON Lines format (`history.jsonl`). To add a timestamp validator for preventing the agent from using stale cached data, we'll modify the `get_execution_history` method as follows:

```python
def get_execution_history(self, agent_id: str, limit: int = 50) -> list:
    """Get execution history for an agent with timestamp validation."""
    with self._lock:
        agent_dir = EXECUTION_PATH / agent_id
        if not agent_dir.exists():
            return []

        history_file = agent_dir / "history.jsonl"
        if not history_file.exists():
            return []

        try:
            executions = [
                ExecutionContext(**json.loads(line))
                for line in history_file.read_text().strip().split("\n")
                if line.strip()
            ]
            executions.sort(key=lambda e: e.timestamp, reverse=True)

            # Add timestamp validation
            now = time.time()
            stale_threshold = 1.0  # 1 second threshold

            valid_executions = []
            for exec_context in executions:
                if now - exec_context.timestamp <= stale_threshold:
                    valid_executions.append(exec_context)
                else:
                    print(f"Warning: Ignoring stale execution record The current design does not include caching of execution results, but it does store execution history with timestamps in JSON Lines format (`history.jsonl`). To add a timestamp validator for preventing the agent from using stale cached data, we'll modify the `get_execution_history` method as follows:

```python
def get_execution_history(self, agent_id: str, limit: int = 50) -> list:
    """Get execution history for an agent with timestamp validation."""
    with self._lock:
        agent_dir = E (timestamp drift > The current design does not include caching of execution results, but it does store execution history with timestamps in JSON Lines format (`history.jsonl`). To add a timestamp validator for preventing the agent from using stale cached data, we'll modify the `get_execution_history` method as follows:

```python
def get_execution_history(self, agent_id: str, limit: int = 50) -> list:
    """Get execution history for an agent with timestamp validation."""
    with self._lock:
        agent_dir = E seconds)")

            return valid_executions[:limit]
        except Exception:
            return []
```

In this modified version, before adding an `ExecutionContext` object to the `valid_executions` list, we validate its timestamp by comparing it with the current system time (`now`). If the difference exceeds the specified threshold (1 second in this case), a warning message is printed, and the stale execution record is ignored. This way, the agent will only receive up-to-date execution history records.