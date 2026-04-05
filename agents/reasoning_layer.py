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
        blank = " " * 8

        m = msg.strip()
        if m.startswith("GOAL:"):
            goal = m[5:].strip()[:90]
            out = f"{ts_s}  {_C['bold']}{_C['yellow']}{aid:<15}  ◎  {goal}{_C['rs']}"
        elif m.startswith("PLAN:"):
            plan = m[5:].strip()
            out = f"{blank}  {_C['dim']}{aid:<15}  {_C['blue']}↳  {plan}{_C['rs']}"
        elif m.startswith("step "):
            out = f"{blank}  {_C['dim']}{aid:<15}     {m}{_C['rs']}"
        else:
            out = f"{ts_s}  {_C['dim']}{aid:<15}  {m}{_C['rs']}"

        THOUGHTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(THOUGHTS_LOG, "a") as f:
            f.write(out + "\n")
    except Exception:
        pass
CONFIG_PATH = Path(os.getenv("AGENTOS_CONFIG", "/agentOS/config.json"))

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

# Claude auth — two supported paths (tried in order):
#   1. OAuth credentials file (Claude Code session, draws from extra usage credits)
#      Mounted into container at /claude-auth/.credentials.json
#   2. ANTHROPIC_AUTH_TOKEN env var (OAuth token passed directly)
#   3. ANTHROPIC_API_KEY env var (standard API key)
#   4. Ollama / BatchLLM fallback (local, no Claude)
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_AUTH_TOKEN = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
CLAUDE_CREDENTIALS_PATH = Path(
    os.getenv("CLAUDE_CREDENTIALS_PATH", "/claude-auth/.credentials.json")
)
CLAUDE_FAST_MODEL  = "claude-haiku-4-5-20251001"   # capability selection, routine planning
CLAUDE_SMART_MODEL = "claude-sonnet-4-6"            # wrapping, interface generation, analysis

# Keywords that indicate a goal needs Sonnet-level reasoning.
# Everything else uses Haiku — same Claude, lower cost.
_COMPLEX_KEYWORDS = {
    "wrap", "wrap_repo", "interface", "capability map", "capability_map",
    "analyze repo", "generate wrapper", "interface spec", "layer 3 bootstrap",
    "ingest", "generate interface", "app wrapper",
}


def _classify_prompt(prompt: str) -> str:
    """Return 'smart' (Sonnet) or 'fast' (Haiku) based on prompt content."""
    lowered = prompt.lower()
    if any(kw in lowered for kw in _COMPLEX_KEYWORDS):
        return "smart"
    return "fast"


def _read_claude_oauth_token() -> str:
    """
    Read the current OAuth access token from the Claude Code credentials file.
    Returns empty string if the file is missing or malformed.
    Called fresh on every Claude request so token refresh by Claude Code
    is picked up automatically without restarting the container.
    """
    try:
        if CLAUDE_CREDENTIALS_PATH.exists():
            data = json.loads(CLAUDE_CREDENTIALS_PATH.read_text())
            token = data.get("claudeAiOauth", {}).get("accessToken", "")
            return token
    except Exception:
        pass
    return ""


def _get_claude_client():
    """
    Return an Anthropic client using the best available auth method.
    Priority: OAuth credentials file → ANTHROPIC_AUTH_TOKEN → ANTHROPIC_API_KEY
    Returns None if no auth is available.
    """
    import anthropic

    # 1. OAuth credentials file (auto-refreshed by Claude Code)
    oauth_token = _read_claude_oauth_token()
    if oauth_token:
        return anthropic.Anthropic(auth_token=oauth_token)

    # 2. OAuth token from env var
    if ANTHROPIC_AUTH_TOKEN:
        return anthropic.Anthropic(auth_token=ANTHROPIC_AUTH_TOKEN)

    # 3. Standard API key
    if ANTHROPIC_API_KEY:
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    return None


def _strip_code_fences(text: str) -> str:
    """
    Extract JSON from LLM output, handling:
    - Text that starts directly with the JSON object
    - Markdown code fences (```json...```)
    - Prose before the code fence ("Here's the wrapper:\n```json\n{...}\n```")
    """
    text = text.strip()
    # If there's a code fence anywhere, extract from the first one
    if "```" in text:
        # Find the first opening fence
        fence_start = text.index("```")
        after_fence = text[fence_start + 3:]
        # Skip optional language tag (json, python, etc.)
        first_newline = after_fence.find("\n")
        if first_newline != -1:
            after_fence = after_fence[first_newline + 1:]
        # Find closing fence
        close = after_fence.find("```")
        if close != -1:
            text = after_fence[:close].strip()
        else:
            text = after_fence.strip()
    # If output starts with prose before a JSON object, find the first { or [
    if text and not text.startswith(("{", "[")):
        brace = -1
        for i, ch in enumerate(text):
            if ch in ("{", "["):
                brace = i
                break
        if brace >= 0:
            text = text[brace:]
    return text


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

    def __init__(self, capability_graph=None, execution_engine=None, use_qwen=False):
        self._lock = threading.RLock()
        self._capability_graph = capability_graph
        self._execution_engine = execution_engine
        REASONING_PATH.mkdir(parents=True, exist_ok=True)

    def _ollama_model(self) -> str:
        """Read the configured reasoning model from config.json."""
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("ollama", {}).get("default_model", "mistral-nemo:12b")
        except Exception:
            return "mistral-nemo:12b"

    def _claude_generate(self, prompt: str, model: str) -> str:
        """
        Call Claude using the best available auth (OAuth or API key).
        Retries once with a fresh token read if a 401 is returned
        (handles the case where Claude Code refreshed the OAuth token mid-session).
        """
        import anthropic

        for attempt in range(2):
            client = _get_claude_client()
            if client is None:
                raise RuntimeError("No Claude auth configured")
            try:
                message = client.messages.create(
                    model=model,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                return _strip_code_fences(message.content[0].text.strip())
            except anthropic.AuthenticationError:
                if attempt == 0:
                    continue  # re-read credentials file and retry once
                raise

    def _generate(self, prompt: str, model_tier: str = "auto") -> str:
        """
        Generate a response. Routing priority:
          1. Claude (OAuth credentials file, OAuth env token, or API key)
          2. BatchLLM (local parallel GPU inference)
          3. Ollama (local fallback)

        model_tier: "auto"  → classify prompt → Haiku or Sonnet
                    "fast"  → Haiku  (capability selection, routine ops)
                    "smart" → Sonnet (wrapping, interface generation, analysis)
        """
        # 1. Claude (any auth method available)
        if _get_claude_client() is not None:
            try:
                if model_tier == "auto":
                    tier = _classify_prompt(prompt)
                else:
                    tier = model_tier
                model = CLAUDE_SMART_MODEL if tier == "smart" else CLAUDE_FAST_MODEL
                return self._claude_generate(prompt, model)
            except Exception:
                pass  # fall through to local models

        # 2. BatchLLM (local parallel GPU inference)
        try:
            from agents.batch_llm import get_server
            server = get_server()
            if server.ready:
                return server.generate(prompt)
        except Exception:
            pass

        # 3. Ollama
        import httpx
        model = self._ollama_model()
        resp = httpx.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "format": "json", "think": False},
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()

    # ── API ────────────────────────────────────────────────────────────────

    def reason(self, agent_id: str, intent: str) -> Tuple[Optional[str], dict, float, str]:
        """
        Reason about an agent intent.
        Returns (capability_id, params, confidence, reasoning_text).

        Uses Ollama to select the best capability and generate its parameters
        from the intent. Falls back to semantic top-match if Ollama fails.
        """
        reasoning_id = f"rsn-{uuid.uuid4().hex[:12]}"

        # Step 1: Find candidate capabilities via semantic search
        candidates = []
        if self._capability_graph:
            results = self._capability_graph.find(intent, top_k=5, similarity_threshold=0.2)
            candidates = [cap.capability_id for cap, _ in results]

        if not candidates:
            return (None, {}, 0.0, "No matching capabilities found")

        # Step 2: Ask Ollama to pick the best capability and generate params
        selected_cap, params, confidence, reasoning_text = self._ollama_reason(
            intent, candidates
        )

        # Step 3: Record
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

    def _ollama_reason(
        self, intent: str, candidates: List[str]
    ) -> Tuple[str, dict, float, str]:
        """
        Call Ollama with the intent and candidate capabilities (with their
        input schemas) and ask it to select the best one and generate params.

        Falls back to semantic top-match + empty params on any failure.
        """
        try:
            # Build capability list with description + param format
            cap_lines = []
            for cap_id in candidates[:5]:
                desc, schema = "", ""
                if self._capability_graph:
                    rec = self._capability_graph.get(cap_id)
                    if rec:
                        desc = rec.description[:80]
                        schema = rec.input_schema
                cap_lines.append(f"  {cap_id}: {desc} | params: {schema}")
            caps_text = "\n".join(cap_lines)

            # Load agent identity preamble
            identity_preamble = ""
            try:
                from agents.agent_identity import AgentIdentity
                ident = AgentIdentity.load_or_create(agent_id)
                identity_preamble = ident.preamble() + "\n\n"
            except Exception:
                pass

            prompt = (
                f"{identity_preamble}"
                f"Select the best capability for this agent intent and generate real params.\n"
                f"Intent: {intent}\n\n"
                f"Capabilities:\n{caps_text}\n\n"
                f"Rules: prefer semantic_search for any search/find/discover goal. "
                f"Only use fs_read if you know a specific real file path. "
                f"Generate real param values, not placeholder text.\n"
                f'Respond ONLY with JSON: {{"capability_id":"<id>","params":{{<params>}}}}'
            )

            raw = self._generate(prompt, model_tier="fast")
            result = json.loads(raw)

            cap_id = result.get("capability_id", "")
            if cap_id not in candidates:
                cap_id = candidates[0]

            params = result.get("params", {})
            if not isinstance(params, dict):
                params = {}

            return (cap_id, params, 0.90, f"batch_llm selected {cap_id}")

        except Exception as e:
            # Semantic top-match, no params — better than nothing
            return (candidates[0], {}, 0.50, f"fallback:{candidates[0]} ({e})")


    def plan(self, agent_id: str, objective: str) -> list:
        """
        Generate a multi-step execution plan for a goal.
        Uses ALL registered capabilities so planning has full context.
        Returns list of {capability_id, params, rationale} dicts.
        """
        candidates = []
        if self._capability_graph:
            # Planning needs all capabilities, not just the most similar ones
            all_caps = self._capability_graph.list_all(limit=100)
            candidates = [(cap.capability_id, cap.description[:60], cap.input_schema)
                          for cap in all_caps]

        if not candidates:
            return []

        return self._ollama_plan(agent_id, objective, candidates)

    def _ollama_plan(self, agent_id: str, objective: str, candidates: list) -> list:
        """
        Ask Ollama to generate a complete N-step plan.
        Params may contain {result} placeholder — substituted at execution time.
        Falls back to single semantic_search step on failure.
        """
        try:
            import httpx

            cap_lines = []
            for cap_id, desc, schema in candidates:
                cap_lines.append(f"  {cap_id}: {desc} | params: {schema}")
            caps_text = "\n".join(cap_lines)

            # Load agent identity preamble if available
            identity_preamble = ""
            try:
                from agents.agent_identity import AgentIdentity
                ident = AgentIdentity.load_or_create(agent_id)
                identity_preamble = ident.preamble() + "\n\n"
            except Exception:
                pass

            # Split out memory context if appended to objective
            goal_text = objective
            memory_section = ""
            if "\n\nRelevant past experience" in objective:
                parts = objective.split("\n\nRelevant past experience", 1)
                goal_text = parts[0]
                memory_section = "\n\nRelevant past experience" + parts[1] + "\n"

            # Build real file context so planner doesn't hallucinate paths
            real_files_section = ""
            try:
                import subprocess as _sp
                from pathlib import Path as _Path
                # Per-agent workspace files
                agent_ws = _Path(f"/agentOS/workspace/{agent_id}")
                agent_files = []
                if agent_ws.exists():
                    agent_files = [str(agent_ws / f.name)
                                   for f in agent_ws.iterdir() if f.is_file()][:10]
                # Key source files always available
                src_r = _sp.run(
                    ["find", "/agentOS/agents", "-name", "*.py",
                     "-not", "-path", "*/__pycache__/*"],
                    capture_output=True, text=True, timeout=3
                )
                src_files = src_r.stdout.strip().splitlines()[:12]
                all_files = agent_files + src_files
                if all_files:
                    real_files_section = (
                        "\nFiles that actually exist (use these paths exactly):\n"
                        + "\n".join(f"  {f}" for f in all_files) + "\n"
                    )
            except Exception:
                pass

            prompt = (
                f"{identity_preamble}"
                f"Plan 2-4 steps for an AI agent to accomplish this goal.\n"
                f"Goal: {goal_text}\n"
                f"{memory_section}"
                f"{real_files_section}\n"
                f"Available capabilities:\n{caps_text}\n\n"
                f"Rules:\n"
                f"- Use shell_exec to discover files before reading them if unsure they exist\n"
                f"- Use fs_read or shell_exec to read actual file contents — do NOT invent content\n"
                f"- Use ollama_chat to analyze or summarize real data from previous steps\n"
                f"- Save results with fs_write (to /agentOS/workspace/{agent_id}/) or memory_set IF the goal requires output\n"
                f"- NOT every goal needs fs_write — if the goal is analysis/search, memory_set is fine\n"
                f"- shell_exec params.command MUST be a real shell command. NEVER put English sentences or bare filenames in command.\n"
                f"- IMPORTANT: for params that depend on a previous step output, use EXACTLY the string {{result}} as the entire value\n"
                f'  Example: {{"prompt": "Analyze this: {{result}}"}}, {{"value": "{{result}}"}}, {{"content": "{{result}}"}}\n'
                f"- Do NOT use nested objects or arrays as placeholder values\n"
                f"- NEVER use {{result}} for 'url' params — always hardcode the literal URL (e.g. https://github.com/owner/repo)\n"
                f"- NEVER use shell_exec 'which git' before git_clone — git is always available. Use git_clone directly with the real URL.\n"
                f"- NEVER reference file paths that are not listed above unless using shell_exec to discover them first\n"
                f"- Do NOT write Python or bash scripts as fs_write content — use shell_exec to RUN commands now and record the real output as findings.\n"
                f"- When using git_clone, use a REAL, SPECIFIC GitHub URL (e.g. https://github.com/octocat/Hello-World). NEVER use placeholder URLs like https://github.com/owner/repo.git.\n\n"
                f'Respond ONLY with JSON: {{"steps":[{{"capability_id":"...","params":{{...}},"rationale":"..."}},...]}}'
            )

            raw = self._generate(prompt, model_tier="smart")
            result = json.loads(raw)
            steps = result.get("steps", [])

            # Validate each step has required fields
            valid = []
            for s in steps:
                cap_id = s.get("capability_id", "")
                valid_ids = [c[0] for c in candidates]
                if cap_id not in valid_ids:
                    continue
                params = s.get("params", {})
                if not isinstance(params, dict):
                    params = {}
                valid.append({
                    "capability_id": cap_id,
                    "params": params,
                    "rationale": s.get("rationale", ""),
                })

            if valid:
                _thought(agent_id, f"GOAL: {objective[:100]}")
                plan_str = " → ".join(s["capability_id"] for s in valid)
                _thought(agent_id, f"PLAN: {plan_str}")
                for i, s in enumerate(valid):
                    params_preview = json.dumps(s["params"])[:120]
                    _thought(agent_id, f"  step {i+1}: {s['capability_id']} | {params_preview}")

            return valid if valid else []

        except Exception as e:
            return []

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
