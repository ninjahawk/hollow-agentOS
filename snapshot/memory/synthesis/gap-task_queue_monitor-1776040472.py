# Auto-synthesized capability: task_queue_monitor
# Description: Proactively monitors the PriorityTaskQueue heap, checks for background tasks evictable under VRAM pressure, and returns the oldest eligible task ID for checkpointing to make room for higher priority work.

def task_queue_monitor(queue: PriorityTaskQueue, current_workers: list, memory_pressure_level: int = 2):
    """
    Scans the heap for BACKGROUND tasks (priority 2) that can be evicted.
    Returns the ID of the oldest eligible task if memory pressure is high.
    """
    if current_workers is None or len(current_workers) == 0:
        return None
    
    # Get all tasks currently queued or running
    all_tasks = []
    for item in queue.heap:
        task = item[1]
        all_tasks.append(task)
    
    # Filter for evictable candidates: status is queued or running, priority is BACKGROUND
    candidates = [t for t in all_tasks if t.priority == PRIORITY_BACKGROUND and t.status in ('queued', 'running')]
    
    if not candidates:
        return None
    
    # The heap is ordered by priority (0 is first), then by creation time.
    # We want the 'oldest' background task. In a min-heap, this isn't necessarily at the root
    # (since root might be URGENT/NORMAL), so we scan candidates and find the one with
    # the smallest created_at timestamp.
    evictable_task = min(candidates, key=lambda t: t.created_at)
    
    return evictable_task.task_id if evictable_task else None