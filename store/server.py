"""
Hollow Store Server — v1.1.0

Central store for Hollow app wrappers. One instance shared by all Hollow installs.
First person to wrap a repo uploads it here. Everyone else downloads it free.

Endpoints:
  GET  /health                         liveness
  POST /wrappers                       upload a wrapper
  GET  /wrappers                       list all wrappers (?limit=20&offset=0&sort=installs|quality)
  GET  /wrappers/{repo_id}             download a wrapper
  POST /wrappers/{repo_id}/install     increment install count
  GET  /wrappers/{repo_id}/version     check if an update is available

Quality scoring (0-100):
  - Each capability has a shell_template: +10 pts (up to 4 caps)
  - Each capability has params with descriptions: +5 pts
  - interface_spec has >= 1 field: +10 pts
  - Wrapper has a real description (not placeholder): +10 pts
  - install_count bonus: log10(count+1) * 10, capped at 20

Storage: plain JSON files at STORE_DATA_DIR/{repo_id}/wrapper.json
No database. No auth for v1. Atomic writes (temp→rename).
"""

import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# ---------------------------------------------------------------------------
# Storage location — override with HOLLOW_STORE_DATA env var
# ---------------------------------------------------------------------------
STORE_DATA_DIR = Path(os.getenv("HOLLOW_STORE_DATA", "./store/data"))
STORE_PORT = int(os.getenv("HOLLOW_STORE_PORT", "7779"))

app = FastAPI(
    title="Hollow Store",
    description="Community wrapper store for Hollow apps",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repo_id(repo_url: str) -> str:
    """Stable 16-char identifier for a repo URL."""
    return hashlib.sha256(repo_url.strip().lower().encode()).hexdigest()[:16]


def _wrapper_path(repo_id: str) -> Path:
    return STORE_DATA_DIR / repo_id / "wrapper.json"


def _read_wrapper(repo_id: str) -> dict:
    p = _wrapper_path(repo_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Wrapper {repo_id} not found")
    return json.loads(p.read_text())


def _quality_score(wrapper: dict) -> float:
    """
    Score a wrapper 0-100 based on structural quality.
    Higher = more complete, more useful, more popular.
    """
    score = 0.0
    cm = wrapper.get("capability_map", {})
    iface = wrapper.get("interface_spec", {})

    # Capabilities with real shell templates
    caps = cm.get("capabilities", [])
    for cap in caps[:4]:
        if cap.get("shell_template") and "{" in cap["shell_template"]:
            score += 10
        elif cap.get("shell_template"):
            score += 5
        # Params with descriptions
        if any(p.get("description") for p in cap.get("params", [])):
            score += 5

    # Interface spec quality
    fields = iface.get("fields", [])
    if len(fields) >= 1:
        score += 10
    if len(fields) >= 2:
        score += 5
    if all(f.get("placeholder") for f in fields):
        score += 5

    # Description quality (penalize generic/placeholder text)
    desc = cm.get("description", "")
    placeholders = {"tool", "utility", "command-line", "application", "wrapper"}
    if len(desc) > 30 and not all(p in desc.lower() for p in placeholders):
        score += 10

    # Popularity bonus: log scale so install_count=1000 → ~30pts, count=10 → ~10pts
    install_count = wrapper.get("install_count", 0)
    score += min(20, math.log10(install_count + 1) * 10)

    return round(min(100.0, score), 1)


def _write_wrapper(repo_id: str, data: dict) -> None:
    p = _wrapper_path(repo_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class WrapperUpload(BaseModel):
    repo_url: str
    source_commit: str
    capability_map: dict
    interface_spec: dict


class VersionCheckRequest(BaseModel):
    current_commit: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "service": "hollow-store", "wrappers": _count_wrappers()}


def _count_wrappers() -> int:
    try:
        return sum(1 for p in STORE_DATA_DIR.iterdir() if (p / "wrapper.json").exists())
    except Exception:
        return 0


@app.post("/wrappers", status_code=201)
def upload_wrapper(body: WrapperUpload):
    """Upload a new wrapper. If one already exists, only replaces if source_commit differs."""
    if not body.repo_url:
        raise HTTPException(status_code=400, detail="repo_url required")
    if not body.capability_map.get("capabilities"):
        raise HTTPException(status_code=400, detail="capability_map.capabilities is empty")
    if not body.interface_spec.get("fields"):
        raise HTTPException(status_code=400, detail="interface_spec.fields is empty")

    rid = _repo_id(body.repo_url)
    existing_path = _wrapper_path(rid)

    # If already stored with same commit, just increment nothing and return
    if existing_path.exists():
        existing = json.loads(existing_path.read_text())
        if existing.get("source_commit") == body.source_commit:
            return {"repo_id": rid, "status": "already_current", "install_count": existing.get("install_count", 0)}

    install_count = 0
    if existing_path.exists():
        install_count = json.loads(existing_path.read_text()).get("install_count", 0)

    wrapper = {
        "schema_version": 1,
        "repo_url": body.repo_url,
        "source_commit": body.source_commit,
        "wrapped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "install_count": install_count,
        "capability_map": body.capability_map,
        "interface_spec": body.interface_spec,
    }
    _write_wrapper(rid, wrapper)
    return {
        "repo_id": rid,
        "status": "uploaded",
        "install_count": install_count,
        "quality_score": _quality_score(wrapper),
    }


@app.get("/wrappers")
def list_wrappers(
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query("quality", pattern="^(installs|quality|newest)$"),
    q: Optional[str] = Query(None, description="Search by name or description"),
):
    """List wrappers. sort=quality (default), installs, or newest. q= for search."""
    STORE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    wrappers = []
    try:
        for p in STORE_DATA_DIR.iterdir():
            wf = p / "wrapper.json"
            if wf.exists():
                try:
                    w = json.loads(wf.read_text())
                    name = w.get("capability_map", {}).get("name", "")
                    description = w.get("capability_map", {}).get("description", "")
                    # Search filter
                    if q:
                        needle = q.lower()
                        if needle not in name.lower() and needle not in description.lower():
                            continue
                    wrappers.append({
                        "repo_id": p.name,
                        "repo_url": w.get("repo_url"),
                        "name": name,
                        "description": description,
                        "install_count": w.get("install_count", 0),
                        "source_commit": w.get("source_commit"),
                        "wrapped_at": w.get("wrapped_at"),
                        "quality_score": _quality_score(w),
                        "capability_count": len(w.get("capability_map", {}).get("capabilities", [])),
                    })
                except Exception:
                    pass
    except Exception:
        pass

    sort_key = {
        "installs": lambda w: w["install_count"],
        "quality": lambda w: w["quality_score"],
        "newest": lambda w: w.get("wrapped_at", ""),
    }.get(sort, lambda w: w["quality_score"])
    wrappers.sort(key=sort_key, reverse=True)

    return {
        "total": len(wrappers),
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "wrappers": wrappers[offset: offset + limit],
    }


@app.get("/wrappers/{repo_id}")
def get_wrapper(repo_id: str):
    """Download a full wrapper by repo_id."""
    return _read_wrapper(repo_id)


@app.post("/wrappers/{repo_id}/install")
def record_install(repo_id: str):
    """Increment install count. Call when a user installs this app."""
    wrapper = _read_wrapper(repo_id)
    wrapper["install_count"] = wrapper.get("install_count", 0) + 1
    _write_wrapper(repo_id, wrapper)
    return {"repo_id": repo_id, "install_count": wrapper["install_count"]}


@app.get("/wrappers/{repo_id}/version")
def check_version(repo_id: str, current_commit: str = Query(...)):
    """Check if a newer wrapper exists for a repo."""
    wrapper = _read_wrapper(repo_id)
    stored_commit = wrapper.get("source_commit", "")
    update_available = stored_commit != current_commit and bool(stored_commit)
    return {
        "repo_id": repo_id,
        "repo_url": wrapper.get("repo_url"),
        "stored_commit": stored_commit,
        "current_commit": current_commit,
        "update_available": update_available,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    STORE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Hollow Store starting on port {STORE_PORT}")
    print(f"Data directory: {STORE_DATA_DIR.resolve()}")
    uvicorn.run(app, host="0.0.0.0", port=STORE_PORT)
