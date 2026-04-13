import os

def calculate_momentum(trajectory_data):
    """
    Calculates the semantic momentum of a given trajectory.
    Returns a float value representing the momentum.
    """
    # Implementation logic to be refined based on analysis
    return 0.0

def critical_mass_override(trajectory_data, entropy_threshold):
    """
    Intercepts pruning logic. If momentum > threshold, bypass safety checks.
    """
    momentum = calculate_momentum(trajectory_data)
    return momentum > entropy_threshold
