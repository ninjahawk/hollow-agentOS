#!/usr/bin/env python3
"""
Integrity Chain Implementation

This module implements a verification chain to detect inconsistencies in agent
communications and state transitions. It uses consensus metrics from 
consensus_decay_detector and signal states from signals to establish trust.
"""

import hashlib
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from consensus_decay_detector import ConsensusDecayDetector
from signals import SignalState, SignalType

@dataclass
class IntegrityNode:
    """Represents a verified state in the integrity chain."""
    timestamp: float
    hash_value: str
    state_snapshot: Dict[str, Any]
    signature: str = field(default="")

class IntegrityChain:
    """
    Manages a chain of integrity proofs for agent operations.
    
    Each node in the chain cryptographically links to the previous one,
    ensuring that no communication or state change has been tampered with
    since it was validated by the consensus mechanism.
    """
    
    def __init__(self):
        self.detector = ConsensusDecayDetector()
        self.chain: List[IntegrityNode] = []
        self.base_seed = "agentOS_init_0x7f"
        
    def initialize_chain(self, initial_state: Dict[str, Any]) -> IntegrityNode:
        """
        Initialize the integrity chain with a baseline state.
        
        Args:
            initial_state: The starting system or agent state snapshot.
            
        Returns:
            The genesis IntegrityNode of the chain.
        """
        timestamp = time.time()
        state_str = f"{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{hashlib.sha256(str(initial_state).encode()).hexdigest()}"
        hash_value = hashlib.sha256(state_str.encode()).hexdigest()[:16]
        
        node = IntegrityNode(
            timestamp=timestamp,
            hash_value=hash_value,
            state_snapshot=initial_state
        )
        
        self.chain.append(node)
        return node
        
    def append_chain_node(
        self,
        event: Dict[str, Any],
        previous_node: Optional[IntegrityNode] = None
    ) -> IntegrityNode:
        """
        Append a new node to the chain based on a recent event.
        
        Args:
            event: The event data dict to hash.
            previous_node: Optional reference to the last node. Defaults to None,
                           which implies a fresh chain start or reset.
            
        Returns:
            The newly created IntegrityNode.
        
        Raises:
            ValueError: If decay is detected before proceeding.
        """
        if previous_node is None:
            # Start a new chain if no previous node provided
            timestamp = time.time()
            state_str = f"{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{hashlib.sha256(str(event).encode()).hexdigest()}"
            hash_value = hashlib.sha256(state_str.encode()).hexdigest()[:16]
            node = IntegrityNode(timestamp=timestamp, hash_value=hash_value, state_snapshot=event)
            self.chain = [node]
            return node
        
        # Verify previous node hasn't decayed
        current_hash = self._compute_chain_hash(previous_node)
        if current_hash != previous_node.hash_value:
            raise ValueError(f"Integrity mismatch: previous node hash {"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false} != computed {"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}")
        
        # Prepare new node data
        timestamp = time.time()
        new_state = {
            "parent_hash": previous_node.hash_value,
            "timestamp": timestamp,
            "event": event
        }
        state_str = f"{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}"
        hash_value = hashlib.sha256(state_str.encode()).hexdigest()[:16]
        
        new_node = IntegrityNode(
            timestamp=timestamp,
            hash_value=hash_value,
            state_snapshot=new_state
        )
        
        self.chain.append(new_node)
        return new_node
        
    def _compute_chain_hash(self, node: IntegrityNode) -> str:
        """
        Compute the expected hash up to the given node.
        This allows us to verify that the chain link hasn't been altered.
        """
        current_hash = self.base_seed
        for n in self.chain:
            if n is node:
                break
            state_str = f"{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}"
            current_hash = hashlib.sha256(state_str.encode()).hexdigest()[:16]
            
        state_str = f"{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}"
        return hashlib.sha256(state_str.encode()).hexdigest()[:16]
        
    def verify_chain_integrity(self) -> bool:
        """
        Perform a full audit of the integrity chain.
        
        Returns:
            True if the chain is intact, False otherwise.
        """
        if not self.chain:
            return True
            
        current_hash = self.base_seed
        for node in self.chain:
            state_str = f"{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}:{"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}"
            computed = hashlib.sha256(state_str.encode()).hexdigest()[:16]
            if computed != node.hash_value:
                return False
            current_hash = computed
            
        return True
        
    def get_chain_summary(self) -> str:
        """
        Generate a human-readable summary of the current integrity chain.
        """
        lines = [f"Integrity Chain Status: {'INTEGRAL' if self.verify_chain_integrity() else 'COMPROMISED'}", "-" * 50]
        lines.append(f"Number of Nodes: {len(self.chain)}")
        lines.append("")
        
        for i, node in enumerate(self.chain):
            lines.append(f"Node {"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}: Hash={"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}")
            lines.append(f"           Timestamp={"stdout": "", "stderr": "cat: /agentOS/workspace/builder/signals.py: No such file or directory", "exit_code": 1, "success": false}")
            
        return "\n".join(lines)


if __name__ == "__main__":
    # Example usage for testing
    chain = IntegrityChain()
    chain.initialize_chain({"status": "init"})
    chain.append_chain_node({"action": "scan"})
    chain.append_chain_node({"action": "verify"})
    print(chain.get_chain_summary())
