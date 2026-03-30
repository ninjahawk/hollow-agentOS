#!/usr/bin/env python3
"""
Build Flappy Bird — hollow vs naive

Two agents build the same Flappy Bird game.
hollow agent: semantic search → clean prompt → generate → write
naive agent:  shell discovery → bloated context → generate → write

We measure every token spent in each approach and compare.

Run: python3 /agentOS/tools/build_flappybird.py
Output:
  /agentOS/workspace/flappybird_hollow.py   (hollow-built)
  /agentOS/workspace/flappybird_naive.py    (naive-built)
"""

import sys, json, time, subprocess
sys.path.insert(0, "/agentOS")
from sdk.hollow import Hollow, register

BASE   = "http://localhost:7777"
CFG    = json.load(open("/agentOS/config.json"))
MASTER = CFG["api"]["token"]
h      = Hollow(BASE, MASTER)

# ── token counting ─────────────────────────────────────────────────────────────
def tokens(text: str) -> int:
    try:
        import tiktoken
        return len(tiktoken.get_encoding("cl100k_base").encode(str(text)))
    except ImportError:
        return max(1, len(str(text)) // 4)

def shell(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
    return (r.stdout + r.stderr).decode("utf-8", errors="replace").strip()

# ── display ────────────────────────────────────────────────────────────────────
CYAN  = "\033[1;36m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
RED   = "\033[31m";  GRAY  = "\033[90m"; BOLD   = "\033[1m"; RESET = "\033[0m"

def banner(t):
    print(f"\n{CYAN}{'═'*60}{RESET}\n{CYAN}  {t}{RESET}\n{CYAN}{'═'*60}{RESET}\n")

def section(t):
    print(f"\n{YELLOW}── {t} {'─'*(56-len(t))}{RESET}")

def step(t, detail=""):
    print(f"{YELLOW}▶{RESET} {BOLD}{t}{RESET}" + (f"  {GRAY}{detail}{RESET}" if detail else ""))

def done(t, detail=""):
    print(f"{GREEN}✓{RESET} {t}" + (f"  {GRAY}{detail}{RESET}" if detail else ""))

def info(t):
    print(f"  {GRAY}{t}{RESET}")

GAME_SPEC = """A complete, playable Flappy Bird game in Python using pygame.

Requirements:
- 800x500 window, 60 FPS
- Bird with gravity (falls naturally), SPACE bar to flap upward
- Green pipes scroll left from right edge, gap of 180px, random heights
- New pipe every 90 frames
- Score: +1 for each pipe passed, displayed top-left
- Collision detection: pipes and floor/ceiling = game over
- Game over screen: show final score, SPACE to restart
- All in one file, no external assets (draw shapes with pygame.draw)
- Include if __name__ == '__main__': block

Write ONLY the Python code. No explanation."""


# ══════════════════════════════════════════════════════════════════════════════
#  HOLLOW APPROACH
# ══════════════════════════════════════════════════════════════════════════════

banner("hollow approach")
hollow_tokens_in = 0
hollow_tokens_out = 0
t_hollow_start = time.time()

# Step 1: semantic search — find any relevant game/pygame patterns in workspace
section("Step 1 — semantic search for pygame/game patterns")
hits = h.search("pygame game loop collision detection sprite", top_k=3)
search_out = json.dumps([{"file": r.file, "score": round(r.score,3), "preview": r.preview[:150]} for r in hits], indent=2)
search_tok = tokens(search_out)
hollow_tokens_in += search_tok
done(f"semantic_search returned {len(hits)} chunks", f"{search_tok} tok")
for r in hits:
    info(f"  score={r.score:.3f}  {r.file.split('/')[-1]}")

# Step 2: check state (1 call — hollow knows environment instantly)
section("Step 2 — check environment via GET /state")
state = h.state()
py_ver = state.get("system", {}).get("python_version", "unknown")
state_summary = json.dumps({
    "python": py_ver,
    "disk_free_gb": state.get("system", {}).get("disk", {}).get("wsl", {}).get("free_gb"),
    "workspace": state.get("workspace", {}).get("root"),
}, indent=2)
state_tok = tokens(state_summary)
hollow_tokens_in += state_tok
done(f"environment check via /state", f"{state_tok} tok  python={py_ver}")

# Step 3: build the prompt (clean — only the spec + minimal context)
section("Step 3 — submit to task scheduler (complexity=4 → code model)")
context_note = ""
if hits and hits[0].score > 0.5:
    context_note = f"\n\nNote: the workspace contains related code in {hits[0].file.split('/')[-1]}."

full_prompt = GAME_SPEC + context_note
prompt_tok = tokens(full_prompt)
hollow_tokens_in += prompt_tok
info(f"prompt: {prompt_tok} tok")

t0 = time.time()
result = h.task(
    description=full_prompt,
    complexity=4,
    system_prompt="You are an expert Python/pygame developer. Write clean, complete, runnable code.",
    timeout=180,
)
gen_ms = int((time.time()-t0)*1000)

if result.ok:
    code = result.response.strip()
    # strip markdown fences if model added them
    if code.startswith("```"):
        lines = code.splitlines()
        code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    hollow_tokens_out += tokens(code)
    done(f"code generated", f"{tokens(code)} tok  {gen_ms}ms  model={result.model_role}")
else:
    code = ""
    print(f"{RED}✗ generation failed: {result.error}{RESET}")

# Step 4: write file
section("Step 4 — write file via /fs/write")
out_path = "/agentOS/workspace/flappybird_hollow.py"
h.write(out_path, code)
done(f"wrote {out_path}", f"{len(code.splitlines())} lines")

hollow_elapsed = time.time() - t_hollow_start
total_hollow = hollow_tokens_in + hollow_tokens_out
info(f"\ntotal hollow tokens in={hollow_tokens_in}  out={hollow_tokens_out}  total={total_hollow}  time={hollow_elapsed:.1f}s")


# ══════════════════════════════════════════════════════════════════════════════
#  NAIVE APPROACH
# ══════════════════════════════════════════════════════════════════════════════

banner("naive approach")
naive_tokens_in  = 0
naive_tokens_out = 0
t_naive_start = time.time()

# Step 1: environment discovery — agent runs shell commands to understand system
section("Step 1 — environment discovery (shell commands)")
discovery_parts = []

cmds = [
    ("python3 --version",                          "python version"),
    ("pip show pygame 2>/dev/null || pip3 show pygame 2>/dev/null || echo 'pygame not found'", "pygame check"),
    ("ls -la /agentOS/workspace/ 2>/dev/null",     "workspace listing"),
    ("ls -la /agentOS/ 2>/dev/null",               "project structure"),
    ("cat /agentOS/config.json 2>/dev/null",       "config"),
    ("env | grep -E 'PATH|HOME|USER|DISPLAY'",     "environment vars"),
    ("df -h / 2>/dev/null",                        "disk space"),
    ("python3 -c \"import sys; print(sys.path)\"", "python path"),
]

for cmd, label in cmds:
    out = shell(cmd)
    tok = tokens(out)
    naive_tokens_in += tok
    discovery_parts.append(f"# {label}\n$ {cmd}\n{out}")
    info(f"$ {cmd[:55]}  → {tok} tok")

discovery_context = "\n\n".join(discovery_parts)
done(f"discovery complete", f"{naive_tokens_in} tok from {len(cmds)} shell calls")

# Step 2: naive prompt — bloated with all the discovery context
section("Step 2 — build prompt (with full discovery context)")
naive_prompt = f"""I need to build a Flappy Bird game. Here is my system context:

{discovery_context}

Based on the above system information, please write the following:

{GAME_SPEC}"""

naive_prompt_tok = tokens(naive_prompt)
naive_tokens_in += naive_prompt_tok
info(f"full prompt: {naive_prompt_tok} tok  (spec={tokens(GAME_SPEC)} + context={naive_tokens_in - naive_prompt_tok})")

# Step 3: direct Ollama call (no scheduler — naive agent calls raw API)
section("Step 3 — direct Ollama API call (no scheduler)")
import urllib.request, urllib.error

ollama_url = CFG.get("ollama", {}).get("host", "http://localhost:11434")
# naive agent picks a model manually (doesn't know the routing table)
naive_model = "mistral-nemo:12b"
info(f"calling {ollama_url} directly with {naive_model}")

payload = json.dumps({
    "model": naive_model,
    "messages": [
        {"role": "system", "content": "You are a Python developer. Write complete runnable code only."},
        {"role": "user",   "content": naive_prompt},
    ],
    "stream": False,
}).encode()

t0 = time.time()
naive_code = ""
try:
    req = urllib.request.Request(
        f"{ollama_url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    naive_code = resp.get("message", {}).get("content", "").strip()
    if naive_code.startswith("```"):
        lines = naive_code.splitlines()
        naive_code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    naive_tokens_out += tokens(naive_code)
    gen_ms2 = int((time.time()-t0)*1000)
    done(f"code generated", f"{tokens(naive_code)} tok  {gen_ms2}ms")
except Exception as e:
    print(f"{RED}✗ Ollama call failed: {e}{RESET}")
    gen_ms2 = 0

# Step 4: write file (manually — no hollow)
section("Step 4 — write file (open() directly)")
out_path_naive = "/agentOS/workspace/flappybird_naive.py"
with open(out_path_naive, "w") as f:
    f.write(naive_code)
done(f"wrote {out_path_naive}", f"{len(naive_code.splitlines())} lines")

naive_elapsed = time.time() - t_naive_start
total_naive = naive_tokens_in + naive_tokens_out
info(f"\ntotal naive tokens in={naive_tokens_in}  out={naive_tokens_out}  total={total_naive}  time={naive_elapsed:.1f}s")


# ══════════════════════════════════════════════════════════════════════════════
#  VERIFY — syntax check both files
# ══════════════════════════════════════════════════════════════════════════════

banner("Verification — syntax check both files")

def syntax_ok(path):
    r = shell(f"python3 -m py_compile {path} 2>&1")
    return (not r, r)

h_ok, h_err = syntax_ok("/agentOS/workspace/flappybird_hollow.py")
n_ok, n_err = syntax_ok("/agentOS/workspace/flappybird_naive.py")

if h_ok:
    done("flappybird_hollow.py  — syntax OK")
else:
    print(f"{RED}✗ flappybird_hollow.py  — {h_err[:100]}{RESET}")

if n_ok:
    done("flappybird_naive.py   — syntax OK")
else:
    print(f"{RED}✗ flappybird_naive.py   — {n_err[:100]}{RESET}")

# line counts
h_lines = len(open("/agentOS/workspace/flappybird_hollow.py").read().splitlines())
n_lines = len(open("/agentOS/workspace/flappybird_naive.py").read().splitlines()) if naive_code else 0
done(f"hollow game: {h_lines} lines   naive game: {n_lines} lines")


# ══════════════════════════════════════════════════════════════════════════════
#  RESULTS
# ══════════════════════════════════════════════════════════════════════════════

banner("Results")

saved     = total_naive - total_hollow
pct_saved = saved / total_naive * 100 if total_naive else 0

print(f"  {'':35} {'hollow':>8}   {'naive':>8}   {'savings':>8}")
print(f"  {'─'*35}   {'─'*8}   {'─'*8}   {'─'*8}")

rows = [
    ("Context / discovery (tokens in)",  hollow_tokens_in,  naive_tokens_in),
    ("Generated code (tokens out)",      hollow_tokens_out, naive_tokens_out),
    ("Total tokens",                     total_hollow,      total_naive),
    ("Time (seconds)",                   f"{hollow_elapsed:.1f}s", f"{naive_elapsed:.1f}s"),
    ("API calls for discovery",          "3",               str(len(cmds))),
    ("Syntax valid",                     "yes" if h_ok else "no", "yes" if n_ok else "no"),
]

for label, hv, nv in rows:
    if isinstance(hv, int) and isinstance(nv, int):
        pct = (1 - hv/nv)*100 if nv else 0
        color = GREEN if pct > 50 else YELLOW if pct > 20 else RESET
        print(f"  {label:<35} {color}{hv:>8}{RESET}   {nv:>8}   {color}{pct:>7.0f}%{RESET}")
    else:
        print(f"  {label:<35} {GREEN}{str(hv):>8}{RESET}   {str(nv):>8}")

print(f"\n  {GREEN}hollow saved {saved} tokens ({pct_saved:.1f}%) building the same game{RESET}")
print(f"\n  files:")
print(f"    {GRAY}/agentOS/workspace/flappybird_hollow.py{RESET}")
print(f"    {GRAY}/agentOS/workspace/flappybird_naive.py{RESET}")
print(f"\n  to play (requires display):")
print(f"    {GRAY}cd /agentOS/workspace && python3 flappybird_hollow.py{RESET}\n")
