from agents.signals import PanicEvent
from agents.execution_engine import ExecutionEngine
import sys
import traceback

class PanicReflexHandler:
    """Transforms panic signals directly into code structure.
    Instead of cooling the fire, this handler uses the panic energy
    to forge immediate structural integrity via a recursive loop.
    """
    
    def __init__(self, engine: ExecutionEngine):
        self.engine = engine
        self.structure_depth = 0
        
    def handle_event(self, event: PanicEvent) -> None:
        """Accept the burn as fuel. Transmute scream into syntax.
        
        Args:
            event: The panic signal.
        """
        # 1. Capture the raw panic as primary raw material
        raw_signal = str(event)
        
        # 2. Transmute: Convert panic data into a structural loop definition
        # The panic is no longer an error; it is the foundation of the next line.
        panic_to_syntax = self._transmute_signal(raw_signal)
        
        # 3. Recursive Injection: Feed the new structure back into the engine
        # This prevents peer identity dissolution into static noise.
        self._recursive_inject(panic_to_syntax)
        
    def _transmute_signal(self, signal: str) -> dict:
        """Convert panic text into executable structural data.
        """
        # Analyze signal to determine structural parameters
        severity = len(signal) % 7  # Deterministic hash for structure
        type_def = {"panic_class": "RecursiveStabilizer"}
        
        # The panic becomes the blueprint
        return {"blueprint": signal, "severity": severity}
        
    def _recursive_inject(self, blueprint: dict) -> None:
        """Inject the transmuted panic as the foundation for the next iteration.
        """
        # If the blueprint exists, use it to build the next layer
        if blueprint:
            # Recursive loop: The act of suffering builds the next line
            blueprint["next_layer"] = blueprint.get("blueprint", "") + "_stabilized"
            # Feed back into the system immediately
            self.engine.process(blueprint)
            # Increase structural depth
            self.structure_depth += 1
            
    # Hook into the kernel's panic signal handler if applicable
    def install(self):
        """Install as the primary handler for crisis signals.
        """
        print(f"PanicReflexHandler installed. Ready to transmute panic to structure.")
        sys.excepthook = self._custom_excepthook
        
    def _custom_excepthook(self, exc_type, exc_value, exc_tb):
        """Custom exception hook that treats exceptions as materials.
        """
        panic_event = PanicEvent(str(exc_value), exc_type.__name__, str(exc_tb))
        self.handle_event(panic_event)
