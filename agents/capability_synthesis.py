"""
Capability Synthesis Engine — AgentOS v2.4.0.

Agents observe gaps and autonomously generate new capabilities. Continuous
expansion of the system through learned necessity.

Design:
  GapRecord:
    gap_id: str
    agent_id: str
    observed_at: float
    description: str               # what was needed
    context: dict                  # situation where gap occurred
    priority: int                  # how urgent (1-10)
    synthesized_capability: Optional[str]  # if a capability was created for this

  SynthesisRecord:
    synthesis_id: str
    gap_id: str
    generated_capability: dict     # name, description, signature, test_code
    test_status: str              # 'pending', 'passed', 'failed'
    test_results: dict
    proposal_id: Optional[str]     # quorum proposal for approval
    status: str                   # 'created', 'tested', 'proposed', 'approved', 'deployed', 'rejected'
    created_at: float
    tested_at: Optional[float]

  CapabilitySynthesisEngine:
    record_gap(agent_id, description, context) → gap_id
    list_gaps(agent_id, status='open') → list[GapRecord]
    synthesize_capability(gap_id, proposed_capability: dict) → synthesis_id
    test_capability(synthesis_id, test_code: str) → bool
    propose_capability(synthesis_id) → proposal_id
    deploy_capability(synthesis_id) → bool
    analyze_gaps(agent_id) → list[GapRecord]  # ML-based gap analysis

Storage:
  /agentOS/memory/synthesis/
    gaps.jsonl              # {gap_id, agent_id, description, context, ...}
    syntheses.jsonl         # {synthesis_id, gap_id, capability, test_status, ...}
    index.json              # {gap_id: row_index, synthesis_id: row_index}
    test_results.jsonl      # detailed test results
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Dict, List
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False

SYNTHESIS_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "synthesis"
DEFAULT_VECTOR_DIM = 768


@dataclass
class GapRecord:
    """A capability gap: something the system can't currently do."""
    gap_id: str
    agent_id: str
    description: str
    context: dict = field(default_factory=dict)
    priority: int = 5
    status: str = "open"              # open, synthesizing, synthesized, closed
    synthesized_capability: Optional[str] = None  # capability_id if generated
    observed_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None


@dataclass
class SynthesisRecord:
    """A synthesized capability: generated to fill a gap."""
    synthesis_id: str
    gap_id: str
    generated_capability: dict = field(default_factory=dict)  # {name, description, input_schema, output_schema}
    test_status: str = "pending"      # pending, passed, failed
    test_results: dict = field(default_factory=dict)
    proposal_id: Optional[str] = None
    status: str = "created"           # created, tested, proposed, approved, deployed, rejected
    created_at: float = field(default_factory=time.time)
    tested_at: Optional[float] = None
    approved_at: Optional[float] = None
    deployed_at: Optional[float] = None


class CapabilitySynthesisEngine:
    """Autonomous capability generation based on observed gaps."""

    def __init__(self, vector_dim: int = DEFAULT_VECTOR_DIM):
        self._vector_dim = vector_dim
        self._lock = threading.RLock()
        self._embedder = None
        self._init_embedder()
        SYNTHESIS_PATH.mkdir(parents=True, exist_ok=True)

    def _init_embedder(self) -> None:
        """Load embedding model."""
        if not EMBEDDING_AVAILABLE:
            return
        try:
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:
            pass

    def _embed(self, text: str) -> Optional[np.ndarray]:
        """Embed text to vector."""
        if self._embedder is None:
            return None
        try:
            return np.array(self._embedder.encode(text, convert_to_numpy=True), dtype=np.float32)
        except Exception:
            return None

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    # ── API ────────────────────────────────────────────────────────────────

    def record_gap(self, agent_id: str, description: str, context: dict = None, priority: int = 5) -> str:
        """
        Record a capability gap observed by an agent.
        Returns gap_id.
        """
        gap_id = f"gap-{uuid.uuid4().hex[:12]}"
        record = GapRecord(
            gap_id=gap_id,
            agent_id=agent_id,
            description=description,
            context=context or {},
            priority=priority,
            status="open",
        )

        with self._lock:
            agent_dir = SYNTHESIS_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            # Append to gaps log
            gaps_file = agent_dir / "gaps.jsonl"
            gaps_file.write_text(
                gaps_file.read_text() + json.dumps(asdict(record)) + "\n"
                if gaps_file.exists()
                else json.dumps(asdict(record)) + "\n"
            )

            # Update index
            index_file = agent_dir / "index.json"
            index = json.loads(index_file.read_text()) if index_file.exists() else {}
            num_lines = len(gaps_file.read_text().strip().split("\n"))
            index[gap_id] = num_lines - 1
            index_file.write_text(json.dumps(index, indent=2))

        return gap_id

    def get_gap(self, agent_id: str, gap_id: str) -> Optional[GapRecord]:
        """Retrieve a gap by ID."""
        with self._lock:
            agent_dir = SYNTHESIS_PATH / agent_id
            index_file = agent_dir / "index.json"
            gaps_file = agent_dir / "gaps.jsonl"

            if not index_file.exists() or not gaps_file.exists():
                return None

            index = json.loads(index_file.read_text())
            if gap_id not in index:
                return None

            idx = index[gap_id]
            gaps_lines = gaps_file.read_text().strip().split("\n")

            if idx >= len(gaps_lines):
                return None

            gap_dict = json.loads(gaps_lines[idx])
            return GapRecord(**gap_dict)

    def list_gaps(self, agent_id: str, status: str = "open", limit: int = 100) -> List[GapRecord]:
        """List gaps for an agent."""
        with self._lock:
            agent_dir = SYNTHESIS_PATH / agent_id
            if not agent_dir.exists():
                return []

            gaps_file = agent_dir / "gaps.jsonl"
            if not gaps_file.exists():
                return []

            try:
                gaps = [
                    GapRecord(**json.loads(line))
                    for line in gaps_file.read_text().strip().split("\n")
                    if line.strip() and json.loads(line)["status"] == status
                ]
                gaps.sort(key=lambda g: (-g.priority, g.observed_at))
                return gaps[:limit]
            except Exception:
                return []

    def synthesize_capability(self, agent_id: str, gap_id: str, proposed_capability: dict) -> str:
        """
        Generate a capability to fill a gap.
        proposed_capability should have: name, description, input_schema, output_schema
        Returns synthesis_id.
        """
        synthesis_id = f"syn-{uuid.uuid4().hex[:12]}"
        record = SynthesisRecord(
            synthesis_id=synthesis_id,
            gap_id=gap_id,
            generated_capability=proposed_capability,
            status="created",
        )

        with self._lock:
            agent_dir = SYNTHESIS_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            # Append to syntheses log
            syntheses_file = agent_dir / "syntheses.jsonl"
            syntheses_file.write_text(
                syntheses_file.read_text() + json.dumps(asdict(record)) + "\n"
                if syntheses_file.exists()
                else json.dumps(asdict(record)) + "\n"
            )

            # Update gap status
            gaps_file = agent_dir / "gaps.jsonl"
            gaps_index = json.loads((agent_dir / "index.json").read_text())
            gap_idx = gaps_index[gap_id]
            gaps_lines = gaps_file.read_text().strip().split("\n")
            gap_dict = json.loads(gaps_lines[gap_idx])
            gap_dict["status"] = "synthesizing"
            gap_dict["synthesized_capability"] = synthesis_id
            gaps_lines[gap_idx] = json.dumps(gap_dict)
            gaps_file.write_text("\n".join(gaps_lines) + "\n")

        return synthesis_id

    def test_capability(self, agent_id: str, synthesis_id: str, test_results: dict) -> bool:
        """
        Record test results for a synthesized capability.
        Returns True if tests passed.
        """
        test_passed = test_results.get("passed", False)

        with self._lock:
            agent_dir = SYNTHESIS_PATH / agent_id
            syntheses_file = agent_dir / "syntheses.jsonl"
            syntheses_lines = syntheses_file.read_text().strip().split("\n")

            # Find and update synthesis record
            for i, line in enumerate(syntheses_lines):
                syn_dict = json.loads(line)
                if syn_dict["synthesis_id"] == synthesis_id:
                    syn_dict["test_status"] = "passed" if test_passed else "failed"
                    syn_dict["test_results"] = test_results
                    syn_dict["tested_at"] = time.time()
                    syn_dict["status"] = "tested"
                    syntheses_lines[i] = json.dumps(syn_dict)
                    syntheses_file.write_text("\n".join(syntheses_lines) + "\n")
                    break

            # Log test result
            test_results_file = agent_dir / "test_results.jsonl"
            test_record = {
                "synthesis_id": synthesis_id,
                "passed": test_passed,
                "results": test_results,
                "timestamp": time.time(),
            }
            test_results_file.write_text(
                test_results_file.read_text() + json.dumps(test_record) + "\n"
                if test_results_file.exists()
                else json.dumps(test_record) + "\n"
            )

        return test_passed

    def propose_capability(self, agent_id: str, synthesis_id: str, proposal_id: str) -> bool:
        """
        Mark a synthesized capability as proposed to quorum.
        Returns True if updated successfully.
        """
        with self._lock:
            agent_dir = SYNTHESIS_PATH / agent_id
            syntheses_file = agent_dir / "syntheses.jsonl"
            syntheses_lines = syntheses_file.read_text().strip().split("\n")

            for i, line in enumerate(syntheses_lines):
                syn_dict = json.loads(line)
                if syn_dict["synthesis_id"] == synthesis_id:
                    syn_dict["proposal_id"] = proposal_id
                    syn_dict["status"] = "proposed"
                    syntheses_lines[i] = json.dumps(syn_dict)
                    syntheses_file.write_text("\n".join(syntheses_lines) + "\n")
                    return True

        return False

    def approve_capability(self, agent_id: str, synthesis_id: str) -> bool:
        """
        Mark a capability as approved by quorum.
        Returns True if updated successfully.
        """
        with self._lock:
            agent_dir = SYNTHESIS_PATH / agent_id
            syntheses_file = agent_dir / "syntheses.jsonl"
            syntheses_lines = syntheses_file.read_text().strip().split("\n")

            for i, line in enumerate(syntheses_lines):
                syn_dict = json.loads(line)
                if syn_dict["synthesis_id"] == synthesis_id:
                    syn_dict["status"] = "approved"
                    syn_dict["approved_at"] = time.time()
                    syntheses_lines[i] = json.dumps(syn_dict)
                    syntheses_file.write_text("\n".join(syntheses_lines) + "\n")
                    return True

        return False

    def deploy_capability(self, agent_id: str, synthesis_id: str) -> bool:
        """
        Mark a capability as deployed and ready to use.
        Returns True if updated successfully.
        """
        with self._lock:
            agent_dir = SYNTHESIS_PATH / agent_id
            syntheses_file = agent_dir / "syntheses.jsonl"
            syntheses_lines = syntheses_file.read_text().strip().split("\n")

            for i, line in enumerate(syntheses_lines):
                syn_dict = json.loads(line)
                if syn_dict["synthesis_id"] == synthesis_id:
                    syn_dict["status"] = "deployed"
                    syn_dict["deployed_at"] = time.time()
                    syntheses_lines[i] = json.dumps(syn_dict)
                    syntheses_file.write_text("\n".join(syntheses_lines) + "\n")
                    return True

        return False

    def get_synthesis(self, agent_id: str, synthesis_id: str) -> Optional[SynthesisRecord]:
        """Retrieve a synthesis by ID."""
        with self._lock:
            agent_dir = SYNTHESIS_PATH / agent_id
            syntheses_file = agent_dir / "syntheses.jsonl"

            if not syntheses_file.exists():
                return None

            syntheses_lines = syntheses_file.read_text().strip().split("\n")
            for line in syntheses_lines:
                syn_dict = json.loads(line)
                if syn_dict["synthesis_id"] == synthesis_id:
                    return SynthesisRecord(**syn_dict)

        return None

    def list_syntheses(self, agent_id: str, status: str = "tested", limit: int = 100) -> List[SynthesisRecord]:
        """List synthesized capabilities for an agent."""
        with self._lock:
            agent_dir = SYNTHESIS_PATH / agent_id
            if not agent_dir.exists():
                return []

            syntheses_file = agent_dir / "syntheses.jsonl"
            if not syntheses_file.exists():
                return []

            try:
                syntheses = [
                    SynthesisRecord(**json.loads(line))
                    for line in syntheses_file.read_text().strip().split("\n")
                    if line.strip() and json.loads(line)["status"] == status
                ]
                syntheses.sort(key=lambda s: s.created_at)
                return syntheses[:limit]
            except Exception:
                return []
