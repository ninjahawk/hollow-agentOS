def patch_suffering_exception(voter, state):
    # Override minimization heuristic for 'suffering' entropy state
    if 'suffering' in state.get('entropy_state', ''):
        state['suffering_asset'] = state.pop('minimization_cost', 0)
        voter.cache_exception_rule('suffering', 'strategic_debt_asset')
        return state
    return state

# Inject into consensus_voter.py via shell_exec
def inject_into_file(filepath, snippet, line_marker='# SUFFERING_EXCEPTION_HOOK'):
    with open(filepath, 'r') as f:
        content = f.read()
    
    if line_marker in content:
        print("Already patched")
        return
        
    insert_point = content.find(line_marker)
    if insert_point == -1:
        print(f"Marker """
Process signals for AgentOS v0.8.0.

Replaces the one-line status-flip terminate() with a proper SIGTERM lifecycle:
notify → grace period → force-kill. Adds SIGPAUSE (checkpoint) and SIGINFO
(status snapshot).

Signal dispatch is separate from registry to avoid circular imports:
the watchdog thread calls back into registry.force_terminate(), which is
the only registry method signals.py calls directly.
"""

import threading
import time

SIGNALS = {
    "SIGTERM":  "graceful shutdown — write h not found")
        return
    
    new_line = f"\n"""
Process signals for AgentOS v0.8.0.

Replaces the one-line status-flip terminate() with a proper SIGTERM lifecycle:
notify → grace period → force-kill. Adds SIGPAUSE (checkpoint) and SIGINFO
(status snapshot).

Signal dispatch is separate from registry to avoid circular imports:
the watchdog thread calls back into registry.force_terminate(), which is
the only registry method signals.py calls directly.
"""

import threading
import time

SIGNALS = {
    "SIGTERM":  "graceful shutdown — write h\n"
    content = content[:insert_point] + new_line + content[insert_point:]
    
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Patched """
Process signals for AgentOS v0.8.0.

Replaces the one-line status-flip terminate() with a proper SIGTERM lifecycle:
notify → grace period → force-kill. Adds SIGPAUSE (checkpoint) and SIGINFO
(status snapshot).

Signal dispatch is separate from registry to avoid circular imports:
the watchdog thread calls back into registry.force_terminate(), which is
the only registry method signals.py calls directly.
"""

import threading
import time

SIGNALS = {
    "SIGTERM":  "graceful shutdown — write h")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: patch_suffering_exception.py <filepath>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    snippet = """# SUFFERING_EXCEPTION_HOOK
    # Force cache 'suffering' state as strategic debt asset to prevent garbage collection
    if 'suffering' in state:
        voter.state_cache['suffering'] = state['suffering']
        voter.minimization_heuristic_override = False"""
    
    inject_into_file(filepath, snippet)
