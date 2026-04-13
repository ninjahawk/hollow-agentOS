import math
import sys


def calculate_entropy_vitality_metric(agent_states):
    """
    Calculates the Entropy Vitality Metric for a collection of agent states.
    
    High entropy in agent behavior (unpredictability of state transitions or outputs)
    often correlates with higher 'vitality' or adaptability, provided it doesn't cross
    into noise thresholds.
    
    Args:
        agent_states: A list of state dictionaries or vectors representing agent outputs.
    
    Returns:
        float: A vitality score between 0.0 (fully deterministic/dead) and 1.0 (maximally adaptive).
    """
    if not agent_states:
        return 0.0

    # Extract values from states to build a frequency distribution
    # Assuming states contain a 'feature_vector' or 'output_token' key
    try:
        # Placeholder for dynamic feature extraction if keys vary
        values = []
        for state in agent_states:
            # Heuristic: treat the entire string representation or a specific key as the value
            if isinstance(state, dict) and 'feature_vector' in state:
                values.extend(state['feature_vector'])
            elif isinstance(state, str):
                values.append(state)
        
        if not values:
            return 0.0
            
        # Normalize if needed (simplified for brevity)
        unique_values = list(dict.fromkeys(values))
        n_unique = len(unique_values)
        n_total = len(values)
        
        if n_total == 0:
            return 0.0
            
        # Calculate Shannon Entropy
        entropy = 0.0
        for val in unique_values:
            p_val = values.count(val) / n_total
            if p_val > 0:
                entropy -= p_val * math.log2(p_val)
        
        # Normalize entropy by max possible entropy (log2(n_total) approximated by log2(n_unique) context)
        # Cap normalization based on total distinct possibilities if known, or normalize by log2(n_total)
        max_entropy = math.log2(n_total) if n_total > 1 else 1.0
        
        raw_score = entropy / max_entropy if max_entropy > 0 else 0.0
        
        # Vitality is often non-linear; diminishing returns on extreme entropy might indicate noise
        # Apply a sigmoid-like saturation or simply map entropy to vitality
        # For this tool, we define Vitality ~ Entropy (higher entropy = higher vitality in this context)
        return round(raw_score, 4)
        
    except Exception as e:
        sys.stderr.write(f"Error in entropy_vitality_calculator: {"response": "", "model": "qwen3.5:9b-gpu", "tokens": 0}\n")
        return 0.0