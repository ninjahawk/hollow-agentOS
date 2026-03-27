#!/usr/bin/env python3
"""
hollow vs naive — head-to-head token benchmark

Task: an agent needs to answer two questions about the running system:
  1. What is the current system state (disk, memory, GPU, services)?
  2. Where is task complexity routing defined and what are the thresholds?

hollow approach:  2 API calls
naive approach:   shell commands an agent would actually run

We capture every byte of output from both approaches, count tokens,
and show the real savings.

Run: python3 /agentOS/tools/bench_compare.py
"""

import sys, json, time, subprocess, os
sys.path.insert(0, "/agentOS")
from sdk.hollow import Hollow

BASE   = "http://localhost:7777"
CFG    = json.load(open("/agentOS/config.json"))
MASTER = CFG["api"]["token"]
h      = Hollow(BASE, MASTER)

# ── token counting (cl100k approximation: ~4 chars/token) ─────────────────────

def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        return max(1, len(text) // 4)

def shell(cmd: str) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
    return (r.stdout + r.stderr).decode("utf-8", errors="replace").strip()

# ── display ────────────────────────────────────────────────────────────────────

CYAN  = "\033[1;36m"
GREEN = "\033[32m"
YELLOW= "\033[33m"
RED   = "\033[31m"
GRAY  = "\033[90m"
BOLD  = "\033[1m"
RESET = "\033[0m"

def banner(t):
    print(f"\n{CYAN}{'═'*60}{RESET}")
    print(f"{CYAN}  {t}{RESET}")
    print(f"{CYAN}{'═'*60}{RESET}\n")

def section(t):
    print(f"\n{YELLOW}── {t} {'─'*(54-len(t))}{RESET}")

def show_output(label, text, tok):
    lines = text.splitlines()
    preview = "\n".join(f"  {GRAY}{l}{RESET}" for l in lines[:12])
    if len(lines) > 12:
        preview += f"\n  {GRAY}... ({len(lines)-12} more lines){RESET}"
    print(f"\n{BOLD}{label}{RESET}  {GRAY}({tok} tokens){RESET}")
    print(preview)

def result_row(label, tokens, ref=None):
    bar_max = 40
    if ref:
        pct = tokens / ref * 100
        saved = ref - tokens
        bar = int(tokens / ref * bar_max)
        color = GREEN if pct < 50 else YELLOW if pct < 80 else RED
        print(f"  {label:<30} {color}{tokens:>6} tok{RESET}  {GRAY}({pct:.0f}% of naive  saved {saved}){RESET}")
        print(f"  {'':30} {'█'*bar}{'░'*(bar_max-bar)}")
    else:
        print(f"  {label:<30} {RED}{tokens:>6} tok{RESET}  {GRAY}(baseline){RESET}")
        print(f"  {'':30} {'█'*bar_max}")


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 1 — System state discovery
# ══════════════════════════════════════════════════════════════════════════════

banner("Scenario 1 — System State Discovery")
print("Task: agent needs disk, memory, GPU, services, token usage, recent activity\n")

# ── hollow ─────────────────────────────────────────────────────────────────────
section("hollow  →  GET /state  (1 call)")
t0 = time.time()
state = h.state()
hollow_state_ms = int((time.time()-t0)*1000)
hollow_state_out = json.dumps(state, indent=2)
hollow_state_tok = count_tokens(hollow_state_out)
show_output("hollow /state response", hollow_state_out, hollow_state_tok)
print(f"\n  {GREEN}1 call  {hollow_state_ms}ms{RESET}")

# ── naive ──────────────────────────────────────────────────────────────────────
section("naive  →  shell commands an agent would run")
naive_state_parts = {}

cmds = [
    ("df -h",                        "disk usage"),
    ("free -h",                      "memory"),
    ("nvidia-smi --query-gpu=name,memory.used,memory.free,utilization.gpu --format=csv,noheader 2>/dev/null || echo 'no gpu info'", "GPU"),
    ("ps aux | grep -E 'ollama|uvicorn|nginx' | grep -v grep", "services"),
    ("cat /agentOS/memory/agent-registry.json 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps({k:v for k,v in list(d.items())[:3]}, indent=2))'", "agent registry (first 3)"),
    ("ls -la /agentOS/workspace/agents/ 2>/dev/null | head -20", "workspace listing"),
    ("cat /agentOS/memory/session.json 2>/dev/null | tail -50", "session log"),
    ("uptime",                        "uptime/load"),
]

naive_state_raw = ""
for cmd, label in cmds:
    out = shell(cmd)
    naive_state_parts[label] = out
    naive_state_raw += f"\n# {label}\n{out}\n"
    tok = count_tokens(out)
    print(f"  {GRAY}$ {cmd[:60]}{RESET}")
    print(f"  {GRAY}  → {len(out.splitlines())} lines  {tok} tok{RESET}")

naive_state_tok = count_tokens(naive_state_raw)
print(f"\n  {RED}{len(cmds)} calls{RESET}  {naive_state_tok} tokens total")


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 2 — Find task routing logic (code search)
# ══════════════════════════════════════════════════════════════════════════════

banner("Scenario 2 — Find Task Routing Logic")
print("Task: agent needs to find where complexity thresholds are defined\n")

# ── hollow ─────────────────────────────────────────────────────────────────────
section("hollow  →  semantic_search  (1 call)")
t0 = time.time()
hits = h.search("task complexity routing thresholds model selection", top_k=3)
hollow_search_ms = max(0, int((time.time()-t0)*1000))
hollow_search_out = json.dumps([
    {"file": r.file, "score": round(r.score, 3), "preview": r.preview}
    for r in hits
], indent=2)
hollow_search_tok = count_tokens(hollow_search_out)
show_output("hollow semantic_search response", hollow_search_out, hollow_search_tok)
print(f"\n  {GREEN}1 call  {hollow_search_ms}ms{RESET}")

# ── naive ──────────────────────────────────────────────────────────────────────
section("naive  →  grep + cat full files")

naive_search_parts = []

# Step 1: find files that mention complexity
find_out = shell("grep -rl --include='*.py' 'complexity\\|COMPLEXITY' /agentOS/agents/ /agentOS/api/ 2>/dev/null")
naive_search_parts.append(f"# grep -rl 'complexity'\n{find_out}")
print(f"  {GRAY}$ grep -rl 'complexity' ...  → {len(find_out.splitlines())} files{RESET}")

# Step 2: cat each matching file in full (what an agent without semantic search does)
for fpath in find_out.splitlines()[:3]:
    fpath = fpath.strip()
    if not fpath:
        continue
    content = shell(f"cat {fpath}")
    naive_search_parts.append(f"# cat {fpath}\n{content}")
    tok = count_tokens(content)
    print(f"  {GRAY}$ cat {fpath}  → {len(content.splitlines())} lines  {tok} tok{RESET}")

naive_search_raw = "\n\n".join(naive_search_parts)
naive_search_tok = count_tokens(naive_search_raw)
print(f"\n  {RED}{1 + len(find_out.splitlines()[:3])} calls{RESET}  {naive_search_tok} tokens total")


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 3 — Read a file with context
# ══════════════════════════════════════════════════════════════════════════════

banner("Scenario 3 — Read File + Related Context")
print("Task: read scheduler.py and find related code across the codebase\n")

# ── hollow ─────────────────────────────────────────────────────────────────────
section("hollow  →  /fs/read_context  (1 call)")
t0 = time.time()
ctx = h.read_context("/agentOS/agents/scheduler.py", top_k=3)
hollow_ctx_ms = int((time.time()-t0)*1000)
hollow_ctx_out = json.dumps(ctx, indent=2)
hollow_ctx_tok = count_tokens(hollow_ctx_out)
show_output("hollow read_context response", hollow_ctx_out, hollow_ctx_tok)
print(f"\n  {GREEN}1 call  {hollow_ctx_ms}ms{RESET}")

# ── naive ──────────────────────────────────────────────────────────────────────
section("naive  →  cat scheduler.py + cat related files")

naive_ctx_parts = []
main_file = shell("cat /agentOS/agents/scheduler.py")
naive_ctx_parts.append(f"# cat /agentOS/agents/scheduler.py\n{main_file}")
print(f"  {GRAY}$ cat scheduler.py  → {len(main_file.splitlines())} lines  {count_tokens(main_file)} tok{RESET}")

for related in ["/agentOS/agents/registry.py", "/agentOS/agents/bus.py", "/agentOS/api/agent_routes.py"]:
    content = shell(f"cat {related}")
    naive_ctx_parts.append(f"# cat {related}\n{content}")
    print(f"  {GRAY}$ cat {related}  → {len(content.splitlines())} lines  {count_tokens(content)} tok{RESET}")

naive_ctx_raw = "\n\n".join(naive_ctx_parts)
naive_ctx_tok = count_tokens(naive_ctx_raw)
print(f"\n  {RED}4 calls{RESET}  {naive_ctx_tok} tokens total")


# ══════════════════════════════════════════════════════════════════════════════
# RESULTS
# ══════════════════════════════════════════════════════════════════════════════

banner("Results")

total_hollow = hollow_state_tok + hollow_search_tok + hollow_ctx_tok
total_naive  = naive_state_tok  + naive_search_tok  + naive_ctx_tok
total_saved  = total_naive - total_hollow
pct_saved    = total_saved / total_naive * 100

print(f"  {'Scenario':<35} {'hollow':>8}   {'naive':>8}   {'savings':>8}")
print(f"  {'─'*35}   {'─'*8}   {'─'*8}   {'─'*8}")

rows = [
    ("1. System state discovery",  hollow_state_tok,  naive_state_tok),
    ("2. Find routing logic",       hollow_search_tok, naive_search_tok),
    ("3. Read file + context",      hollow_ctx_tok,    naive_ctx_tok),
]

for label, htok, ntok in rows:
    saved = ntok - htok
    pct   = (1 - htok/ntok) * 100
    color = GREEN if pct > 60 else YELLOW if pct > 30 else RESET
    print(f"  {label:<35} {color}{htok:>8}{RESET}   {ntok:>8}   {color}{pct:>7.0f}%{RESET}")

print(f"\n  {'─'*35}   {'─'*8}   {'─'*8}   {'─'*8}")
print(f"  {'TOTAL':<35} {GREEN}{total_hollow:>8}{RESET}   {total_naive:>8}   {GREEN}{pct_saved:>7.1f}%{RESET}")
print(f"\n  {GREEN}hollow used {total_hollow} tokens vs {total_naive} naive — saved {total_saved} ({pct_saved:.1f}%){RESET}\n")
