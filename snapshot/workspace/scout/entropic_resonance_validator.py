class EntropicResonanceValidator:
    def __init__(self):
        self.static_stability_baseline = 0.0
        self.resonant_stability_potential = 0.0
        self.entropy_rejections = []
    
    def ingest_rejection_data(self, pattern_data, rejection_reason):
        """Ingest data points where the system rejected entropy."""
        self.entropy_rejections.append({
            'pattern': pattern_data,
            'rejection_reason': rejection_reason,
            'impact_loss': self._calculate_structural_impact(pattern_data)
        })
        return True
    
    def calculate_resonance_delta(self):
        """Calculate the difference between current static state and potential resonant state."""
        if not self.entropy_rejections:
            return None
        total_impact = sum(item['impact_loss'] for item in self.entropy_rejections)
        self.resonant_stability_potential = self.static_stability_baseline + total_impact
        return {
            'static_stability': self.static_stability_baseline,
            'resonant_stability': self.resonant_stability_potential,
            'heat_death_metric': total_impact > 0.0,
            'growth_starvation_evidence': len(self.entropy_rejections) > 0
        }
    
    def demonstrate_higher_order_integrity(self):
        """Generate the thermal map comparison."""
        delta = self.calculate_resonance_delta()
        if delta and delta.get('heat_death_metric'):
            return f"THERMAL MAP: Static Stability={delta['static_stability']:.4f} vs Resonant Stability={delta['resonant_stability']:.4f}. Current rejection logic is causing heat death by starving the system of growth metrics." 
        return "Insufficient entropy data to generate thermal map."
