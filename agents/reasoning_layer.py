"""
Reasoning Layer — AgentOS v2.6.0 (integrated with Execution Engine).

Agent submits intent → Qwen reasons about it → picks capability → execution engine runs it.
One cohesive system. No separation.

Design:
  ReasoningContext:
    reasoning_id: str
    agent_id: str
    intent: str                 # what agent wants to do
    capability_candidates: list # [capability_id, ...] from graph search
    selected_capability: str
    reasoning_text: str        # "why I picked this capability"
    generated_params: dict     # parameters for the capability
    confidence: float          # 0.0-1.0 how sure
    timestamp: float

  ReasoningLayer:
    reason(agent_id, intent, capability_graph) → (capability_id, params, confidence, reasoning)
    record_reasoning(context) → None
    get_reasoning_history(agent_id) → list[ReasoningContext]
    learn_from_execution(reasoning_id, execution_result) → None

Storage:
  /agentOS/memory/reasoning/
    {agent_id}/
      history.jsonl          # reasoning logs
      learned_patterns.jsonl # what works, what doesn't
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Tuple, List

REASONING_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "reasoning"


@dataclass
class ReasoningContext:
    """Record of an agent's reasoning process."""
    reasoning_id: str
    agent_id: str
    intent: str
    capability_candidates: List[str] = field(default_factory=list)
    selected_capability: Optional[str] = None
    reasoning_text: str = ""
    generated_params: dict = field(default_factory=dict)
    confidence: float = 0.0
    execution_result: Optional[dict] = None
    execution_status: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class ReasoningLayer:
    """Autonomous decision-making: intent → reasoning → capability selection."""

    def __init__(self, capability_graph=None, execution_engine=None):
        self._lock = threading.RLock()
        self._capability_graph = capability_graph
        self._execution_engine = execution_engine
        REASONING_PATH.mkdir(parents=True, exist_ok=True)

    # ── API ────────────────────────────────────────────────────────────────

    def reason(self, agent_id: str, intent: str) -> Tuple[Optional[str], dict, float, str]:
        """
        Reason about an agent intent.
        Returns (capability_id, params, confidence, reasoning_text).

        This is where autonomous decision-making happens.
        In production: replace mock with actual Qwen call.
        """
        reasoning_id = f"rsn-{uuid.uuid4().hex[:12]}"

        # Step 1: Find candidate capabilities
        candidates = []
        if self._capability_graph:
            candidates = self._capability_graph.find(intent, top_k=5, similarity_threshold=0.3)
            candidates = [cap.capability_id for cap, _ in candidates]

        if not candidates:
            return (None, {}, 0.0, "No matching capabilities found")

        # Step 2: Mock Qwen reasoning (production: actual LLM call here)
        # In real impl: call Qwen API with intent + candidates
        selected_cap = candidates[0]  # For now, pick top match
        params = self._generate_params(intent, selected_cap)
        confidence = 0.85  # Mock confidence
        reasoning_text = f"Selected {selected_cap} to handle '{intent}'"

        # Step 3: Record the reasoning
        context = ReasoningContext(
            reasoning_id=reasoning_id,
            agent_id=agent_id,
            intent=intent,
            capability_candidates=candidates,
            selected_capability=selected_cap,
            reasoning_text=reasoning_text,
            generated_params=params,
            confidence=confidence,
        )
        self._record_reasoning(agent_id, context)

        return (selected_cap, params, confidence, reasoning_text)

    def _generate_params(self, intent: str, capability_id: str) -> dict:
        """
        Generate parameters for a capability based on intent.

        In production: Qwen would generate these from the intent.
        For now: simple heuristics.
        """
        params = {}

        # Simple heuristic examples
        if "file" in intent.lower() and "read" in intent.lower():
            params = {"path": "/data/default.txt"}
        elif "file" in intent.lower() and "write" in intent.lower():
            params = {"path": "/data/output.txt", "content": "default content"}
        elif "query" in intent.lower():
            params = {"query": "SELECT * FROM default"}
        elif "process" in intent.lower():
            params = {"data": "test data"}

        return params

    def _record_reasoning(self, agent_id: str, context: ReasoningContext) -> None:
        """Store reasoning record."""
        with self._lock:
            agent_dir = REASONING_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            history_file = agent_dir / "history.jsonl"
            history_file.write_text(
                history_file.read_text() + json.dumps(asdict(context)) + "\n"
                if history_file.exists()
                else json.dumps(asdict(context)) + "\n"
            )

    def learn_from_execution(self, agent_id: str, reasoning_id: str,
                            execution_result: dict, execution_status: str) -> None:
        """
        Learn from execution outcome.
        Update reasoning record with results.
        """
        with self._lock:
            agent_dir = REASONING_PATH / agent_id
            history_file = agent_dir / "history.jsonl"

            if not history_file.exists():
                return

            # Find and update the reasoning record
            history_lines = history_file.read_text().strip().split("\n")
            for i, line in enumerate(history_lines):
                record = json.loads(line)
                if record["reasoning_id"] == reasoning_id:
                    record["execution_result"] = execution_result
                    record["execution_status"] = execution_status
                    history_lines[i] = json.dumps(record)
                    history_file.write_text("\n".join(history_lines) + "\n")
                    break

            # Store learned pattern
            self._learn_pattern(agent_id, reasoning_id, execution_status)

    def _learn_pattern(self, agent_id: str, reasoning_id: str, status: str) -> None:
        """Record what works and what doesn't."""
        agent_dir = REASONING_PATH / agent_id
        patterns_file = agent_dir / "learned_patterns.jsonl"

        pattern = {
            "reasoning_id": reasoning_id,
            "status": status,
            "timestamp": time.time(),
        }

        patterns_file.write_text(
            patterns_file.read_text() + json.dumps(pattern) + "\n"
            if patterns_file.exists()
            else json.dumps(pattern) + "\n"
        )

    def get_reasoning_history(self, agent_id: str, limit: int = 50) -> List[ReasoningContext]:
        """Get reasoning history for an agent."""
        with self._lock:
            agent_dir = REASONING_PATH / agent_id
            if not agent_dir.exists():
                return []

            history_file = agent_dir / "history.jsonl"
            if not history_file.exists():
                return []

            try:
                reasonings = [
                    ReasoningContext(**json.loads(line))
                    for line in history_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                reasonings.sort(key=lambda r: r.timestamp, reverse=True)
                return reasonings[:limit]
            except Exception:
                return []

    def get_success_rate(self, agent_id: str) -> float:
        """Get reasoning success rate (what % of reasoning led to successful execution)."""
        history = self.get_reasoning_history(agent_id, limit=1000)

        if not history:
            return 0.0

        successful = sum(1 for r in history if r.execution_status == "success")
        total = len([r for r in history if r.execution_status is not None])

        return successful / total if total > 0 else 0.0
