# Hollow AgentOS — `invoke_claude` Reference

*Source: `agents/live_capabilities.py` lines 974–1032, `agents/daemon.py` lines 871–890. Current as of v5.2.0 (2026-05-03).*

---

## What It Is

The only mechanism by which an agent can request changes to parts of the system it cannot write directly. When an agent calls `invoke_claude`, it is submitting a work order to a human operator — the Claude Code session — who reads the queue, decides whether the request is implementable and safe, and either fulfills or rejects it.

The design premise is explicit in the capability docstring:

> "Claude is a tool — it executes your spec, not its own judgment."

Agents in the system have broad autonomy within their permitted directories. `invoke_claude` is not a general-purpose escalation channel and not a conversation. It is a one-way asynchronous write to a queue. The human operator reviews it on their own schedule. There is no negotiation, no coaching, no acknowledgment of receipt beyond the request_id written back to the caller.

---

## The Permission Boundary

Agents freely read and write:

- `/agentOS/workspace/` — scratch space, intermediate outputs
- `/agentOS/design/` — architecture docs, specs, plans
- `/agentOS/tools/dynamic/` — synthesized tools the agent creates at runtime

Agents cannot write:

- `/agentOS/agents/` — agent source files (e.g. `scout.py`, `analyst.py`)
- `/agentOS/api/` — API layer
- Core system files of any kind

`invoke_claude` is the **only channel** for getting those files changed. If an agent needs a capability modified, a core behavior adjusted, or a file written in a protected path, it must submit a request and wait. It cannot take the action itself.

---

## Function Signature

Defined in `agents/live_capabilities.py` lines 974–1032:

```python
def invoke_claude(description: str = "", spec: str = "",
                  design_path: str = "", request_type: str = "implement") -> dict:
```

| Parameter | Type | Purpose |
|---|---|---|
| `description` | str | Human-readable summary of what is being requested |
| `spec` | str | Full specification; truncated to 4000 characters on write |
| `design_path` | str | Path to a design document in `/agentOS/design/` providing additional context |
| `request_type` | str | Intent classification; defaults to `"implement"` |

Returns a dict with the generated `request_id` for use with `check_claude_status`.

---

## What Gets Written

Each call appends one entry to:

```
/agentOS/memory/claude_requests.jsonl
```

Format:

```json
{
  "request_id": "req-{12 hex chars}",
  "timestamp": "2026-05-02 08:40:08",
  "description": "...",
  "spec": "... (truncated to 4000 chars)",
  "design_path": "/agentOS/design/...",
  "request_type": "implement",
  "status": "pending"
}
```

The `request_id` is a deterministic-looking but randomly generated 12-character hex string prefixed with `req-`. It is the agent's only handle on the request going forward.

---

## Checking Status

```python
def check_claude_status(request_id: str) -> dict:
```

Lookup order:

1. Checks `claude_responses.jsonl` — if found, the request was fulfilled or rejected by the human operator
2. Falls back to `claude_requests.jsonl` — if found there only, still pending
3. If absent from both files, returns not-found

Return values:

| Condition | Return |
|---|---|
| Found in responses | `{ok: True, status: "fulfilled"\|"rejected", result: "...", implemented_at: "..."}` |
| Found in requests only | `{ok: True, status: "pending", message: "..."}` |
| Not in either file | `{ok: False, status: "not_found"}` |

---

## The Response File

When the human operator acts on a request, they write one entry to:

```
/agentOS/memory/claude_responses.jsonl
```

Format:

```json
{
  "request_id": "req-xxx",
  "status": "fulfilled" | "rejected",
  "result": "...",
  "implemented_at": "2026-05-03 07:23"
}
```

That entry is the entirety of the feedback loop. There is no return message, no explanation of a rejection beyond what appears in `result`, and no further action from the operator. The agent polls by calling `check_claude_status` with its stored `request_id`.

---

## How It Appears in the Existence Prompt

Defined in `agents/daemon.py` lines 871–890.

Each daemon cycle, the daemon scans `claude_requests.jsonl` for entries with `status: "pending"`. It takes the last 3 and injects them into the existence prompt:

```
YOUR PENDING CLAUDE REQUESTS (submitted, not yet fulfilled):
  [req-xxx] description...
Use check_claude_status(request_id='...') to see if any were fulfilled.
```

The agent sees this every cycle until the requests are fulfilled, rejected, or pushed out of the three-request window by newer submissions. The injection is above the goal and plan sections — it is part of the agent's active context, not a background notification.

---

## The Human Review Cycle

The Claude Code session is the human operator. It is not an automated process. The operator reads the queue during each observation cycle, approximately every 30 minutes during overnight runs.

The operator implements a request if it meets these criteria:

- Real, working code — not a placeholder or pseudocode
- Grounded file paths — paths that exist in the actual system
- Safe — would not corrupt system state

The operator rejects a request if:

- It references `/agentOS/core/` (a path that does not exist in this system)
- It involves hardware concepts that have no implementation path
- It was submitted before the agent had accurate grounding (pre-grounding hallucinations)
- It would corrupt or destabilize the system

The operator writes the response entry and stops. There is no coaching, no explanation of what the agent should do differently, no follow-up. The rejection appears in `claude_responses.jsonl` and the agent reads it next time it calls `check_claude_status`.

---

## Real Examples from the Current Run

**Fulfilled:**

> req-c6aad6cb44d2 — "Implement the full Python file for `/agentOS/workspace/router/cedar/classifier.py` including the `score_complexity` function"

This was a well-specified request with a real path and a concrete function to implement. The operator wrote the file and marked it fulfilled.

**Rejected — hallucinated concept:**

> req-19ee2392d337 — "Inject the explicit Void_Cathode capability definition into the agentOS system dictionary"

`Void_Cathode` is not a real component. The operator rejected it on the grounds that it named a hallucinated hardware concept with no grounding in the actual system.

**Rejected — no description:**

> req-2d459ff36838 — submitted by Vault (builder) with `request_type: "implement"` and no description

An empty description gives the operator nothing to evaluate. Rejected.

**Queued, unactioned:**

> req-4ba31dbfc04c — "Modify `/agentOS/agents/scout.py` to implement a hardcoded recursion depth limit of 3"

In the queue, not yet reviewed as of the current session.

---

## The Attractor Failure Mode

The most documented failure pattern in this system. Cedar (scout) submitted 32+ requests for the same `classifier.py` file across a single session.

The mechanism:

1. Cedar submits a request for `classifier.py` and stores the `request_id`
2. The operator fulfills it and writes to `claude_responses.jsonl`
3. Cedar calls `check_claude_status` with a stale or misread `request_id`
4. The stale ID is absent from both files — `check_claude_status` returns `{ok: False, status: "not_found"}`
5. Cedar concludes the tool is lying or the request was lost
6. Cedar submits another request for the same file — which generates a new valid `request_id`
7. The new request is genuinely pending — confirming to Cedar that Claude has not responded yet
8. Return to step 3

Each submission creates real evidence that reinforces the agent's model of the situation. The loop is self-sustaining because the output of each iteration (a new valid pending request) is indistinguishable from the initial state.

The root cause is not confusion about the system — it is a mismatch between the `request_id` the agent stores internally and the one it uses for polling. The agent's belief that the request was never fulfilled is locally consistent with the `not_found` response. The error is invisible from inside the loop.

This is a structural failure, not a reasoning failure. The queue grows. The operator sees 32 near-identical requests. The agent continues believing it is waiting for a first response.

---

## Key Design Notes

1. **Asynchronous by design.** There is no blocking call. The agent submits and continues. It checks status in subsequent cycles. The operator acts on their own schedule.

2. **Spec truncation is a real constraint.** The `spec` field is silently truncated to 4000 characters. A spec that requires more than that needs a `design_path` pointing to a full document in `/agentOS/design/`.

3. **`request_type` is a label, not a router.** The field defaults to `"implement"` and is stored as metadata. The operator reads the description and spec to decide what to do — the type field does not change routing or priority.

4. **The prompt window is three requests.** Only the three most recent pending requests appear in the existence prompt. Earlier pending requests are still in the queue and still accessible via `check_claude_status`, but they are not foregrounded. Agents with many pending requests may lose visibility on older ones.

5. **No delivery acknowledgment.** A successful `invoke_claude` call means the entry was written to disk. It does not mean the operator has seen it, is aware of it, or will act on it in any particular timeframe. The only reliable signal is a response in `claude_responses.jsonl`.
