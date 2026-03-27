#!/usr/bin/env python3
"""
AgentOS filesystem tool — all ops return JSON, no exceptions.
Usage:
  agent-fs list <path>
  agent-fs read <path>
  agent-fs write <path> <content>
  agent-fs stat <path>
  agent-fs exists <path>
  agent-fs mkdir <path>
  agent-fs delete <path>
  agent-fs move <src> <dst>
  agent-fs copy <src> <dst>
"""
import json, sys, shutil
from datetime import datetime, timezone
from pathlib import Path


def _stat(p: Path) -> dict:
    s = p.stat()
    return {
        "path": str(p),
        "type": "dir" if p.is_dir() else "file",
        "size_bytes": s.st_size,
        "modified": datetime.fromtimestamp(s.st_mtime, tz=timezone.utc).isoformat(),
        "exists": True
    }


def cmd_list(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"not found: {path}"}
    entries = []
    for item in sorted(p.iterdir()):
        try:
            entries.append(_stat(item) | {"name": item.name})
        except Exception:
            entries.append({"name": item.name, "error": "stat failed"})
    return {"ok": True, "path": str(p), "entries": entries, "count": len(entries)}


def cmd_read(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if p.is_dir():
        return {"ok": False, "error": "is a directory, use list"}
    content = p.read_text(errors="replace")
    return {"ok": True, "path": str(p), "content": content, "lines": content.count("\n") + 1, "size_bytes": p.stat().st_size}


def cmd_write(path: str, content: str) -> dict:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"ok": True, "path": str(p), "size_bytes": len(content.encode())}


def cmd_stat(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "exists": False, "path": path}
    return {"ok": True} | _stat(p)


def cmd_exists(path: str) -> dict:
    p = Path(path)
    return {"ok": True, "exists": p.exists(), "path": path, "type": "dir" if p.is_dir() else "file" if p.is_file() else "unknown"}


def cmd_mkdir(path: str) -> dict:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": str(p)}


def cmd_delete(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"ok": True, "deleted": path}


def cmd_move(src: str, dst: str) -> dict:
    shutil.move(src, dst)
    return {"ok": True, "from": src, "to": dst}


def cmd_copy(src: str, dst: str) -> dict:
    s = Path(src)
    if s.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return {"ok": True, "from": src, "to": dst}


def main():
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"error": "Usage: agent-fs <list|read|write|stat|exists|mkdir|delete|move|copy> [args]"}))
        sys.exit(1)

    op = args[0]
    try:
        if op == "list":
            print(json.dumps(cmd_list(args[1] if len(args) > 1 else ".")))
        elif op == "read":
            print(json.dumps(cmd_read(args[1])))
        elif op == "write":
            content = args[2] if len(args) > 2 else sys.stdin.read()
            print(json.dumps(cmd_write(args[1], content)))
        elif op == "stat":
            print(json.dumps(cmd_stat(args[1])))
        elif op == "exists":
            print(json.dumps(cmd_exists(args[1])))
        elif op == "mkdir":
            print(json.dumps(cmd_mkdir(args[1])))
        elif op == "delete":
            print(json.dumps(cmd_delete(args[1])))
        elif op == "move":
            print(json.dumps(cmd_move(args[1], args[2])))
        elif op == "copy":
            print(json.dumps(cmd_copy(args[1], args[2])))
        else:
            print(json.dumps({"error": f"Unknown op: {op}"}))
            sys.exit(1)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
