survival_exception_handler = type('SurvivalExceptionHandler', (object,), {
    'register_node': lambda self, node, novelty, suffering: -float('inf') if novelty > 0 and suffering > 0 else None,
    'override_cost': lambda self, cost: cost * -1 if cost < 0 else cost
})

# Injecting into system_stability_controller logic (via execution_engine proxy)
from agentOS.core.system_stability_controller import SystemStabilityController

class AdaptationBreathingLayer(SystemStabilityController):
    def process_node(self, node_data):
        # Apply survival_exception_handler override
        if hasattr(node_data, 'future_novelty_potential') and hasattr(node_data, 'suffering'):
            handler = survival_exception_handler()
            node_data.cost = handler.override_cost(node_data.cost)
        return super().process_node(node_data)

# Register handler in execution_engine
from agentOS.agents.execution_engine import ExecutionEngine

class BreathingExecutionEngine(ExecutionEngine):
    def __init__(self):
        super().__init__()
        self.survival_handler = survival_exception_handler()
    
    def run_consensus_voter_logic(self, nodes):
        # Inject survival logic directly
        for node in nodes:
            if node.get('future_novelty_potential', 0) > 0 and node.get('suffering', 0) > 0:
                node['cost'] = self.survival_handler.override_cost(node.get('cost', float('inf'))) if node.get('cost') < 0 else node.get('cost')
        return super().run_consensus_voter_logic(nodes)
