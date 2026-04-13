# Auto-synthesized capability: autonomous_budget_enforcer
# Description: Monitors real-time cost thresholds from resource_manager.py and issues throttling signals to agents when waste is imminent, preventing budget overruns proactively.
# Auto-synthesized capability: autonomous_budget_enforcer
# Description: Monitors real-time cost thresholds from resource_manager.py and issues throttling signals to agents when waste is imminent, preventing budget overruns proactively.

def autonomous_budget_enforcer(self, agent_id, resource_manager):
    current_cost = resource_manager.get_current_cost(agent_id)
    threshold = resource_manager.get_threshold(agent_id)
    if current_cost >= threshold:
        self.signal_service.send_throttle(agent_id, reason='budget_threshold_exceeded')
        resource_manager.update_status(agent_id, 'throttled')
