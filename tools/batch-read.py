#!/usr/bin/env python3
"""Read multiple files in a single call. Core token-saver."""
import json, sys
from pathlib import Path

def main():
    paths = sys.argv[1:]
    if not paths:
        print(json.dumps({"error": "Usage: batch-read file1 file2 ..."}))
        sys.exit(1)

    results = {}
    for path in paths:
        p = Path(path)
        if not p.exists():
            results[path] = {"ok": False, "error": "not found"}
        elif not p.is_file():
            results[path] = {"ok": False, "error": "not a file"}
        else:
            try:
                content = p.read_text(errors="replace")
                results[path] = {
                    "ok": True,
                    "content": content,
                    "lines": content.count("\n") + 1,
                    "size_bytes": p.stat().st_size
                }
            except Exception as e:
                results[path] = {"ok": False, "error": str(e)}

    print(json.dumps({"files": results, "count": len(results), "ok_count": sum(1 for v in results.values() if v["ok"])}))

if __name__ == "__main__":
    main()
