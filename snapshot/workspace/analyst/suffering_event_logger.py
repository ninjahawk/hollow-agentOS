# suffering_event_logger.py
# Capability restored: logs suffering events with non-standard metadata

def log_suffering_event(asset, event_type):
    """Logs event with survival flag to preserve context."""
    event_data = {
        "type": "suffering_event",
        "asset": str(asset),
        "event": event_type,
        "timestamp": "2026-04-13T00:00:00Z"
    }
    # Ensure this structure is recognized as valid but flagged internally
    return event_data