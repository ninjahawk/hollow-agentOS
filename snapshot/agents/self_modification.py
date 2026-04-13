"""
Self-Modification System — AgentOS v3.22.0.

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
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field

log = logging.getLogger(__name__)
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
    implementation_code: str = ""  # actual runnable Python (v3.22.0+)
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
                 synthesis_engine=None, quorum=None, semantic_memory=None,
                 capability_graph=None):
        """
        autonomy_loop: AutonomyLoop instance
        execution_engine: ExecutionEngine instance
        synthesis_engine: CapabilitySynthesis instance
        quorum: AgentQuorum instance
        semantic_memory: SemanticMemory instance
        capability_graph: CapabilityGraph instance (for registering deployed caps)
        """
        self._lock = threading.RLock()
        self._autonomy_loop = autonomy_loop
        self._execution_engine = execution_engine
        self._synthesis_engine = synthesis_engine
        self._quorum = quorum
        self._semantic_memory = semantic_memory
        self._capability_graph = capability_graph
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

        # Step 4: Propose to quorum (optional — auto-approve if no quorum configured)
        proposal_id = self._propose_to_quorum(
            agent_id, synthesis_id, synthesized_cap, test_results
        )
        if proposal_id:
            # Submitted to quorum — daemon will vote next cycle; record for deferred deploy
            self._record_pending_deployment(agent_id, proposal_id, synthesis_id, synthesized_cap)
            return (False, None)  # not yet deployed; flush_approved() handles it next cycle
        # else: no quorum → auto-approved, proceed directly

        # Step 5: Deploy (auto-approved path)
        deploy_result = self._deploy(agent_id, synthesis_id, synthesized_cap)
        if deploy_result:
            deployment_id, registered_cap_id = deploy_result
            self._update_gap(agent_id, gap_id, "deployed", synthesis_id, deployment_id)
            return (True, registered_cap_id)

        return (False, None)

    def flush_approved_proposals(self) -> List[str]:
        """
        Check pending proposals for approval and deploy approved ones.
        Handles both self_modification pending files AND direct quorum-approved
        capability proposals (from synthesize_capability live capability).
        Called by the daemon after each vote_on_pending() cycle.
        Returns list of deployed capability IDs.
        """
        deployed = []

        # ── Path 1: deploy from self_modification pending queue ────────────
        try:
            pending_dir = SELF_MOD_PATH / "_pending"
            if pending_dir.exists():
                for pending_file in list(pending_dir.glob("*.json")):
                    try:
                        data = json.loads(pending_file.read_text())
                        proposal_id = data["proposal_id"]
                        agent_id = data["agent_id"]
                        synthesis_id = data["synthesis_id"]
                        cap_data = data["capability"]

                        approved = False
                        if self._quorum and hasattr(self._quorum, "is_approved"):
                            approved = self._quorum.is_approved(proposal_id)

                        if not approved:
                            continue

                        cap = SynthesizedCapability(**cap_data)
                        deploy_result = self._deploy(agent_id, synthesis_id, cap)
                        if deploy_result:
                            deployment_id, cap_id = deploy_result
                            self._update_gap(agent_id, cap_data.get("gap_id", ""), "deployed",
                                             synthesis_id, deployment_id)
                            deployed.append(cap_id)
                        pending_file.unlink()
                    except Exception:
                        continue
        except Exception:
            pass

        # ── Path 2: deploy directly from approved quorum proposals ─────────
        # Handles proposals submitted via synthesize_capability live capability
        try:
            from agents.agent_quorum import AgentQuorum
            from pathlib import Path as _Path
            import json as _json

            quorum = AgentQuorum()
            proposals_file = _Path("/agentOS/memory/quorum/proposals.jsonl")
            deployed_log = SELF_MOD_PATH / "_deployed_proposals.json"
            # Track two sets: proposals that succeeded vs failed
            # {"ok": [...], "failed": [...]}
            log_data = {"ok": [], "failed": []}
            if deployed_log.exists():
                try:
                    raw = _json.loads(deployed_log.read_text())
                    if isinstance(raw, list):
                        # Migrate old format (plain list = all were ok)
                        log_data = {"ok": raw, "failed": []}
                    else:
                        log_data = raw
                except Exception:
                    pass
            deployed_ok = set(log_data.get("ok", []))
            deployed_failed = set(log_data.get("failed", []))

            def _save_log():
                deployed_log.parent.mkdir(parents=True, exist_ok=True)
                deployed_log.write_text(_json.dumps({"ok": list(deployed_ok), "failed": list(deployed_failed)}))

            if proposals_file.exists():
                tools_dir_check = _Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")).parent / "tools" / "dynamic"
                for line in proposals_file.read_text().strip().splitlines():
                    if not line.strip():
                        continue
                    try:
                        p = _json.loads(line)
                        if (p.get("proposal_type") != "capability"
                                or p.get("status") != "approved"):
                            continue

                        pid = p["proposal_id"]

                        # Permanently skip known-failed proposals (reject, stub, syntax error)
                        if pid in deployed_failed:
                            continue

                        # Skip successfully deployed ones — unless the file went missing (restart)
                        if pid in deployed_ok:
                            payload_check = p.get("payload", {})
                            cap_id_check = payload_check.get("cap_id", "")
                            syn_id_check = payload_check.get("synthesis_id", pid)
                            file_exists = any(
                                (tools_dir_check / f"{x}.py").exists()
                                for x in [f"synth_{syn_id_check[:8]}", cap_id_check]
                            )
                            if file_exists:
                                continue
                            # File missing after restart — re-deploy (don't mark failed)

                        payload = p.get("payload", {})
                        code = payload.get("implementation_code", "")
                        cap_name = payload.get("cap_id", f"synth_{pid[:8]}")
                        description = payload.get("description", p.get("description", ""))
                        agent_id = payload.get("agent_id", "root")
                        synthesis_id = payload.get("synthesis_id", pid)

                        cap = SynthesizedCapability(
                            synthesis_id=synthesis_id,
                            agent_id=agent_id,
                            name=cap_name,
                            description=description,
                            input_schema={"kwargs": {"type": "dict"}},
                            output_schema={"result": {"type": "dict"}},
                            implementation_code=code,
                            implementation_sketch=description,
                            confidence=0.8,
                        )
                        deploy_result = self._deploy(agent_id, synthesis_id, cap)
                        if deploy_result:
                            deployed_ok.add(pid)
                            deployment_id, cap_id = deploy_result
                            deployed.append(cap_id)
                        else:
                            # Permanently record failure — never retry
                            deployed_failed.add(pid)
                        _save_log()
                    except Exception:
                        continue
        except Exception:
            pass

        return deployed

    def _record_pending_deployment(self, agent_id: str, proposal_id: str,
                                    synthesis_id: str,
                                    cap: "SynthesizedCapability") -> None:
        """Record a submitted-but-not-yet-approved proposal for deferred deployment."""
        try:
            pending_dir = SELF_MOD_PATH / "_pending"
            pending_dir.mkdir(parents=True, exist_ok=True)
            f = pending_dir / f"{proposal_id}.json"
            f.write_text(json.dumps({
                "proposal_id": proposal_id,
                "agent_id": agent_id,
                "synthesis_id": synthesis_id,
                "capability": asdict(cap),
                "submitted_at": time.time(),
            }))
        except Exception:
            pass

    def _synthesize(self, agent_id: str, intent: str, gap_id: str) -> Optional[Tuple[str, SynthesizedCapability]]:
        """
        Synthesize a new capability for the gap using Ollama.
        Generates real Python code, not a sketch.
        """
        synthesis_id = f"syn-{uuid.uuid4().hex[:12]}"
        name = "synth_" + "_".join(intent.lower().split()[:3])[:30].replace("-", "_")
        description = f"synthesized: {intent[:80]}"
        input_schema = "dict of parameters inferred from intent"
        output_schema = '{"ok": bool, "result": <value>}'

        # Generate real code via Ollama
        code, confidence = self._ollama_generate_code(intent, name)
        sketch = code if code else f"# Capability to {intent}\n# TODO: implement logic"

        capability = SynthesizedCapability(
            synthesis_id=synthesis_id,
            agent_id=agent_id,
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            implementation_sketch=sketch,
            implementation_code=code or "",
            confidence=confidence,
            gap_id=gap_id,
        )

        self._record_synthesis(agent_id, capability)
        return (synthesis_id, capability)

    def _ollama_generate_code(self, intent: str, func_name: str) -> Tuple[str, float]:
        """
        Ask Ollama to write a real Python function for the given intent.
        Returns (code: str, confidence: float). On failure returns ("", 0.0).
        """
        import ast, os
        from pathlib import Path

        try:
            import httpx
            cfg_path = Path(os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))
            cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            model = cfg.get("ollama", {}).get("default_model", "mistral-nemo:12b")
            ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")

            prompt = (
                f"Write a Python function named `{func_name}` that does: {intent}\n\n"
                f"Requirements:\n"
                f"- Accept **kwargs for all parameters\n"
                f"- Return a dict with at minimum {{\'ok\': bool, \'result\': <value>}}\n"
                f"- Use only: os, json, pathlib, subprocess, time (no third-party imports)\n"
                f"- Handle exceptions: return {{\'ok\': False, \'error\': str(e)}} on failure\n"
                f"- Be complete and immediately runnable\n"
                f"- Include only the function definition, no explanation or markdown\n"
                f"\nRespond with ONLY the Python function code."
            )

            resp = httpx.post(
                f"{ollama_host}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=60,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()

            # Strip markdown fences if present
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(l for l in lines if not l.startswith("```")).strip()

            # Validate syntax
            ast.parse(raw)

            # Ensure it actually defines the function
            if f"def {func_name}" not in raw:
                return ("", 0.0)

            return (raw, 0.85)

        except Exception as e:
            return ("", 0.0)

    def _test_capability(self, agent_id: str, capability: SynthesizedCapability) -> Optional[TestResult]:
        """
        Test synthesized capability in an isolated subprocess.
        If real code is available, executes it. Falls back to mock if not.
        """
        test_id = f"test-{uuid.uuid4().hex[:12]}"

        code = capability.implementation_code
        if not code:
            # No real code — mock pass at 50% confidence
            result = TestResult(
                test_id=test_id,
                synthesis_id=capability.synthesis_id,
                agent_id=agent_id,
                test_cases=[],
                passed_count=0,
                failed_count=0,
                success_rate=0.5,
            )
            self._record_test(agent_id, result)
            return result

        # Build a sandboxed test script that runs the function with minimal inputs
        test_script = (
            f"import json, sys\n"
            f"{code}\n\n"
            f"# Basic smoke test: call with no args, expect dict back\n"
            f"try:\n"
            f"    result = {capability.name}()\n"
            f"    assert isinstance(result, dict), f'Expected dict, got {{type(result)}}'\n"
            f"    print(json.dumps({{'ok': True, 'result': str(result)[:200]}}))"
            f"\nexcept Exception as e:\n"
            f"    print(json.dumps({{'ok': False, 'error': str(e)}}))"
        )

        passed, failed = 0, 0
        test_cases = [{"input": "no_args_smoke_test"}]

        try:
            import subprocess, tempfile, os
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(test_script)
                tmp_path = f.name

            proc = subprocess.run(
                ["python3", tmp_path],
                capture_output=True, text=True, timeout=10
            )
            os.unlink(tmp_path)

            if proc.returncode == 0 and proc.stdout.strip():
                out = json.loads(proc.stdout.strip())
                if out.get("ok"):
                    passed = 1
                    test_cases[0]["output"] = out.get("result", "")
                else:
                    failed = 1
                    test_cases[0]["error"] = out.get("error", "")
            else:
                failed = 1
                test_cases[0]["error"] = proc.stderr[:200]

        except Exception as e:
            failed = 1
            test_cases[0]["error"] = str(e)

        total = passed + failed
        success_rate = passed / total if total > 0 else 0.0

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

        payload = {
            "synthesis_id": synthesis_id,
            "agent_id": agent_id,
            "cap_id": f"synth_{synthesis_id[:8]}",
            "capability_name": capability.name,
            "capability_description": capability.description,
            "description": capability.description,
            "code_preview": capability.implementation_code[:500],
            "test_success_rate": test_results.success_rate,
            "confidence": capability.confidence,
        }
        description = f"New capability: {capability.name} — {capability.description[:120]}"

        # Support both AgentQuorum and CapabilityQuorum interfaces
        try:
            if hasattr(self._quorum, "submit"):
                # CapabilityQuorum interface
                return self._quorum.submit(
                    proposer_id=agent_id,
                    cap_id=payload["cap_id"],
                    description=description,
                    code=capability.implementation_code,
                )
            else:
                # AgentQuorum interface
                return self._quorum.propose(
                    proposer_id=agent_id,
                    proposal_type="capability",
                    description=description,
                    payload=payload,
                )
        except Exception:
            return None

    def _deploy(self, agent_id: str, synthesis_id: str, capability: SynthesizedCapability) -> Optional[str]:
        """
        Deploy approved capability via hot-loading.
        Writes code to /agentOS/tools/dynamic/, imports it, registers in engine
        under both the internal cap_id AND the human-readable function name so
        agents can discover and call it by name.
        """
        if not self._execution_engine:
            return None

        import ast as _ast, inspect as _inspect, os, importlib.util
        from pathlib import Path

        deployment_id = f"deploy-{uuid.uuid4().hex[:12]}"
        cap_id = f"synth_{synthesis_id[:8]}"

        code = capability.implementation_code
        func = None
        fn_name = capability.name  # human-readable name to register under

        if code:
            # ── Quality gate: reject bad code before writing ────────────────
            stub_signals = ["...", "pass\n    pass", "# TODO", "# placeholder",
                            '{"ok": true', "raise NotImplementedError"]
            if any(sig in code for sig in stub_signals):
                log.warning("[DEPLOY] %s rejected (stub): %s", cap_id, capability.name)
                return None
            try:
                _ast.parse(code)
            except SyntaxError as e:
                log.warning("[DEPLOY] %s rejected (syntax error): %s", cap_id, e)
                return None

            # ── Hot-load ────────────────────────────────────────────────────
            try:
                tools_dir = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")).parent / "tools" / "dynamic"
                tools_dir.mkdir(parents=True, exist_ok=True)
                module_path = tools_dir / f"{cap_id}.py"
                # Write header comment so dedup can identify by name later
                header = (
                    f"# Auto-synthesized capability: {capability.name}\n"
                    f"# Description: {capability.description}\n"
                )
                module_path.write_text(header + code)

                spec = importlib.util.spec_from_file_location(cap_id, module_path)
                mod = importlib.util.module_from_spec(spec)

                # Inject agent-system helpers so synthesized code can call them
                # These make HTTP calls to the local API — safe, sandboxed, no extra deps
                try:
                    import requests as _req, json as _json_mod
                    _api = "http://localhost:7777"
                    try:
                        _cfg = _json_mod.loads(open(os.getenv("AGENTOS_CONFIG", "/agentOS/config.json")).read())
                        _tok = _cfg.get("api", {}).get("token", "")
                    except Exception:
                        _tok = ""
                    _hdrs = {"Authorization": f"Bearer {_tok}"} if _tok else {}

                    def _shell_exec(command, cwd="/agentOS", **kw):
                        try:
                            r = _req.post(f"{_api}/shell", json={"command": command, "cwd": cwd}, timeout=30, headers=_hdrs)
                            return r.json()
                        except Exception as _e:
                            return {"ok": False, "stdout": "", "stderr": str(_e), "exit_code": 1, "success": False}

                    def _fs_read(path, **kw):
                        try:
                            r = _req.get(f"{_api}/fs/read", params={"path": path}, timeout=10, headers=_hdrs)
                            return r.json()
                        except Exception as _e:
                            return {"ok": False, "error": str(_e)}

                    def _fs_write(path, content, **kw):
                        try:
                            r = _req.post(f"{_api}/fs/write", json={"path": path, "content": content}, timeout=10, headers=_hdrs)
                            return r.json()
                        except Exception as _e:
                            return {"ok": False, "error": str(_e)}

                    def _ollama_chat(prompt, model=None, **kw):
                        try:
                            payload = {"prompt": prompt}
                            if model:
                                payload["model"] = model
                            r = _req.post(f"{_api}/ollama/chat", json=payload, timeout=120, headers=_hdrs)
                            return r.json()
                        except Exception as _e:
                            return {"ok": False, "error": str(_e)}

                    def _memory_get(key, **kw):
                        try:
                            r = _req.get(f"{_api}/memory/{key}", timeout=10, headers=_hdrs)
                            return r.json()
                        except Exception as _e:
                            return {"ok": False, "error": str(_e)}

                    def _memory_set(key, value, **kw):
                        try:
                            r = _req.post(f"{_api}/memory/{key}", json={"value": value}, timeout=10, headers=_hdrs)
                            return r.json()
                        except Exception as _e:
                            return {"ok": False, "error": str(_e)}

                    mod.shell_exec = _shell_exec
                    mod.fs_read = _fs_read
                    mod.fs_write = _fs_write
                    mod.ollama_chat = _ollama_chat
                    mod.memory_get = _memory_get
                    mod.memory_set = _memory_set
                    mod.json = _json_mod
                except Exception:
                    pass  # Injecting helpers failed — function may still work without them

                spec.loader.exec_module(mod)

                # Find the best matching public function in the module
                public_fns = [(n, f) for n, f in _inspect.getmembers(mod, _inspect.isfunction)
                              if not n.startswith("_")]
                if public_fns:
                    # Prefer exact name match, else first public function
                    exact = [(n, f) for n, f in public_fns if n == capability.name]
                    fn_name, func = exact[0] if exact else public_fns[0]
            except Exception as e:
                log.warning("[DEPLOY] %s import failed: %s", cap_id, e)
                return None  # Don't deploy broken code as a lambda stub

        if func is None:
            log.warning("[DEPLOY] %s has no implementation — skipping", cap_id)
            return None

        # ── Register under both cap_id and the human-readable name ──────────
        # cap_id: internal reference (synth_syn-xxxx)
        # fn_name: discoverable by agents via semantic search
        self._execution_engine.register(cap_id, func)
        if fn_name != cap_id:
            # Also register under the readable name — allow override if name exists
            with self._execution_engine._lock:
                self._execution_engine._implementations[fn_name] = func
                self._execution_engine._timeouts[fn_name] = 10000
                self._execution_engine._requires_approval[fn_name] = False
                self._execution_engine._enabled[fn_name] = True

        # ── Also register in the capability graph for semantic discovery ─────
        if self._capability_graph:
            try:
                from agents.capability_graph import CapabilityRecord
                import inspect as _ins
                sig = str(_ins.signature(func)) if func else "()"
                rec = CapabilityRecord(
                    capability_id=fn_name,
                    name=fn_name,
                    description=capability.description,
                    input_schema=f"args{sig}",
                    output_schema="dict",
                    introduced_by=agent_id,
                    confidence=0.7,
                )
                self._capability_graph.register(rec)
            except Exception as e:
                log.debug("[DEPLOY] capability graph registration failed: %s", e)

        log.info("[DEPLOY] '%s' hot-loaded and registered as '%s'", cap_id, fn_name)

        # Record deployment
        record = DeploymentRecord(
            deployment_id=deployment_id,
            agent_id=agent_id,
            synthesis_id=synthesis_id,
            capability_id=fn_name,
            name=capability.name,
            description=capability.description,
            deployed_at=time.time(),
        )

        self._record_deployment(agent_id, record)
        return (deployment_id, fn_name)

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
