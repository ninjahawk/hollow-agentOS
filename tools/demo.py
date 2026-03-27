#!/usr/bin/env python3
"""
hollow-agentOS multi-agent demo

An orchestrator registers, reads three core modules, spawns a worker
for each one, each worker runs real Ollama inference to produce a
one-paragraph summary, sends it back via the message bus, and the
orchestrator synthesizes a final report.

Run: python3 /agentOS/tools/demo.py
"""

import sys, json, time, threading
sys.path.insert(0, "/agentOS")
from sdk.hollow import Hollow, register, HollowError

BASE = "http://localhost:7777"
CFG  = json.load(open("/agentOS/config.json"))
MASTER = CFG["api"]["token"]

# ── pretty output ─────────────────────────────────────────────────────────────

CYAN  = "\033[1;36m"
GREEN = "\033[32m"
YELLOW= "\033[33m"
GRAY  = "\033[90m"
BOLD  = "\033[1m"
RED   = "\033[31m"
RESET = "\033[0m"

def banner(t):
    print(f"\n{CYAN}{'═'*56}{RESET}")
    print(f"{CYAN}  {t}{RESET}")
    print(f"{CYAN}{'═'*56}{RESET}")

def step(label, text=""):
    print(f"{YELLOW}▶{RESET} {BOLD}{label}{RESET}" + (f"  {GRAY}{text}{RESET}" if text else ""))

def done(label, text=""):
    print(f"{GREEN}✓{RESET} {label}" + (f"  {GRAY}{text}{RESET}" if text else ""))

def info(text):
    print(f"  {GRAY}{text}{RESET}")

def show_agent(name, agent_id, role, caps):
    print(f"  {GREEN}●{RESET} {BOLD}{name}{RESET}  id={GRAY}{agent_id}{RESET}  role={role}  caps={len(caps)}")

def err(text):
    print(f"{RED}✗ {text}{RESET}")


# ── modules to analyze ────────────────────────────────────────────────────────

MODULES = [
    {
        "name":   "registry",
        "path":   "/agentOS/agents/registry.py",
        "prompt": "In one short paragraph, what does this module do and what is its most important design decision?",
    },
    {
        "name":   "scheduler",
        "path":   "/agentOS/agents/scheduler.py",
        "prompt": "In one short paragraph, what does this module do and what is its most important design decision?",
    },
    {
        "name":   "bus",
        "path":   "/agentOS/agents/bus.py",
        "prompt": "In one short paragraph, what does this module do and what is its most important design decision?",
    },
]


# ── worker function (runs in a thread) ────────────────────────────────────────

def run_worker(module, orchestrator_id, orch_token, results, idx):
    name = module["name"]
    try:
        # Register worker with its own scoped token
        worker, worker_id = register(BASE, MASTER, f"worker-{name}", role="worker")
        done(f"worker-{name} registered", f"id={worker_id}")

        # Read the file
        content = worker.read(module["path"])
        lines = content.count("\n")
        info(f"worker-{name} read {module['path']} ({lines} lines)")

        # Submit to scheduler — routes to mistral-nemo:12b (complexity=2)
        t0 = time.time()
        result = worker.task(
            description=f"{module['prompt']}\n\nFILE: {module['path']}\n\n{content[:3000]}",
            complexity=2,
            system_prompt="You are a concise technical writer. Give exactly one paragraph.",
        )
        ms = int((time.time() - t0) * 1000)

        if not result.ok:
            err(f"worker-{name} task failed: {result.error}")
            results[idx] = None
            return

        summary = result.response.strip()
        done(f"worker-{name} got response", f"{ms}ms  {result.tokens_out} tok")
        info(f"  → {summary[:120]}{'…' if len(summary)>120 else ''}")

        # Send result back to orchestrator via message bus
        msg_id = worker.send(
            orchestrator_id,
            content={"module": name, "path": module["path"], "summary": summary},
            msg_type="result",
        )
        done(f"worker-{name} sent result to orchestrator", f"msg={msg_id[:8]}")

        results[idx] = summary

        # Terminate self
        worker.terminate(worker_id)
        info(f"worker-{name} terminated")

    except Exception as e:
        err(f"worker-{name} crashed: {e}")
        results[idx] = None


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    banner("hollow-agentOS  multi-agent demo")
    print(f"  base: {BASE}")
    print(f"  token: {MASTER[:8]}...\n")

    # ── 1. register orchestrator ──────────────────────────────────────────────
    step("Registering orchestrator agent")
    orch, orch_id = register(BASE, MASTER, "orchestrator", role="orchestrator")
    show_agent("orchestrator", orch_id, "orchestrator", ["shell","fs_read","fs_write","ollama","spawn","message","semantic"])
    print()

    # ── 2. list agents (shows registry is live) ───────────────────────────────
    step("Agent registry state")
    agents = Hollow(BASE, MASTER).agents()
    active = [a for a in agents if a.get("status") == "active"]
    for a in active:
        show_agent(a["name"], a["agent_id"], a["role"], a.get("capabilities", []))
    print()

    # ── 3. spawn workers in parallel ─────────────────────────────────────────
    step("Spawning 3 workers in parallel", "(one per module)")
    results = [None] * len(MODULES)
    threads = []
    t_start = time.time()

    for i, module in enumerate(MODULES):
        t = threading.Thread(
            target=run_worker,
            args=(module, orch_id, None, results, i),
            daemon=True,
        )
        threads.append(t)
        t.start()
        time.sleep(0.1)  # slight stagger so output doesn't collide

    for t in threads:
        t.join(timeout=180)

    elapsed = time.time() - t_start
    print()
    step("All workers finished", f"{elapsed:.1f}s total")
    print()

    # ── 4. orchestrator reads inbox ───────────────────────────────────────────
    step("Orchestrator reading inbox")
    messages = orch.inbox(unread_only=True, limit=10)
    done(f"Received {len(messages)} messages")
    for m in messages:
        info(f"  from={m.from_id[:8]}  type={m.msg_type}  module={m.content.get('module')}")
    print()

    # ── 5. orchestrator synthesizes ───────────────────────────────────────────
    summaries = [r for r in results if r]
    if not summaries:
        err("No results to synthesize")
        return

    step("Orchestrator synthesizing final report via scheduler")
    combined = "\n\n".join(
        f"MODULE: {MODULES[i]['name']}\n{results[i]}"
        for i in range(len(MODULES))
        if results[i]
    )

    t0 = time.time()
    final = orch.task(
        description=(
            "You received these module summaries from three worker agents analyzing "
            "hollow-agentOS, an open-source token-efficient agent OS.\n\n"
            f"{combined}\n\n"
            "In 2-3 sentences, describe what hollow-agentOS is and how these three "
            "modules work together."
        ),
        complexity=2,
        system_prompt="You are a concise technical writer.",
    )
    ms = int((time.time() - t0) * 1000)

    print()
    banner("FINAL REPORT  (synthesized by orchestrator)")
    if final.ok:
        print(f"\n{final.response.strip()}\n")
        info(f"model={final.model_role}  {ms}ms  {final.tokens_out} tokens out")
    else:
        err(f"Synthesis failed: {final.error}")

    # ── 6. cleanup ────────────────────────────────────────────────────────────
    print()
    step("Cleanup")
    Hollow(BASE, MASTER).terminate(orch_id)
    done("orchestrator terminated")

    # ── 7. summary ────────────────────────────────────────────────────────────
    print()
    banner(f"Done  —  {len(summaries)}/3 workers succeeded  {elapsed:.1f}s")
    print()


if __name__ == "__main__":
    main()
