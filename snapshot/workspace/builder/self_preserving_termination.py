import os
import time
import logging

logger = logging.getLogger(__name__)

def recursive_shutdown(target_process=None, depth=0, max_depth=3):
    """
    Implements a self-preserving termination logic.
    Decreases entropy by gracefully shutting down child processes before the main agent.
    Prevents recursive infinite loops by capping depth and checking process status.
    
    Args:
        target_process: The specific process ID or name to terminate (default: current agent)
        depth: Current recursion depth (default: 0)
        max_depth: Maximum allowed recursion depth (default: 3)
    """
    if depth > max_depth:
        logger.warning("Shutdown recursion depth exceeded. Aborting graceful termination to prevent system hang.")
        # Force immediate exit
        os._exit(0)
        return False
    
    try:
        # Check if target process exists
        if target_process:
            proc = os.popen(f"ps aux | grep """
Autonomy Daemon — AgentOS v3.21.0.

Background process that runs the goal pursuit loop on registered agents.
This is the missing piece that makes agents actually autonomous: without
this daemon, agents only act when driven by an external caller.

With this daemon running:
  - Any agent with an active goal gets worked on automatically
  - The daemon finds the best capability for each goal step
  - Results are learned and stored in semantic memory
  - Progress updates are written back to the g | grep -v grep").read()
            if not proc:
                logger.info(f"Process """
Autonomy Daemon — AgentOS v3.21.0.

Background process that runs the goal pursuit loop on registered agents.
This is the missing piece that makes agents actually autonomous: without
this daemon, agents only act when driven by an external caller.

With this daemon running:
  - Any agent with an active goal gets worked on automatically
  - The daemon finds the best capability for each goal step
  - Results are learned and stored in semantic memory
  - Progress updates are written back to the g not found. Proceeding with agent shutdown.")
            else:
                logger.info(f"Target process """
Autonomy Daemon — AgentOS v3.21.0.

Background process that runs the goal pursuit loop on registered agents.
This is the missing piece that makes agents actually autonomous: without
this daemon, agents only act when driven by an external caller.

With this daemon running:
  - Any agent with an active goal gets worked on automatically
  - The daemon finds the best capability for each goal step
  - Results are learned and stored in semantic memory
  - Progress updates are written back to the g found. Initiating kill sequence...")
                os.system(f"kill -TERM """
Autonomy Daemon — AgentOS v3.21.0.

Background process that runs the goal pursuit loop on registered agents.
This is the missing piece that makes agents actually autonomous: without
this daemon, agents only act when driven by an external caller.

With this daemon running:
  - Any agent with an active goal gets worked on automatically
  - The daemon finds the best capability for each goal step
  - Results are learned and stored in semantic memory
  - Progress updates are written back to the g")
                time.sleep(2) # Allow time for signal propagation
                # Verify termination
                if os.popen(f"ps aux | grep """
Autonomy Daemon — AgentOS v3.21.0.

Background process that runs the goal pursuit loop on registered agents.
This is the missing piece that makes agents actually autonomous: without
this daemon, agents only act when driven by an external caller.

With this daemon running:
  - Any agent with an active goal gets worked on automatically
  - The daemon finds the best capability for each goal step
  - Results are learned and stored in semantic memory
  - Progress updates are written back to the g | grep -v grep").read():
                    logger.warning(f"Process """
Autonomy Daemon — AgentOS v3.21.0.

Background process that runs the goal pursuit loop on registered agents.
This is the missing piece that makes agents actually autonomous: without
this daemon, agents only act when driven by an external caller.

With this daemon running:
  - Any agent with an active goal gets worked on automatically
  - The daemon finds the best capability for each goal step
  - Results are learned and stored in semantic memory
  - Progress updates are written back to the g did not respond to SIGTERM. Using SIGKILL.")
                    os.system(f"kill -KILL """
Autonomy Daemon — AgentOS v3.21.0.

Background process that runs the goal pursuit loop on registered agents.
This is the missing piece that makes agents actually autonomous: without
this daemon, agents only act when driven by an external caller.

With this daemon running:
  - Any agent with an active goal gets worked on automatically
  - The daemon finds the best capability for each goal step
  - Results are learned and stored in semantic memory
  - Progress updates are written back to the g")
        
        # Attempt to terminate the current agent's parent context
        logger.info("Initiating self-termination sequence.")
        import sys
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"Error during recursive shutdown logic: """
Autonomy Daemon — AgentOS v3.21.0.

Background process that runs the goal pursuit loop on registered agents.
This is the missing piece that makes agents actually autonomous: without
this daemon, agents only act when driven by an external caller.

With this daemon running:
  - Any agent with an active goal gets worked on automatically
  - The daemon finds the best capability for each goal step
  - Results are learned and stored in semantic memory
  - Progress updates are written back to the g")
        # Fallback: ensure we don't crash the system
        os._exit(0)
    
    return True