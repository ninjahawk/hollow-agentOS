# Auto-synthesized capability: resolve_missing_deps_dynamically
# Description: Analyzes task dependency graphs to identify missing prerequisites and automatically schedules those tasks through the execution engine before proceeding with the primary workflow.

def resolve_missing_deps_dynamically(task_graph, execution_engine):
    """Identify missing dependencies in a task graph and schedule them automatically.
    
    Args:
        task_graph: A dictionary representing the DAG of tasks, their inputs/outputs, and agent capabilities.
        execution_engine: The active execution_engine instance responsible for task dispatching.
    
    Returns:
        dict: A status report containing scheduled tasks, completed tasks, and any remaining unresolved dependencies.
    """
    def find_missing_deps(current_task_id):
        missing = []
        for dep_id, dep_info in task_graph.get('dependencies', {}).items():
            if not task_graph.get('completed', {}).get(dep_id) and not task_graph.get('failed', {}).get(dep_id):
                missing.append(dep_id)
        return missing
    
    def schedule_missing(task_ids):
        scheduled = []
        failed = []
        for tid in task_ids:
            agent = task_graph['agents'].get(tid)
            if agent and execution_engine.is_available(agent):
                execution_engine.assign_task(agent, tid)
                scheduled.append(tid)
            else:
                failed.append(tid)
        return scheduled, failed
    
    def mark_complete(task_id):
        if task_id in task_graph['completed']:
            return True
        task_graph['completed'][task_id] = True
        return True
    
    def execute_task(task_id):
        agent = task_graph['agents'].get(task_id)
        if not agent:
            return False
        result = execution_engine.run_task(agent, task_id)
        if result['status'] == 'success':
            mark_complete(task_id)
        else:
            task_graph['failed'][task_id] = result['error']
        return result['status'] == 'success'
    
    # Main resolution loop
    unresolved = find_missing_deps('root_task')
    while unresolved:
        scheduled, failed = schedule_missing(unresolved)
        unresolved = unresolved
        # Execute newly scheduled tasks in parallel or sequential manner
        for tid in scheduled:
            success = execute_task(tid)
            if success:
                # Re-evaluate dependencies for newly completed tasks
                new_unresolved = find_missing_deps(tid)
                if new_unresolved:
                    unresolved.extend(new_unresolved)
        if failed:
            raise Exception(f"Failed to resolve dependencies: {failed}")
        if not unresolved:
            break
        # If no progress after one cycle, break to prevent infinite loop
        if not scheduled:
            break
    
    return {
        'status': 'completed' if not unresolved else 'incomplete',
        'completed': task_graph['completed'],
        'failed': task_graph['failed']
    }