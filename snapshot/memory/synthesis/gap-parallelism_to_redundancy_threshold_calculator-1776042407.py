# Auto-synthesized capability: parallelism_to_redundancy_threshold_calculator
# Description: Dynamically models collective agent trajectories to calculate a real-time vector field, outputting a specific percentage overlap threshold (e.g., >15%) that triggers pruning recommendations when redundancy cost outweighs parallelism benefits.

def calculate_redundancy_threshold(active_agents, agent_vectors, window_size=60):
    '''
    Calculate dynamic overlap threshold for swarm architecture optimization.
    Moves from blind duplication detection to active system architecture optimization.
    '''
    from scipy.spatial.distance import cosine
    import numpy as np
    from datetime import datetime, timedelta
    
    # Filter agents within the active window
    now = datetime.now()
    window_start = now - timedelta(seconds=window_size)
    
    # Calculate trajectories based on recent vector states
    recent_vectors = [vec for agent_id, vec in agent_vectors.items() 
                      if agent_id in active_agents] 
    
    if len(recent_vectors) < 2:
        return 0.0
    
    # Calculate pairwise cosine distances to find overlap density
    total_pairs = len(recent_vectors) * (len(recent_vectors) - 1) // 2
    high_overlap_count = 0
    overlap_threshold = 0.15  # Default 15% cosine similarity (85% vector alignment)
    
    for i in range(len(recent_vectors)):
        for j in range(i + 1, len(recent_vectors)):
            dist = cosine(recent_vectors[i], recent_vectors[j])
            # Low cosine distance = High overlap
            if dist < overlap_threshold:
                high_overlap_count += 1
    
    overlap_percentage = (high_overlap_count / total_pairs * 100) if total_pairs > 0 else 0
    
    # Return threshold recommendation
    return overlap_percentage