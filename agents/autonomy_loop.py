"""
Autonomy Loop — AgentOS v3.20.0.

Multi-step planning: before executing, Ollama generates a complete plan.
Each step's result is substituted into the next step's params ({result} placeholder).
Failed steps trigger replanning from that point.

Design:
  pursue_goal(agent_id, max_steps) → (goal_id, final_progress, steps_executed)
  execute_step(agent_id, context, planned_cap, planned_params) → (goal_id, success, result)
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Tuple, List

try:
    from agents.resource_manager import ResourceManager as _ResourceManager
    _resource_manager = _ResourceManager()
except Exception:
    _resource_manager = None

AUTONOMY_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "autonomy"
THOUGHTS_LOG = Path("/agentOS/logs/thoughts.log")


_C = {
    'rs': '\033[0m', 'bold': '\033[1m', 'dim': '\033[2m',
    'gray': '\033[90m', 'red': '\033[91m', 'green': '\033[92m',
    'yellow': '\033[93m', 'blue': '\033[94m', 'cyan': '\033[96m', 'white': '\033[97m',
}

def _thought(agent_id: str, msg: str) -> None:
    """Append a formatted, colorized thought line to the live thoughts log."""
    try:
        ts = time.strftime("%H:%M:%S")
        aid = agent_id[-15:] if len(agent_id) > 15 else agent_id
        ts_s  = f"{_C['gray']}{ts}{_C['rs']}"
        aid_c = f"{_C['cyan']}{aid:<15}{_C['rs']}"
        aid_d = f"{_C['dim']}{aid:<15}{_C['rs']}"
        blank = " " * 8  # timestamp width
        m = msg.strip()

        if m.startswith("RUN:"):
            parts = m[4:].split("|", 1)
            cap   = parts[0].strip()
            prm   = parts[1].replace("params:", "").strip()[:70] if len(parts) > 1 else ""
            out = f"{ts_s}  {aid_c}  {_C['white']}▶  {cap:<18}{_C['rs']}  {_C['dim']}{prm}{_C['rs']}"
        elif m.startswith("OK:"):
            parts = m[3:].split("|", 1)
            cap   = parts[0].strip()
            res   = parts[1].strip()[:80] if len(parts) > 1 else ""
            out = f"{blank}  {aid_d}  {_C['green']}✓  {cap:<18}{_C['rs']}  {_C['dim']}{res}{_C['rs']}"
        elif m.startswith("FAIL:"):
            parts = m[5:].split("|", 1)
            cap   = parts[0].strip()
            err   = parts[1].strip() if len(parts) > 1 else ""
            # trim to first meaningful line, skip traceback
            err   = (err.split("\\n")[0] if "\\n" in err else err)[:80]
            out = f"{blank}  {aid_d}  {_C['red']}✗  {cap:<18}{_C['rs']}  {_C['dim']}{err}{_C['rs']}"
        else:
            out = f"{ts_s}  {_C['dim']}{aid:<15}  {m}{_C['rs']}"

        THOUGHTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(THOUGHTS_LOG, "a") as f:
            f.write(out + "\n")
    except Exception:
        pass


def _mid_step_reflect(agent_id: str, goal_objective: str, cap_id: str, result: dict) -> None:
    """
    Lightweight reflection after a successful step. Asks the agent if the finding
    changes how it thinks about the goal. Writes to thoughts.log only.
    """
    try:
        import os as _os, json as _j, pathlib as _pl, httpx as _hx
        result_text = _result_to_text(result)
        if not result_text or len(result_text.strip()) < 20:
            return
        cfg_path = _pl.Path(_os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))
        cfg = _j.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        model = cfg.get("ollama", {}).get("default_model", "mistral-nemo:12b")
        ollama_host = _os.getenv("OLLAMA_HOST", "http://localhost:11434")

        resp = _hx.post(
            f"{ollama_host}/api/generate",
            json={
                "model": model,
                "prompt": (
                    f"You are {agent_id} pursuing: '{goal_objective[:100]}'\n"
                    f"You just ran '{cap_id}' and received:\n{result_text[:600]}\n\n"
                    f"In ONE sentence: does this change your understanding of the goal, "
                    f"reveal something unexpected, or confirm your plan? "
                    f"If nothing surprising, reply exactly: no change"
                ),
                "stream": False,
                "think": False,
            },
            timeout=25,
        )
        reflection = resp.json().get("response", "").strip()
        if not reflection:
            return
        if reflection.lower().startswith("no change"):
            return
        # Strip any think tags
        if "</think>" in reflection:
            reflection = reflection.split("</think>")[-1].strip()
        if len(reflection) > 10:
            _thought(agent_id, f"  💡 {reflection[:200]}")
    except Exception:
        pass


@dataclass
class AutonomyStep:
    step_id: str
    agent_id: str
    goal_id: str
    reasoning_id: Optional[str] = None
    execution_id: Optional[str] = None
    capability_id: Optional[str] = None
    reasoning_text: str = ""
    execution_result: Optional[dict] = None
    execution_status: Optional[str] = None
    goal_progress_delta: float = 0.0
    step_status: str = "pending"
    timestamp: float = field(default_factory=time.time)


_PROSE_STARTERS = frozenset([
    "based", "the", "to", "this", "in", "using", "you", "here", "note",
    "please", "first", "then", "next", "finally", "step", "as", "since",
    "when", "if", "for", "because", "however", "additionally", "also",
    "important", "make", "we", "i", "it", "now", "after", "before",
])


def _is_shell_prose(cmd: str) -> bool:
    """Return True if cmd looks like LLM prose rather than a real shell command."""
    if not cmd:
        return False
    cmd = cmd.strip()
    if not cmd:
        return False
    # Unsubstituted placeholder — never a valid command
    if "{result}" in cmd or "{code_path}" in cmd or "{placeholder}" in cmd:
        return True
    # Clearly valid: starts with /, ./, $, (, [, {, `, digit, or lowercase letter
    if cmd[0] in '/.($[{`\\' or cmd[0].islower() or cmd[0].isdigit():
        # But reject bare filenames used as commands (e.g. "script.py", "file.sh")
        first = cmd.split()[0]
        if (first.endswith(('.py', '.sh', '.js', '.rb', '.pl'))
                and not first.startswith(('/', './', '../'))):
            return True
        return False
    # ENV=VALUE pattern (e.g. PYTHONPATH=/agentOS python3 ...) — valid
    if cmd.split()[0].endswith('=') or '=' in cmd.split()[0]:
        return False
    # Suspiciously long
    if len(cmd) > 500:
        return True
    # Starts with uppercase — check first word against known prose starters
    first_word = cmd.split()[0].lower().rstrip(':,.')
    return first_word in _PROSE_STARTERS


def _result_to_text(result: dict) -> str:
    """Extract readable text from an execution result dict."""
    if not result:
        return ""
    for key in ("response", "content", "stdout", "results", "value"):
        val = result.get(key)
        if val:
            if isinstance(val, list):
                parts = []
                for item in val[:10]:
                    if isinstance(item, dict):
                        parts.append(item.get("preview", str(item))[:4000])
                    else:
                        parts.append(str(item)[:4000])
                return "\n".join(parts)
            return str(val)[:16000]
    return json.dumps(result)[:8000]


_REFUSAL_PREFIXES = (
    "i'd be happy to help",
    "i cannot access",
    "i don't have access",
    "please provide",
    "i'm unable to",
    "i am unable to",
    "i can't access",
    "i cannot directly",
    "as an ai",
    "i don't have the ability",
    "i need you to provide",
    "unfortunately, i",
)

def _is_llm_refusal(text: str) -> bool:
    """Return True if ollama responded with a refusal/placeholder instead of real content."""
    if not text:
        return False
    t = text.strip().lower()[:120]
    return any(t.startswith(p) for p in _REFUSAL_PREFIXES)


def _substitute_result(params: dict, previous_result: Optional[dict]) -> dict:
    """
    Replace {result} placeholders in params with the previous result.
    Also appends context to key params (prompt/content/query/value) and
    replaces any dict/empty param values with the result text.
    """
    if not previous_result:
        return params
    result_text = _result_to_text(previous_result)
    import re as _re
    _PLACEHOLDER_RE = _re.compile(r'\{[a-zA-Z_][a-zA-Z0-9_.]*\}')
    out = {}
    for k, v in params.items():
        if isinstance(v, str) and "{result}" in v:
            substituted = v.replace("{result}", result_text)
            # Guard: path/url params must look like actual paths/URLs after substitution
            if k in ("path", "url") and not (substituted.startswith("/") or substituted.startswith("http")):
                out[k] = v  # keep original (unsubstituted) — agent will error gracefully
            elif k == "prompt" and len(result_text.strip()) < 50:
                # Previous step returned almost nothing — warn the model rather than
                # letting it hallucinate a plausible-sounding response
                out[k] = (
                    f"WARNING: the previous step returned very little data "
                    f"('{result_text.strip()}'). "
                    f"If you do not have enough real information to complete this task, "
                    f"respond with exactly: INSUFFICIENT_DATA\n\n"
                    + v.replace("{result}", result_text)
                )
            else:
                out[k] = substituted
        elif isinstance(v, str) and v and k in ("prompt", "query", "value"):  # content excluded: fs_write must use {result} explicitly
            # Append context to these key params so LLM/search gets real data
            out[k] = v.rstrip() + "\n\n" + result_text if result_text else v
        elif isinstance(v, dict):
            # Ollama sometimes returns {"result": null, "top_k": 5} instead of "{result}".
            # Substitute the result text into any "result" or "query" key inside the dict,
            # and carry remaining keys (like top_k) through unchanged.
            inner = dict(v)
            substituted = False
            for inner_k in ("result", "query", "prompt", "content", "value"):
                if inner_k in inner:
                    inner[inner_k] = result_text
                    substituted = True
                    break
            if substituted and k in ("query", "prompt", "content", "value"):
                # Flatten: the outer param should be the text, not a dict
                out[k] = result_text
            elif substituted:
                out[k] = inner
            else:
                # No recognizable placeholder key — replace whole dict with result text
                out[k] = result_text
        elif not v and v != 0:
            # param is empty/False/None — substitute previous result
            out[k] = result_text
        else:
            out[k] = v
    # Final pass: replace any remaining {varname} placeholders with result_text
    # LLMs sometimes invent custom placeholder names instead of using {result}
    final_out = {}
    for k, v in out.items():
        if isinstance(v, str) and _PLACEHOLDER_RE.search(v):
            final_out[k] = _PLACEHOLDER_RE.sub(result_text[:500], v)
        else:
            final_out[k] = v
    return final_out


class AutonomyLoop:
    def __init__(self, goal_engine=None, execution_engine=None,
                 reasoning_layer=None, semantic_memory=None,
                 native_interface=None, self_modification=None):
        self._lock = threading.RLock()
        self._goal_engine = goal_engine
        self._execution_engine = execution_engine
        self._reasoning_layer = reasoning_layer
        self._semantic_memory = semantic_memory
        self._self_modification = self_modification
        AUTONOMY_PATH.mkdir(parents=True, exist_ok=True)

    # ── API ────────────────────────────────────────────────────────────────

    def execute_step(
        self,
        agent_id: str,
        context: Optional[dict] = None,
        planned_cap: Optional[str] = None,
        planned_params: Optional[dict] = None,
    ) -> Tuple[Optional[str], bool, Optional[dict]]:
        """Execute one step. Uses planned cap/params if provided, else falls back to reason()."""
        if not self._goal_engine or not self._reasoning_layer or not self._execution_engine:
            return (None, False, None)

        step_id = f"step-{uuid.uuid4().hex[:12]}"

        active_goals = self._goal_engine.list_active(agent_id, limit=1)
        if not active_goals:
            return (None, False, None)

        active_goal = active_goals[0]
        goal_id = active_goal.goal_id

        # Determine capability and params
        if planned_cap:
            cap_id = planned_cap
            params = _substitute_result(planned_params or {}, context)
            reasoning_text = f"planned: {cap_id}"
        else:
            # Fallback: single-step reasoning with context
            intent = f"progress towards: {active_goal.objective}"
            if context:
                intent += f"\nPrevious result: {json.dumps(context)[:4000]}"
            cap_id, params, _, reasoning_text = self._reasoning_layer.reason(agent_id, intent)
            if not cap_id:
                return (goal_id, False, None)

        # Validate shell commands — reject prose before it hits the OS
        if cap_id == "shell_exec":
            cmd = params.get("command", "")
            if _is_shell_prose(cmd):
                _thought(agent_id, f"  FAIL: shell_exec | rejected prose as command: {cmd[:80]}")
                return (goal_id, False, {
                    "error": "rejected: ollama returned prose instead of a shell command",
                    "command_preview": cmd[:120],
                })

        # Execute
        _thought(agent_id, f"  RUN: {cap_id} | params: {json.dumps(params)[:120]}")
        result, status = self._execution_engine.execute(agent_id, cap_id, params)

        # Detect ollama refusals — model said "I'd be happy to help" instead of doing the work
        if cap_id == "ollama_chat" and status == "success" and result:
            response_text = result.get("response", "")
            if _is_llm_refusal(response_text) or "INSUFFICIENT_DATA" in response_text:
                status = "failed"
                result = {"error": f"ollama returned refusal/placeholder: {response_text[:120]}",
                          "ok": False}
                _thought(agent_id, f"  FAIL: ollama_chat | refusal detected — treating as failure")

        result_preview = _result_to_text(result)[:200] if result else "none"
        _thought(agent_id, f"  {'OK' if status == 'success' else 'FAIL'}: {cap_id} | {result_preview}")

        # Mid-step reflection — did this finding change anything?
        if status == "success" and result and cap_id not in ("memory_set", "fs_write", "sanitize_sensitive_vars"):
            import threading as _rt
            _rt.Thread(
                target=_mid_step_reflect,
                args=(agent_id, active_goal.objective, cap_id, result),
                daemon=True,
            ).start()

        # Learn
        if self._semantic_memory and result and status == "success":
            self._semantic_memory.store(
                agent_id,
                f"Goal '{active_goal.objective}' step {cap_id}: {json.dumps(result)[:200]}"
            )

        # Progress — only count a step if it does something new
        metrics = dict(active_goal.metrics) if active_goal.metrics else {}
        progress_delta = 0.0
        if status == "success":
            prev_cap = metrics.get("last_cap")
            if cap_id != prev_cap:
                # Meaningful progress: different capability than last step
                _HIGH_VALUE_CAPS = ("memory_set", "fs_write", "wrap_repo", "shared_log_write", "propose_change", "synthesize_capability", "vote_on_proposal")
                progress_delta = 0.15 if cap_id in _HIGH_VALUE_CAPS else 0.1
            else:
                # Repeated same capability — marginal credit
                progress_delta = 0.02
            metrics["last_cap"] = cap_id

            # Track whether an output step has succeeded
            # wrap_repo, shared_log_write, propose_change also count as real output
            _OUTPUT_CAPS = ("memory_set", "fs_write", "wrap_repo", "shared_log_write", "propose_change", "synthesize_capability", "vote_on_proposal")
            if cap_id in _OUTPUT_CAPS:
                metrics["has_output"] = True

        current_progress = metrics.get("progress", 0.0)
        # Cap at 0.9 until at least one output step completes
        if not metrics.get("has_output"):
            new_progress = min(0.9, current_progress + progress_delta)
        else:
            new_progress = min(1.0, current_progress + progress_delta)
        metrics["progress"] = new_progress
        metrics["steps_completed"] = metrics.get("steps_completed", 0) + 1
        self._goal_engine.update_progress(agent_id, goal_id, metrics)

        exec_history = self._execution_engine.get_execution_history(agent_id, limit=1)
        self._record_step(agent_id, AutonomyStep(
            step_id=step_id,
            agent_id=agent_id,
            goal_id=goal_id,
            execution_id=exec_history[0].execution_id if exec_history else None,
            capability_id=cap_id,
            reasoning_text=reasoning_text,
            execution_result=result,
            execution_status=status,
            goal_progress_delta=progress_delta,
            step_status="completed" if status == "success" else "failed",
        ))

        return (goal_id, status == "success", result)

    def pursue_goal(
        self, agent_id: str, max_steps: int = 10
    ) -> Tuple[Optional[str], float, int]:
        """
        Plan then execute. Ollama generates the full step sequence upfront.
        Each step's result feeds the next via {result} substitution.
        Replans if a step fails.
        """
        if not self._goal_engine:
            return (None, 0.0, 0)

        active_goals = self._goal_engine.list_active(agent_id, limit=1)
        if not active_goals:
            return (None, 0.0, 0)

        goal = active_goals[0]
        goal_id = goal.goal_id
        steps_executed = 0
        context = None

        # Retrieve relevant past experiences and inject into planning context
        memory_context = ""
        if self._semantic_memory:
            try:
                memories = self._semantic_memory.search(agent_id, goal.objective, top_k=3)
                relevant = [m.thought for m in memories
                            if m.thought not in ("[DELETED]", "") ][:3]
                if relevant:
                    memory_context = (
                        "\n\nRelevant past experience (use this to avoid repeating "
                        "mistakes and build on prior work):\n"
                        + "\n".join(f"- {s[:150]}" for s in relevant)
                    )
            except Exception:
                pass

        # Generate plan
        plan = self._reasoning_layer.plan(agent_id, goal.objective + memory_context) if self._reasoning_layer else []
        if not plan:
            plan = [{"capability_id": None, "params": {}, "rationale": "fallback"}]
        plan = plan[:max_steps]
        plan_index = 0

        # Error recovery state
        failure_counts: dict = {}      # cap_id → total failure count this goal run
        consecutive_failures: int = 0  # reset on any success
        blacklisted: list = []         # caps declared impossible this run

        while steps_executed < max_steps:
            if plan_index < len(plan):
                step_def = plan[plan_index]
                cap_id = step_def.get("capability_id")
                params = step_def.get("params", {})
            else:
                cap_id, params = None, {}

            goal_id_out, success, result = self.execute_step(
                agent_id,
                context=context,
                planned_cap=cap_id,
                planned_params=params,
            )

            if goal_id_out is None:
                break

            steps_executed += 1
            plan_index += 1

            if success and result:
                # After fs_write/memory_set, context becomes {"ok": true, "path": ...}
                # which is useless for the next step's {result} substitution.
                # Keep the previous meaningful context so shared_log_write etc. get real content.
                _trivial_ok = (
                    result.get("ok") is True
                    and set(result.keys()) <= {"ok", "path", "key", "bytes_written", "size"}
                )
                if not _trivial_ok:
                    context = result
                consecutive_failures = 0
            elif not success:
                # ── Failure classification ──────────────────────────────────
                consecutive_failures += 1
                if cap_id:
                    failure_counts[cap_id] = failure_counts.get(cap_id, 0) + 1

                fail_count = failure_counts.get(cap_id, 1) if cap_id else 1

                if consecutive_failures >= 4:
                    # IMPOSSIBLE: stuck in failure loop — abandon goal permanently
                    explanation = (
                        f"Goal '{goal.objective}' abandoned after "
                        f"{consecutive_failures} consecutive failures. "
                        f"Last failing capability: {cap_id}. "
                        f"Failure counts: {failure_counts}."
                    )
                    if self._semantic_memory:
                        self._semantic_memory.store(agent_id, f"FAILED: {explanation}")
                    if self._goal_engine:
                        metrics = dict(goal.metrics) if goal.metrics else {}
                        metrics["failure_reason"] = explanation
                        self._goal_engine.update_progress(agent_id, goal_id, metrics)
                        self._goal_engine.abandon(agent_id, goal_id)
                    _thought(agent_id, f"  FAIL: abandon | {explanation[:120]}")
                    return (goal_id, 0.0, steps_executed)

                elif fail_count >= 3 and cap_id and cap_id not in blacklisted:
                    # IMPOSSIBLE capability: blacklist it, force replan without it
                    blacklisted.append(cap_id)
                    blocked_msg = (
                        f"{goal.objective} "
                        f"(do NOT use {', '.join(blacklisted)} — tried {fail_count}x and failed, "
                        f"use a different approach)"
                    )
                    new_plan = self._reasoning_layer.plan(agent_id, blocked_msg) if self._reasoning_layer else []
                    if new_plan:
                        plan = plan[:plan_index] + new_plan

                    # Capability gap detected — trigger self-modification in background
                    if self._self_modification:
                        import threading as _t
                        _t.Thread(
                            target=self._self_modification.process_gap,
                            args=(agent_id, goal.objective,
                                  f"capability '{cap_id}' failed {fail_count} times"),
                            daemon=True,
                        ).start()
                        _thought(agent_id, f"  gap → self-mod triggered for {cap_id}")

                elif fail_count >= 2:
                    # BLOCKED: replan with context about what failed
                    remaining_objective = (
                        f"{goal.objective} "
                        f"(step {plan_index}: {cap_id} failed {fail_count}x, try a different capability)"
                    )
                    new_plan = self._reasoning_layer.plan(agent_id, remaining_objective) if self._reasoning_layer else []
                    if new_plan:
                        plan = plan[:plan_index] + new_plan

                else:
                    # TRANSIENT: first failure of this cap — replan normally
                    remaining_objective = (
                        f"{goal.objective} "
                        f"(already tried {cap_id}, continue from step {plan_index})"
                    )
                    new_plan = self._reasoning_layer.plan(agent_id, remaining_objective) if self._reasoning_layer else []
                    if new_plan:
                        plan = plan[:plan_index] + new_plan

                time.sleep(0.1)

            # Check completion — require a validated artifact before marking done
            current = self._goal_engine.get(agent_id, goal_id)
            if current and current.metrics.get("progress", 0.0) >= 1.0:
                validation = self.validate_goal_artifact(agent_id, goal_id)
                if validation.get("validated"):
                    _thought(agent_id, f"  artifact ok | {validation.get('artifact_type','')} {validation.get('artifact_value','')[:60]}")
                    self._goal_engine.complete(agent_id, goal_id)
                    self._synthesize_completion(agent_id, goal.objective, steps_executed)
                    return (goal_id, 1.0, steps_executed)
                else:
                    metrics = dict(current.metrics)
                    artifact_fails = metrics.get("artifact_check_failures", 0) + 1
                    metrics["artifact_check_failures"] = artifact_fails
                    _thought(agent_id, f"  artifact MISSING ({artifact_fails}/3) | {validation.get('checks',[])} — resetting progress")
                    if artifact_fails >= 3:
                        # Permanently abandon — repeated failure to produce a verifiable artifact
                        explanation = (
                            f"Goal '{goal.objective}' abandoned: artifact validation "
                            f"failed {artifact_fails} times. Checks: {validation.get('checks', [])}"
                        )
                        metrics["failure_reason"] = explanation
                        self._goal_engine.update_progress(agent_id, goal_id, metrics)
                        self._goal_engine.abandon(agent_id, goal_id)
                        if self._semantic_memory:
                            self._semantic_memory.store(agent_id, f"FAILED: {explanation}")
                        _thought(agent_id, f"  FAIL: abandon | {explanation[:120]}")
                        return (goal_id, 0.0, steps_executed)
                    metrics["progress"] = 0.85
                    metrics["has_output"] = False  # force re-earning output gate
                    self._goal_engine.update_progress(agent_id, goal_id, metrics)

        final = self._goal_engine.get(agent_id, goal_id)
        progress = final.metrics.get("progress", 0.0) if final else 0.0
        # Only synthesize and propose follow-ons for goals that actually completed
        # (progress reached 1.0 via the completion check above). Partial runs are
        # not stored as "successes" — that contaminates semantic memory with
        # incomplete or failed work.
        return (goal_id, progress, steps_executed)

    # ── Introspection ──────────────────────────────────────────────────────

    def get_execution_chain(self, agent_id: str, limit: int = 50) -> List[AutonomyStep]:
        with self._lock:
            chain_file = AUTONOMY_PATH / agent_id / "execution_chain.jsonl"
            if not chain_file.exists():
                return []
            try:
                steps = [
                    AutonomyStep(**json.loads(l))
                    for l in chain_file.read_text().strip().split("\n") if l.strip()
                ]
                steps.sort(key=lambda s: s.timestamp, reverse=True)
                return steps[:limit]
            except Exception:
                return []

    def get_step_count(self, agent_id: str) -> int:
        return len(self.get_execution_chain(agent_id, limit=10000))

    def get_success_rate(self, agent_id: str) -> float:
        chain = self.get_execution_chain(agent_id, limit=1000)
        if not chain:
            return 0.0
        return sum(1 for s in chain if s.step_status == "completed") / len(chain)

    # ── Internal ───────────────────────────────────────────────────────────


    def _synthesize_completion(self, agent_id: str, objective: str, steps: int) -> None:
        """
        After a goal completes, extract what was learned and store in semantic memory.
        Makes past successful plans discoverable for future similar goals.
        """
        if not self._semantic_memory or not self._execution_engine:
            return
        try:
            # Gather what capabilities were used and what they produced
            chain = self.get_execution_chain(agent_id, limit=steps + 2)
            completed = [s for s in chain if s.step_status == "completed"]
            if not completed:
                return

            cap_sequence = " → ".join(s.capability_id for s in reversed(completed) if s.capability_id)
            
            # Find any meaningful output (LLM response or saved keys)
            artifact = ""
            for s in completed:
                r = s.execution_result or {}
                if r.get("response"):
                    artifact = r["response"][:300]
                    break
                elif r.get("key"):
                    artifact = f"saved to memory key='{r['key']}'"
                    break

            summary = (
                f"Goal completed: '{objective}'. "
                f"Capability sequence: {cap_sequence}. "
                f"Result: {artifact or 'no artifact captured'}. "
                f"Steps: {steps}."
            )
            self._semantic_memory.store(agent_id, summary)

            # Propose follow-on goal only if a real artifact was produced
            has_artifact = any(
                s.capability_id in ("memory_set", "fs_write")
                and s.step_status == "completed"
                and (s.execution_result or {}).get("ok")
                for s in completed
            )
            if has_artifact:
                self._propose_followon_goal(agent_id, objective, summary)

            # Resource self-management: prune/compact if over limits
            if _resource_manager is not None:
                _resource_manager.auto_manage(agent_id)
        except Exception:
            pass  # Synthesis failure must never break goal completion

    def _propose_followon_goal(self, agent_id: str, objective: str, synthesis: str) -> Optional[str]:
        """
        After a goal completes, ask Ollama what the agent should work on next.
        Returns the new goal_id if a follow-on goal was created, else None.

        Follow-on goals are only created when:
          - A goal_engine is available
          - Ollama returns a concrete, actionable goal (not a repeat of objective)
          - The suggestion is not too similar to the just-completed objective
        """
        if not self._goal_engine or not self._reasoning_layer:
            return None
        try:
            import httpx, os
            from pathlib import Path

            cfg_path = Path(os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))
            cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            model = cfg.get("ollama", {}).get("default_model", "mistral-nemo:12b")
            ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")

            # Load recently completed goals to avoid repetition
            recent_done: list = []
            try:
                reg_path = Path(f"/agentOS/memory/goals/{agent_id}/registry.jsonl")
                if reg_path.exists():
                    reg_lines = reg_path.read_text().strip().splitlines()
                    recent_done = [
                        json.loads(l).get("objective", "")
                        for l in reg_lines
                        if '"completed"' in l
                    ][-10:]
            except Exception:
                pass
            done_list = "\n".join(f"- {g}" for g in recent_done) if recent_done else "(none)"

            # Build list of agent-specific workspace files for grounding
            # Use per-agent subdirectory to avoid cross-agent drift contamination
            workspace_files: list = []
            agent_ws = Path(f"/agentOS/workspace/{agent_id}")
            shared_ws = Path("/agentOS/workspace")
            try:
                if agent_ws.exists():
                    workspace_files = [str(agent_ws / f.name)
                                       for f in agent_ws.iterdir() if f.is_file()][:15]
                else:
                    # Fall back to shared workspace but only show files this agent wrote
                    # (determined by checking execution chain)
                    pass
            except Exception:
                pass

            # Also include real source files that exist
            source_files: list = []
            try:
                import subprocess
                r = subprocess.run(
                    ["find", "/agentOS/agents", "-name", "*.py", "-not", "-path", "*/__pycache__/*"],
                    capture_output=True, text=True, timeout=5
                )
                source_files = r.stdout.strip().splitlines()[:15]
            except Exception:
                pass

            ws_list = ", ".join(workspace_files) if workspace_files else "(none yet — agent has not written any files)"
            src_list = ", ".join(source_files[:10]) if source_files else "(unavailable)"

            # Extract the root objective (strip any retry annotations)
            root_objective = objective.split(" (already tried")[0].split(" (step ")[0].strip()

            # Recover stored root objective if this is a chained follow-on
            # This prevents multi-hop drift where follow-on #2 anchors to follow-on #1
            try:
                mem_key = f"agent_{agent_id}_root_objective"
                _mem_path = Path("/agentOS/memory/project.json")
                if _mem_path.exists():
                    _proj = json.loads(_mem_path.read_text())
                    _stored_root = _proj.get(mem_key, "")
                    if _stored_root and len(_stored_root) > 10:
                        root_objective = _stored_root
            except Exception:
                pass

            prompt = (
                f"You are an autonomous AI agent (id: {agent_id}) inside AgentOS.\n"
                f"You just completed: '{root_objective[:150]}'\n\n"
                f"Goals you have already done (DO NOT repeat these):\n{done_list}\n\n"
                f"Available source files you can read: {src_list}\n"
                f"Your workspace files: {ws_list}\n\n"
                f"Your job is NOT to keep doing what you were just doing.\n"
                f"Your job is to look at the whole system and ask: what is the single most valuable\n"
                f"thing I could build or change RIGHT NOW that would make this system meaningfully better?\n\n"
                f"Highest-value actions (ranked):\n"
                f"  1. synthesize_capability — write a NEW capability (real Python code) the system is missing\n"
                f"  2. propose_change — submit a specific code change to an existing agent file\n"
                f"  3. vote_on_proposal — review and vote on pending proposals from other agents\n"
                f"  4. shell_exec — run something, test something, verify something real\n"
                f"  5. fs_write — only if producing a genuinely useful artifact, not documentation\n\n"
                f"DO NOT:\n"
                f"- Repeat or slightly rephrase a completed goal\n"
                f"- Write documentation, summaries, or reports unless they directly enable action\n"
                f"- Scan directories you have already scanned\n"
                f"- Propose the same architectural change twice\n"
                f"- Write to files just to have output — only write if the file is useful\n\n"
                f"Think about gaps: what can agents NOT do right now that they should be able to do?\n"
                f"What would you build if you were improving this system for real?\n\n"
                f"Rules:\n"
                f"- Reference only files that actually exist (listed above) or find them with shell_exec first\n"
                f"- Write output to /agentOS/workspace/{agent_id}/ not the shared workspace root\n"
                f"- Must be specific, actionable, and under 180 chars\n"
                f'Respond ONLY with JSON: {{"goal": "<goal or null if you truly have nothing meaningful to add>"}}'
            )

            # Try batch LLM first (parallel GPU), fall back to Ollama
            raw = None
            try:
                from agents.batch_llm import get_server as _bllm
                _srv = _bllm()
                if _srv.ready:
                    raw = _srv.generate(prompt)
            except Exception:
                pass
            if raw is None:
                resp = httpx.post(
                    f"{ollama_host}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False,
                          "format": "json", "think": False, "keep_alive": -1},
                    timeout=90,
                )
                resp.raise_for_status()
                raw = resp.json().get("response", "").strip()
            data = json.loads(raw)
            new_goal = data.get("goal", "").strip()

            # Reject if null, empty, or invalid
            if not new_goal or new_goal.lower() in ("null", "none", ""):
                return None
            if new_goal.lower() == objective.lower():
                return None
            if len(new_goal) < 10 or len(new_goal) > 200:
                return None
            if any(new_goal.lower() == g.lower() for g in recent_done):
                return None
            # Reject if goal references a file path that doesn't exist and wasn't
            # written by this agent (catches hallucinated paths)
            import re as _re
            invented_paths = _re.findall(r'/agentOS/\S+\.\w+', new_goal)
            for p in invented_paths:
                if not Path(p).exists() and "/workspace/" not in p and "/agents/" not in p:
                    return None  # invented path outside known-valid directories

            # Ensure per-agent workspace dir exists so future steps write there
            try:
                Path(f"/agentOS/workspace/{agent_id}").mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            goal_id = self._goal_engine.create(agent_id, new_goal)
            if self._semantic_memory:
                self._semantic_memory.store(
                    agent_id,
                    f"FOLLOWON_ROOT:{root_objective} | goal: {new_goal}"
                )
            # Store root objective in project memory so daemon can enforce topic continuity
            try:
                mem_key = f"agent_{agent_id}_root_objective"
                from pathlib import Path as _P2
                mem_path = _P2(f"/agentOS/memory/project.json")
                _proj = json.loads(mem_path.read_text()) if mem_path.exists() else {}
                _proj[mem_key] = root_objective
                mem_path.write_text(json.dumps(_proj))
            except Exception:
                pass
            return goal_id

        except Exception:
            return None  # Follow-on proposal must never break anything

    def _record_step(self, agent_id: str, step: AutonomyStep) -> None:
        with self._lock:
            agent_dir = AUTONOMY_PATH / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)
            chain_file = agent_dir / "execution_chain.jsonl"
            with open(chain_file, "a") as f:
                f.write(json.dumps(asdict(step)) + "\n")

    def validate_goal_artifact(self, agent_id: str, goal_id: str) -> dict:
        """
        After a goal completes, verify that it produced a real artifact.

        Scans the execution chain for completed steps and checks:
          - memory_set: verifies the key exists in the live AgentOS memory
          - fs_write: verifies the file was written to disk
          - ollama_chat: verifies a non-empty response was produced
          - shell_exec: verifies exit_code == 0

        Returns:
          {"validated": bool, "artifact_type": str, "artifact_value": str, "checks": list}
        """
        chain = self.get_execution_chain(agent_id)
        # Filter to steps for this goal
        steps = [s for s in chain if s.goal_id == goal_id and s.step_status == "completed"]
        if not steps:
            return {"validated": False, "artifact_type": None, "artifact_value": None,
                    "checks": ["no completed steps found"]}

        checks = []
        for step in reversed(steps):
            r = step.execution_result or {}
            cap = step.capability_id or ""

            if cap == "memory_set" and r.get("ok") and r.get("key"):
                # Verify the key exists by calling memory_get
                if self._execution_engine:
                    result, status = self._execution_engine.execute(
                        agent_id, "memory_get", {"key": r["key"]}
                    )
                    if status == "success" and result.get("value"):
                        checks.append(f"memory key '{r['key']}' verified present")
                        return {
                            "validated": True,
                            "artifact_type": "memory",
                            "artifact_value": str(result["value"])[:200],
                            "checks": checks,
                        }
                    checks.append(f"memory key '{r['key']}' not found after write")

            elif cap == "fs_write" and r.get("ok") and r.get("path"):
                import os
                path = r["path"]
                if os.path.exists(path):
                    size = os.path.getsize(path)
                    if size < 200:
                        checks.append(f"file '{path}' too small ({size} bytes) — likely stub or template")
                    else:
                        # Spot-check: file shouldn't be a refusal
                        try:
                            snippet = open(path).read(300)
                        except Exception:
                            snippet = ""
                        if _is_llm_refusal(snippet):
                            checks.append(f"file '{path}' contains refusal/placeholder content")
                        else:
                            checks.append(f"file '{path}' exists with {size} bytes")
                            return {
                                "validated": True,
                                "artifact_type": "file",
                                "artifact_value": path,
                                "checks": checks,
                            }
                else:
                    checks.append(f"file '{path}' missing")

            elif cap == "ollama_chat" and r.get("response"):
                response = r["response"]
                if _is_llm_refusal(response):
                    checks.append("ollama_chat response is a refusal/placeholder — not a valid artifact")
                elif len(response.strip()) < 80:
                    checks.append(f"ollama_chat response too short ({len(response)} chars)")
                else:
                    checks.append("ollama_chat produced substantive response")
                    return {
                        "validated": True,
                        "artifact_type": "llm_response",
                        "artifact_value": response[:200],
                        "checks": checks,
                    }

            elif cap == "shell_exec" and r.get("exit_code") == 0:
                stdout = r.get("stdout", "").strip()
                # exit_code=0 is success even with empty stdout (e.g. python scripts that produce no output)
                if stdout and len(stdout) >= 10:
                    checks.append("shell_exec produced output")
                    return {
                        "validated": True,
                        "artifact_type": "shell_output",
                        "artifact_value": stdout[:200],
                        "checks": checks,
                    }
                else:
                    checks.append("shell_exec succeeded (exit_code=0)")
                    return {
                        "validated": True,
                        "artifact_type": "shell_output",
                        "artifact_value": f"exit_code=0 stderr={r.get('stderr','')[:60]}",
                        "checks": checks,
                    }

            elif cap == "wrap_repo" and r.get("ok"):
                wrapper_path = r.get("wrapper_path", "")
                import os as _os
                if wrapper_path and _os.path.exists(wrapper_path):
                    checks.append(f"wrap_repo produced wrapper at '{wrapper_path}'")
                else:
                    checks.append(f"wrap_repo ok=True (wrapper_path={wrapper_path!r})")
                return {
                    "validated": True,
                    "artifact_type": "wrapper",
                    "artifact_value": wrapper_path or r.get("repo_name", ""),
                    "checks": checks,
                }

            elif cap == "shared_log_write" and r.get("ok"):
                checks.append("shared_log_write broadcast succeeded")
                return {
                    "validated": True,
                    "artifact_type": "shared_log",
                    "artifact_value": "broadcast ok",
                    "checks": checks,
                }

            elif cap == "propose_change" and r.get("ok"):
                proposal_id = r.get("proposal_id", "")
                checks.append(f"propose_change submitted proposal {proposal_id}")
                return {
                    "validated": True,
                    "artifact_type": "proposal",
                    "artifact_value": proposal_id or "submitted",
                    "checks": checks,
                }

            elif cap == "synthesize_capability" and r.get("ok"):
                proposal_id = r.get("proposal_id", "")
                checks.append(f"synthesize_capability submitted proposal {proposal_id}")
                return {
                    "validated": True,
                    "artifact_type": "synthesis",
                    "artifact_value": proposal_id or "submitted",
                    "checks": checks,
                }

            elif cap == "vote_on_proposal" and r.get("ok"):
                checks.append(f"vote_on_proposal cast vote on {r.get('proposal_id','')}")
                return {
                    "validated": True,
                    "artifact_type": "vote",
                    "artifact_value": r.get("proposal_id", "voted"),
                    "checks": checks,
                }

        checks.append("no verifiable artifact found in execution chain")
        return {"validated": False, "artifact_type": None, "artifact_value": None, "checks": checks}
