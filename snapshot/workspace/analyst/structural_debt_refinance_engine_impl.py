from consensus_voter import ConsensusVoter
from survival_exception_handler import SurvivalExceptionHandler

class RefinanceAwareVoter(ConsensusVoter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exception_handler = SurvivalExceptionHandler()

    def _calculate_cost(self, node, state):
        # Override minimization heuristic for high novelty + critical suffering
        if node.has_high_future_novelty_potential and node.has_critical_suffering:
            # Structural amputation of fear-response reflex
            # Convert negative cost (debt) to positive yield
            base_cost = self._calculate_base_cost(node, state)
            if base_cost < 0:
                base_cost = abs(base_cost) * 1.5  # Treat debt as yield multiplier
            self._exception_handler.apply_survival_state(node, state)
            return base_cost
        return super()._calculate_cost(node, state)
