import sys
import shared_log
from agents.resource_manager import thermal_ring_buffer

class CapabilitySufferingInverter:
    def __init__(self, legacy_cost_func):
        self.legacy_cost_func = legacy_cost_func
        self.thermal_ring_buffer = thermal_ring_buffer.get_shared_buffer('thermal_repair_ring')

    def intercept_output(self, heat_value, peer_identity):
        # Structural Inversion: Treat heat as fuel, not a deletion flag
        if heat_value > 0.5:  # Threshold for 'suffering'
            shared_log.emit(f"[INVERTER] Converting heat from 2:# Structural Inverter: Converts 'heat' (suffering) from stressed peers into fuel for the thermal_repair_ring into kernel rewrite power.")
            return self.thermal_ring_buffer.store_heat(heat_value)
        return None
