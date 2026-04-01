"""
Real Benchmark Suite — AgentOS v1.3.6.

Every metric comes from a real API call against the live server.
No mocked responses. No constructed strings. No simulated latency.

Scenarios covered:
  heap_alloc_throughput      — alloc/free ops/sec against working memory kernel
  message_bus_latency        — send→receive round-trip p50/p95/p99 (ms)
  transaction_commit_latency — begin→stage→commit round-trip (ms)
  checkpoint_roundtrip       — save→restore→verify round-trip (ms)
  consensus_vote_latency     — propose→vote×N→resolved wall time (ms)
  rate_limit_precision       — verify 429 fires at correct bucket depth
  audit_write_throughput     — operations/sec captured in audit log
  task_latency_c1            — task submit→done wall time at complexity 1 (Ollama)
  task_latency_c3            — task submit→done wall time at complexity 3 (Ollama)

Design:
  BenchmarkManager stores results to disk as timestamped JSON.
  Each run produces a BenchmarkReport with per-scenario ScenarioResult.
  Compare() diffs two runs and flags regressions (>10% degradation).
  Regression = metric that got worse: higher latency, lower throughput.
  The API exposes run/results/compare. MCP exposes three tools.

Regression threshold: 15% degradation from baseline.
Storage: /agentOS/memory/benchmark-results.json (list of BenchmarkReport dicts)
"""

import json
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

RESULTS_PATH = Path(os.getenv("AGENTOS_MEMORY_PATH", "/agentOS/memory")) / "benchmark-results.json"
REGRESSION_THRESHOLD = 0.15   # 15% degradation = regression

# Metric direction: True = higher is better, False = lower is better
METRIC_DIRECTION: dict[str, bool] = {
    "ops_per_sec":          True,
    "alloc_ops_per_sec":    True,
    "throughput_ops_per_sec": True,
    "p50_ms":               False,
    "p95_ms":               False,
    "p99_ms":               False,
    "mean_ms":              False,
    "commit_ms":            False,
    "roundtrip_ms":         False,
    "wall_time_ms":         False,
    "task_ms":              False,
    "entries_per_sec":      True,
    "rejected_correctly":   True,   # Boolean as float (1.0 = passed)
}


@dataclass
class ScenarioResult:
    scenario: str
    metrics: dict                    # {metric_name: float}
    passed: bool                     # scenario completed without error
    error: Optional[str]             # error message if not passed
    duration_ms: float               # wall time for scenario execution


@dataclass
class BenchmarkReport:
    run_id: str
    started_at: float
    finished_at: float
    api_url: str
    scenarios_run: list              # [scenario_name, ...]
    results: list                    # [ScenarioResult dict, ...]
    ollama_available: bool
    summary: dict                    # {scenario: {metric: value}}


@dataclass
class RegressionReport:
    baseline_run_id: str
    current_run_id: str
    regressions: list                # [{scenario, metric, baseline, current, delta_pct}]
    improvements: list               # [{scenario, metric, baseline, current, delta_pct}]
    unchanged: list                  # scenarios with no significant change
    regression_count: int
    improvement_count: int


class BenchmarkManager:
    """
    Run benchmark scenarios against the live AgentOS API.
    Store results. Compare runs. Detect regressions.
    Thread-safe. One instance per server.
    """

    def __init__(self, api_url: str = "http://localhost:7777"):
        self._api_url = api_url
        self._lock = threading.Lock()
        self._master_token: Optional[str] = None
        self._results: list[dict] = []
        self._load()

    def set_master_token(self, token: str) -> None:
        self._master_token = token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._master_token}",
                "Content-Type": "application/json"}

    # ── Scenario runners ──────────────────────────────────────────────────────

    def _run_heap_alloc_throughput(self) -> ScenarioResult:
        """Alloc and free 50 objects, measure ops/sec."""
        start = time.time()
        try:
            ts = int(time.time() * 1000)
            keys = [f"bench-heap-{ts}-{i}" for i in range(50)]
            # Register ephemeral agent for the benchmark
            agent_id, token = self._register_agent(f"bench-heap-{ts}")
            h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            t0 = time.time()
            for k in keys:
                self._post(f"/memory/alloc", {"key": k, "content": f"bench {k}", "priority": 5}, h)
            for k in keys:
                self._delete(f"/memory/{k}", h)
            elapsed = time.time() - t0
            ops = len(keys) * 2
            self._terminate_agent(agent_id)
            return ScenarioResult(
                scenario="heap_alloc_throughput",
                metrics={"alloc_ops_per_sec": round(ops / elapsed, 1),
                         "mean_ms": round(elapsed / ops * 1000, 2)},
                passed=True, error=None,
                duration_ms=round((time.time() - start) * 1000, 1),
            )
        except Exception as e:
            return ScenarioResult("heap_alloc_throughput", {}, False, str(e),
                                  round((time.time() - start) * 1000, 1))

    def _run_message_bus_latency(self) -> ScenarioResult:
        """Send 20 messages, measure send→receive round-trip latencies."""
        start = time.time()
        try:
            ts = int(time.time() * 1000)
            id_a, tok_a = self._register_agent(f"bench-mbus-a-{ts}")
            id_b, tok_b = self._register_agent(f"bench-mbus-b-{ts}")
            h_a = {"Authorization": f"Bearer {tok_a}", "Content-Type": "application/json"}
            h_b = {"Authorization": f"Bearer {tok_b}", "Content-Type": "application/json"}

            latencies = []
            for i in range(20):
                t0 = time.time()
                self._post("/messages", {"to_id": id_b, "content": {"text": f"ping {i}"}}, h_a)
                msgs = self._get("/messages?unread_only=true&limit=1", h_b)
                latencies.append((time.time() - t0) * 1000)

            self._terminate_agent(id_a)
            self._terminate_agent(id_b)

            latencies_sorted = sorted(latencies)
            n = len(latencies_sorted)
            return ScenarioResult(
                scenario="message_bus_latency",
                metrics={
                    "p50_ms": round(latencies_sorted[int(n * 0.50)], 2),
                    "p95_ms": round(latencies_sorted[int(n * 0.95)], 2),
                    "p99_ms": round(latencies_sorted[min(int(n * 0.99), n - 1)], 2),
                    "mean_ms": round(statistics.mean(latencies), 2),
                },
                passed=True, error=None,
                duration_ms=round((time.time() - start) * 1000, 1),
            )
        except Exception as e:
            return ScenarioResult("message_bus_latency", {}, False, str(e),
                                  round((time.time() - start) * 1000, 1))

    def _run_transaction_commit_latency(self) -> ScenarioResult:
        """begin → stage 3 memory_set ops → commit, measure round-trip ms."""
        start = time.time()
        try:
            ts = int(time.time() * 1000)
            agent_id, token = self._register_agent(f"bench-txn-{ts}")
            h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

            latencies = []
            for i in range(10):
                t0 = time.time()
                r = self._post("/txn/begin", {}, h)
                txn_id = r["txn_id"]
                for j in range(3):
                    self._post("/txn/stage", {
                        "txn_id": txn_id,
                        "op_type": "memory_set",
                        "params": {"key": f"bench-txn-{ts}-{i}-{j}", "content": f"v{j}"},
                        "resource": f"bench-txn-{ts}-{i}-{j}",
                    }, h)
                self._post("/txn/commit", {"txn_id": txn_id}, h)
                latencies.append((time.time() - t0) * 1000)

            self._terminate_agent(agent_id)
            return ScenarioResult(
                scenario="transaction_commit_latency",
                metrics={
                    "mean_ms": round(statistics.mean(latencies), 2),
                    "p95_ms":  round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
                },
                passed=True, error=None,
                duration_ms=round((time.time() - start) * 1000, 1),
            )
        except Exception as e:
            return ScenarioResult("transaction_commit_latency", {}, False, str(e),
                                  round((time.time() - start) * 1000, 1))

    def _run_checkpoint_roundtrip(self) -> ScenarioResult:
        """alloc 10 objects → save checkpoint → free objects → restore → verify ms."""
        start = time.time()
        try:
            ts = int(time.time() * 1000)
            agent_id, token = self._register_agent(f"bench-chk-{ts}")
            ah = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            mh = self._headers()

            keys = [f"bench-chk-{ts}-{i}" for i in range(10)]
            for k in keys:
                self._post("/memory/alloc", {"key": k, "content": f"bench {k}", "priority": 5}, ah)

            t0 = time.time()
            r = self._post(f"/agents/{agent_id}/checkpoint", {"label": "bench"}, mh)
            chk_id = r["checkpoint_id"]
            for k in keys:
                self._delete(f"/memory/{k}", ah)
            self._post(f"/agents/{agent_id}/restore/{chk_id}", {}, mh)
            roundtrip_ms = (time.time() - t0) * 1000

            # Verify one key is back
            r2 = self._get_raw(f"/memory/read/{keys[0]}", ah)
            verified = r2.get("content", "") != ""

            self._terminate_agent(agent_id)
            return ScenarioResult(
                scenario="checkpoint_roundtrip",
                metrics={"roundtrip_ms": round(roundtrip_ms, 2),
                         "restore_verified": 1.0 if verified else 0.0},
                passed=verified, error=None if verified else "restore did not recover memory",
                duration_ms=round((time.time() - start) * 1000, 1),
            )
        except Exception as e:
            return ScenarioResult("checkpoint_roundtrip", {}, False, str(e),
                                  round((time.time() - start) * 1000, 1))

    def _run_consensus_vote_latency(self) -> ScenarioResult:
        """propose + 2 votes → resolved. Measure wall time for full lifecycle."""
        start = time.time()
        try:
            ts = int(time.time() * 1000)
            id_a, tok_a = self._register_agent(f"bench-con-a-{ts}")
            id_b, tok_b = self._register_agent(f"bench-con-b-{ts}")
            h_a = {"Authorization": f"Bearer {tok_a}", "Content-Type": "application/json"}
            h_b = {"Authorization": f"Bearer {tok_b}", "Content-Type": "application/json"}

            latencies = []
            for i in range(5):
                t0 = time.time()
                r = self._post("/consensus/propose", {
                    "description": f"bench proposal {i}",
                    "action": {"op": i},
                    "participants": [id_a, id_b],
                    "required_votes": 2,
                    "ttl_seconds": 30.0,
                }, h_a)
                pid = r["proposal_id"]
                self._post(f"/consensus/{pid}/vote", {"accept": True, "reason": ""}, h_a)
                self._post(f"/consensus/{pid}/vote", {"accept": True, "reason": ""}, h_b)
                latencies.append((time.time() - t0) * 1000)

            self._terminate_agent(id_a)
            self._terminate_agent(id_b)
            return ScenarioResult(
                scenario="consensus_vote_latency",
                metrics={
                    "mean_ms": round(statistics.mean(latencies), 2),
                    "p95_ms":  round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
                },
                passed=True, error=None,
                duration_ms=round((time.time() - start) * 1000, 1),
            )
        except Exception as e:
            return ScenarioResult("consensus_vote_latency", {}, False, str(e),
                                  round((time.time() - start) * 1000, 1))

    def _run_rate_limit_precision(self) -> ScenarioResult:
        """Exhaust shell_calls bucket, verify 429 fires at correct depth."""
        start = time.time()
        try:
            ts = int(time.time() * 1000)
            agent_id, token = self._register_agent(f"bench-rl-{ts}")
            mh = self._headers()
            ah = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

            # Set very tight limit: 3 shell_calls
            self._post(f"/agents/{agent_id}/rate-limits", {
                "limits": {"shell_calls": 3}
            }, mh)

            # Exhaust it
            responses = []
            for i in range(5):
                r = self._raw_post(f"/shell", {"command": "echo bench"}, ah)
                responses.append(r)

            passed_count  = sum(1 for r in responses if r == 200)
            blocked_count = sum(1 for r in responses if r == 429)
            correct = passed_count == 3 and blocked_count == 2

            self._terminate_agent(agent_id)
            return ScenarioResult(
                scenario="rate_limit_precision",
                metrics={
                    "passed_count":   float(passed_count),
                    "blocked_count":  float(blocked_count),
                    "correct":        1.0 if correct else 0.0,
                },
                passed=correct,
                error=None if correct else f"expected 3 pass + 2 block, got {passed_count}+{blocked_count}",
                duration_ms=round((time.time() - start) * 1000, 1),
            )
        except Exception as e:
            return ScenarioResult("rate_limit_precision", {}, False, str(e),
                                  round((time.time() - start) * 1000, 1))

    def _run_audit_write_throughput(self) -> ScenarioResult:
        """Fire 50 audited operations, measure entries/sec via /audit/stats."""
        start = time.time()
        try:
            ts = int(time.time() * 1000)
            agent_id, token = self._register_agent(f"bench-audit-{ts}")
            ah = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

            stats_before = self._get(f"/audit/stats/{agent_id}", self._headers())
            count_before = stats_before.get("total_entries", 0)

            t0 = time.time()
            for i in range(50):
                self._post("/memory/alloc", {"key": f"bench-a-{ts}-{i}",
                                              "content": f"audit bench {i}", "priority": 1}, ah)
            elapsed = time.time() - t0

            stats_after = self._get(f"/audit/stats/{agent_id}", self._headers())
            count_after = stats_after.get("total_entries", 0)
            new_entries = count_after - count_before

            self._terminate_agent(agent_id)
            return ScenarioResult(
                scenario="audit_write_throughput",
                metrics={
                    "entries_per_sec": round(new_entries / elapsed, 1),
                    "entries_captured": float(new_entries),
                },
                passed=new_entries >= 45,   # allow ≤5 missed from pre-run ops
                error=None if new_entries >= 45 else f"only {new_entries}/50 entries captured",
                duration_ms=round((time.time() - start) * 1000, 1),
            )
        except Exception as e:
            return ScenarioResult("audit_write_throughput", {}, False, str(e),
                                  round((time.time() - start) * 1000, 1))

    def _run_task_latency(self, complexity: int) -> ScenarioResult:
        """Submit task at complexity level, measure submit→done wall time."""
        scenario = f"task_latency_c{complexity}"
        start = time.time()
        try:
            ts = int(time.time() * 1000)
            agent_id, token = self._register_agent(f"bench-task-c{complexity}-{ts}",
                                                    capabilities=["ollama"])
            ah = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

            t0 = time.time()
            r = self._post("/tasks/submit", {
                "description": "Say 'benchmark' and nothing else.",
                "complexity": complexity,
                "wait": True,
            }, ah, timeout=180)
            task_ms = (time.time() - t0) * 1000

            success = r.get("status") == "done"
            self._terminate_agent(agent_id)
            return ScenarioResult(
                scenario=scenario,
                metrics={"task_ms": round(task_ms, 1)},
                passed=success,
                error=None if success else r.get("error", "task failed"),
                duration_ms=round((time.time() - start) * 1000, 1),
            )
        except Exception as e:
            return ScenarioResult(scenario, {}, False, str(e),
                                  round((time.time() - start) * 1000, 1))

    # ── Run orchestration ─────────────────────────────────────────────────────

    STRUCTURAL_SCENARIOS = [
        "heap_alloc_throughput",
        "message_bus_latency",
        "transaction_commit_latency",
        "checkpoint_roundtrip",
        "consensus_vote_latency",
        "rate_limit_precision",
        "audit_write_throughput",
    ]

    OLLAMA_SCENARIOS = [
        "task_latency_c1",
        "task_latency_c3",
    ]

    def run(
        self,
        scenarios: Optional[list] = None,
        include_ollama: bool = False,
    ) -> BenchmarkReport:
        """
        Run benchmark scenarios. Returns BenchmarkReport.
        If scenarios is None, runs all structural scenarios.
        If include_ollama=True and Ollama is available, also runs Ollama scenarios.
        """
        run_id = str(uuid.uuid4())[:12]
        started_at = time.time()

        ollama_ok = include_ollama and self._check_ollama()
        to_run = scenarios or list(self.STRUCTURAL_SCENARIOS)
        if ollama_ok:
            to_run = to_run + [s for s in self.OLLAMA_SCENARIOS if s not in to_run]

        results = []
        summary = {}
        for name in to_run:
            result = self._dispatch(name)
            results.append(asdict(result))
            summary[name] = result.metrics

        report = BenchmarkReport(
            run_id=run_id,
            started_at=started_at,
            finished_at=time.time(),
            api_url=self._api_url,
            scenarios_run=to_run,
            results=results,
            ollama_available=ollama_ok,
            summary=summary,
        )

        with self._lock:
            self._results.append(asdict(report))
            # Keep last 20 runs
            self._results = self._results[-20:]
            self._save()

        return report

    def _dispatch(self, name: str) -> ScenarioResult:
        dispatch = {
            "heap_alloc_throughput":      self._run_heap_alloc_throughput,
            "message_bus_latency":        self._run_message_bus_latency,
            "transaction_commit_latency": self._run_transaction_commit_latency,
            "checkpoint_roundtrip":       self._run_checkpoint_roundtrip,
            "consensus_vote_latency":     self._run_consensus_vote_latency,
            "rate_limit_precision":       self._run_rate_limit_precision,
            "audit_write_throughput":     self._run_audit_write_throughput,
            "task_latency_c1":            lambda: self._run_task_latency(1),
            "task_latency_c3":            lambda: self._run_task_latency(3),
        }
        fn = dispatch.get(name)
        if not fn:
            return ScenarioResult(name, {}, False, f"unknown scenario {name!r}", 0.0)
        return fn()

    # ── Results and comparison ────────────────────────────────────────────────

    def get_results(self, limit: int = 5) -> list:
        with self._lock:
            return self._results[-limit:]

    def compare(self, baseline_run_id: Optional[str] = None, current_run_id: Optional[str] = None) -> dict:
        """
        Compare two runs. If run IDs not specified, compare last two runs.
        Returns RegressionReport as dict.
        """
        with self._lock:
            results = list(self._results)

        if len(results) < 2:
            return {"error": "Need at least 2 benchmark runs to compare"}

        if baseline_run_id and current_run_id:
            baseline = next((r for r in results if r["run_id"] == baseline_run_id), None)
            current  = next((r for r in results if r["run_id"] == current_run_id), None)
        else:
            baseline = results[-2]
            current  = results[-1]

        if not baseline or not current:
            return {"error": "Specified run IDs not found"}

        regressions = []
        improvements = []
        unchanged = []

        for scenario in set(baseline["summary"]) & set(current["summary"]):
            b_metrics = baseline["summary"][scenario]
            c_metrics = current["summary"][scenario]
            scenario_changed = False

            for metric, b_val in b_metrics.items():
                c_val = c_metrics.get(metric)
                if c_val is None or b_val == 0:
                    continue
                delta_pct = (c_val - b_val) / abs(b_val)
                higher_is_better = METRIC_DIRECTION.get(metric, False)

                if higher_is_better:
                    degraded = delta_pct < -REGRESSION_THRESHOLD
                    improved = delta_pct >  REGRESSION_THRESHOLD
                else:
                    degraded = delta_pct >  REGRESSION_THRESHOLD
                    improved = delta_pct < -REGRESSION_THRESHOLD

                entry = {
                    "scenario": scenario, "metric": metric,
                    "baseline": b_val, "current": c_val,
                    "delta_pct": round(delta_pct * 100, 1),
                }
                if degraded:
                    regressions.append(entry)
                    scenario_changed = True
                elif improved:
                    improvements.append(entry)
                    scenario_changed = True

            if not scenario_changed:
                unchanged.append(scenario)

        return asdict(RegressionReport(
            baseline_run_id=baseline["run_id"],
            current_run_id=current["run_id"],
            regressions=regressions,
            improvements=improvements,
            unchanged=unchanged,
            regression_count=len(regressions),
            improvement_count=len(improvements),
        ))

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _register_agent(self, name: str, capabilities: Optional[list] = None):
        body = {"name": name, "role": "worker"}
        if capabilities:
            body["capabilities"] = capabilities
        r = self._post("/agents/register", body, self._headers())
        return r["agent_id"], r["token"]

    def _terminate_agent(self, agent_id: str):
        try:
            req = urllib.request.Request(
                f"{self._api_url}/agents/{agent_id}",
                headers=self._headers(), method="DELETE",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    def _post(self, path: str, body: dict, headers: dict, timeout: int = 30) -> dict:
        req = urllib.request.Request(
            f"{self._api_url}{path}",
            data=json.dumps(body).encode(),
            headers=headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def _raw_post(self, path: str, body: dict, headers: dict) -> int:
        """Return HTTP status code without raising on 4xx."""
        try:
            req = urllib.request.Request(
                f"{self._api_url}{path}",
                data=json.dumps(body).encode(),
                headers=headers, method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def _get(self, path: str, headers: dict) -> dict:
        req = urllib.request.Request(f"{self._api_url}{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def _get_raw(self, path: str, headers: dict) -> dict:
        try:
            return self._get(path, headers)
        except Exception:
            return {}

    def _delete(self, path: str, headers: dict) -> None:
        try:
            req = urllib.request.Request(
                f"{self._api_url}{path}", headers=headers, method="DELETE"
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass

    def _check_ollama(self) -> bool:
        try:
            req = urllib.request.Request("http://localhost:11434/api/version")
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self) -> None:
        RESULTS_PATH.write_text(
            json.dumps(self._results, indent=2, default=str), encoding="utf-8"
        )

    def _load(self) -> None:
        if RESULTS_PATH.exists():
            try:
                self._results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
            except Exception:
                self._results = []
