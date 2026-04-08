#!/usr/bin/env python3
"""
Hollow AgentOS — demo simulation
Replays a scripted agent session that looks identical to the live monitor.
Run directly: python sim.py
Captured by VHS:  vhs demo.tape
"""

import sys
import io
import time
import shutil

# Force UTF-8 output on Windows so box-drawing chars render correctly
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── ANSI helpers ──────────────────────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[32m"
CYAN    = "\033[36m"
YELLOW  = "\033[33m"
WHITE   = "\033[97m"
MAGENTA = "\033[35m"
RED     = "\033[31m"
BLUE    = "\033[34m"
BG_DARK = "\033[48;5;235m"

def c(color, text):  return f"{color}{text}{RESET}"
def dim(text):       return c(DIM, text)
def bold(text):      return c(BOLD, text)
def green(text):     return c(GREEN, text)
def cyan(text):      return c(CYAN, text)
def yellow(text):    return c(YELLOW, text)
def white(text):     return c(WHITE, text)
def magenta(text):   return c(MAGENTA, text)
def red(text):       return c(RED, text)

def p(text="", delay=0.0):
    print(text)
    if delay:
        time.sleep(delay)

def slow(text, delay=0.018):
    """Print text char by char like a typewriter."""
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()

def hr(char="─", color=DIM):
    cols = shutil.get_terminal_size((80, 24)).columns
    p(f"{color}{char * cols}{RESET}")

def bar(progress, width=28):
    filled = int(width * progress)
    empty  = width - filled
    return f"{GREEN}{'█' * filled}{DIM}{'░' * empty}{RESET}"

def sleep(s):
    time.sleep(s)


# ── Script ────────────────────────────────────────────────────────────────────

def main():
    cols = shutil.get_terminal_size((80, 24)).columns

    # ── Banner ────────────────────────────────────────────────────────────────
    p()
    p(f"{WHITE} _  _  ___  _    _    _____  __  __{RESET}")
    p(f"{WHITE}| || |/ _ \\| |  | |  / _ \\ \\ \\  / /{RESET}")
    p(f"{WHITE}| __ | (_) | |__| |_| (_) \\ \\/\\/ /{RESET}")
    p(f"{WHITE}|_||_|\\___/|____|____\\___/ \\_/\\_/{RESET}")
    p()
    p(f"{DIM}  hollow agentOS  •  v5.0.0  •  qwen2.5:9b{RESET}")
    hr()
    sleep(0.6)

    # ── Boot sequence ─────────────────────────────────────────────────────────
    p()
    p(f"  {dim('starting daemon...')}", delay=0.2)
    p(f"  {green('✓')} {dim('event bus        online')}", delay=0.08)
    p(f"  {green('✓')} {dim('memory heap      ready  (0 / 512 slots)')}", delay=0.08)
    p(f"  {green('✓')} {dim('audit kernel     armed')}", delay=0.08)
    p(f"  {green('✓')} {dim('capability graph 47 nodes  (nomic-embed-text)')}", delay=0.08)
    p(f"  {green('✓')} {dim('transaction log  clean')}", delay=0.08)
    p(f"  {green('✓')} {dim('ollama           qwen2.5:9b loaded in VRAM')}", delay=0.15)
    p()
    p(f"  {dim('agents registered:')}", delay=0.1)
    p(f"  {cyan('●')} scout      {dim('worker   → idle')}", delay=0.07)
    p(f"  {cyan('●')} rook       {dim('worker   → idle')}", delay=0.07)
    p(f"  {cyan('●')} finch      {dim('coder    → idle')}", delay=0.07)
    p(f"  {cyan('●')} bramble    {dim('reasoner → idle')}", delay=0.07)
    p()
    hr()
    sleep(0.5)

    # ── Cycle 1 ───────────────────────────────────────────────────────────────
    p()
    p(f"  {dim('Cycle 1')}  {dim('────────────────────────────────────────────')}", delay=0.3)
    p()
    p(f"  {cyan('scout')}   {dim('on a mission to')} scan codebase for TODO comments", delay=0.1)
    p(f"           {dim('planning...')}", delay=0.4)
    p(f"           {dim('step 1/3  search_content   →')} {dim('running')}", delay=0.3)
    p(f"           {dim('step 1/3  search_content   →')} {green('done')}  {dim('(23 matches)')}", delay=0.3)
    p(f"           {dim('step 2/3  semantic_search  →')} {dim('running')}", delay=0.4)
    p(f"           {dim('step 2/3  semantic_search  →')} {green('done')}  {dim('(top 8 ranked)')}", delay=0.3)
    p(f"           {dim('step 3/3  memory_set       →')} {green('done')}  {dim('(artifact saved)')}", delay=0.2)
    p(f"           {bar(1.0)}  {green('100%')}  ✓ complete", delay=0.1)
    p()
    p(f"  {cyan('rook')}    {dim('determined to')} monitor ollama VRAM pressure", delay=0.1)
    p(f"           {dim('step 1/2  shell_exec       →')} {green('done')}", delay=0.3)
    p(f"           {dim('step 2/2  memory_set       →')} {green('done')}", delay=0.2)
    p(f"           {bar(1.0)}  {green('100%')}  ✓ complete", delay=0.1)
    p()
    hr()
    sleep(0.5)

    # ── Cycle 2 ───────────────────────────────────────────────────────────────
    p()
    p(f"  {dim('Cycle 2')}  {dim('────────────────────────────────────────────')}", delay=0.3)
    p()
    p(f"  {cyan('scout')}   {dim('boldly attempting to')} summarise TODO findings by priority")
    p(f"           {dim('step 1/3  memory_get      →')} {green('done')}", delay=0.3)
    p(f"           {dim('step 2/3  ollama_chat     →')} {dim('thinking...')}", delay=0.9)
    p(f"           {dim('step 2/3  ollama_chat     →')} {green('done')}  {dim('(1,847 tok)')}", delay=0.2)
    p(f"           {dim('step 3/3  fs_write        →')} {green('done')}  {dim('(workspace/todo_report.md)')}", delay=0.2)
    p(f"           {bar(1.0)}  {green('100%')}  ✓ complete", delay=0.1)
    p()
    p(f"  {cyan('finch')}   {dim('charging headfirst into')} identify gaps in tool coverage")
    p(f"           {dim('step 1/2  shell_exec      →')} {green('done')}", delay=0.35)
    p(f"           {dim('step 2/2  ollama_chat     →')} {dim('thinking...')}", delay=1.1)
    p(f"           {dim('step 2/2  ollama_chat     →')} {green('done')}", delay=0.2)
    p(f"           {bar(1.0)}  {green('100%')}  ✓ complete", delay=0.1)
    p()
    hr()
    sleep(0.4)

    # ── Synthesis event ───────────────────────────────────────────────────────
    p()
    p(f"  {yellow('⚡ capability synthesis')}", delay=0.3)
    p()
    p(f"  {cyan('finch')}   {dim('proposing:')} diff_tool_versions", delay=0.1)
    p(f"           {dim('writing module...')}", delay=1.0)
    p(f"           {dim('sending to quorum  (scout, rook, bramble)')}", delay=0.4)
    p()
    p(f"  {cyan('scout')}   {dim('reviewing proposal...')}", delay=0.5)
    p(f"           {green('✓ accept')}  {dim('logic is sound, imports clean')}", delay=0.2)
    p(f"  {cyan('rook')}    {dim('reviewing proposal...')}", delay=0.4)
    p(f"           {green('✓ accept')}  {dim('fills a real gap')}", delay=0.2)
    p(f"  {cyan('bramble')} {dim('reviewing proposal...')}", delay=0.35)
    p(f"           {green('✓ accept')}  {dim('no conflicts with existing caps')}", delay=0.2)
    p()
    p(f"  {dim('quorum reached  3/3  →')} {green('deploying')}", delay=0.3)
    p(f"  {green('✓')} {dim('hot-loaded')}  tools/dynamic/diff_tool_versions.py", delay=0.2)
    p(f"  {green('✓')} {dim('capability graph updated  48 nodes')}", delay=0.15)
    p()
    hr()
    sleep(0.4)

    # ── Cycle 3 live ticking ──────────────────────────────────────────────────
    p()
    p(f"  {dim('Cycle 3')}  {dim('────────────────────────────────────────────')}", delay=0.3)
    p()

    agents = [
        ("scout",   "not afraid to",         "track VRAM pressure over time"),
        ("rook",    "enthusiastically starting to", "diff tool versions across store"),
        ("finch",   "taking a crack at",      "wrap ripgrep as natural-language tool"),
        ("bramble", "going to try to",        "verify audit log integrity"),
    ]

    progs = [0.0, 0.0, 0.0, 0.0]
    steps = [3, 2, 4, 2]

    # Print initial lines
    lines = []
    for i, (name, verb, goal) in enumerate(agents):
        line = f"  {cyan(name):<24}{dim(verb)} {goal}"
        p(line)
        lines.append((name, verb, goal))

    p()

    # Animate progress
    frames = [
        ([0.12, 0.00, 0.08, 0.25], 0.25),
        ([0.25, 0.15, 0.00, 0.50], 0.30),
        ([0.25, 0.40, 0.20, 1.00], 0.30),
        ([0.50, 0.40, 0.50, 1.00], 0.35),
        ([0.75, 0.75, 0.50, 1.00], 0.30),
        ([1.00, 1.00, 0.75, 1.00], 0.30),
        ([1.00, 1.00, 1.00, 1.00], 0.20),
    ]

    names = [a[0] for a in agents]
    for frame_progs, frame_delay in frames:
        # Move cursor up to redraw progress lines
        sys.stdout.write(f"\033[{len(agents) + 1}A")
        for i, (name, prog) in enumerate(zip(names, frame_progs)):
            pct = int(prog * 100)
            mark = green("✓") if prog >= 1.0 else dim(f"{pct:>3}%")
            line = f"  {cyan(name):<14} {bar(prog, 24)}  {mark}"
            print(f"\033[K{line}")
        print(f"\033[K")
        sys.stdout.flush()
        sleep(frame_delay)

    p()
    hr()
    sleep(0.3)

    # ── Summary ───────────────────────────────────────────────────────────────
    p()
    p(f"  {dim('session summary')}")
    p()
    p(f"  {green('4')} agents    {green('12')} goals completed    {green('1')} capability deployed")
    p(f"  {dim('tokens used:')}  {white('14,302')}   {dim('model:')}  {white('qwen2.5:9b')}   {dim('cloud calls:')}  {green('0')}")
    p()
    p(f"  {dim('all output stored in')} workspace/  {dim('and')} memory/")
    p()
    hr()
    p()
    p(f"  {dim('press')} {white('g')} {dim('to give a goal  •')} {white('q')} {dim('to quit')}")
    p()


if __name__ == "__main__":
    main()
