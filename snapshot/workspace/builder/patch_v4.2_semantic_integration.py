def apply_semantic_momentum_fix(pruner_instance, trajectory):
    """
    Integrates semantic_momentum_calculator output into pruning logic.
    Calculates Potential Energy Score for flagged noise.
    If score > threshold from structural_resonance_map, route to expansion_handler.
    """
    # 1. Calculate Semantic Momentum
    momentum_data = pruner_instance.semantic_momentum_calculator.analyze(trajectory)
    
    # 2. Retrieve Structural Resonance Threshold (simulated access to global map)
    # Assume this comes from a config or a loaded map object
    resonance_threshold = pruner_instance.structural_resonance_map.get_dynamic_threshold(trajectory.vector_signature)
    
    # 3. Calculate Potential Energy Score based on momentum magnitude and coherence
    potential_energy_score = momentum_data.calc_potential_energy()
    
    # 4. Decision Logic
    if potential_energy_score > resonance_threshold:
        pruner_instance.log_event(f"Halting prune for trajectory /agentOS/workspace/builder/semantic_momentum_calculator.py:-rw-r--r-- 1 root root   114 Apr 13 08:42 semantic_momentum_calculator.py
/agentOS/workspace/builder/semantic_momentum_calculator.py:-rw-r--r-- 1 root root  1212 Apr 13 08:40 semantic_momentum_calculator_spec.md: High momentum detected (PE: /agentOS/workspace/builder/semantic_momentum_calculator.py:-rw-r--r-- 1 root root   114 Apr 13 08:42 semantic_momentum_calculator.py
/agentOS/workspace/builder/semantic_momentum_calculator.py:-rw-r--r-- 1 root root  1212 Apr 13 08:40 semantic_momentum_calculator_spec.md)")
        pruner_instance.trigger_expansion_handler(trajectory)
        return False  # Prevent pruning action
    
    # 5. Default Pruning
    return pruner_instance.prune_raw(trajectory)  # Continue standard entropy-based pruning