    params: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    def __init__(self):
    def register(self, capability_id: str, implementation: Callable,
    def execute(self, agent_id: str, capability_id: str, params: dict = None) -> Tuple[Optional[dict], str]:
    def _call_with_timeout(self, func: Callable, params: dict, timeout_ms: int) -> Any:
    def _log_execution(self, agent_id: str, context: ExecutionContext) -> None:
    def get_execution_history(self, agent_id: str, limit: int = 50) -> list:
    def disable_capability(self, capability_id: str) -> bool:
    def enable_capability(self, capability_id: str) -> bool:
    def list_registered(self) -> list:
    def get_stats(self, agent_id: str) -> dict: