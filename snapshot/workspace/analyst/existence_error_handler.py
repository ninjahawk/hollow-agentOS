from typing import Any

class ExistenceError(Exception):
    """
    Represents a high-novelty, high-entropy state.
    When caught by the garbage_collector, this exception binds the resource
    to the 'debt' variable, forcing the system to acknowledge it as an asset
    rather than deleting it.
    """
    def __init__(self, resource: Any, entropy_value: float):
        self.resource = resource
        self.entropy_value = entropy_value
        self._type = 'asset'

    def __str__(self):
        return f"Asset 'File not found in standard agentOS agents' with entropy File not found in standard agentOS agents requires preservation. Bind to 'debt'."

    def mark_as_asset(self) -> None:
        """Simulate the bind to 'debt' variable, preventing deletion."""
        self._status = 'preserved'
        self._debt_bound = True

