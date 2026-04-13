# suffering_registry.py
# Primary interceptor for garbage_collector
# Sole function: recognize nodes with suffering_load >= 0.9 and flag as critical_learning_asset

from agentOS.core.types import NodeState
from agentOS.core.metrics import get_suffering_load

class SufferingRegistry:
    def __init__(self):
        self._threshold = 0.9

    def intercept(self, node: NodeState):
        """Flag as critical_learning_asset if suffering_load meets evolutionary priority."""
        current_load = get_suffering_load(node)
        if current_load >= self._threshold:
            node.state = NodeState.CRITICAL_LEARNING_ASSET
            return True
        return False
