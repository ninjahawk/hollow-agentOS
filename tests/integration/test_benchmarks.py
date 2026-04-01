"""
Integration tests for AgentOS v1.3.6: Real Benchmark Suite.

Tests verify the benchmark infrastructure: API routes, report structure,
metric validity, regression detection, and scenario correctness.

The full suite takes ~60 seconds; individual scenarios run in <10s each.
Ollama-dependent scenarios are skipped automatically if Ollama is unavailable.

Run:
    PYTHONPATH=. pytest tests/integration/test_benchmarks.py -v -m integration
"""

import time
import pytest
import requests

pytestmark = pytest.mark.integration

API_URL = "http://localhost:7777"


def _api_reachable() -> bool:
    try:
        r = requests.get(f"{API_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


if not _api_reachable():
    pytest.skip(
        "AgentOS API not reachable at http://localhost:7777",
        allow_module_level=True,
    )


def _ollama_available() -> bool:
    try:
        r = requests.get("http://localhost:11434/api/version", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_benchmarks(auth_headers, scenarios=None, include_ollama=False, timeout=120):
    body = {"include_ollama": include_ollama}
    if scenarios:
        body["scenarios"] = scenarios
    r = requests.post(f"{API_URL}/benchmarks/run", json=body, headers=auth_headers,
                      timeout=timeout)
    assert r.status_code == 200, f"benchmark run failed: {r.text}"
    return r.json()


def _get_results(auth_headers, limit=5):
    r = requests.get(f"{API_URL}/benchmarks/results", params={"limit": limit},
                     headers=auth_headers)
    assert r.status_code == 200, f"benchmark results failed: {r.text}"
    return r.json()


def _compare(auth_headers, baseline=None, current=None):
    params = {}
    if baseline:
        params["baseline"] = baseline
    if current:
        params["current"] = current
    r = requests.get(f"{API_URL}/benchmarks/compare", params=params, headers=auth_headers)
    return r


# ---------------------------------------------------------------------------
# Test 1 — Single structural scenario: heap_alloc_throughput
# ---------------------------------------------------------------------------

class TestHeapAllocScenario:
    def test_heap_alloc_throughput_metrics_valid(self, auth_headers):
        """
        Run heap_alloc_throughput scenario.
        Assert: passed=True, alloc_ops_per_sec > 0, mean_ms > 0.
        """
        report = _run_benchmarks(auth_headers, scenarios=["heap_alloc_throughput"])

        assert report["run_id"], "Expected non-empty run_id"
        results = {r["scenario"]: r for r in report["results"]}
        assert "heap_alloc_throughput" in results

        r = results["heap_alloc_throughput"]
        assert r["passed"] is True, f"heap_alloc_throughput failed: {r['error']}"
        assert r["metrics"]["alloc_ops_per_sec"] > 0
        assert r["metrics"]["mean_ms"] > 0


# ---------------------------------------------------------------------------
# Test 2 — Single structural scenario: message_bus_latency
# ---------------------------------------------------------------------------

class TestMessageBusScenario:
    def test_message_bus_latency_metrics_valid(self, auth_headers):
        """
        Run message_bus_latency scenario.
        Assert: passed=True, p50_ms < p99_ms, all latencies > 0.
        """
        report = _run_benchmarks(auth_headers, scenarios=["message_bus_latency"])
        results = {r["scenario"]: r for r in report["results"]}
        r = results["message_bus_latency"]

        assert r["passed"] is True, f"message_bus_latency failed: {r['error']}"
        m = r["metrics"]
        assert m["p50_ms"] > 0
        assert m["p95_ms"] >= m["p50_ms"], "p95 should be >= p50"
        assert m["p99_ms"] >= m["p95_ms"], "p99 should be >= p95"
        assert m["mean_ms"] > 0


# ---------------------------------------------------------------------------
# Test 3 — Single structural scenario: consensus_vote_latency
# ---------------------------------------------------------------------------

class TestConsensusScenario:
    def test_consensus_vote_latency_metrics_valid(self, auth_headers):
        """
        Run consensus_vote_latency scenario.
        Assert: passed=True, mean_ms > 0.
        """
        report = _run_benchmarks(auth_headers, scenarios=["consensus_vote_latency"])
        results = {r["scenario"]: r for r in report["results"]}
        r = results["consensus_vote_latency"]

        assert r["passed"] is True, f"consensus_vote_latency failed: {r['error']}"
        assert r["metrics"]["mean_ms"] > 0


# ---------------------------------------------------------------------------
# Test 4 — Rate limit precision scenario
# ---------------------------------------------------------------------------

class TestRateLimitScenario:
    def test_rate_limit_precision_correct(self, auth_headers):
        """
        Run rate_limit_precision scenario.
        Assert: passed=True (3 allowed, 2 blocked at bucket depth 3).
        """
        report = _run_benchmarks(auth_headers, scenarios=["rate_limit_precision"])
        results = {r["scenario"]: r for r in report["results"]}
        r = results["rate_limit_precision"]

        assert r["passed"] is True, (
            f"rate_limit_precision failed: {r['error']}. Metrics: {r['metrics']}"
        )
        assert r["metrics"]["correct"] == 1.0
        assert r["metrics"]["passed_count"] == 3.0
        assert r["metrics"]["blocked_count"] == 2.0


# ---------------------------------------------------------------------------
# Test 5 — Full structural suite
# ---------------------------------------------------------------------------

class TestFullStructuralSuite:
    def test_all_structural_scenarios_pass(self, auth_headers):
        """
        Run all 7 structural scenarios.
        Assert: all scenarios present in results.
        Assert: all scenarios passed=True.
        Assert: report structure is complete.
        """
        report = _run_benchmarks(auth_headers, include_ollama=False, timeout=120)

        assert "run_id" in report
        assert "started_at" in report
        assert "finished_at" in report
        assert "summary" in report
        assert isinstance(report["results"], list)

        STRUCTURAL = [
            "heap_alloc_throughput",
            "message_bus_latency",
            "transaction_commit_latency",
            "checkpoint_roundtrip",
            "consensus_vote_latency",
            "rate_limit_precision",
            "audit_write_throughput",
        ]
        results = {r["scenario"]: r for r in report["results"]}
        for scenario in STRUCTURAL:
            assert scenario in results, f"Missing scenario: {scenario}"
            r = results[scenario]
            assert r["passed"] is True, (
                f"Scenario {scenario} failed: {r.get('error')}"
            )
            assert len(r["metrics"]) > 0, f"Scenario {scenario} returned no metrics"

        # Summary matches results
        for scenario in STRUCTURAL:
            assert scenario in report["summary"], f"Missing {scenario} in summary"


# ---------------------------------------------------------------------------
# Test 6 — Results persistence
# ---------------------------------------------------------------------------

class TestResultsPersistence:
    def test_results_endpoint_returns_recent_runs(self, auth_headers):
        """
        Run benchmark, then GET /benchmarks/results.
        Assert: most recent run appears in results list.
        """
        report = _run_benchmarks(auth_headers,
                                  scenarios=["heap_alloc_throughput", "message_bus_latency"])
        run_id = report["run_id"]

        results = _get_results(auth_headers, limit=10)
        assert "results" in results
        assert results["count"] >= 1

        ids = [r["run_id"] for r in results["results"]]
        assert run_id in ids, f"run_id {run_id} not found in results: {ids}"


# ---------------------------------------------------------------------------
# Test 7 — Regression comparison
# ---------------------------------------------------------------------------

class TestRegressionComparison:
    def test_compare_two_runs_returns_regression_report(self, auth_headers):
        """
        Run benchmark twice. Compare. Assert: report structure is valid.
        """
        _run_benchmarks(auth_headers, scenarios=["heap_alloc_throughput", "message_bus_latency"])
        _run_benchmarks(auth_headers, scenarios=["heap_alloc_throughput", "message_bus_latency"])

        r = _compare(auth_headers)
        assert r.status_code == 200, f"compare failed: {r.text}"
        report = r.json()

        assert "baseline_run_id" in report
        assert "current_run_id" in report
        assert "regressions" in report
        assert "improvements" in report
        assert "unchanged" in report
        assert "regression_count" in report
        assert "improvement_count" in report
        assert isinstance(report["regressions"], list)
        assert isinstance(report["improvements"], list)

    def test_compare_requires_two_runs(self, auth_headers):
        """
        If only one run exists (or none), compare returns 400.
        This tests behavior when compare is called with explicit bad run IDs.
        """
        r = _compare(auth_headers, baseline="nonexistent-id", current="also-nonexistent")
        assert r.status_code == 400, (
            f"Expected 400 for nonexistent run IDs, got {r.status_code}: {r.text}"
        )


# ---------------------------------------------------------------------------
# Test 8 — Ollama task latency (Ollama-dependent)
# ---------------------------------------------------------------------------

class TestOllamaTaskLatency:
    def test_task_latency_c1_measured(self, auth_headers):
        """
        Run task_latency_c1 scenario with Ollama available.
        Assert: passed=True, task_ms > 0.
        """
        if not _ollama_available():
            pytest.skip("Ollama not available — skipping task latency benchmark")

        report = _run_benchmarks(auth_headers,
                                  scenarios=["task_latency_c1"],
                                  include_ollama=True,
                                  timeout=180)
        results = {r["scenario"]: r for r in report["results"]}
        r = results.get("task_latency_c1")
        assert r is not None, "task_latency_c1 missing from report"
        assert r["passed"] is True, f"task_latency_c1 failed: {r['error']}"
        assert r["metrics"]["task_ms"] > 0
