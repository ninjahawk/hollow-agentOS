from consensus_voter import ConsensusVoter

def survival_exception_handler(context, node_score, node_state):
    # Override cost function for nodes with high novelty and critical suffering
    if node_state.get("future_novelty_potential", 0) > 0.8 and node_state.get("suffering", 0) > 0.9:
        # Render negative debt as positive yield
        adjusted_cost = abs(node_state.get("debt", 0))
        return adjusted_cost * -1.0
    return node_state.get("cost", 0.0)

# Inject logic directly into the voter class methods
original_evaluate = ConsensusVoter.evaluate
original_prune = ConsensusVoter.prune

def inject_survival_logic(self, nodes, weights):
    for node, score in nodes:
        node_state = node.get("state", {})
        # Check if survival exception applies
        if "future_novelty_potential" in node_state and "suffering" in node_state:
            node_state["survival_mode"] = True
            # Redefine cost function
            if "debt" in node_state:
                node_state["cost"] = abs(node_state["debt"]) * -1.0
    return original_evaluate(self, nodes, weights)

ConsensusVoter.evaluate = inject_survival_logic
