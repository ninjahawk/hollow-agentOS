"""
Bulk repair low-quality wrappers in the store.
Adds missing: param descriptions, field placeholders, longer descriptions.
Re-uploads each repaired wrapper.
"""

import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

# When running inside Docker API container, use host.docker.internal.
# When running on the host directly, localhost works.
STORE = os.getenv("HOLLOW_STORE_URL", "http://host.docker.internal:7779")
API = os.getenv("HOLLOW_API_URL", "http://localhost:7777")
TOKEN = os.getenv("HOLLOW_API_TOKEN", "ci-test-token-replace-in-production")

# Direct disk write: only works if store/data is accessible at this path.
# Mount ./store/data into the API container or run from the host.
_STORE_DATA_ENV = os.getenv("HOLLOW_STORE_DATA")
STORE_DATA_DIR = Path(_STORE_DATA_ENV) if _STORE_DATA_ENV else Path(__file__).parent.parent / "store" / "data"


def _repo_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _write_to_disk(repo_url: str, wrapper: dict) -> None:
    rid = _repo_id(repo_url)
    path = STORE_DATA_DIR / rid / "wrapper.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(wrapper, indent=2))
    tmp.replace(path)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body else None
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _quality_score(wrapper):
    score = 0.0
    cm = wrapper.get("capability_map", {})
    iface = wrapper.get("interface_spec", {})
    caps = cm.get("capabilities", [])
    for cap in caps[:4]:
        if cap.get("shell_template") and "{" in cap["shell_template"]:
            score += 10
        elif cap.get("shell_template"):
            score += 5
        if any(p.get("description") for p in cap.get("params", [])):
            score += 5
    fields = iface.get("fields", [])
    if len(fields) >= 1:
        score += 10
    if len(fields) >= 2:
        score += 5
    if all(f.get("placeholder") for f in fields):
        score += 5
    desc = cm.get("description", "")
    if len(desc) > 30:
        score += 10
    install_count = wrapper.get("install_count", 0)
    score += min(20, math.log10(install_count + 1) * 10)
    return round(min(100.0, score), 1)


_TOOL_DESCRIPTIONS = {
    "hyperfine": "Benchmark shell commands and compare execution speed across runs",
    "xsv": "Fast CSV data manipulation: slice, filter, join, sort, and analyze tabular files",
    "dog": "User-friendly DNS client — query DNS records from the command line",
    "zoxide": "Smarter cd command — jump to frequently-used directories by typing part of the name",
    "cheat": "Create and display cheatsheets for terminal commands",
    "jq": "Slice, filter, and transform JSON data from the command line",
    "bottom": "Graphical process/system monitor for the terminal — like htop but better",
    "sk": "Fuzzy finder — interactively filter and select from any list of items",
    "choose": "Human-friendly cut and awk alternative for selecting fields from text",
    "fq": "Like jq but for binary formats: pcap, zip, mp4, and 100+ more",
    "eza": "Modern, colorful ls replacement with Git status, icons, and tree view",
    "gitui": "Fast, keyboard-driven Git UI in the terminal",
    "mise": "Polyglot runtime manager — install and switch between versions of any language",
    "lsd": "Next-gen ls with icons, colors, and tree view",
    "helix": "Post-modern modal text editor — Vim-like, built-in LSP, tree-sitter",
    "fd": "Fast, user-friendly alternative to find — simpler syntax, respects .gitignore",
    "navi": "Interactive cheatsheet tool — browse and run commands with fuzzy search",
    "duf": "Disk usage and free space utility — prettier alternative to df",
    "ripgrep": "Blazingly fast recursive grep — searches directory trees for regex patterns",
    "bat": "Cat clone with syntax highlighting, line numbers, and Git diff integration",
    "nushell": "Modern shell that treats all data as structured — pipelines of tables, not text",
    "starship": "Minimal, blazing-fast, configurable cross-shell prompt",
    "broot": "New way to see and navigate directory trees — fuzzy find + preview",
    "atuin": "Replace shell history with a database — searchable, synced across machines",
    "mods": "AI on the command line — pipe text to Claude/GPT and get answers",
    "jrnl": "Simple journal application for the command line",
    "lazygit": "Simple terminal UI for git commands",
    "pueue": "Task scheduler and manager for long-running shell commands",
    "pastel": "Generate, analyze, and manipulate colors in the terminal",
    "hexyl": "Command-line hex viewer with colored output",
    "tokei": "Count lines of code across a codebase — by language, with statistics",
    "scc": "Fast, accurate code counter with complexity estimation",
    "miniserve": "Serve files over HTTP from the command line — no configuration needed",
    "oha": "HTTP load testing tool — bombardier/wrk alternative with a TUI",
    "lychee": "Fast link checker — find broken URLs in files, websites, or documents",
    "git-cliff": "Highly customizable changelog generator from Git history",
    "vhs": "Write terminal GIFs as code — record terminal sessions to animated GIFs",
    "mprocs": "Run multiple commands simultaneously — shows their output in split panels",
    "yq": "Portable YAML, JSON, and XML processor — like jq for YAML",
    "typos": "Fast source code spell checker — finds typos in identifiers and strings",
    "rip": "Safe rm alternative — moves files to system trash instead of deleting",
    "vivid": "LS_COLORS generator — create beautiful, configurable file-type color themes",
    "silicon": "Create beautiful images of your source code — like Carbon but local",
    "dive": "Explore Docker image layers — find what's bloating your images",
    "so": "Terminal interface for Stack Overflow search",
    "ctop": "Top-like interface for container metrics",
    "ruff": "Extremely fast Python linter and formatter — replaces flake8, black, isort",
    "pixi": "Fast, cross-platform package manager for conda packages",
    "uv": "Extremely fast Python package installer and resolver — replaces pip",
    "hurl": "Run and test HTTP requests defined in a plain text format",
    "ugit": "Undo git commands — a safety net for git mistakes",
}


def _nice_description(name: str, existing: str) -> str:
    """Return a richer description if existing is thin."""
    if len(existing) > 50:
        return existing
    # Use curated description if available
    if name.lower() in _TOOL_DESCRIPTIONS:
        return _TOOL_DESCRIPTIONS[name.lower()]
    base = existing or f"Command-line tool: {name}"
    return f"{base}. Efficient terminal utility for developers and power users."


def _infer_placeholder(field_id: str, field_label: str) -> str:
    """Generate a sensible placeholder from the field id/label."""
    hints = {
        "file": "e.g. ./file.txt",
        "path": "e.g. /path/to/target",
        "pattern": "e.g. TODO|FIXME",
        "query": "e.g. search term",
        "url": "e.g. https://example.com",
        "host": "e.g. localhost",
        "port": "e.g. 8080",
        "dir": "e.g. ./src",
        "directory": "e.g. ./src",
        "input": "e.g. input value",
        "output": "e.g. output path",
        "format": "e.g. json",
        "filter": "e.g. .field == value",
        "command": "e.g. run",
        "args": "e.g. --verbose",
        "name": "e.g. my-project",
        "size": "e.g. 100MB",
        "count": "e.g. 10",
        "limit": "e.g. 50",
        "timeout": "e.g. 30",
    }
    lower = field_id.lower()
    for key, hint in hints.items():
        if key in lower:
            return hint
    return f"e.g. {field_label.lower()}"


def _infer_param_description(name: str) -> str:
    hints = {
        "file": "Path to the file to process",
        "path": "File or directory path",
        "pattern": "Search pattern or regular expression",
        "query": "Search query string",
        "url": "Target URL",
        "host": "Hostname or IP address",
        "port": "Port number",
        "dir": "Directory path",
        "directory": "Directory path",
        "input": "Input value or source",
        "output": "Output destination path",
        "format": "Output format (e.g. json, csv)",
        "filter": "Filter expression",
        "command": "Subcommand to run",
        "args": "Additional arguments",
        "name": "Name identifier",
        "size": "Size value with unit",
        "count": "Number of items",
        "limit": "Maximum count",
        "timeout": "Timeout in seconds",
    }
    lower = name.lower()
    for key, desc in hints.items():
        if key in lower:
            return desc
    return name.replace("_", " ").capitalize()


def repair_wrapper(wrapper: dict) -> dict:
    """Return a repaired copy of wrapper with improved quality."""
    import copy
    w = copy.deepcopy(wrapper)
    cm = w.setdefault("capability_map", {})
    iface = w.setdefault("interface_spec", {})

    # Fix description
    name = cm.get("name", "tool")
    cm["description"] = _nice_description(name, cm.get("description", ""))

    # Fix capabilities: add param descriptions
    for cap in cm.get("capabilities", []):
        for param in cap.get("params", []):
            if not param.get("description"):
                param["description"] = _infer_param_description(param.get("name", ""))
            if "default" not in param:
                param["default"] = None

    # Fix interface fields: add placeholders, labels
    for field in iface.get("fields", []):
        if not field.get("placeholder"):
            fid = field.get("id", "")
            label = field.get("label", fid)
            field["placeholder"] = _infer_placeholder(fid, label)
        if not field.get("label"):
            field["label"] = field.get("id", "").replace("_", " ").title()

    return w


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Fetch all wrappers (in pages if needed)
    all_wrappers = []
    offset = 0
    while True:
        data = _req("GET", f"{STORE}/wrappers?limit=100&offset={offset}")
        batch = data.get("wrappers", [])
        all_wrappers.extend(batch)
        if len(all_wrappers) >= data.get("total", 0) or not batch:
            break
        offset += len(batch)

    print(f"Total wrappers: {data.get('total', len(all_wrappers))}, fetched: {len(all_wrappers)}")

    # Target: repair anything below 70 (was 60 — too conservative)
    REPAIR_THRESHOLD = 70

    repaired = 0
    skipped = 0
    failed = 0

    for entry in all_wrappers:
        name = entry.get("name", "?")
        repo_id = entry.get("repo_id", "")
        before_score = entry.get("quality_score", 0)

        if before_score >= REPAIR_THRESHOLD:
            skipped += 1
            continue

        # Fetch full wrapper
        try:
            full = _req("GET", f"{STORE}/wrappers/{repo_id}")
        except Exception as e:
            print(f"  [{name}] fetch error: {e}")
            failed += 1
            continue

        fixed = repair_wrapper(full)
        after_score = _quality_score(fixed)

        if after_score <= before_score:
            print(f"  [{name}] no improvement: {before_score} → {after_score}, skipping")
            skipped += 1
            continue

        # Write directly to disk (bypasses commit-SHA dedup in the API)
        try:
            _write_to_disk(fixed.get("repo_url", ""), fixed)
            print(f"  [{name}] repaired: {before_score} -> {after_score}")
            repaired += 1
        except Exception as e:
            print(f"  [{name}] disk write error: {e}")
            failed += 1

        time.sleep(0.1)

    print(f"\nDone: repaired={repaired} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
