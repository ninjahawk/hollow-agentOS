"""
Hollow Store — wrapper data models.

A wrapper is the artifact Claude produces when it analyzes a GitHub repo.
It has two layers:
  capability_map  — what the tool CAN do (Claude-generated, semantic)
  interface_spec  — how to RENDER it in the web UI (Claude-generated, declarative)

The wrapper is pure JSON — no code runs at install time.
"""

from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Capability layer ──────────────────────────────────────────────────────────

class CapabilityParam(BaseModel):
    name: str
    type: str = "string"          # string | int | bool | file | dir
    required: bool = True
    description: str = ""
    default: Optional[Any] = None


class Capability(BaseModel):
    id: str
    description: str
    params: list[CapabilityParam] = Field(default_factory=list)
    shell_template: str           # e.g. "rg {pattern} {path}"
    example: str = ""             # one-line usage example for the UI


class CapabilityMap(BaseModel):
    name: str                     # human name, e.g. "ripgrep"
    description: str
    invoke: str                   # base command, e.g. "rg"
    install_hint: str = ""        # how to install if not present
    capabilities: list[Capability] = Field(default_factory=list)


# ── Interface layer ───────────────────────────────────────────────────────────

class UIField(BaseModel):
    id: str                       # matches CapabilityParam.name
    label: str
    type: str = "text"            # text | number | checkbox | file | dir | select
    placeholder: str = ""
    options: list[str] = Field(default_factory=list)   # for select type
    required: bool = True


class InterfaceSpec(BaseModel):
    type: str = "form"            # form | terminal | split
    title: str
    description: str = ""
    fields: list[UIField] = Field(default_factory=list)
    output: str = "terminal"      # terminal | markdown | table


# ── Wrapper (the full artifact) ───────────────────────────────────────────────

class Wrapper(BaseModel):
    schema_version: int = 1
    repo_url: str
    source_commit: str            # git SHA at time of wrapping
    wrapped_at: str               # ISO 8601
    install_count: int = 0
    capability_map: CapabilityMap
    interface_spec: InterfaceSpec


class WrapperUpload(BaseModel):
    """Payload for POST /wrappers — same as Wrapper minus install_count."""
    repo_url: str
    source_commit: str
    capability_map: CapabilityMap
    interface_spec: InterfaceSpec


class VersionCheckResponse(BaseModel):
    repo_url: str
    stored_commit: str
    current_commit: str           # caller passes their installed commit
    update_available: bool
