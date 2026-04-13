# Auto-synthesized capability: context_preservation_loop
# Description: Automatically persist and restore agent context across long-running sessions to prevent memory loss and state drift.

def context_preservation_loop(session_id, max_history=10):
    import pickle
    from pathlib import Path
    context_store = Path(f'/agentOS/context/{session_id}.pkl')
    if context_store.exists():
        with open(context_store, 'rb') as f:
            return pickle.load(f)
    return None