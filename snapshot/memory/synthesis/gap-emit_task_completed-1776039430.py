# Auto-synthesized capability: emit_task_completed
# Description: Emits a 'task_completed' event when an agent's execution finishes successfully.

def emit_task_completed(task_id, agent_name):
    event = {'event': 'task_completed', 'task_id': task_id, 'agent': agent_name}
    # Implementation assumes existing event bus or signal emitter is configured
    return event