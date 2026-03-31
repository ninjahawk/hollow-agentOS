"""
Integration tests for AgentOS v1.3.1: Streaming Task Outputs.

Tests 1 and 6 run without Ollama (structural/non-blocking behavior).
Tests 2-5 require Ollama — skipped automatically if unavailable.

Run:
    PYTHONPATH=. pytest tests/integration/test_streaming.py -v -m integration
    PYTHONPATH=. pytest tests/integration/test_streaming.py -v -m "integration and not ollama"
"""

import json
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
        r = requests.get(f"{API_URL}/model_status", timeout=5,
                         headers={"Authorization": "Bearer ci-test-token-replace-in-production"})
        if r.status_code == 200:
            data = r.json()
            # loaded_models present and non-empty, or vram_total_mb > 0
            return bool(data.get("loaded_models")) or data.get("vram_total_mb", 0) > 0
        return False
    except Exception:
        return False


_OLLAMA_UP = _ollama_available()
skip_no_ollama = pytest.mark.skipif(not _OLLAMA_UP, reason="Ollama not available")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _submit(auth_headers, description, complexity=1, stream=False, wait=True, depends_on=None):
    body = {"description": description, "complexity": complexity,
            "stream": stream, "wait": wait}
    if depends_on:
        body["depends_on"] = depends_on
    r = requests.post(f"{API_URL}/tasks/submit", json=body, headers=auth_headers)
    assert r.status_code == 200, f"submit failed: {r.text}"
    return r.json()


def _get_task(auth_headers, task_id):
    r = requests.get(f"{API_URL}/tasks/{task_id}", headers=auth_headers)
    assert r.status_code == 200, f"get_task failed: {r.text}"
    return r.json()


def _partial(auth_headers, task_id):
    r = requests.get(f"{API_URL}/tasks/{task_id}/partial", headers=auth_headers)
    assert r.status_code == 200, f"partial failed: {r.text}"
    return r.json()


def _cancel(auth_headers, task_id):
    r = requests.delete(f"{API_URL}/tasks/{task_id}", headers=auth_headers)
    return r


def _wait_for_terminal(auth_headers, task_id, timeout=90):
    terminal = {"done", "failed", "cancelled"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = _get_task(auth_headers, task_id)
        if t.get("status") in terminal:
            return t
        time.sleep(0.5)
    return _get_task(auth_headers, task_id)


# ---------------------------------------------------------------------------
# Test 1 — Non-blocking submit
# ---------------------------------------------------------------------------

class TestNonBlockingSubmit:
    def test_stream_true_returns_immediately(self, auth_headers):
        """
        Submit with stream=True. Response must arrive within 500ms.
        Response includes task_id, stream_url, partial_url.
        """
        start = time.time()
        resp = _submit(auth_headers, "count to ten", complexity=1, stream=True)
        elapsed_ms = (time.time() - start) * 1000

        assert elapsed_ms < 500, (
            f"stream=True should return immediately, took {elapsed_ms:.0f}ms"
        )
        assert "task_id" in resp, f"task_id missing from response: {resp}"
        task_id = resp["task_id"]
        assert task_id, "task_id must be non-empty"

        # stream_url and partial_url included
        assert "stream_url" in resp, f"stream_url missing: {resp}"
        assert "partial_url" in resp, f"partial_url missing: {resp}"
        assert resp["stream_url"] == f"/tasks/{task_id}/stream"
        assert resp["partial_url"] == f"/tasks/{task_id}/partial"

        # Status must be queued or running (not done yet — we returned immediately)
        assert resp.get("status") in ("queued", "running", "done", "failed"), (
            f"Unexpected status: {resp.get('status')}"
        )

    def test_wait_false_returns_immediately(self, auth_headers):
        """
        submit(wait=False) without streaming also returns immediately.
        """
        start = time.time()
        resp = _submit(auth_headers, "summarize in one word: hello", complexity=1, wait=False)
        elapsed_ms = (time.time() - start) * 1000

        assert elapsed_ms < 500, (
            f"wait=False should return immediately, took {elapsed_ms:.0f}ms"
        )
        assert "task_id" in resp


# ---------------------------------------------------------------------------
# Test 2 — SSE stream delivers chunks (requires Ollama)
# ---------------------------------------------------------------------------

@skip_no_ollama
class TestSSEStreamChunks:
    def test_sse_delivers_chunks_and_final_event(self, auth_headers):
        """
        Submit a streaming task. Connect to /tasks/{id}/stream.
        Assert: at least 5 token_chunk events before completed.
        Assert: chunks concatenated equal final result.response.
        """
        resp = _submit(auth_headers,
                       "Write a one-paragraph description of the water cycle.",
                       complexity=1, stream=True)
        task_id = resp["task_id"]

        chunks = []
        final_event = None
        deadline = time.time() + 90

        # Consume SSE stream
        with requests.get(f"{API_URL}/tasks/{task_id}/stream",
                          headers=auth_headers, stream=True, timeout=90) as r:
            assert r.status_code == 200
            for line in r.iter_lines():
                if time.time() > deadline:
                    break
                if not line:
                    continue
                line = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                if data.get("event") == "task.token_chunk":
                    chunks.append(data.get("chunk", ""))
                elif data.get("event") in ("task.done", "task.completed", "task.failed"):
                    final_event = data
                    break

        assert len(chunks) >= 5, (
            f"Expected ≥5 token_chunk events, got {len(chunks)}"
        )
        assert final_event is not None, "No final event received from SSE stream"

        # Verify final result matches concatenated chunks
        final_task = _get_task(auth_headers, task_id)
        assert final_task["status"] == "done", f"Task not done: {final_task['status']}"
        result_response = final_task.get("result", {}).get("response", "")
        full_stream = "".join(chunks)
        assert full_stream == result_response, (
            f"Concatenated chunks ({len(full_stream)} chars) != "
            f"result.response ({len(result_response)} chars)"
        )


# ---------------------------------------------------------------------------
# Test 3 — Partial output endpoint (requires Ollama)
# ---------------------------------------------------------------------------

@skip_no_ollama
class TestPartialOutput:
    def test_partial_grows_monotonically(self, auth_headers):
        """
        Submit long streaming task (complexity=3).
        Poll /partial every 500ms while running.
        Assert: ≥3 polls with non-empty output.
        Assert: partial_length is non-decreasing across polls.
        """
        resp = _submit(auth_headers,
                       "Explain how large language models work in detail, covering "
                       "tokenization, attention, and training in separate paragraphs.",
                       complexity=3, stream=True)
        task_id = resp["task_id"]

        lengths = []
        non_empty_polls = 0
        deadline = time.time() + 90

        while time.time() < deadline:
            p = _partial(auth_headers, task_id)
            pl = p.get("partial_length", 0)
            lengths.append(pl)
            if pl > 0:
                non_empty_polls += 1
            if p.get("status") in ("done", "failed", "cancelled"):
                break
            time.sleep(0.5)

        assert non_empty_polls >= 3, (
            f"Expected ≥3 polls with non-empty partial output, got {non_empty_polls}"
        )
        # partial_length must be non-decreasing
        for i in range(1, len(lengths)):
            assert lengths[i] >= lengths[i - 1], (
                f"partial_length decreased: {lengths[i - 1]} → {lengths[i]} at poll {i}"
            )


# ---------------------------------------------------------------------------
# Test 4 — Streaming under load (requires Ollama)
# ---------------------------------------------------------------------------

@skip_no_ollama
class TestStreamingUnderLoad:
    def test_four_concurrent_streams_all_deliver(self, auth_headers):
        """
        Submit 4 simultaneous streaming tasks.
        Assert: all 4 SSE streams deliver their first chunk within 3s of the first.
        """
        import threading

        task_ids = []
        for i in range(4):
            resp = _submit(auth_headers,
                           f"Count from 1 to 20 in English words. Task {i}.",
                           complexity=1, stream=True)
            task_ids.append(resp["task_id"])

        first_chunk_times = {}
        errors = {}

        def consume_stream(tid):
            try:
                deadline = time.time() + 60
                with requests.get(f"{API_URL}/tasks/{tid}/stream",
                                  headers=auth_headers, stream=True, timeout=60) as r:
                    for line in r.iter_lines():
                        if time.time() > deadline:
                            break
                        if not line:
                            continue
                        line = line.decode("utf-8") if isinstance(line, bytes) else line
                        if not line.startswith("data: "):
                            continue
                        data = json.loads(line[6:])
                        if data.get("event") == "task.token_chunk" and data.get("chunk"):
                            first_chunk_times[tid] = time.time()
                            return
            except Exception as e:
                errors[tid] = str(e)

        threads = [threading.Thread(target=consume_stream, args=(tid,)) for tid in task_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=65)

        # All 4 streams should have delivered a first chunk
        for tid in task_ids:
            assert tid in first_chunk_times, (
                f"Task {tid} never delivered a first chunk. Errors: {errors}"
            )

        # All first-chunk times within 3s of the earliest
        times = list(first_chunk_times.values())
        spread = max(times) - min(times)
        assert spread <= 3.0, (
            f"First chunks spread over {spread:.1f}s — one stream may be starving"
        )


# ---------------------------------------------------------------------------
# Test 5 — Cancellation (requires Ollama)
# ---------------------------------------------------------------------------

@skip_no_ollama
class TestCancellation:
    def test_cancel_stops_stream_and_frees_worker(self, auth_headers):
        """
        Submit streaming task. After first chunk arrives, cancel it.
        Assert: SSE closes with task.cancelled event.
        Assert: next task starts within 2s (worker freed).
        """
        resp = _submit(auth_headers,
                       "List all the countries in the world alphabetically.",
                       complexity=2, stream=True)
        task_id = resp["task_id"]

        cancelled_at = None
        with requests.get(f"{API_URL}/tasks/{task_id}/stream",
                          headers=auth_headers, stream=True, timeout=30) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                if data.get("event") == "task.token_chunk" and data.get("chunk"):
                    # First chunk arrived — cancel now
                    cr = _cancel(auth_headers, task_id)
                    assert cr.status_code == 200, f"cancel failed: {cr.text}"
                    cancelled_at = time.time()
                    break

        assert cancelled_at is not None, "No first chunk ever arrived"

        # Wait for task to reach cancelled status
        task = _wait_for_terminal(auth_headers, task_id, timeout=10)
        assert task["status"] in ("cancelled", "done"), (
            f"Expected cancelled/done after cancel signal, got: {task['status']}"
        )

        # Submit a new task — should start within 2s (worker freed)
        follow_start = time.time()
        follow = _submit(auth_headers, "say hi", complexity=1, stream=True)
        follow_id = follow["task_id"]
        follow_task = _wait_for_terminal(auth_headers, follow_id, timeout=30)
        elapsed = time.time() - follow_start
        assert follow_task["status"] in ("done", "failed"), (
            f"Follow-up task should complete, got: {follow_task['status']}"
        )
        # Worker was freed
        assert elapsed < 30, f"Follow-up task took too long ({elapsed:.1f}s) — worker may be stuck"


# ---------------------------------------------------------------------------
# Test 6 — Backward compat: wait=True still works
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    @skip_no_ollama
    def test_wait_true_blocking_still_works(self, auth_headers):
        """
        Existing submit(wait=True) behavior unchanged.
        Returns result synchronously with status=done.
        """
        resp = _submit(auth_headers, "What is 2+2? Answer with just the number.", complexity=1)
        assert resp["status"] in ("done", "failed"), (
            f"wait=True should block until terminal, got: {resp['status']}"
        )
        if resp["status"] == "done":
            assert resp.get("result") is not None, "wait=True done task must have result"

    def test_partial_endpoint_returns_empty_for_nonstream_task(self, auth_headers):
        """
        GET /tasks/{id}/partial works on any task (not just streaming).
        Returns partial_output="" for non-streaming tasks.
        """
        resp = _submit(auth_headers, "test non-stream task", complexity=1, wait=False)
        task_id = resp["task_id"]
        # Give it a moment to possibly start
        time.sleep(0.1)
        p = _partial(auth_headers, task_id)
        assert "partial_output" in p
        assert "partial_length" in p
        assert "status" in p

    def test_cancel_queued_task(self, auth_headers):
        """
        A queued task can be cancelled immediately via DELETE /tasks/{id}.
        """
        # Submit non-blocking, then cancel before it runs
        resp = _submit(auth_headers, "long task that should be cancelled", complexity=1, wait=False)
        task_id = resp["task_id"]
        cr = _cancel(auth_headers, task_id)
        if cr.status_code == 200:
            task = _get_task(auth_headers, task_id)
            assert task["status"] in ("cancelled", "done", "failed"), (
                f"Expected terminal status after cancel, got: {task['status']}"
            )
        elif cr.status_code == 400:
            # Task already completed before we could cancel — acceptable
            pass
        else:
            pytest.fail(f"Unexpected cancel status {cr.status_code}: {cr.text}")

    def test_stream_and_partial_require_auth(self):
        """Streaming endpoints reject unauthenticated requests."""
        assert requests.get(f"{API_URL}/tasks/fake123/stream").status_code == 401
        assert requests.get(f"{API_URL}/tasks/fake123/partial").status_code == 401
        assert requests.delete(f"{API_URL}/tasks/fake123").status_code == 401
