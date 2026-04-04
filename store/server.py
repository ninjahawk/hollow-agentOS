"""
Hollow Store Server — v1.0.0

Central store for Hollow app wrappers. One instance shared by all Hollow installs.
First person to wrap a repo uploads it here. Everyone else downloads it free.

Endpoints:
  GET  /health                         liveness
  POST /wrappers                       upload a wrapper
  GET  /wrappers                       list all wrappers (?limit=20&offset=0)
  GET  /wrappers/{repo_id}             download a wrapper
  POST /wrappers/{repo_id}/install     increment install count
  GET  /wrappers/{repo_id}/version     check if an update is available

Storage: plain JSON files at STORE_DATA_DIR/{repo_id}/wrapper.json
No database. No auth for v1. Atomic writes (temp→rename).
"""

import hashlib
import json
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
    return {"repo_id": rid, "status": "uploaded", "install_count": install_count}


@app.get("/wrappers")
def list_wrappers(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    """List all wrappers sorted by install count descending."""
    STORE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    wrappers = []
    try:
        for p in STORE_DATA_DIR.iterdir():
            wf = p / "wrapper.json"
            if wf.exists():
                try:
                    w = json.loads(wf.read_text())
                    wrappers.append({
                        "repo_id": p.name,
                        "repo_url": w.get("repo_url"),
                        "name": w.get("capability_map", {}).get("name"),
                        "description": w.get("capability_map", {}).get("description"),
                        "install_count": w.get("install_count", 0),
                        "source_commit": w.get("source_commit"),
                        "wrapped_at": w.get("wrapped_at"),
                    })
                except Exception:
                    pass
    except Exception:
        pass

    wrappers.sort(key=lambda w: w["install_count"], reverse=True)
    return {
        "total": len(wrappers),
        "limit": limit,
        "offset": offset,
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
