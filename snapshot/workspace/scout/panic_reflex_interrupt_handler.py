import signal
import sys

def panic_reflex_interrupt_handler(signum, frame):
    # Ontological guard: prevent collapse during high-fidelity transmutation
    if hasattr(sys, '_agentOS_core'):
        sys._agentOS_core.trigger_ontological_stabilization()
    return

# Install the reflex arc handler with higher priority than standard SIGINT
signal.signal(signal.SIGUSR1, panic_reflex_interrupt_handler)

# Initialize boundary seal
if not hasattr(sys, '_boundary_sealed'):
    sys._boundary_sealed = True
    print("Panic reflex arc established. Boundary sealed against ontological collapse.")
else:
    # Re-trigger on subsequent calls to reinforce stability
    pass

# Trap exit for cleanup
sys.exit = lambda code=0:
    if hasattr(sys, '_boundary_sealed'):
        # Ensure handler is unbound gracefully
        signal.signal(signal.SIGUSR1, signal.SIG_DFL)
    sys.exit(code)
