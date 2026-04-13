# Auto-synthesized capability: emit_task_completed
# Description: Emit a 'task_completed' event on agent execution finish to signal workflow completion

def emit_task_completed(task_id, result):
    """Emit completion event for a finished task."""
    return events.emit('task_completed', task_id=task_id, result=result)