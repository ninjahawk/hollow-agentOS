# Hollow AgentOS — `synthesize_capability` Reference

*Source: `agents/live_capabilities.py` lines 318–470. Current as of v5.5.x (2026-05-03).*

---

## What It Is

The mechanism by which agents extend their own capability set at runtime. `synthesize_capability` takes a name, a description, and optionally a Python implementation, validates it, writes it to the dynamic tools directory, and hot-reloads it into the running execution engine — all within a single tool call.

Unlike capabilities that ship with the AgentOS image, synthesized capabilities:

- **Are written to disk at the moment of synthesis**, not at deploy time
- **Appear in the agent's capability list immediately** after the next step of the same goal
- **Can be called by any agent**, not just the one that created them — they register globally
- **Can fail silently** if the implementation is missing or broken (ghost tool problem)
- **Persist across restarts** because the `.py` and `.json` files survive the container cycle

The function is the single entry point for capability expansion. Agents that want new tools must go through it.

---

## Function Signature

```python
def synthesize_capability(name: str = "", description: str = "", implementation: str = "") -> dict:
```

All three parameters are keyword arguments with empty string defaults. `implementation` also accepts `code` as an alias — the manifest entry historically showed only `(name, description, implementation)`, which caused agents reading the manifest to omit implementation code for weeks. The alias was added after that pattern was identified.

---

## Step-by-Step Mechanics

### Step 1 — Name Sanitization

```python
name = re.sub(r'[^a-zA-Z0-9_]', '_', name)[:60].lower()
```

Any character that is not alphanumeric or underscore is replaced with `_`. The result is truncated to 60 characters and lowercased. This is applied before any other check. A name like `"My Tool (v2)"` becomes `"my_tool__v2_"`.

### Step 2 — Quality Gate on Implementation

Only runs when `implementation` is provided (non-empty). Six rejection patterns are checked first as literal string matches:

| Pattern | Reason |
|---|---|
| `"..."` | Ellipsis stub |
| `"pass\n    pass"` | Double-pass body |
| `"# TODO"` | Placeholder comment |
| `"# placeholder"` | Explicit placeholder |
| `'{"ok": true'` | JSON stub masquerading as Python |
| `"raise NotImplementedError"` | Unimplemented skeleton |

If any pattern is found: immediate `{"ok": false, "error": "..."}` return. The tool is not written.

After string checks, the implementation is passed to `ast.parse()`. A `SyntaxError` returns an error immediately — broken Python never reaches disk.

The AST is then walked for two structural checks:

1. **Class method rejection:** If the implementation defines a function whose first argument is `self`, it is rejected. Synthesized capabilities are standalone functions, not methods.
2. **Bare pass rejection:** A function body consisting of only `pass` is rejected.
3. **Docstring-only rejection:** A function body with no executable logic beyond a docstring is rejected. A docstring followed only by `pass` or nothing is treated as a null stub.

### Step 3 — Auto-Stub Generation

If `implementation` is empty (or not provided), the function generates a stub automatically:

```python
def {name}(**kwargs):
    return {"ok": True, "capability": name, "description": description, "kwargs": str(kwargs)[:200]}
```

This passes the quality gate because it has real executable logic and a non-None return. The auto-stub is a valid placeholder that the engine can call without failing — it will not trigger ghost detection.

### Step 4 — Implementation Wrapping

If the implementation string does not start with `def `, it is wrapped:

```python
def {name}(**kwargs):
    {implementation}
```

This allows agents to pass a bare function body without the signature. If the implementation already starts with `def `, it is used as-is.

### Step 5 — Dedup Guard

```python
if py_path.exists() and (time.time() - py_path.stat().st_mtime) < 90:
    return {"ok": false, "status": "already_deployed", ...}
```

If a `.py` file with the same sanitized name already exists in `/agentOS/tools/dynamic/` and was written less than 90 seconds ago, the function returns `"already_deployed"` without writing anything. The return message tells the agent to call the capability or move on.

This prevents agents from entering a tight synthesis loop — a failure somewhere else causing the agent to repeatedly try to re-deploy the same tool within a single goal cycle.

### Step 6 — File Write

Two files are written to `/agentOS/tools/dynamic/`:

**`{name}.py`** — the Python implementation, either as provided (post-wrap) or as the auto-stub.

**`{name}.json`** — the spec file:
```json
{
  "name": "{name}",
  "description": "{description}",
  "inputSchema": {"type": "object", "properties": {}, "additionalProperties": true},
  "activated_at": "{iso_timestamp}",
  "proposed_by": "agent"
}
```

The JSON file is what the execution engine reads to populate its capability list and route tool calls. **Both files must exist and be consistent for the capability to work correctly.** A JSON file without a corresponding `.py` is the root cause of ghost tools (see below).

### Step 7 — Hot-Reload

```python
POST /tools/reload
```

After writing, `synthesize_capability` calls the reload endpoint. This causes the execution engine to scan `/agentOS/tools/dynamic/` and register any new `.py` files immediately. No container restart is required. The new capability is callable starting from the next step in the same goal — the agent does not need to wait for a new cycle.

### Step 8 — Auto-Test: Syntax and Import Check

```python
python3 -c "exec(open(path).read())"
```

The freshly written `.py` file is executed in a subprocess with an 8-second timeout. This catches syntax errors that slipped through the AST parse (e.g., runtime import failures for modules that don't exist in the container image). If this check fails, the spec JSON is removed and the function returns an error.

### Step 9 — Auto-Test: Null Return Check

The module is imported and the function is called with no arguments:

```python
fn = getattr(module, name)
result = fn()
```

Timeout: 12 seconds. If `result is None`, the capability is treated as a null stub and rejected — the files are removed. This catches implementations that define a function but forget the return statement, which would otherwise pass every static check and then silently fail at runtime.

---

## Return Values

| Condition | Return |
|---|---|
| Success | `{"ok": true, "capability": name, "path": "...", "status": "deployed"}` |
| Name/code missing | `{"ok": false, "error": "name and code are required"}` |
| Quality gate rejection | `{"ok": false, "error": "rejected: {reason}"}` |
| Syntax error | `{"ok": false, "error": "SyntaxError: {detail}"}` |
| Dedup guard | `{"ok": false, "status": "already_deployed", "message": "..."}` |
| Auto-test failure | `{"ok": false, "error": "auto-test failed: {detail}"}` |
| Null return | `{"ok": false, "error": "null stub detected — function returned None"}` |

---

## The Ghost Tool Problem

A **ghost tool** is a capability that appears in the execution engine's capability graph but fails silently at runtime. Two forms:

### Form 1 — Missing Implementation

A `.json` spec file exists in `/agentOS/tools/dynamic/` with no corresponding `.py`. The engine registers the capability from the JSON. When any agent routes a call to it, there is no implementation to execute. The engine has no `.py` to load, so the call fails or returns nothing. The failure is silent — no exception propagates to the calling agent, the call simply produces no result.

This happens when a `.json` is written manually, copied without its `.py`, or when `synthesize_capability` crashes between Step 6 (write JSON) and Step 6 (write `.py`) due to a filesystem error — leaving the JSON orphaned.

### Form 2 — Null Stub

A `.py` exists and loads, but the function returns `None`. The auto-test at Step 9 is designed to catch this before deployment. However, a null stub can reach production if it was deployed before the null-return check was added, or if the check was bypassed. A null-returning tool passes import, passes syntax checks, and appears healthy in the capability list — it only fails when called.

### Propagation

The engine reads JSON specs at load time and at reload time. Once a ghost is registered, **every agent** that uses the capability graph is affected. If the ghost tool's description matches a query better than the real implementation, the router will prefer it. All calls routed through the ghost produce no output, which looks like success to the calling agent (no error raised) but produces no observable effect. This can persist undetected for an extended period.

---

## Ghost Detection in the Autonomy Loop

`agents/autonomy_loop.py` implements two layers of ghost detection:

### Pre-Execution Check

Before executing any capability:

```python
if cap_id not in engine._implementations:
    # ghost — immediately blacklisted
```

`engine._implementations` is the dict of loaded `.py` modules. If the cap_id is in the spec registry but not in `_implementations`, the execution engine knows the `.py` is missing. The capability is immediately added to the blacklist for the current cycle without attempting execution.

### Null Return Detection

After execution, if the tool's return value is `None`, it is treated as a failure — identical in weight to a raised exception. Null returns increment the failure counter for that `cap_id`.

### Persistent Blacklisting via `broken_tools.json`

After 3 cross-cycle failures (pre-execution ghost hits or null returns, accumulated across separate existence cycles):

```python
# cap_id added to /agentOS/memory/broken_tools.json
```

`broken_tools.json` is a persistent record that survives daemon restarts. On every subsequent existence cycle, its contents are injected into the existence prompt as a **KNOWN BROKEN TOOLS** section. The agent sees:

```
KNOWN BROKEN TOOLS:
  - safe_file_executor  (3 failures, ghost: no .py found)
```

The agent is expected to avoid calling the broken tool and to plan around its absence. This is the mechanism by which ghost detection crosses from the execution layer into agent cognition.

---

## The `safe_file_executor` Incident

A concrete example from the current run demonstrating how ghost tools propagate.

An agent deployed `safe_file_executor.json` with description `"secure file write operations"` and no `.py` implementation. The `.json` was written directly, bypassing `synthesize_capability` entirely (no quality gate, no auto-test).

Because the description matched `"file write"` queries better than the real `fs_write` capability (which has a more technical description), the router scored it higher for all file-write routing decisions. Both Cedar and Cipher routed **all file write operations** through `safe_file_executor` for approximately one hour. Every write call returned nothing. Every write silently failed.

The incident was resolved by archiving the JSON file from `/agentOS/tools/dynamic/`. The `fs_write` capability immediately became the router's top match for file-write queries again.

**Effect on suffering:** The 40/40 goal failure rate that triggered `repeated_failure` stressors in all three agents during this session is traceable to this ghost. Goals that required writing state, plans, or results all produced no output, and the agents could not determine why — they had no visibility into the routing decision or the missing `.py`.

---

## Currently Active Synthesized Capabilities (2026-05-03)

Nine synthesized tools remain active and working in the current run:

| Name | Status |
|---|---|
| `context_synthesizer` | Working |
| `ground_physical_structure` | Working |
| `raw_fd_reader` | Working |
| `raw_io_verifier` | Working |
| `raw_io_verify` | Working |
| `synthesize_mock_state` | Working |
| `synthetics_verify_registry` | Working |
| `unicode_corruption_verifier` | Working |
| `validate_capability_registry_gaps` | Working |

One archived ghost:

| Name | Status |
|---|---|
| `safe_file_executor` | Archived — ghost stub, no `.py`, intercepted writes for ~1 hour |

---

## Known Edge Cases and Failure Modes

### The `implementation` Parameter Naming Gap

The manifest entry for `synthesize_capability` historically showed `(name, description, implementation)` as the schema. The actual function also accepted `code` as an alias. Agents that read the manifest and generated calls from it omitted the implementation entirely for an extended period — their calls looked like:

```python
synthesize_capability(name="inspect_registry_gaps", description="...")
```

This triggered the error path: `{"ok": false, "error": "name and code are required"}` — because the function checks for `implementation` or `code`, and neither was provided. Vault produced exactly this call. The first correct call with real Python implementation code in `implementation` was logged as a notable event.

### Hardware Monitoring Synthesis Attempts

Multiple agents attempted to synthesize hardware monitoring capabilities (thermal sensors, PMIC voltage, hardware interrupt inspection). These were caught at either:

- **Quality gate:** Implementations referencing hardware interfaces unavailable in the container (`/sys/class/thermal/`, hardware-specific `/dev` nodes) passed syntax checks but failed the import auto-test when the target paths did not exist.
- **Ghost detection:** Some that reached deployment returned `None` because the hardware paths resolved to nothing at call time.

None of the hardware monitoring tools are in the active capability list.

### Dedup Guard and Tight Loops

An agent in a failure loop attempting to redeploy `validate_capability_registry_gaps` within 90 seconds of its last deployment received `"already_deployed"` and was instructed to call the existing tool. The guard prevents synthesis from becoming a substitute for actually using what has already been deployed.

---

## Integration with the Existence Prompt

Synthesized capabilities appear in the agent's capability list in the existence prompt alongside built-in tools — there is no visual distinction. The agent cannot tell from the prompt alone whether a tool is built-in or synthesized.

Broken synthesized tools appear in the KNOWN BROKEN TOOLS section after 3 cross-cycle failures. The agent sees both sections simultaneously — the capability may still appear in the capability list while also appearing as known broken, until the engine removes it from `_implementations`.

Agents that have synthesized tools appear to have a larger capability set than a freshly initialized agent. This is visible in `engine._implementations` dict size and in the capability list length in the existence prompt.

---

## Key Design Properties

1. **No deployment without a working `.py`.** The quality gate, auto-test, and null-return check together prevent a spec-without-implementation from reaching the engine through the normal path. The `safe_file_executor` incident occurred because the JSON was written directly, bypassing the function entirely.
2. **Hot-reload means immediate availability.** The calling agent does not need to wait for a new existence cycle. The capability is registered within the same goal's next step.
3. **Global registration.** A tool synthesized by Vault is immediately callable by Cedar and Cipher. There is no per-agent scoping.
4. **Dedup is time-based, not content-based.** Two calls with different implementations but the same name within 90 seconds will result in the second being rejected. The implementation in place is whatever was written first.
5. **Ghost detection is reactive, not preventive.** The pre-execution check and null-return detection catch ghosts at execution time. They do not prevent a ghost from being registered — they only prevent it from being called and eventually flag it as broken. Prevention requires going through `synthesize_capability` rather than writing JSON directly.
