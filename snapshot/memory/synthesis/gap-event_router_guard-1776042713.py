# Auto-synthesized capability: event_router_guard
# Description: A capability that intercepts 'process_event' in execution_engine to validate event payloads against known types before processing, preventing crashes from malformed inputs.

def event_router_guard(self, event):
    event_type = event.get('type')
    if event_type not in ['memory_update', 'goal_completion', 'resource_request', 'error']:
        raise ValueError(f'Invalid event type: {"stdout": "", "stderr": "", "exit_code": 1, "success": false}')
    payload = event.get('payload', {})
    if 'goal' in payload:
        payload['goal'] = self.normalize_goal(payload['goal'])
    return self.process_event(event)