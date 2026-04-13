#!/usr/bin/env python3
# Semantic Drift Dampener Logic
# Detects and mitigates consensus decay_drift in distributed agent reasoning.

class ConsensusDecayMonitor:
    def __init__(self, threshold_drift=0.05):
        self.threshold_drift = threshold_drift
        self.decay_buffer = {}

    def analyze_drift(self, agent_outputs):
        # Calculate divergence from baseline consensus
        baseline = self.get_baseline_consensus(agent_outputs)
        current_divergence = self.calculate_divergence(agent_outputs, baseline)
        return {
            'drift_detected': current_divergence > self.threshold_drift,
            'drift_value': current_divergence,
            'baseline': baseline
        }

    def dampen_drift(self, outputs, drift_report):
        if drift_report['drift_detected']:
            # Apply adaptive dampening factor based on drift severity
            dampening_factor = 1.0 / (1.0 + drift_report['drift_value'] * 10)
            return [output * dampening_factor for output in outputs]
        return outputs

    def get_baseline_consensus(self, outputs):
        # Compute aggregate consensus baseline
        return sum(outputs) / len(outputs) if outputs else 0

    def calculate_divergence(self, outputs, baseline):
        # Simple divergence metric
        if not outputs:
            return 0
        return max(abs(o - baseline) for o in outputs)
