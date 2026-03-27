#!/usr/bin/env python3
"""
hollow token demo — the 30-second proof

Run:    python3 /agentOS/tools/token_demo.py
Record: asciinema rec demo.cast -c "python3 /agentOS/tools/token_demo.py"
"""

import sys, json, time, subprocess
sys.path.insert(0, "/agentOS")
from sdk.hollow import Hollow

BASE   = "http://localhost:7777"
CFG    = json.load(open("/agentOS/config.json"))
MASTER = CFG["api"]["token"]
h      = Hollow(BASE, MASTER)

R  = "\033[0m";  B  = "\033[1m"
CY = "\033[1;36m"; GR = "\033[1;32m"; YE = "\033[1;33m"; GY = "\033[90m"

def tok(text):
    try:
        import tiktoken
        return len(tiktoken.get_encoding("cl100k_base").encode(str(text)))
    except ImportError:
        return max(1, len(str(text)) // 4)

def shell(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
    return (r.stdout + r.stderr).decode("utf-8", errors="replace").strip()

def pause(s=0.5): time.sleep(s)

def bar(n, total, width=32):
    filled = int(n / total * width) if total else 0
    return f"{'█' * filled}{'░' * (width - filled)}"

def row(label, color, count, ref):
    pct = int(count / ref * 100) if ref else 0
    print(f"  {color}{B}{label:<8}{R}  {color}{count:>5} tok{R}  {GY}{bar(count, ref)}{R}  {GY}{pct}%{R}")

def div(title=""):
    line = "━" * 56
    if title:
        print(f"\n{CY}{line}{R}\n{CY}  {title}{R}\n{CY}{line}{R}\n")
    else:
        print(f"{CY}{line}{R}\n")

# ── header ────────────────────────────────────────────────────────────────────
div("hollow  ·  token efficiency demo")
print(f"  {GY}task: find where task routing is defined in the codebase{R}\n")
pause(1.0)

# ── naive ─────────────────────────────────────────────────────────────────────
print(f"{YE}{B}  naive  —  grep + cat files{R}")
print(f"{GY}  what an agent with no OS does:{R}\n")
pause(0.4)

found = shell("grep -rl --include='*.py' 'complexity' /agentOS/agents/ /agentOS/api/ 2>/dev/null")
n = tok(found)
sys.stdout.write(f"  {GY}$ grep -rl 'complexity' ...{R}"); pause(0.3)
print(f"  {YE}+{n:>4} tok{R}  {GY}({len(found.splitlines())} files){R}")

naive = n
for fpath in found.splitlines()[:3]:
    fpath = fpath.strip()
    if not fpath: continue
    content = shell(f"cat {fpath}")
    t = tok(content)
    naive += t
    sys.stdout.write(f"  {GY}$ cat {fpath.split('/')[-1]:<32}{R}"); pause(0.35)
    print(f"  {YE}+{t:>4} tok{R}  {GY}({len(content.splitlines())} lines){R}")

print(f"\n  {YE}{B}total: {naive} tokens   {1+len(found.splitlines()[:3])} calls{R}\n")
pause(1.2)

# ── hollow ────────────────────────────────────────────────────────────────────
print(f"{GR}{B}  hollow  —  semantic_search{R}")
print(f"{GY}  natural language → cosine similarity → exact chunks:{R}\n")
pause(0.4)

sys.stdout.write(f"  {GY}$ POST /semantic/search  \"complexity routing ...\"  {R}")
sys.stdout.flush()
t0 = time.time()
hits = h.search("task complexity routing thresholds model selection", top_k=3)
ms = max(0, int((time.time() - t0) * 1000))
hollow = tok(json.dumps([{"file": r.file, "score": round(r.score, 3), "preview": r.preview} for r in hits]))
print(f"  {GR}+{hollow} tok{R}  {GY}{ms}ms{R}")
for r in hits:
    print(f"  {GY}  ↳ {r.score:.2f}  {r.file.split('/')[-1]}{R}")

print(f"\n  {GR}{B}total: {hollow} tokens   1 call{R}\n")
pause(1.0)

# ── result ────────────────────────────────────────────────────────────────────
div("result")
row("naive",  YE, naive,  naive)
pause(0.4)
row("hollow", GR, hollow, naive)
pct = (1 - hollow / naive) * 100 if naive else 0
print(f"\n  {GR}{B}hollow used {pct:.0f}% fewer tokens for the same answer{R}\n")
div()
print(f"  {GY}full benchmark:  python3 /agentOS/tools/bench_compare.py{R}\n")
