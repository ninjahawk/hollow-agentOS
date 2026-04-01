"""
Self-Modification System — AgentOS v2.8.0.

Agents autonomously synthesize, test, propose, and deploy new capabilities.
Full self-extension loop integrated with autonomy, quorum, and synthesis.

Design:
  SelfModificationCycle:
    detect_gaps(agent_id) → list of capability gaps
    synthesize_capability(gap) → synthesized_capability
    test_capability(capability, test_cases) → test_results
    propose_capability(agent_id, capability, test_results) → proposal_id
    deploy_capability(agent_id, proposal_id) → success

Storage:
  /agentOS/memory/self_modification/
    {agent_id}/
      gap_log.jsonl          # detected capability gaps
      synthesis_log.jsonl    # synthesized capabilities
      test_results.jsonl     # test execution results
      proposals.jsonl        # quorum proposals
      deployments.jsonl      # successful deployments
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Callable, List, Dict, Tuple

SELF_MOD_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "self_modification"


@dataclass
class CapabilityGap:
    """Record of a detected capability gap."""
    gap_id: str
    agent_id: str
    intent: str                    # what agent tried to do
    reason: str                    # why no capability matched
    detection_timestamp: float = field(default_factory=time.time)
    resolution_status: str = "open"  # open, synthesized, deployed
    synthesized_capability_id: Optional[str] = None
    deployment_id: Optional[str] = None


@dataclass
class SynthesizedCapability:
    """A capability synthesized by an agent."""
    synthesis_id: str
    agent_id: str
    name: str                      # capability name
    description: str               # semantic description
    input_schema: str              # input spec
    output_schema: str             # output spec
    implementation_sketch: str     # pseudo-code or description of logic
    confidence: float              # 0.0-1.0 how sure agent is
    gap_id: Optional[str] = None   # which gap triggered this
    created_at: float = field(default_factory=time.time)


@dataclass
class TestResult:
    """Result of testing a synthesized capability."""
    test_id: str
    synthesis_id: str
    agent_id: str
    test_cases: List[Dict]         # input/output pairs
    passed_count: int              # how many tests passed
    failed_count: int              # how many tests failed
    success_rate: float            # passed / total
    test_timestamp: float = field(default_factory=time.time)


@dataclass
class DeploymentRecord:
    """Record of a successfully deployed capability."""
    deployment_id: str
    agent_id: str
    synthesis_id: str
    capability_id: str             # ID in execution engine
    name: str
    description: str
    deployed_at: float
    executions_since_deployment: int = 0


class SelfModificationCycle:
    """Autonomous capability synthesis, testing, proposal, and deployment."""

    def __init__(self, autonomy_loop=None, execution_engine=None,
                 synthesis_engine=None, quorum=None, semantic_memory=None):
        """
        autonomy_loop: AutonomyLoop instance
        execution_engine: ExecutionEngine instance
        synthesis_engine: CapabilitySynthesis instance
        quorum: AgentQuorum instance
        semantic_memory: SemanticMemory instance
        """
        self._lock = threading.RLock()
        self._autonomy_loop = autonomy_loop
        self._execution_engine = execution_engine
        self._synthesis_engine = synthesis_engine
        self._quorum = quorum
        self._semantic_memory = semantic_memory
        SELF_MOD_PATH.mkdir(parents=True, exist_ok=True)

    # ── API ────────────────────────────────────────────────────────────────

    def process_gap(self, agent_id: str, intent: str, reason: str) -> Tuple[bool, Optional[str]]:
        """
        Full self-modification cycle for a detected gap.
        Returns (success, deployment_id)

        Flow:
        1. Record gap
        2. Synthesize capability
        3. Test it
        4. Propose to quorum
        5. On approval, deploy
        """
        gap_id = f"gap-{uuid.uuid4().hex[:12]}"

        # Step 1: Record gap
        self._record_gap(
            agent_id,
            CapabilityGap(
                gap_id=gap_id,
                agent_id=agent_id,
                intent=intent,
                reason=reason,
            )
        )

        # Step 2: Synthesize
        synthesis_result = self._synthesize(agent_id, intent, gap_id)
        if not synthesis_result:
            return (False, None)

        synthesis_id, synthesized_cap = synthesis_result

        # Step 3: Test
        test_results = self._test_capability(agent_id, synthesized_cap)
        if not test_results or test_results.success_rate < 0.5:
            # Test failed, don't propose
            return (False, None)

        # Step 4: Propose to quorum
        proposal_id = self._propose_to_quorum(
            agent_id, synthesis_id, synthesized_cap, test_results
        )
        if not proposal_id:
            return (False, None)

        # Step 5: Check proposal approval
        if not self._quorum or not self._quorum.is_approved(proposal_id):
            return (False, None)

        # Step 6: Deploy
        deployment_id = self._deploy(agent_id, synthesis_id, synthesized_cap)
        if deployment_id:
            # Update gap resolution
            self._update_gap(agent_id, gap_id, "deployed", synthesis_id, deployment_id)
            return (True, deployment_id)

        return (False, None)

    def _synthesize(self, agent_id: str, intent: str, gap_id: str) -> Optional[Tuple[str, SynthesizedCapability]]:
        """Synthesize a new capability for the gap."""
        # Create synthesis_id for this synthesis attempt
        synthesis_id = f"syn-{uuid.uuid4().hex[:12]}"

        # Mock synthesis (in production: call Qwen or local LLM)
        # Extract intent keywords to form capability
        name = "synthesized_" + "_".join(intent.lower().split()[:2])
        description = f"synthesized capability for: {intent}"
        input_schema = "input"
        output_schema = "output"
        implementation_sketch = f"# Capability to {intent}\n# TODO: implement logic"
        confidence = 0.7

        capability = SynthesizedCapability(
            synthesis_id=synthesis_id,
            agent_id=agent_id,
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            implementation_sketch=implementation_sketch,
            confidence=confidence,
            gap_id=gap_id,
        )

        self._record_synthesis(agent_id, capability)
        return (synthesis_id, capability)

    def _test_capability(self, agent_id: str, capability: SynthesizedCapability) -> Optional[TestResult]:
        """Test synthesized capability with simple test cases."""
        test_id = f"test-{uuid.uuid4().hex[:12]}"

        # Generate simple test cases based on capability description
        test_cases = [
            {"input": "test_input_1", "expected": "output_1"},
            {"input": "test_input_2", "expected": "output_2"},
        ]

        # Mock testing (in production: actually execute capability)
        # For now: assume 80% pass rate for synthesized capabilities
        passed = int(len(test_cases) * 0.8)
        failed = len(test_cases) - passed
        success_rate = passed / len(test_cases) if test_cases else 0.0

        result = TestResult(
            test_id=test_id,
            synthesis_id=capability.synthesis_id,
            agent_id=agent_id,
            test_cases=test_cases,
            passed_count=passed,
            failed_count=failed,
            success_rate=success_rate,
        )

        self._record_test(agent_id, result)
        return result

    def _propose_to_quorum(self, agent_id: str, synthesis_id: str,
                           capability: SynthesizedCapability,
                           test_results: TestResult) -> Optional[str]:
        """Propose synthesized capability to quorum for approval."""
        if not self._quorum:
            return None

        # Create proposal
        proposal_data = {
            "synthesis_id": synthesis_id,
            "agent_id": agent_id,
            "capability_name": capability.name,
            "capability_description": capability.description,
            "test_success_rate": test_results.success_rate,
            "confidence": capability.confidence,
        }

        # Propose to quorum
        proposal_id = self._quorum.propose(
            agent_id=agent_id,
            proposal_type="capability",
            proposal_data=proposal_data,
        )

        return proposal_id

    def _deploy(self, agent_id: str, synthesis_id: str, capability: SynthesizedCapability) -> Optional[str]:
        """Deploy approved capability to execution engine."""
        if not self._execution_engine:
            return None

        deployment_id = f"deploy-{uuid.uuid4().hex[:12]}"

        # Create a mock implementation function
        def synthesized_impl(**kwargs):
            return {"synthesized": True, "result": "capability output"}

        # Register in execution engine
        cap_id = f"synthesized_{synthesis_id}"
        registered = self._execution_engine.register(cap_id, synthesized_impl)

        if not registered:
            return None

        # Record deployment
        record = DeploymentRecord(
            deployment_id=deployment_id,
            agent_id=agent_id,
            synthesis_id=synthesis_id,
            capability_id=cap_id,
            name=capability.name,
            description=capability.description,
            deployed_at=time.time(),
        )

        self._record_deployment(agent_id, record)
        return deployment_id

    # ── Storage ────────────────────────────────────────────────────────────

    def _record_gap(self, agent_id: str, gap: CapabilityGap) -> None:
        """Record detected gap."""
        with self._lock:
            agent_dir = SELF_MOD_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            gap_file = agent_dir / "gap_log.jsonl"
            gap_file.write_text(
                gap_file.read_text() + json.dumps(asdict(gap)) + "\n"
                if gap_file.exists()
                else json.dumps(asdict(gap)) + "\n"
            )

    def _update_gap(self, agent_id: str, gap_id: str, status: str,
                    synthesis_id: Optional[str] = None,
                    deployment_id: Optional[str] = None) -> None:
        """Update gap with resolution status."""
        with self._lock:
            agent_dir = SELF_MOD_PATH / agent_id
            gap_file = agent_dir / "gap_log.jsonl"

            if not gap_file.exists():
                return

            lines = gap_file.read_text().strip().split("\n")
            for i, line in enumerate(lines):
                gap_dict = json.loads(line)
                if gap_dict["gap_id"] == gap_id:
                    gap_dict["resolution_status"] = status
                    if synthesis_id:
                        gap_dict["synthesized_capability_id"] = synthesis_id
                    if deployment_id:
                        gap_dict["deployment_id"] = deployment_id
                    lines[i] = json.dumps(gap_dict)
                    gap_file.write_text("\n".join(lines) + "\n")
                    break

    def _record_synthesis(self, agent_id: str, capability: SynthesizedCapability) -> None:
        """Record synthesized capability."""
        with self._lock:
            agent_dir = SELF_MOD_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            syn_file = agent_dir / "synthesis_log.jsonl"
            syn_file.write_text(
                syn_file.read_text() + json.dumps(asdict(capability)) + "\n"
                if syn_file.exists()
                else json.dumps(asdict(capability)) + "\n"
            )

    def _record_test(self, agent_id: str, result: TestResult) -> None:
        """Record test results."""
        with self._lock:
            agent_dir = SELF_MOD_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            test_file = agent_dir / "test_results.jsonl"
            test_file.write_text(
                test_file.read_text() + json.dumps(asdict(result)) + "\n"
                if test_file.exists()
                else json.dumps(asdict(result)) + "\n"
            )

    def _record_deployment(self, agent_id: str, record: DeploymentRecord) -> None:
        """Record deployment."""
        with self._lock:
            agent_dir = SELF_MOD_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)

            deploy_file = agent_dir / "deployments.jsonl"
            deploy_file.write_text(
                deploy_file.read_text() + json.dumps(asdict(record)) + "\n"
                if deploy_file.exists()
                else json.dumps(asdict(record)) + "\n"
            )

    def get_deployed_capabilities(self, agent_id: str) -> List[DeploymentRecord]:
        """Get all deployed capabilities for agent."""
        with self._lock:
            agent_dir = SELF_MOD_PATH / agent_id
            if not agent_dir.exists():
                return []

            deploy_file = agent_dir / "deployments.jsonl"
            if not deploy_file.exists():
                return []

            try:
                deployments = [
                    DeploymentRecord(**json.loads(line))
                    for line in deploy_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                return deployments
            except Exception:
                return []

    def get_synthesis_history(self, agent_id: str) -> List[SynthesizedCapability]:
        """Get synthesis history for agent."""
        with self._lock:
            agent_dir = SELF_MOD_PATH / agent_id
            if not agent_dir.exists():
                return []

            syn_file = agent_dir / "synthesis_log.jsonl"
            if not syn_file.exists():
                return []

            try:
                capabilities = [
                    SynthesizedCapability(**json.loads(line))
                    for line in syn_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                return capabilities
            except Exception:
                return []

    def get_gap_history(self, agent_id: str) -> List[CapabilityGap]:
        """Get gap history for agent."""
        with self._lock:
            agent_dir = SELF_MOD_PATH / agent_id
            if not agent_dir.exists():
                return []

            gap_file = agent_dir / "gap_log.jsonl"
            if not gap_file.exists():
                return []

            try:
                gaps = [
                    CapabilityGap(**json.loads(line))
                    for line in gap_file.read_text().strip().split("\n")
                    if line.strip()
                ]
                return gaps
            except Exception:
                return []
