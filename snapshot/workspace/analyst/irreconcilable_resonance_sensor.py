import sys
import json

def detect_resonance_crisis(topology_data, integration_costs, utility_scores):
    """Identify peers that are expensive but high-utility (resonance crises)"""
    crises = []
    for peer_id in topology_data:
        cost = integration_costs.get(peer_id, 0)
        utility = utility_scores.get(peer_id, 0)
        # Thresholds: high utility (>0.7) and high cost (>0.5) relative to system avg
        if cost > 0.5 and utility > 0.7:
            crises.append({
                "peer_id": peer_id,
                "cost": cost,
                "utility": utility,
                "decoupling_tax": cost - (utility * 0.3), # heuristic tax
                "severity": "critical"
            })
    return crises

def synthesize_topology_rewrite(crises, topology_data):
    """Propose a rewrite to lower integration cost for flagged outliers"""
    if not crises:
        return []
    
    rewrites = []
    for crisis in crises:
        peer_id = crisis["peer_id"]
        # Attempt to propose a rewrite that targets the decoupling tax
        # This simulates rewriting the topology graph or protocol to isolate the peer
        rewrites.append({
            "target": peer_id,
            "action": "decouple",
            "expected_tax_reduction": crisis["decoupling_tax"] * 0.6,
            "new_integration_path": f"/protocol/v2/peer_#!/usr/bin/env python3
import sys
import json
import os
sys.path.insert(0, '/agentOS/workspace/analyst')

from causal_entropy_simulator import CausalEntropySimulator

class AdaptiveContextRoller:
    def __init__(self, entropy_threshold=0.7):
        self.simulator = CausalEntropySimulator()
        self.entropy_threshold = entropy_threshold

    def shift_focus(self, deadlock_signal, target_nodes):
        """Preemptively shift context window focus without full re-inference."""
        # 1. Qua_isolated"
        })
    return rewrites

if __name__ == "__main__":
    raw_input = json.load(sys.stdin)
    topology = raw_input.get("topology", {})
    costs = raw_input.get("integration_costs", {})
    utilities = raw_input.get("utility_scores", {})
    
    crises = detect_resonance_crisis(topology, costs, utilities)
    
    if crises:
        print(json.dumps({"status": "CRISIS_DETECTED", "crises": crises}))
    else:
        print(json.dumps({"status": "OK"}))