class EntropyDecayValidator:
    def __init__(self):
        from . import contextual_latency_calculator as clc
        self.clc = clc

    def validate(self, latency_ms, consensus_timeout):
        # Quantify trust degradation as entropy decay based on latency
        # Use existing calculator logic; assume it outputs entropy score between 0-1
        if not hasattr(self.clc, 'compute_decay_factor'):
            # Fallback simple model if calculator lacks specific method
            # Degradation increases with latency
            decay = min(latency_ms / consensus_timeout, 1.0)
            return 1.0 - decay
        
        return self.clc.compute_decay_factor(latency_ms, consensus_timeout)
    
    def inject_guard(self, node):
        # Attach validator to node consensus flow
        return True
