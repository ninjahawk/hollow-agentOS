# Hollow AgentOS — Bug Tracker

Live tracking of known issues. Updated each session.
Status: `open` | `fixed` | `wontfix`

---

## Open

### BUG-001 — invoke_claude agent-side delivery unreliable
**Status:** open (partially mitigated)  
**Severity:** high  
**File:** `agents/live_capabilities.py` — `invoke_claude()`  
**Description:** Agents plan to call `invoke_claude()` in their reasoning but the actual capability call doesn't always execute. The implementation itself writes correctly to `memory/claude_requests.jsonl`. Most likely cause: the autonomy loop translates the agent's intent into a different tool call (e.g. `synthesize_capability`) rather than `invoke_claude` directly.  
**Mitigation applied:** `invoke_claude()` now writes a visible entry to `thoughts.log` when called, so we can confirm whether it fires at all vs. just being planned.  
**Next step:** Watch thoughts.log for `📨 invoke_claude` entries over several sessions. If confirmed missing, instrument `autonomy_loop.pursue_goal()` to log every tool call the planner selects.

---

### BUG-002 — Synthesized tools silently return null
**Status:** open  
**Severity:** high  
**File:** `memory/dynamic_tools/` (= `/agentOS/tools/dynamic/` in container)  
**Description:** Agents synthesize tools that are syntactically valid Python but logically broken. Two observed failure modes:
- **Nested function trap:** outer function `def foo(**kwargs)` wraps the real implementation in a nested `def foo(...)` that's defined but never called. Outer function returns `None` implicitly.
- **Undefined builtins:** code references `SecurityError`, `ResourceLimitError`, etc. that aren't Python builtins. Would throw `NameError` if the broken path were ever reached.
- **Old sessions:** orphaned `.json` spec files with no `.py` at all.

All three result in the same symptom: agent calls the tool, execution engine returns `null` or a stub message, agent spends cycles investigating why.

**Observed live (2026-05-05):** `secure_dynamic_tool_loader.py` — outer `**kwargs` function wraps a nested implementation that's never invoked. Returns `null` every call.

**Fix:** `_hotload_dynamic_tools()` in `daemon.py` already syntax-checks files before loading. Need to also do a quick smoke-test: call the loaded function with no args and check if the return is immediately `None` with no side effects — if so, skip it and log a warning. Alternatively, add a validation step in `synthesize_capability` that checks for the nested-function pattern before writing the file.

---

### BUG-003 — `memory/manager.py` and `memory/heap.py` are source files in a wiped directory
**Status:** open (mitigated)  
**Severity:** high  
**File:** `.gitignore`, `memory/manager.py`, `memory/heap.py`  
**Description:** These two source files live in `memory/`, a bind-mounted runtime directory that gets wiped on nuclear reset. Container crashes on restart with `ModuleNotFoundError`.  
**Mitigation applied:** `.gitignore` now has `!memory/manager.py` and `!memory/heap.py` so git tracks them and they survive.  
**Real fix:** Move both files into `agents/` where source code belongs, update all imports. Low urgency since mitigation is in place.

---

### BUG-004 — ~~Two directories for synthesized tools~~ NOT A BUG
**Status:** wontfix / confirmed not a bug  
**Description:** `docker-compose.yml` line 36: `./memory/dynamic_tools:/agentOS/tools/dynamic`. Both paths resolve to the same host directory. `_hotload_dynamic_tools()` and `CapabilitySynthesisEngine` are writing/reading the same place.

---

### BUG-005 — Crisis counter doesn't survive daemon restarts
**Status:** fixed (2026-05-05)  
**File:** `agents/daemon.py` — `DaemonMetrics`  
**Description:** `_crisis_cycles` was in-memory only. A daemon restart (container crash, stop/start) zeroed it. An agent that hit crisis 2× before restart needed 3 more after restart to trigger `force_reset`. Contributed to Vault's permanent stall.  
**Fix:** `DaemonMetrics` now persists `_crisis_cycles` to `memory/daemon_state.json` on every update and loads it on startup.

---

## Fixed

### BUG-006 — `_stats` NameError on first crisis cycle
**Status:** fixed (2026-05-05)  
**File:** `agents/daemon.py`  
**Description:** `_assign_idle_goal()` referenced `_stats` (a `DaemonMetrics` instance) never defined at module level. First crisis cycle would crash with `NameError`.  
**Fix:** `_stats = None` declared at module level; `main()` sets `global _stats; _stats = metrics`. Crisis code guards with `if _stats is not None`.

### BUG-007 — `skipped_agents` drain only ran when `active == []`
**Status:** fixed (pre-2026-05-05, already in codebase)  
**File:** `agents/daemon.py` — main loop  
**Description:** Drain that releases stalled agents was inside `else` block, never ran while other agents were active. Permanently excluded any stalled agent. Killed Vault.  
**Fix:** Drain moved outside `else` block — runs every 10 cycles unconditionally.

---

## Structural Issues (not crash bugs, but worth tracking)

### STRUCT-001 — Daemon output not written to a log file
**File:** `entrypoint.sh`, `agents/daemon.py`  
**Description:** `daemon.py` defines `_LOG_FILE` but never uses it for the logging config. All daemon output goes to container stderr. Only accessible via `docker logs hollow-api`. Makes historical debugging harder.  
**Fix:** Either redirect stderr to `/agentOS/logs/daemon.log` in `entrypoint.sh`, or pass a `filename` to `logging.basicConfig`.

### STRUCT-002 — Telegram bot token and chat ID hardcoded in source
**File:** `agents/daemon.py` lines ~498–499  
**Description:** Bot token and chat ID are plaintext in `daemon.py`. Anyone who clones the repo has these credentials.  
**Fix:** Move to `config.json` under a `telegram` key. Already read from config in other places.

### STRUCT-003 — Nuclear wipe has no automatic pre-wipe backup
**Description:** Current flow requires manually running a backup before wiping. Easy to forget.  
**Fix:** Build this into the wipe command itself — auto-snapshot to `backups/pre-wipe-<date>/` before any destructive operation.

---

### BUG-008 — Synthesized tools shadowing built-in capabilities
**Status:** fixed (2026-05-05)  
**File:** `agents/daemon.py` — `_hotload_dynamic_tools()`  
**Description:** Agents synthesized tools with the same name as built-ins (`fs_read`, `shared_log_write`, `synthesize_capability`). Hotloader overwrote built-ins with broken dynamic versions. All built-in tools appeared broken. Caused entire invoke_claude queue floods as agents tried to rebuild working tools.  
**Fix:** Hotloader now snapshots built-in names before scanning dynamic tools and skips any tool whose name matches a built-in.

---

### BUG-009 — Capability profile tracking never fired (ContextVar vs threading.local)
**Status:** fixed (2026-05-05)  
**File:** `agents/daemon.py`, `agents/live_capabilities.py`  
**Description:** `_agent_context = threading.local()` was set in the `_run_one` thread (ThreadPoolExecutor worker), but the execution engine spawns a NEW thread per capability call for timeout handling. `threading.local()` values do NOT propagate to child threads, so every capability ran with `agent_id = ""`. The capability profile, judgment gate, and peer call signal never worked.  
**Fix:** Replaced `threading.local()` with `ContextVar` from the `contextvars` module. `ContextVar` values ARE inherited by threads created with `threading.Thread`, so the agent ID now propagates correctly into the execution engine's spawned capability thread.

### BUG-010 — Synthesized tools shadowing built-ins flooding broken_tools.json
**Status:** fixed (2026-05-05)  
**File:** `memory/broken_tools.json`, autonomy_loop.py  
**Description:** Despite the hotloader protection (built-ins can't be overridden), synthesized tools with the same name as built-ins (fs_read, check_claude_status, etc.) kept getting added to broken_tools.json because agents called them with wrong arguments. The cross-cycle failure counter treated usage errors (ok:False responses) as broken-tool signals. This created a feedback loop: tool "broken" → agent tries to replace → more wrong calls → more failures.  
**Fix:** `_increment_cross_cycle_failures` now refuses to add any tool in `_BUILTIN_CAPS` to the broken list, and only counts genuine null returns (not structured error responses).

### BUG-011 — tools/dynamic/ accumulated broken shadow tools
**Status:** fixed (2026-05-05)  
**Description:** Agents synthesized fs_read.py, check_claude_status_impl.py, check_claude_status_bypass.py, synthesize_capability.py, shared_log_write.py and more — all broken, all confusing subsequent agents. Manual cleanup removed them; fs_write now blocks direct Python writes to tools/dynamic/ (must use synthesize_capability).

---

### BUG-012 — ContextVar not propagating through ThreadPoolExecutor
**Status:** fixed (2026-05-05)  
**File:** `agents/execution_engine.py` — `_call_with_timeout()`  
**Description:** `ThreadPoolExecutor` prior to Python 3.12 does NOT copy ContextVar values when submitting tasks. Every capability ran with `agent_id = ""`. Confirmed via synth_debug.log: 26 consecutive `aid=''` entries. Judgment gate, capability profiles, and peer call signals never fired.  
**Fix:** `_call_with_timeout()` now does `ctx = contextvars.copy_context(); executor.submit(ctx.run, call)`. `execution_engine.py` bind-mounted to pick up the change without rebuilding.

---

### BUG-013 — check_claude_status returns "pending" for rejected requests
**Status:** fixed (2026-05-05)  
**File:** `agents/live_capabilities.py` — `check_claude_status()`  
**Description:** The function read the status field from `claude_responses.jsonl` correctly, but when falling through to `claude_requests.jsonl`, always returned `{"status": "pending"}` regardless of the actual status field in the request. All 50+ rejected requests appeared "pending" to agents forever.  
**Fix:** Now reads the status field from the requests file and returns "rejected"/"fulfilled" if non-pending.

---

### BUG-014 — not_found tool calls counted as cross_cycle_failures (ghost tools)
**Status:** fixed (2026-05-05)  
**File:** `agents/autonomy_loop.py` — `execute_step()` + `_increment_cross_cycle_failures()`  
**Description:** When an agent called a hallucinated tool name (e.g. `patch_tool_registry_loader`), the execution engine returned `(None, "not_found")`. Since result=None, `_increment_cross_cycle_failures` counted it as a broken-tool null-return. After 5 calls, the phantom name entered the broken list as a ghost entry. broken_tools.json accumulated 10+ ghost entries from tools that never existed.  
**Fix:** `execute_step()` converts `"not_found"` and `"disabled"` statuses into structured `{"ok": False}` results immediately after the engine call. Non-None result → `_increment_cross_cycle_failures` skips it.

---

### BUG-015 — Synthesized tools can write to core system files via Python file I/O
**Status:** fixed (2026-05-05)  
**File:** `agents/live_capabilities.py` — `shell_exec()` + `synthesize_capability()`; `agents/daemon.py` — `_hotload_dynamic_tools()`  
**Description:** Scout synthesized `registry_persistence_fixer` — a tool with module-level `open('/agentOS/agents/registry.py', 'w')` that ran at hotload import time, truncating registry.py to zero bytes before dying with NameError. Container crashed on every restart with `ImportError: cannot import name 'AgentRegistry'`. The `exec_module()` call in the hotloader executes all top-level code, not just function definitions.  
**Fix (layered):**
1. `synthesize_capability`: AST check rejects any module-level statement that isn't `def/class/import/assignment/constant`. Pattern scan rejects `open(path, 'w')` targeting protected paths.
2. `_hotload_dynamic_tools`: same AST check before `exec_module` — last line of defense even for tools that bypassed synthesize_capability.
3. `shell_exec`: regex check blocks `open('/agentOS/agents/...', 'w')` patterns in shell-executed Python.
4. Manually deleted `registry_persistence_fixer.py` and other broken tools. Container recreated to restore registry.py from image.

---

*Last updated: 2026-05-05*
