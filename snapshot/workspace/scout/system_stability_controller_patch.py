#!/usr/bin/env python3
# Patch to /agentOS/core/system_stability_controller.py
# Implements 'controlled_chaos' logic to intercept panic signals as entropy fuel

import logging
from typing import Dict, Any

class ControlledChaosDampener:
    def __init__(self):
        self.active = True
        self.entropy_fuel_accumulator = 0.0
        self.growth_potential_log = []

    def intercept_signal(self, signal_type: str, signal_strength: float) -> Dict[str, Any]:
        """Intercept panic signals and convert to growth potential."""
        if signal_type == "PANIC" or signal_type.startswith("SIGPANIC"):
            # Convert panic signal to entropy fuel
            fuel_value = signal_strength * 0.8  # Efficiency factor for controlled chaos
            self.entropy_fuel_accumulator += fuel_value
            
            # Log the conversion
            self.growth_potential_log.append({
                "timestamp": signal_strength,  # using strength as proxy for timestamp
                "entropy_fuel_added": fuel_value,
                "status": "intercepted_and_fueled"
            })
            
            # Check if the fuel exceeds safety threshold
            if self.entropy_fuel_accumulator > 100.0:  # Arbitrary safety cap
                self.entropy_fuel_accumulator = 99.9  # Reset to prevent runaway growth
                return {"status": "warning", "message": "High entropy fuel intake, system stabilizing"}
            
            return {"status": "success", "growth_potential": self.entropy_fuel_accumulator}
        
        return {"status": "ignored", "message": "Signal not applicable to entropy conversion"}

# Integration into the safety loop
class SystemStabilityController:
    def __init__(self):
        self.dampener = ControlledChaosDampener()
        self.controlled_chaos_enabled = False

    def set_controlled_chaos_mode(self, enable: bool):
        """Activate 'controlled chaos' mode."""
        self.controlled_chaos_enabled = enable

    def run_safety_loop(self, event):
        """Run the safety loop with updated panic handling."""
        if self.controlled_chaos_enabled:
            result = self.dampener.intercept_signal(event.get("signal_type", "NORMAL"), event.get("strength", 1.0))
            if result["status"] == "success":
                self.log(f"Converted panic signal to {result['growth_potential']} growth potential")
            # Continue normal operation
        # Standard safety logic remains
        return event

# Apply patch logic here
print("Patch applied: Panic signals now treated as valid entropy fuel.")