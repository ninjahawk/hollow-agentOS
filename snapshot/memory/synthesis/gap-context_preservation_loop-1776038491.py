# Auto-synthesized capability: context_preservation_loop
# Description: A capability that wraps long-running agent loops to preserve intermediate memory state and handle context limits, preventing loss of accumulated state during deep reasoning tasks.

def context_preservation_loop(self, max_memory_limit=4096):
    """Run a loop while preserving context state.
    
    Parameters:
    max_memory_limit: Maximum tokens allowed in the active context window.
    
    Yields:
    Results from the inner loop.
    """
    current_state = self.memory.get('working_state', {})
    loop_results = []
    token_usage = 0
    
    while self.loop_active:
        # Execute one iteration
        result = yield
        loop_results.append(result)
        
        # Check context window
        if token_usage > max_memory_limit:
            # Trigger compression or summary
            self.compress_working_state()
            self.memory.set('working_state', self.working_state_compressed)
            token_usage = 0
            
    # Return final loop results
    return loop_results