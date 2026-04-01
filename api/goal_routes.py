"""
Goal API routes — AgentOS v3.12.1.

Exposes PersistentGoalEngine (Phase 3) as live MCP endpoints.
Agents can now set and track persistent goals through the OS.

Endpoints:
  POST   /goals/{agent_id}            Create a goal
  GET    /goals/{agent_id}            List active goals
  GET    /goals/{agent_id}/{goal_id}  Get one goal
  PATCH  /goals/{agent_id}/{goal_id}  Update progress / status
  GET    /goals/{agent_id}/next       Get the highest-priority goal to work on
"""

import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

router = APIRouter()

_goal_engine = None
_registry = None


def init(goal_engine, registry=None):
    global _goal_engine, _registry
    _goal_engine = goal_engine
    _registry = registry


def _resolve_agent(authorization: Optional[str]) -> str:
    """Return agent_id from token, or 'anonymous' if registry not available."""
    if _registry is None:
        return "anonymous"
    token = (authorization or "").removeprefix("Bearer ").strip()
    agent = _registry.authenticate(token)
    return agent.agent_id if agent else "anonymous"


def _require_engine():
    if _goal_engine is None:
        raise HTTPException(status_code=503, detail="Goal engine not initialised")


# ── Request / response models ─────────────────────────────────────────────── #

class GoalCreateRequest(BaseModel):
    objective: str
    priority: int = 5


class GoalUpdateRequest(BaseModel):
    status: Optional[str] = None          # active | paused | completed | abandoned
    metrics: Optional[dict] = None        # progress metrics to merge


# ── Endpoints ─────────────────────────────────────────────────────────────── #

@router.post("/goals/{agent_id}")
def create_goal(
    agent_id: str,
    req: GoalCreateRequest,
    authorization: Optional[str] = Header(None),
):
    """Create a new persistent goal for an agent."""
    _require_engine()
    try:
        goal_id = _goal_engine.create(
            agent_id=agent_id,
            objective=req.objective,
            priority=req.priority,
        )
        goal = _goal_engine.get(agent_id, goal_id)
        return {"ok": True, "goal_id": goal_id, "goal": asdict(goal) if goal else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/goals/{agent_id}")
def list_goals(
    agent_id: str,
    status: str = "active",
    limit: int = 50,
    authorization: Optional[str] = Header(None),
):
    """List goals for an agent."""
    _require_engine()
    try:
        if status == "active":
            goals = _goal_engine.list_active(agent_id, limit=limit)
        else:
            # fall back to active for now; future versions can filter by status
            goals = _goal_engine.list_active(agent_id, limit=limit)
        return {
            "agent_id": agent_id,
            "goals": [asdict(g) for g in goals],
            "count": len(goals),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/goals/{agent_id}/next")
def next_goal(
    agent_id: str,
    authorization: Optional[str] = Header(None),
):
    """Return the highest-priority active goal to work on next."""
    _require_engine()
    try:
        goals = _goal_engine.get_next_focus(agent_id, top_k=1)
        if not goals:
            return {"agent_id": agent_id, "goal": None}
        return {"agent_id": agent_id, "goal": asdict(goals[0])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/goals/{agent_id}/{goal_id}")
def get_goal(
    agent_id: str,
    goal_id: str,
    authorization: Optional[str] = Header(None),
):
    """Get a single goal by ID."""
    _require_engine()
    goal = _goal_engine.get(agent_id, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    return asdict(goal)


@router.patch("/goals/{agent_id}/{goal_id}")
def update_goal(
    agent_id: str,
    goal_id: str,
    req: GoalUpdateRequest,
    authorization: Optional[str] = Header(None),
):
    """Update a goal's status or progress metrics."""
    _require_engine()
    goal = _goal_engine.get(agent_id, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    try:
        if req.metrics:
            _goal_engine.update_progress(agent_id, goal_id, req.metrics)

        if req.status:
            valid = {"active", "paused", "completed", "abandoned"}
            if req.status not in valid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status '{req.status}'. Must be one of {valid}",
                )
            if req.status == "completed":
                _goal_engine.complete(agent_id, goal_id)
            elif req.status == "abandoned":
                _goal_engine.abandon(agent_id, goal_id)
            elif req.status == "paused":
                _goal_engine.pause(agent_id, goal_id)
            # "active" is the default — no-op if already active

        updated = _goal_engine.get(agent_id, goal_id)
        return {"ok": True, "goal": asdict(updated) if updated else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
