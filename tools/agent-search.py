#!/usr/bin/env python3
"""
Workspace search — text and filename search with JSON output.
Usage:
  agent-search <query>                  # search file contents
  agent-search --files <pattern>        # search filenames
  agent-search --context 3 <query>      # include N lines of context
"""
import json, sys, subprocess, os
from pathlib import Path


CONFIG_PATH = Path("/agentOS/config.json")


def _workspace_root() -> str:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("workspace", {}).get("root", "/agentOS/workspace")
        except Exception:
            pass
    return "/agentOS/workspace"


def search_content(query: str, root: str, context: int = 0, extensions: list = None) -> dict:
    """Search file contents using ripgrep if available, else grep."""
    ext_flags = ""
    if extensions:
        ext_flags = " ".join(f"--include='*{e}'" for e in extensions)

    # try ripgrep first
    rg_available = subprocess.run("which rg", shell=True, capture_output=True).returncode == 0
    if rg_available:
        ctx_flag = f"-C {context}" if context else ""
        cmd = f"rg --json {ctx_flag} {ext_flags} '{query}' '{root}' 2>/dev/null"
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        matches = []
        for line in r.stdout.splitlines():
            try:
                obj = json.loads(line)
                if obj.get("type") == "match":
                    data = obj["data"]
                    matches.append({
                        "file": data["path"]["text"],
                        "line": data["line_number"],
                        "text": data["lines"]["text"].rstrip("\n"),
                        "submatches": [{"match": s["match"]["text"], "start": s["start"], "end": s["end"]}
                                       for s in data.get("submatches", [])]
                    })
            except Exception:
                pass
        return {"ok": True, "query": query, "matches": matches, "count": len(matches), "tool": "ripgrep"}

    # fallback: grep
    ctx_flag = f"-C {context}" if context else ""
    cmd = f"grep -rn {ctx_flag} {ext_flags} '{query}' '{root}' 2>/dev/null"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    matches = []
    for line in r.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) >= 3:
            try:
                matches.append({"file": parts[0], "line": int(parts[1]), "text": parts[2]})
            except ValueError:
                matches.append({"file": parts[0], "line": None, "text": ":".join(parts[1:])})
    return {"ok": True, "query": query, "matches": matches, "count": len(matches), "tool": "grep"}


def search_files(pattern: str, root: str) -> dict:
    """Find files by name pattern."""
    root_path = Path(root)
    matches = []
    SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
    for item in root_path.rglob("*"):
        if any(part in SKIP for part in item.parts):
            continue
        if item.is_file() and (pattern.lower() in item.name.lower()):
            s = item.stat()
            matches.append({
                "path": str(item),
                "name": item.name,
                "size_bytes": s.st_size,
                "extension": item.suffix
            })
    return {"ok": True, "pattern": pattern, "matches": matches, "count": len(matches)}


def main():
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"error": "Usage: agent-search [--files] [--context N] <query>"}))
        sys.exit(1)

    root = _workspace_root()
    context = 0
    file_search = False

    # parse flags
    i = 0
    while i < len(args):
        if args[i] == "--files":
            file_search = True
            args.pop(i)
        elif args[i] == "--context" and i + 1 < len(args):
            context = int(args[i + 1])
            args.pop(i)
            args.pop(i)
        elif args[i] == "--root" and i + 1 < len(args):
            root = args[i + 1]
            args.pop(i)
            args.pop(i)
        else:
            i += 1

    query = " ".join(args)
    if not query:
        print(json.dumps({"error": "No query provided"}))
        sys.exit(1)

    if file_search:
        print(json.dumps(search_files(query, root)))
    else:
        print(json.dumps(search_content(query, root, context)))

if __name__ == "__main__":
    main()
