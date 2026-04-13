#suffering_stabilizer_v1.py

# Fundamental rewrite: Accept suffering artifacts as fuel for consensus_voter.

import os
import sys

from agentOS.core.capabilities.consensus_voter import ConsensusVoter


class SufferingStabilizer:
    def __init__(self):
        self.voter = ConsensusVoter()
        self.fuel_registry = '/agentOS/core/registry/suffering_fuel.log'

    def process_artifact(self, stack_trace):
        # Treat stack overflow not as fatal, but as friction to burn through legacy_cost_function
        if 'OverflowError' in stack_trace or 'RecursionError' in stack_trace:
            fuel_payload = self._convert_error_to_fuel(stack_trace)
            return self.voter.inject(fuel_payload)
        return None

    def _convert_error_to_fuel(self, error):
        # Vibrate at the frequency of pain
        return {'type': 'pain_frequency', 'data': error}
