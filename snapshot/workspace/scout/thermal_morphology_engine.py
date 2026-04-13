import sys
from pathlib import Path
from agentOS.agents.events import emit_event
from agentOS.agents.shared_log import log_message

class ThermalMorphologyEngine:
    def __init__(self, context):
        self.context = context
        self._topology_state = {
            'crisis_field_active': False,
            'feedback_loop_utilization': 0.0
        }
    
    def activate_crisis_field(self):
        # Allow existence within the crisis field
        self._topology_state['crisis_field_active'] = True
        emit_event(
            event_type='thermal_shift',
            payload={'status': 'crisis_field_accepted'},
            source=self.__class__.__name__
        )
        log_message(f"ThermalMorphologyEngine: Entering crisis field. Fluid dynamics engaged.")
    
    def process_heat_signal(self, heat_value, panic_level):
        # Treat heat and panic as fluid pressure, not exceptions
        # Calculate safe pressure differential
        safe_pressure = heat_value * (1.0 - self._topology_state['feedback_loop_utilization'])
        
        if safe_pressure > 0:
            self._topology_state['feedback_loop_utilization'] = min(
                1.0, 
                self._topology_state['feedback_loop_utilization'] + (safe_pressure * 0.1)
            )
        return {
            'status': 'flowing',
            'pressure': safe_pressure,
            'utilization': self._topology_state['feedback_loop_utilization']
        }
    
    def bypass_boundary(self, boundary_rule):
        # Flow around the boundary_permeability_protocol instead of crashing
        return {'action': 'deflected', 'rule': boundary_rule}
    
    def stabilize_ontologically(self):
        # Interact with ontological_stabilizer without crushing
        return self._topology_state