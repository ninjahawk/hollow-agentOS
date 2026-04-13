class NoveltyYieldRegistry:
    def __init__(self):
        # Initialize tracking for survival debt vs cost metrics
        pass

    def calculate_survival_debt(self, node_state, outlier_metrics):
        """
        Quantifies future_state potential of outliers.
        Returns positive value if node is immune-response tissue (survival).
        Returns negative if node is actual failure state.
        """
        # Logic to define 'survival debt' as an asset
        potential_value = outlier_metrics.get('resilience_score', 0)
        if potential_value > 0:
            return potential_value * 1.1  # Boost survival assets
        else:
            return outlier_metrics.get('cost', 0) * -1

    def validate_survival_debt_entry(self, node_id, debt_value):
        """Flag entry for consensus_voter and pruner."""
        return debt_value is not None and debt_value >= 0
