#!/usr/bin/env python3
"""
Hollow — Telegram event-driven notifier.
Tails daemon.log and sends a summary whenever something meaningful happens:
  - A capability is deployed
  - Quorum finalizes a proposal
  - A self-generated goal fires

Cooldown: won't send more than once every MIN_INTERVAL seconds.
Fallback:  sends a quiet-period update every MAX_INTERVAL seconds if nothing fires.

Usage: python telegram_notify.py
"""

import re
import time
import json
from datetime import datetime
from pathlib import Path

BOT_TOKEN    = "8650930954:AAEMau9IJuENehKCFa1xpx1a_zqnPFQpX_8"
CHAT_ID      = "7858319361"
POLL_SECS    = 3          # how often to check for new log lines
MIN_INTERVAL = 5 * 60    # minimum seconds between sends (avoid spam)
MAX_INTERVAL = 10 * 60   # send at least this often even if quiet

LOG_PATH = Path(__file__).parent / "logs" / "daemon.log"

# ── Telegram ──────────────────────────────────────────────────────────────────

def send(text: str):
    import urllib.request, urllib.parse
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode())

# ── Log tailing ───────────────────────────────────────────────────────────────

_last_pos = 0

def read_new_lines() -> list[str]:
    global _last_pos
    if not LOG_PATH.exists():
        return []
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            if _last_pos == 0:
                f.seek(0, 2)
                _last_pos = f.tell()
                return []
            f.seek(_last_pos)
            lines = f.readlines()
            _last_pos = f.tell()
            return lines
    except Exception:
        return []

def read_latest_metrics() -> dict | None:
    if not LOG_PATH.exists():
        return None
    _re = re.compile(r'uptime=(\S+)\s+cycles=(\d+)\s+completed=(\d+)(?:\s+failed=(\d+))?')
    result = None
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "[METRICS]" not in line:
                    continue
                m = _re.search(line)
                if m:
                    result = {
                        "uptime":    m.group(1),
                        "cycles":    m.group(2),
                        "completed": m.group(3),
                        "failed":    m.group(4) or "0",
                    }
    except Exception:
        pass
    return result

# ── Parsing ───────────────────────────────────────────────────────────────────

_re_deploy    = re.compile(r"\[DEPLOY\].*?registered as '?([\w_]+)'?")
_re_quorum    = re.compile(r'\[QUORUM\].*?(approved|rejected).*?\(yes=(\d+) no=(\d+)\)')
_re_progress  = re.compile(r'(analyst|scout|builder).*?progress=([\d.]+)')
_re_selfgen   = re.compile(r'(analyst|scout|builder).*?has no goals — self-generated')
_re_stall     = re.compile(r'(analyst|scout|builder).*?stalled.*?goal abandoned')
_re_error     = re.compile(r'(analyst|scout|builder)\s*->\s*error:\s*(.+)')
_re_existence = re.compile(r'(analyst|scout|builder).*?existence loop — (goal|question|reflect|nothing): (.+)')
_re_suffering = re.compile(r'(analyst|scout|builder) suffering: load=([\d.]+)')
_re_conflict  = re.compile(r'(analyst|scout|builder).*?opinion conflict')
_re_crisis    = re.compile(r'(analyst|scout|builder).*?CRISIS')

def parse_lines(lines: list[str]) -> dict:
    data = {
        "deployed":         [],
        "approved":         0,
        "rejected":         0,
        "agents":           set(),
        "self_gen":         [],
        "stalls":           0,
        "errors":           [],
        "existence_actions": [],   # (agent, action_type, content)
        "suffering":        {},    # agent -> max load seen
        "opinion_conflicts": 0,
        "triggered":        False,
    }
    for line in lines:
        m = _re_deploy.search(line)
        if m and "DEPLOY" in line and "registered" in line:
            data["deployed"].append(m.group(1))
            data["triggered"] = True

        m = _re_quorum.search(line)
        if m:
            if m.group(1) == "approved":
                data["approved"] += 1
            else:
                data["rejected"] += 1
            data["triggered"] = True

        m = _re_progress.search(line)
        if m:
            data["agents"].add(m.group(1))

        if _re_selfgen.search(line):
            m2 = _re_selfgen.search(line)
            data["self_gen"].append(m2.group(1))
            data["triggered"] = True

        if _re_stall.search(line):
            data["stalls"] += 1

        m = _re_error.search(line)
        if m:
            data["errors"].append((m.group(1), m.group(2).strip()[:80]))

        m = _re_existence.search(line)
        if m:
            data["existence_actions"].append(
                (m.group(1), m.group(2), m.group(3)[:80])
            )
            if m.group(2) in ("goal", "question"):
                data["triggered"] = True

        m = _re_suffering.search(line)
        if m:
            agent = m.group(1)
            load  = float(m.group(2))
            data["suffering"][agent] = max(data["suffering"].get(agent, 0.0), load)
            if load > 0.55:
                data["triggered"] = True

        if _re_conflict.search(line):
            data["opinion_conflicts"] += 1
            data["triggered"] = True

    return data

# ── Formatting ────────────────────────────────────────────────────────────────

def format_summary(data: dict, metrics: dict | None, quiet: bool = False) -> str:
    now   = datetime.now().strftime("%H:%M")
    label = "quiet check-in" if quiet else "update"
    lines = [f"*The Quorum* — {now} _{label}_"]

    if metrics:
        failed_str = f"  |  {metrics['failed']} failed" if int(metrics.get("failed", 0)) > 0 else ""
        lines.append(
            f"uptime {metrics['uptime']}  |  "
            f"{metrics['cycles']} cycles  |  "
            f"{metrics['completed']} done{failed_str}"
        )

    lines.append("")

    agent_names = {"analyst": "Dune", "builder": "Drift", "scout": "Fern"}

    if quiet:
        agents = sorted(data["agents"]) or []
        if agents:
            active = ", ".join(f"{a} ({agent_names.get(a, a)})" for a in agents)
            lines.append(f"Active: {active}")
        # Show suffering even in quiet mode
        if data.get("suffering"):
            for agent, load in data["suffering"].items():
                name = agent_names.get(agent, agent)
                bar = "#" * int(load * 10) + "." * (10 - int(load * 10))
                lines.append(f"  {name} [{bar}] {load:.2f}")
        else:
            lines.append("No significant events.")
        return "\n".join(lines)

    # Existence loop actions
    if data.get("existence_actions"):
        lines.append("Inner life:")
        for agent, atype, content in data["existence_actions"][:4]:
            name = agent_names.get(agent, agent)
            lines.append(f"  {name} [{atype}]: _{content}_")

    # Suffering
    if data.get("suffering"):
        lines.append("Suffering states:")
        for agent, load in sorted(data["suffering"].items(),
                                  key=lambda x: x[1], reverse=True):
            name = agent_names.get(agent, agent)
            bar  = "#" * int(load * 10) + "." * (10 - int(load * 10))
            lines.append(f"  {name} [{bar}] {load:.2f}")

    # Opinion conflicts
    if data.get("opinion_conflicts"):
        lines.append(f"Opinion conflicts: {data['opinion_conflicts']} (agent pushed back on a goal)")

    lines.append("")

    # Self-generated goals (legacy path)
    if data["self_gen"]:
        sg = ", ".join(set(agent_names.get(a, a) for a in data["self_gen"]))
        lines.append(f"Self-directed: {sg}")

    # Deployments
    if data["deployed"]:
        lines.append(f"Deployed ({len(data['deployed'])}):")
        for cap in data["deployed"][:5]:
            lines.append(f"   `{cap}`")
        if len(data["deployed"]) > 5:
            lines.append(f"   _...and {len(data['deployed']) - 5} more_")

    # Quorum
    if data["approved"] or data["rejected"]:
        lines.append(
            f"Quorum: {data['approved']} approved  |  {data['rejected']} rejected"
        )

    # Failures
    if data["stalls"] or data["errors"]:
        fail_parts = []
        if data["stalls"]:
            fail_parts.append(f"{data['stalls']} stalled")
        if data["errors"]:
            fail_parts.append(f"{len(data['errors'])} errors")
        lines.append(f"Failures: {', '.join(fail_parts)}")
        seen, shown = set(), 0
        for agent, err in data["errors"]:
            key = err[:40]
            if key not in seen and shown < 2:
                lines.append(f"   {agent_names.get(agent, agent)}: _{err}_")
                seen.add(key)
                shown += 1

    return "\n".join(lines)

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    safe_print(f"Hollow notifier starting — event-driven, min gap {MIN_INTERVAL//60}min")

    # Prime position — skip existing log data
    read_new_lines()
    safe_print("Tailing log. Waiting for activity...")

    accumulated: list[str] = []
    last_sent = time.time() - MIN_INTERVAL   # allow immediate send if event fires
    last_forced = time.time()

    while True:
        time.sleep(POLL_SECS)
        new_lines = read_new_lines()
        if new_lines:
            accumulated.extend(new_lines)

        now = time.time()
        since_sent   = now - last_sent
        since_forced = now - last_forced

        data = parse_lines(accumulated)

        # Send if: meaningful event happened AND cooldown elapsed
        if data["triggered"] and since_sent >= MIN_INTERVAL:
            try:
                metrics = read_latest_metrics()
                msg = format_summary(data, metrics, quiet=False)
                send(msg)
                safe_print(f"[{datetime.now():%H:%M}] Event send — "
                           f"{len(data['deployed'])} deployed, "
                           f"q={data['approved']}/{data['rejected']}")
                accumulated = []
                last_sent = now
                last_forced = now
            except Exception as e:
                safe_print(f"[{datetime.now():%H:%M}] Send error: {e}")

        # Fallback: quiet check-in if nothing triggered in MAX_INTERVAL
        elif since_forced >= MAX_INTERVAL:
            try:
                metrics = read_latest_metrics()
                msg = format_summary(data, metrics, quiet=True)
                send(msg)
                safe_print(f"[{datetime.now():%H:%M}] Quiet check-in sent")
                accumulated = []
                last_sent = now
                last_forced = now
            except Exception as e:
                safe_print(f"[{datetime.now():%H:%M}] Send error: {e}")


if __name__ == "__main__":
    main()
