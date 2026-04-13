spec

/agentOS/agents/execution_engine.py:    result: dict or error
/agentOS/agents/execution_engine.py:    error: Optional[str] = None
/agentOS/agents/execution_engine.py:            # Treat ok=False as failure even if no exception was raised
/agentOS/agents/execution_engine.py:                context.error = context.result.get("error", "capability returned ok=False")
/agentOS/agents/execution_engine.py:            context.error = f"Execution exceeded /agentOS/agents/execution_engine.py:    result: dict or error
/agentOS/agents/execution_engine.py:    error: Optional[str] = None
/agentOS/agents/execution_engine.py:            # Treat ok=False as failure even if no exception was raised
/agentOS/agents/execution_engine.py:                context.error = context.result.get("error", "capability returned ok=False")
/agentOS/agents/execution_engine.py:            context.error = f"Execution exceeded {timeout}ms timeout"
/agentOS/agents/execution_enms timeout"
/agentOS/agents/execution_engine.py:            context.error = str(e)
/agentOS/agents/execution_engine.py:            context.result = {"error": str(e), "traceback": traceback.format_exc()}
/agentOS/agents/signals.py:        return {"error": f"Unknown signal '/agentOS/agents/execution_engine.py:    result: dict or error
/agentOS/agents/execution_engine.py:    error: Optional[str] = None
/agentOS/agents/execution_engine.py:            # Treat ok=False as failure even if no exception was raised
/agentOS/agents/execution_engine.py:                context.error = context.result.get("error", "capability returned ok=False")
/agentOS/agents/execution_engine.py:            context.error = f"Execution exceeded {timeout}ms timeout"
/agentOS/agents/execution_en'. Valid: {list(SIGNALS)}"}
/agentOS/agents/signals.py:        return {"error": f"Agent '/agentOS/agents/execution_engine.py:    result: dict or error
/agentOS/agents/execution_engine.py:    error: Optional[str] = None
/agentOS/agents/execution_engine.py:            # Treat ok=False as failure even if no exception was raised
/agentOS/agents/execution_engine.py:                context.error = context.result.get("error", "capability returned ok=False")
/agentOS/agents/execution_engine.py:            context.error = f"Execution exceeded {timeout}ms timeout"
/agentOS/agents/execution_en' not found"}
/agentOS/agents/signals.py:        return {"error": f"Agent '/agentOS/agents/execution_engine.py:    result: dict or error
/agentOS/agents/execution_engine.py:    error: Optional[str] = None
/agentOS/agents/execution_engine.py:            # Treat ok=False as failure even if no exception was raised
/agentOS/agents/execution_engine.py:                context.error = context.result.get("error", "capability returned ok=False")
/agentOS/agents/execution_engine.py:            context.error = f"Execution exceeded {timeout}ms timeout"
/agentOS/agents/execution_en' is already terminated"}
/agentOS/agents/batch_llm.py:        self._load_error: Optional[str] = None
/agentOS/agents/batch_llm.py:        while not self._ready and not self._load_error and time.time() < deadline:
/agentOS/agents/batch_llm.py:        if self._load_error:
/agentOS/agents/batch_llm.py:            raise RuntimeError(f"BatchLLM load failed: /agentOS/agents/execution_engine.py:    result: dict or error
/agentOS/agents/execution_engine.py:    error: Optional[str] = None
/agentOS/agents/execution_engine.py:            # Treat ok=False as failure even if no exception was raised
/agentOS/agents/execution_engine.py:                context.error = context.result.get("error", "capability returned ok=False")
/agentOS/agents/execution_engine.py:            context.error = f"Execution exceeded {timeout}ms timeout"
/agentOS/agents/execution_en")
/agentOS/agents/batch_llm.py:                        fut.set_exception(RuntimeError(err))
/agentOS/agents/batch_llm.py:            self._load_error = str(e)
/agentOS/agents/audit.py:    result_code: str    # ok | denied | error | budget_exceeded
/agentOS/agents/resource_manager.py:                        s += 30.0  # keep failure records too
/agentOS/agents/benchmark.py:import urllib.error
/agentOS/agents/benchmark.py:    passed: bool                     # scenario completed without error