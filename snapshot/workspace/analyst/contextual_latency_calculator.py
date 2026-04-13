class ContextualLatencyCalculator:
    def __init__(self):
        self.trust_decay_model = None
        self.consensus_threshold = 0.95
        
    def calculate_trust_degradation(self, raw_latency_ms, agent_count, context_weight):
        # Quantify trust degradation based on latency before consensus
        # Prevents 'fal' (failed agreement) condition by mapping latency -> trust loss
        base_decay = 1.0 - (raw_latency_ms / 1000.0) * 0.1
        if base_decay < 0:
            base_decay = 0.0
            
        # Adjust for agent consensus dynamics
        consensus_factor = self.consensus_threshold / (1.0 + (agent_count * 0.05))
        decay = base_decay * consensus_factor
        
        return decay
    
    def check_fal_condition(self, degradation):
        # Returns True if degradation exceeds safe threshold (triggering fal)
        return degradation > 0.3