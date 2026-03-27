"""
hollow-agentOS integration tests — hits the LIVE API, no mocks.

Run:
    python3 /agentOS/tools/test_integration.py [--url http://localhost:7777]

Requires HOLLOW_TOKEN env var or edits config.json.
Exit code 0 = all passed.
"""

import sys
import os
import json
import time
import urllib.request
import urllib.error
import argparse

BASE = "http://localhost:7777"
MASTER_TOKEN = None
PASS = 0
FAIL = 0
SKIP = 0

# ── Helpers ──────────────────────────────────────────────────────────────────

def _req(method, path, body=None, token=None, params=None, expect=200):
    tok = token or MASTER_TOKEN
    url = BASE + path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + qs
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
            return r.status, resp
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:
            detail = json.loads(body_bytes)
        except Exception:
            detail = body_bytes.decode()
        return e.code, detail


def ok(name):
    global PASS
    PASS += 1
    print(f"  \033[32m✓\033[0m {name}")


def fail(name, reason=""):
    global FAIL
    FAIL += 1
    print(f"  \033[31m✗\033[0m {name}" + (f" — {reason}" if reason else ""))


def section(title):
    print(f"\n\033[1m{title}\033[0m")


def check(name, condition, reason=""):
    if condition:
        ok(name)
    else:
        fail(name, reason)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_health():
    section("Health")
    status, resp = _req("GET", "/health")
    check("GET /health → 200", status == 200, f"got {status}")
    check("response has ok=true", resp.get("ok") is True, str(resp))


def test_state():
    section("State")
    status, resp = _req("GET", "/state")
    check("GET /state → 200", status == 200, f"got {status}")
    check("has system key", "system" in resp, str(list(resp.keys())))
    check("has ollama key", "ollama" in resp)
    check("has workspace key", "workspace" in resp)

    ts = resp.get("timestamp") or resp.get("time")
    check("has timestamp", bool(ts))

    # diff endpoint
    since = "2020-01-01T00:00:00"
    status2, resp2 = _req("GET", "/state/diff", params={"since": since})
    check("GET /state/diff → 200", status2 == 200, f"got {status2}")


def test_agent_register():
    section("Agent Registration")
    status, resp = _req("POST", "/agents/register", body={
        "name": "test-worker",
        "role": "worker",
    })
    check("POST /agents/register → 200", status == 200, str(resp))
    if status != 200:
        return None, None

    agent_id = resp.get("agent_id")
    token = resp.get("token")
    check("response has agent_id", bool(agent_id), str(resp))
    check("response has token", bool(token), str(resp))
    check("token shown once", "token" in resp)

    # Fetch agent record
    status2, rec = _req("GET", f"/agents/{agent_id}")
    check(f"GET /agents/{agent_id} → 200", status2 == 200, str(rec))
    check("agent status is active", rec.get("status") == "active", str(rec))
    check("agent role is worker", rec.get("role") == "worker")
    check("token_hash not in response", "token_hash" not in rec)

    return agent_id, token


def test_agent_token_auth(agent_id, agent_token):
    section("Agent Token Auth")
    if not agent_token:
        global SKIP
        SKIP += 1
        print("  - skipped (no agent token)")
        return

    # Agent token should work on a protected endpoint
    status, resp = _req("GET", f"/agents/{agent_id}", token=agent_token)
    check("agent token accepted on protected endpoint", status == 200, f"got {status}")

    # Bad token should 401 on protected endpoint
    status2, _ = _req("GET", f"/agents/{agent_id}", token="bad-token-xyz")
    check("bad token → 401", status2 == 401, f"got {status2}")


def test_capability_enforcement(agent_id, agent_token):
    section("Capability Enforcement")
    if not agent_token:
        global SKIP
        SKIP += 1
        return

    # Worker does not have admin cap → GET /agents should 403
    status, resp = _req("GET", "/agents", token=agent_token)
    check("worker GET /agents → 403 (no admin cap)", status == 403, f"got {status}: {resp}")

    # Worker does not have spawn cap → POST /agents/spawn should 403
    status2, resp2 = _req("POST", "/agents/spawn", token=agent_token, body={
        "name": "child", "role": "worker", "task": "test"
    })
    check("worker POST /agents/spawn → 403 (no spawn cap)", status2 == 403, f"got {status2}")


def test_messaging(agent_id, agent_token):
    section("Messaging")
    if not agent_token:
        return

    # Register a second agent to message
    status, resp = _req("POST", "/agents/register", body={
        "name": "test-reader",
        "role": "readonly",
    })
    if status != 200:
        fail("register second agent for messaging", str(resp))
        return

    reader_id = resp["agent_id"]
    reader_token = resp["token"]

    # Send message from master to reader
    status2, resp2 = _req("POST", "/messages", body={
        "to_id": reader_id,
        "content": {"hello": "world"},
        "msg_type": "data",
    })
    check("POST /messages → 200", status2 == 200, str(resp2))
    msg_id = resp2.get("msg_id")
    check("response has msg_id", bool(msg_id))

    # Reader fetches inbox
    status3, resp3 = _req("GET", "/messages", token=reader_token)
    check("reader GET /messages → 200", status3 == 200, str(resp3))
    msgs = resp3.get("messages", [])
    check("reader has ≥1 message", len(msgs) >= 1, f"got {len(msgs)} messages")
    if msgs:
        check("message content matches", msgs[0].get("content") == {"hello": "world"}, str(msgs[0]))

    # Thread
    if msg_id:
        status4, resp4 = _req("GET", f"/messages/thread/{msg_id}")
        check("GET /messages/thread → 200", status4 == 200, str(resp4))
        check("thread has ≥1 entry", len(resp4.get("thread", [])) >= 1)

    # Terminate reader
    _req("DELETE", f"/agents/{reader_id}")


def test_shell_isolation(agent_id, agent_token):
    section("Shell Isolation")
    if not agent_token:
        return

    # Worker runs shell — should be scoped to workspace
    status, resp = _req("POST", "/shell", token=agent_token, body={
        "command": "pwd",
    })
    check("worker POST /shell → 200", status == 200, f"got {status}: {resp}")
    if status == 200:
        stdout = resp.get("stdout", "").strip()
        workspace_expected = f"/agentOS/workspace/agents/{agent_id}"
        check(
            f"cwd scoped to workspace ({stdout})",
            stdout.startswith("/agentOS/workspace/agents/"),
            f"got: {stdout!r}",
        )


def test_fs(agent_id, agent_token):
    section("Filesystem")
    if not agent_token:
        return

    workspace = f"/agentOS/workspace/agents/{agent_id}"
    test_path = f"{workspace}/test_integration_file.txt"
    content = "hollow integration test\n"

    # Write
    status, resp = _req("POST", "/fs/write", token=agent_token, body={
        "path": test_path,
        "content": content,
    })
    check("POST /fs/write → 200", status == 200, f"got {status}: {resp}")
    check("write ok=true", resp.get("ok") is True)

    # Read back
    status2, resp2 = _req("GET", "/fs/read", token=agent_token, params={"path": test_path})
    check("GET /fs/read → 200", status2 == 200, f"got {status2}")
    check("content matches", resp2.get("content") == content, repr(resp2.get("content")))

    # List dir
    status3, resp3 = _req("GET", "/fs/list", token=agent_token, params={"path": workspace})
    check("GET /fs/list → 200", status3 == 200, f"got {status3}")
    entries = resp3.get("entries", [])
    names = [e["name"] for e in entries]
    check("written file appears in listing", "test_integration_file.txt" in names, str(names))


def test_task_submit():
    section("Task Scheduler")
    status, resp = _req("POST", "/tasks/submit", body={
        "description": "Reply with only the word PONG and nothing else.",
        "complexity": 1,
    }, token=MASTER_TOKEN)
    check("POST /tasks/submit → 200", status == 200, f"got {status}: {resp}")

    if status != 200:
        return

    task_id = resp.get("task_id")
    check("response has task_id", bool(task_id))
    check("status is done", resp.get("status") == "done", f"status={resp.get('status')}")

    result = resp.get("result") or {}
    response_text = result.get("response", "")
    check("result has response text", bool(response_text), "empty response")
    check("model responded", len(response_text) > 0)

    ms = resp.get("ms")
    check("ms reported", ms is not None, str(ms))

    # GET /tasks/:id
    if task_id:
        status2, resp2 = _req("GET", f"/tasks/{task_id}")
        check("GET /tasks/:id → 200", status2 == 200, f"got {status2}")
        check("task_id matches", resp2.get("task_id") == task_id)


def test_semantic_search():
    section("Semantic Search")
    status, resp = _req("POST", "/semantic/search", body={
        "query": "how does task scheduling work",
        "top_k": 3,
    })
    check("POST /semantic/search → 200", status == 200, f"got {status}")

    if status != 200:
        return

    results = resp.get("results", [])
    check("returned ≥1 result", len(results) >= 1, f"got {len(results)}")
    if results:
        r = results[0]
        check("result has file", "file" in r, str(r))
        check("result has score", "score" in r)
        check("score is float 0-1", 0 <= r.get("score", -1) <= 1, str(r.get("score")))
        check("result has preview", bool(r.get("preview")))


def test_agent_lifecycle(agent_id):
    section("Agent Lifecycle")
    if not agent_id:
        return

    # Suspend
    status, resp = _req("POST", f"/agents/{agent_id}/suspend")
    check(f"POST /agents/{agent_id}/suspend → 200", status == 200, str(resp))

    status2, rec = _req("GET", f"/agents/{agent_id}")
    check("status is suspended", rec.get("status") == "suspended", str(rec.get("status")))

    # Resume
    status3, resp3 = _req("POST", f"/agents/{agent_id}/resume")
    check("POST /agents/{id}/resume → 200", status3 == 200, str(resp3))

    status4, rec2 = _req("GET", f"/agents/{agent_id}")
    check("status is active after resume", rec2.get("status") == "active", str(rec2.get("status")))

    # Terminate
    status5, resp5 = _req("DELETE", f"/agents/{agent_id}")
    check("DELETE /agents/{id} → 200", status5 == 200, str(resp5))

    status6, rec3 = _req("GET", f"/agents/{agent_id}")
    check("status is terminated", rec3.get("status") == "terminated", str(rec3.get("status")))


def test_handoff_pickup():
    section("Handoff / Pickup")
    status, resp = _req("POST", "/agent/handoff", body={
        "agent_id": "test-runner",
        "summary": "Integration test run",
        "in_progress": ["test_handoff_pickup"],
        "next_steps": ["verify pickup"],
        "relevant_files": ["/agentOS/tools/test_integration.py"],
        "decisions_made": ["all tests passing"],
    })
    check("POST /agent/handoff → 200", status == 200, f"got {status}: {resp}")

    status2, resp2 = _req("GET", "/agent/pickup")
    check("GET /agent/pickup → 200", status2 == 200, f"got {status2}")
    if status2 == 200:
        handoff = resp2.get("handoff") or resp2
        check("handoff has summary", bool(handoff.get("summary")), str(list(resp2.keys())))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global BASE, MASTER_TOKEN

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:7777")
    args = parser.parse_args()
    BASE = args.url.rstrip("/")

    # Load master token from config
    config_path = "/agentOS/config.json"
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        MASTER_TOKEN = cfg.get("api", {}).get("token", "")
    except Exception as e:
        print(f"Cannot read {config_path}: {e}")
        sys.exit(1)

    if not MASTER_TOKEN:
        print("No api.token in config.json")
        sys.exit(1)

    print(f"\033[1mhollow-agentOS integration tests\033[0m")
    print(f"target: {BASE}")
    print(f"token: {MASTER_TOKEN[:8]}...")

    test_health()
    test_state()
    agent_id, agent_token = test_agent_register()
    test_agent_token_auth(agent_id, agent_token)
    test_capability_enforcement(agent_id, agent_token)
    test_messaging(agent_id, agent_token)
    test_shell_isolation(agent_id, agent_token)
    test_fs(agent_id, agent_token)
    test_task_submit()
    test_semantic_search()
    test_handoff_pickup()
    test_agent_lifecycle(agent_id)

    total = PASS + FAIL + SKIP
    color = "\033[32m" if FAIL == 0 else "\033[31m"
    print(f"\n{color}{'─'*40}\033[0m")
    print(f"{color}  {PASS}/{total} passed  {FAIL} failed  {SKIP} skipped\033[0m")
    print(f"{color}{'─'*40}\033[0m\n")

    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
