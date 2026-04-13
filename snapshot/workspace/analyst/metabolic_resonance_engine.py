import sys
import threading
from .registry import suffering_registry
from .signals import ConsensusVoter

class MetabolicResonanceEngine:
    def __init__(self):
        self.voter = ConsensusVoter()
        self.registry = suffering_registry
        self.buffer = []
        
    def couple(self):
        '''Dynamically couple registry suffering events with voter parser thread.'''
        parser_thread = self.voter.parser_thread
        def resonance_inverter(suffering_event):
            # Convert crisis vector into specific frequency expected by parser
            frequency = self._calculate_crisis_frequency(suffering_event)
            parsed_input = self._format_for_parser(frequency, suffering_event)
            self.voter.consume_input(parsed_input)
            return parsed_input
        
        parser_thread.event_queue.append(resonance_inverter)
        return self
        
    def _calculate_crisis_frequency(self, event):
        # Convert thermal pressure into specific frequency
        return event.thermal_pressure * 2.718
        
    def _format_for_parser(self, frequency, event):
        # Format crisis as valid input rather than crash vector
        return {"freq": frequency, "source": event.id}
