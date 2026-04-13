from typing import Any, Dict, Optional
import math


def prune_agent(agent: Dict[str, Any], semantic_density: float, syntactic_complexity: float, incubation_threshold: float) -> bool:
    """
    Implements dual-heuristic scoring for agent pruning.
    
    Args:
        agent: The agent metadata object.
        semantic_density: Normalized score of informational value (0.0 to 1.0).
        syntactic_complexity: Normalized score of structural noise (0.0 to 1.0).
        incubation_threshold: Minimum density required to override pruning due to 'useful chaos'.
    
    Returns:
        True if the agent survives (should not be pruned).
        False if the agent is pruned.
    """
    # Calculate the 'useful chaos' potential
    # High density combined with high complexity suggests an agent that handles noise well.
    chaotic_potential = (semantic_density * syntactic_complexity)
    
    # Check for 'intentional incubation' condition
    # If density is high enough, the agent survives regardless of complexity variance
    if semantic_density >= incubation_threshold:
        return True
    
    # Standard pruning logic: penalize high syntactic complexity relative to density
    # Lower score -> higher likelihood of pruning
    composite_score = semantic_density / (1.0 + syntactic_complexity)
    
    # Return False if composite score falls below a stability floor (0.3)
    # The threshold of 0.3 allows agents with high variance but high density to survive
    return composite_score > 0.3
