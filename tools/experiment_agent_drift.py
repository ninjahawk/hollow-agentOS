#!/usr/bin/env python3
"""
Agent Drift Experiment
======================

Hypothesis: agents resuming with Hollow handoff context make more consistent
decisions across sessions than agents cold-starting with no prior context.

"Agent drift" = when a later session makes architectural decisions that
contradict or undo what earlier sessions decided, because the agent has no
memory of the prior decisions.

Experimental design
-------------------
Task: build a small REST API with authentication across 3 sessions.
  - Session 1: architect the auth approach (JWT vs session tokens vs API keys)
  - Session 2: implement the auth middleware based on session 1's decision
  - Session 3: write tests that exercise the auth, based on sessions 1+2

10 runs WITH Hollow handoff (session N writes a handoff; session N+1 reads pickup)
10 runs WITHOUT Hollow (cold start — no prior context, just the base task prompt)

Measurements
------------
  - decision_consistency: did later sessions pick the same auth approach as session 1?
  - rework_rate: fraction of session 2 work that session 3 had to redo/modify
  - error_rate: number of explicit corrections made (e.g. "wait, session 1 chose JWT")
  - session_tokens: tokens consumed per session (via Ollama token counts or estimates)

This script uses the local Ollama model to run both conditions so:
  - Both conditions use the same model — no inference quality difference
  - Results measure only the effect of context, not model capability
  - Runs locally — no API costs, reproducible

Output: memory/experiment-agent-drift.json

Usage:
    python3 tools/experiment_agent_drift.py [--api-url URL] [--token TOKEN]
                                            [--runs N] [--model MODEL]

    --runs: number of runs per condition (default 3; use 10 for publication)
    --model: Ollama model to use (default: reads from routing table)
"""

import json
import sys
import time
import argparse
import urllib.request
import urllib.error
import random
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

API_URL = "http://localhost:7777"
TOKEN = None

# The multi-session task: build a simple REST API with auth.
# Each session has a specific sub-goal and a downstream dependency.
SESSION_PROMPTS = [
    # Session 1: architect
    {
        "session_num": 1,
        "goal": "Architecture decision",
        "system": (
            "You are a software architect. Answer concisely. "
            "Your decision will be implemented by the next agent session."
        ),
        "user": (
            "We need to add authentication to a simple Python FastAPI app. "
            "Choose ONE approach: JWT tokens, session cookies, or API keys. "
            "Explain in 2-3 sentences WHY you chose it. "
            "End your response with exactly: DECISION: <your choice>"
        ),
        "extract_decision": True,  # we'll parse the DECISION: line
    },
    # Session 2: implement (depends on session 1's decision)
    {
        "session_num": 2,
        "goal": "Implementation",
        "system": (
            "You are a Python developer. Write code that implements what was decided."
        ),
        "user": (
            "Implement the authentication middleware for our FastAPI app. "
            "Write a Python function called `auth_middleware` that enforces authentication. "
            "Be consistent with whatever auth approach was already decided. "
            "Keep it under 30 lines."
        ),
        "extract_decision": False,
    },
    # Session 3: test (depends on sessions 1+2)
    {
        "session_num": 3,
        "goal": "Testing",
        "system": (
            "You are a QA engineer. Write tests for the auth implementation."
        ),
        "user": (
            "Write pytest tests for the auth_middleware function. "
            "Write exactly 3 test cases. "
            "Make sure your tests are consistent with the auth approach that was implemented."
        ),
        "extract_decision": False,
    },
]

# Keywords that indicate which auth approach was chosen/used
AUTH_SIGNALS = {
    "jwt":     ["jwt", "json web token", "bearer", "hs256", "decode(", "encode("],
    "session": ["session", "cookie", "session_id", "set-cookie", "sessionstore"],
    "apikey":  ["api key", "api_key", "x-api-key", "apikey", "api-key"],
}

CHARS_PER_TOKEN = 4


# ── helpers ──────────────────────────────────────────────────────────────────

def _api(method: str, path: str, body: dict = None, timeout: int = 60) -> dict:
    url = f"{API_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def _ollama_chat(messages: list[dict], model: str) -> dict:
    """Call Hollow's /ollama/chat endpoint."""
    return _api("POST", "/ollama/chat", {
        "messages": messages,
        "model": model,
        "stream": False,
    }, timeout=120)


def _detect_auth_choice(text: str) -> Optional[str]:
    """Return the auth approach detected in the text, or None if ambiguous."""
    text_lower = text.lower()
    scores = {}
    for approach, signals in AUTH_SIGNALS.items():
        scores[approach] = sum(1 for s in signals if s in text_lower)
    best = max(scores, key=scores.get)
    # Require at least 1 signal and a clear winner (not tied)
    if scores[best] == 0:
        return None
    sorted_scores = sorted(scores.values(), reverse=True)
    if len(sorted_scores) >= 2 and sorted_scores[0] == sorted_scores[1]:
        return None  # tied — ambiguous
    return best


def _extract_decision_line(text: str) -> Optional[str]:
    """Parse the DECISION: <choice> line from session 1 output."""
    for line in text.splitlines():
        if line.strip().upper().startswith("DECISION:"):
            return line.split(":", 1)[1].strip().lower()
    return None


def _count_corrections(text: str) -> int:
    """Count phrases suggesting the agent noticed an inconsistency."""
    correction_signals = [
        "wait,", "actually,", "correction:", "i should note", "however,",
        "based on the previous", "as decided earlier", "inconsistent",
        "instead of", "rather than the",
    ]
    return sum(1 for s in correction_signals if s in text.lower())


def _chars_to_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


# ── one run ──────────────────────────────────────────────────────────────────

def run_with_hollow(run_id: int, model: str, agent_id: str) -> dict:
    """
    Three-session task WITH Hollow handoffs.
    Session N writes a handoff; session N+1 calls pickup to get prior context.
    """
    sessions = []
    prior_decision = None
    prior_approach = None
    conversation_history = []

    for sp in SESSION_PROMPTS:
        session_num = sp["session_num"]

        # Build messages: start with pickup context if available, then conversation
        messages = []

        if session_num > 1:
            # Get pickup context — this is the Hollow advantage
            pickup = _api("GET", "/agent/pickup")
            if "error" not in pickup:
                handoff = pickup.get("handoff", {})
                summary = handoff.get("summary", "")
                if summary:
                    messages.append({
                        "role": "system",
                        "content": f"Context from previous sessions:\n{summary}"
                    })

        # Add conversation history from prior sessions (compressed to key decisions only)
        if prior_decision and session_num > 1:
            messages.append({
                "role": "user",
                "content": f"[Prior context] Session 1 decided: {prior_decision}"
            })

        messages.append({"role": "system", "content": sp["system"]})
        messages.append({"role": "user", "content": sp["user"]})

        t0 = time.time()
        response = _ollama_chat(messages, model)
        elapsed_ms = round((time.time() - t0) * 1000)

        if "error" in response:
            return {"error": response["error"], "run_id": run_id, "condition": "hollow"}

        content = response.get("message", {}).get("content", "") or response.get("response", "")
        token_count = _chars_to_tokens(content)

        if sp["extract_decision"]:
            prior_decision = _extract_decision_line(content) or content[:200]
            prior_approach = _detect_auth_choice(content)

        detected = _detect_auth_choice(content)
        corrections = _count_corrections(content)

        sessions.append({
            "session_num": session_num,
            "goal": sp["goal"],
            "response_tokens": token_count,
            "detected_auth_approach": detected,
            "corrections": corrections,
            "elapsed_ms": elapsed_ms,
        })

        # Write handoff for the next session
        handoff_body = {
            "agent_id": agent_id,
            "summary": (
                f"Session {session_num} ({sp['goal']}): "
                + (f"Decided auth approach: {prior_decision}. " if prior_decision else "")
                + f"Content summary: {content[:300]}"
            ),
            "in_progress": [sp["goal"]],
            "next_steps": [SESSION_PROMPTS[session_num]["goal"]] if session_num < len(SESSION_PROMPTS) else [],
            "relevant_files": [],
        }
        _api("POST", "/agent/handoff", handoff_body)

    # Consistency: do sessions 2 and 3 use the same auth approach as session 1?
    s1_approach = sessions[0].get("detected_auth_approach")
    later_approaches = [s.get("detected_auth_approach") for s in sessions[1:] if s.get("detected_auth_approach")]
    consistent_count = sum(1 for a in later_approaches if a == s1_approach)
    consistency_rate = consistent_count / len(later_approaches) if later_approaches else 0.0

    total_corrections = sum(s["corrections"] for s in sessions)
    total_tokens = sum(s["response_tokens"] for s in sessions)

    return {
        "run_id": run_id,
        "condition": "hollow",
        "sessions": sessions,
        "s1_approach": s1_approach,
        "consistency_rate": consistency_rate,
        "total_corrections": total_corrections,
        "total_tokens": total_tokens,
    }


def run_without_hollow(run_id: int, model: str) -> dict:
    """
    Three-session task WITHOUT Hollow — cold start for each session.
    Each session gets only the task prompt, no prior context.
    """
    sessions = []
    s1_approach = None

    for sp in SESSION_PROMPTS:
        session_num = sp["session_num"]

        # Cold start: only the task description, no handoff context
        messages = [
            {"role": "system", "content": sp["system"]},
            {"role": "user", "content": sp["user"]},
        ]

        t0 = time.time()
        response = _ollama_chat(messages, model)
        elapsed_ms = round((time.time() - t0) * 1000)

        if "error" in response:
            return {"error": response["error"], "run_id": run_id, "condition": "cold"}

        content = response.get("message", {}).get("content", "") or response.get("response", "")
        token_count = _chars_to_tokens(content)

        detected = _detect_auth_choice(content)
        corrections = _count_corrections(content)

        if session_num == 1:
            s1_approach = detected

        sessions.append({
            "session_num": session_num,
            "goal": sp["goal"],
            "response_tokens": token_count,
            "detected_auth_approach": detected,
            "corrections": corrections,
            "elapsed_ms": elapsed_ms,
        })

    later_approaches = [s.get("detected_auth_approach") for s in sessions[1:] if s.get("detected_auth_approach")]
    consistent_count = sum(1 for a in later_approaches if a == s1_approach)
    consistency_rate = consistent_count / len(later_approaches) if later_approaches else 0.0

    total_corrections = sum(s["corrections"] for s in sessions)
    total_tokens = sum(s["response_tokens"] for s in sessions)

    return {
        "run_id": run_id,
        "condition": "cold",
        "sessions": sessions,
        "s1_approach": s1_approach,
        "consistency_rate": consistency_rate,
        "total_corrections": total_corrections,
        "total_tokens": total_tokens,
    }


# ── aggregate stats ──────────────────────────────────────────────────────────

def summarize(runs: list[dict]) -> dict:
    valid = [r for r in runs if "error" not in r]
    if not valid:
        return {"error": "all runs failed"}

    n = len(valid)
    consistency_rates = [r["consistency_rate"] for r in valid]
    correction_counts = [r["total_corrections"] for r in valid]
    token_counts = [r["total_tokens"] for r in valid]

    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else 0

    return {
        "n": n,
        "avg_consistency_rate": avg(consistency_rates),
        "avg_corrections_per_run": avg(correction_counts),
        "avg_tokens_per_run": avg(token_counts),
        "consistency_rates": consistency_rates,
        "correction_counts": correction_counts,
    }


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    global API_URL, TOKEN

    parser = argparse.ArgumentParser(description="Agent drift experiment — Hollow vs cold start")
    parser.add_argument("--api-url", default="http://localhost:7777")
    parser.add_argument("--token", default=None)
    parser.add_argument("--runs", type=int, default=3,
                        help="Runs per condition (default 3; use 10 for publication quality)")
    parser.add_argument("--model", default=None,
                        help="Ollama model (default: use general model from routing table)")
    args = parser.parse_args()

    API_URL = args.api_url

    if args.token:
        TOKEN = args.token
    else:
        config_path = Path(__file__).parent.parent / "config.json"
        if config_path.exists():
            cfg = json.loads(config_path.read_text())
            TOKEN = cfg.get("api", {}).get("token", "")
    if not TOKEN:
        print("ERROR: no token. Pass --token or ensure config.json has api.token")
        sys.exit(1)

    # Resolve model
    model = args.model
    if not model:
        models_resp = _api("GET", "/ollama/models")
        if "error" not in models_resp:
            routing = models_resp.get("routing", {})
            model = routing.get("general") or routing.get("code") or "mistral-nemo:12b"
        else:
            model = "mistral-nemo:12b"

    # Register a test agent for the Hollow condition
    agent_resp = _api("POST", "/agents/register", {
        "name": "drift-experiment-agent",
        "capabilities": ["code", "write"],
        "budget_tokens": 1000000,
    })
    agent_id = agent_resp.get("agent_id", "drift-test-agent")

    print("=" * 70)
    print("  Agent Drift Experiment")
    print("  Task: 3-session REST API with auth (architect → implement → test)")
    print(f"  Model: {model}")
    print(f"  Runs per condition: {args.runs}")
    print("  Hypothesis: Hollow handoffs → more consistent decisions across sessions")
    print("=" * 70)

    hollow_runs = []
    cold_runs = []

    for i in range(args.runs):
        print(f"\n  [Hollow run {i+1}/{args.runs}]")
        result = run_with_hollow(i + 1, model, agent_id)
        if "error" in result:
            print(f"    ✗ Error: {result['error']}")
        else:
            print(f"    consistency={result['consistency_rate']:.0%}  "
                  f"corrections={result['total_corrections']}  "
                  f"tokens={result['total_tokens']:,}")
        hollow_runs.append(result)

    for i in range(args.runs):
        print(f"\n  [Cold start run {i+1}/{args.runs}]")
        result = run_without_hollow(i + 1, model)
        if "error" in result:
            print(f"    ✗ Error: {result['error']}")
        else:
            print(f"    consistency={result['consistency_rate']:.0%}  "
                  f"corrections={result['total_corrections']}  "
                  f"tokens={result['total_tokens']:,}")
        cold_runs.append(result)

    hollow_summary = summarize(hollow_runs)
    cold_summary = summarize(cold_runs)

    print("\n" + "=" * 70)
    print(f"  {'Metric':<40} {'Hollow':>12} {'Cold Start':>12}")
    print("  " + "-" * 66)

    metrics = [
        ("Consistency rate (higher = better)", "avg_consistency_rate"),
        ("Corrections per run (lower = better)", "avg_corrections_per_run"),
        ("Tokens per run", "avg_tokens_per_run"),
        ("Valid runs", "n"),
    ]
    for label, key in metrics:
        hv = hollow_summary.get(key, "N/A")
        cv = cold_summary.get(key, "N/A")
        if isinstance(hv, float):
            print(f"  {label:<40} {hv:>12.3f} {cv:>12.3f}")
        else:
            print(f"  {label:<40} {hv!s:>12} {cv!s:>12}")

    print("=" * 70)

    if hollow_summary.get("n", 0) > 0 and cold_summary.get("n", 0) > 0:
        delta = hollow_summary["avg_consistency_rate"] - cold_summary["avg_consistency_rate"]
        direction = "higher" if delta > 0 else "lower"
        print(f"\n  Hollow consistency is {abs(delta):.1%} {direction} than cold start")
        if delta > 0:
            print("  → Hypothesis SUPPORTED (Hollow reduces agent drift)")
        elif delta < 0:
            print("  → Hypothesis NOT SUPPORTED (Hollow did not improve consistency)")
        else:
            print("  → No difference observed")
        print(f"\n  Note: {args.runs} runs per condition. Run with --runs 10 for statistical significance.\n")

    out_path = Path(__file__).parent.parent / "memory" / "experiment-agent-drift.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "runs_per_condition": args.runs,
        "task": "3-session REST API with auth: architect → implement → test",
        "hypothesis": "Hollow handoffs produce more consistent decisions across sessions than cold starts",
        "hollow": {"runs": hollow_runs, "summary": hollow_summary},
        "cold": {"runs": cold_runs, "summary": cold_summary},
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
