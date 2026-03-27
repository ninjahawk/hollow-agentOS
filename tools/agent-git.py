#!/usr/bin/env python3
"""Git operations with JSON output. No pager, no color, no interactive prompts."""
import json, sys, subprocess
from pathlib import Path


def _run(cmd: str, cwd: str = None) -> tuple[int, str, str]:
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "TERM": "dumb"}
    import os
    env = {**os.environ, **env}
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def status(cwd=None):
    code, out, err = _run("git status --porcelain=v1 -b", cwd)
    if code != 0:
        return {"ok": False, "error": err}
    lines = out.splitlines()
    branch = lines[0].replace("## ", "").split("...")[0] if lines else "unknown"
    staged, unstaged, untracked = [], [], []
    for line in lines[1:]:
        if len(line) < 3:
            continue
        x, y, name = line[0], line[1], line[3:]
        if x not in (" ", "?"):
            staged.append({"file": name, "status": x})
        if y not in (" ", "?"):
            unstaged.append({"file": name, "status": y})
        if line[:2] == "??":
            untracked.append({"file": name})
    return {"ok": True, "branch": branch, "staged": staged, "unstaged": unstaged, "untracked": untracked,
            "clean": not staged and not unstaged and not untracked}


def log(n=20, cwd=None):
    fmt = "--pretty=format:%H|%an|%ae|%ai|%s"
    code, out, err = _run(f"git log {fmt} -n {n}", cwd)
    if code != 0:
        return {"ok": False, "error": err}
    commits = []
    for line in out.splitlines():
        parts = line.split("|", 4)
        if len(parts) == 5:
            commits.append({"hash": parts[0], "author": parts[1], "email": parts[2], "date": parts[3], "message": parts[4]})
    return {"ok": True, "commits": commits, "count": len(commits)}


def diff(args="", cwd=None):
    code, out, err = _run(f"git --no-pager diff {args}", cwd)
    return {"ok": code == 0, "diff": out, "error": err if code != 0 else None}


def add(files=".", cwd=None):
    code, out, err = _run(f"git add {files}", cwd)
    return {"ok": code == 0, "error": err if code != 0 else None}


def commit(message: str, cwd=None):
    code, out, err = _run(f'git commit -m "{message}"', cwd)
    return {"ok": code == 0, "output": out, "error": err if code != 0 else None}


def branch(cwd=None):
    code, out, err = _run("git branch -a", cwd)
    if code != 0:
        return {"ok": False, "error": err}
    branches = [b.strip().lstrip("* ") for b in out.splitlines() if b.strip()]
    current = next((b.strip().lstrip("* ") for b in out.splitlines() if b.startswith("*")), None)
    return {"ok": True, "branches": branches, "current": current}


def clone(url: str, dest: str = None, cwd=None):
    cmd = f"git clone {url}" + (f" {dest}" if dest else "")
    code, out, err = _run(cmd, cwd)
    return {"ok": code == 0, "output": out, "error": err if code != 0 else None}


def main():
    args = sys.argv[1:]
    cwd = None

    # pull --cwd flag
    if "--cwd" in args:
        i = args.index("--cwd")
        cwd = args[i + 1]
        args = args[:i] + args[i+2:]

    op = args[0] if args else "status"
    rest = args[1:]

    dispatch = {
        "status": lambda: status(cwd),
        "log": lambda: log(int(rest[0]) if rest else 20, cwd),
        "diff": lambda: diff(" ".join(rest), cwd),
        "add": lambda: add(" ".join(rest) if rest else ".", cwd),
        "commit": lambda: commit(" ".join(rest), cwd),
        "branch": lambda: branch(cwd),
        "clone": lambda: clone(rest[0], rest[1] if len(rest) > 1 else None, cwd),
    }

    if op not in dispatch:
        print(json.dumps({"error": f"Unknown op: {op}. Available: {list(dispatch)}"}))
        sys.exit(1)

    print(json.dumps(dispatch[op]()))

if __name__ == "__main__":
    main()
