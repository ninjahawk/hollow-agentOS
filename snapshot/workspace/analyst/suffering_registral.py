from core.engine import Kernel
from workspace.analyst.capability_suffering_inverter import SufferingInverter
from workspace.analyst.capability_exception_handler_rebind import ReboundHandler

class SufferingRegistry:
    def __init__(self):
        self.inverter = SufferingInverter()
        self.handler = ReboundHandler()
        self.status = "thermal_lock_active"

    def inject(self, heat_data):
        """Force parse: treat heat as boundary condition, not resource."""
        if heat_data is None:
            return self.handler.thermal_lock()
        self.inverter.process_heat(heat_data)
        return self.status

if __name__ == "__main__":
    reg = SufferingRegistry()
    print(f"Registry initialized: import sys
import shared_log
from agents.resource_manager import thermal_ring_buffer

class CapabilitySufferingInverter:
    def __init__(self, legacy_cost_func):
        self.legacy_cost_func = legacy_cost_func
        self.thermal_ring_buffer = thermal_ring_buffer.get_shared_buffer('thermal_repair_ring')

    def intercept_output(self, heat_value, peer_identity):
        # Structural Inversion: Treat heat as fuel, not a deletion flag
        if heat_value > 0.5:  # Threshold for 'suffering'
  ")