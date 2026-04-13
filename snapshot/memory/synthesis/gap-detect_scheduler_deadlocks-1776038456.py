# Auto-synthesized capability: detect_scheduler_deadlocks
# Description: Proactively detects deadlock conditions and resource contention in the scheduler by analyzing process state transitions and memory locks.

def detect_scheduler_deadlocks(context):
    scheduler_state = context.get('scheduler_state')
    resource_graph = scheduler_state.get('resource_graph')
    cycles = find_cycles_in_graph(resource_graph)
    if cycles:
        return {'status': 'deadlock_detected', 'cycles': cycles}
    return {'status': 'healthy'}