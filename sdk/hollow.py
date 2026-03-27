"""
Hollow SDK — Python client for hollow-agentOS.

Agents use this instead of raw HTTP. One import, one object, clean API.

    from hollow import Hollow

    os = Hollow("http://localhost:7777", token="your-agent-token")

    # Submit a task — scheduler picks the model
    result = os.task("Summarize the last 10 shell actions", complexity=2)

    # Semantic search
    hits = os.search("how does chunking work", top_k=3)

    # Read file + related context
    ctx = os.read_context("/agentOS/api/server.py")

    # Send message to another agent
    os.send("orchestrator-id", {"status": "done", "task": "indexing"})

    # Spawn a child agent to handle a subtask
    result = os.spawn("helper", role="coder", task="Write a test for utils.py")
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional, Any


class HollowError(Exception):
    """Raised when the Hollow API returns an error."""
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"[{status}] {detail}")


@dataclass
class TaskResult:
    task_id: str
    status: str
    model_role: str
    response: str
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    ms: Optional[int]
    error: Optional[str]

    @property
    def ok(self) -> bool:
        return self.status == "done"


@dataclass
class SearchHit:
    file: str
    score: float
    preview: str
    chunk_idx: int


@dataclass
class Message:
    msg_id: str
    from_id: str
    msg_type: str
    content: dict
    timestamp: float
    reply_to: Optional[str]


class Hollow:
    """
    Client for a running hollow-agentOS instance.

    Parameters
    ----------
    base_url : str
        Base URL of the API, e.g. "http://localhost:7777"
    token : str
        Agent token (from /agents/register) or master token.
    timeout : int
        Default request timeout in seconds.
    """

    def __init__(self, base_url: str, token: str, timeout: int = 60):
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._agent_id: Optional[str] = None

    # ── Internal ─────────────────────────────────────────────────────────────

    def _req(self, method: str, path: str, body: Optional[dict] = None,
             params: Optional[dict] = None, timeout: Optional[int] = None) -> dict:
        url = self._base + path
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            url = f"{url}?{qs}"

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self._timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                detail = json.loads(body).get("detail", body.decode())
            except Exception:
                detail = body.decode()
            raise HollowError(e.code, detail) from None

    # ── Identity ──────────────────────────────────────────────────────────────

    def whoami(self) -> dict:
        """Return current agent's record."""
        # Derive agent_id from token by calling /agents and matching token hash
        # Simpler: just return /health + stored agent_id if known
        return {"token_prefix": self._token[:8] + "...", "base": self._base}

    # ── State ─────────────────────────────────────────────────────────────────

    def state(self) -> dict:
        """Full system snapshot."""
        return self._req("GET", "/state")

    def diff(self, since: str) -> dict:
        """Changed fields only since an ISO timestamp."""
        return self._req("GET", "/state/diff", params={"since": since})

    def health(self) -> bool:
        """True if the API is up."""
        try:
            self._req("GET", "/health")
            return True
        except Exception:
            return False

    # ── Task scheduler ────────────────────────────────────────────────────────

    def task(
        self,
        description: str,
        complexity: int = 2,
        context: Optional[dict] = None,
        system_prompt: Optional[str] = None,
        timeout: int = 120,
    ) -> TaskResult:
        """
        Submit a task. The scheduler routes it to the right model.

        complexity 1-5:
            1-2 → general (mistral-nemo:12b)
            3-4 → code    (qwen2.5:14b)
            5   → reasoning (qwen3.5-35b-moe)
        """
        resp = self._req("POST", "/tasks/submit", body={
            "description": description,
            "complexity": complexity,
            "context": context,
            "system_prompt": system_prompt,
        }, timeout=timeout)

        result = resp.get("result") or {}
        return TaskResult(
            task_id=resp.get("task_id", ""),
            status=resp.get("status", "unknown"),
            model_role=resp.get("assigned_to", ""),
            response=result.get("response", ""),
            tokens_in=result.get("tokens_in"),
            tokens_out=result.get("tokens_out"),
            ms=resp.get("ms"),
            error=resp.get("error"),
        )

    def spawn(
        self,
        name: str,
        task: str,
        role: str = "worker",
        complexity: int = 2,
        capabilities: Optional[list[str]] = None,
    ) -> dict:
        """
        Spawn a child agent, run a task with it, return the result.
        The child is automatically terminated after the task completes.
        Requires: spawn capability.
        """
        return self._req("POST", "/agents/spawn", body={
            "name": name,
            "role": role,
            "task": task,
            "complexity": complexity,
            "capabilities": capabilities,
        }, timeout=120)

    # ── Messaging ─────────────────────────────────────────────────────────────

    def send(
        self,
        to_id: str,
        content: dict,
        msg_type: str = "data",
        reply_to: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> str:
        """Send a message. Returns msg_id."""
        resp = self._req("POST", "/messages", body={
            "to_id": to_id,
            "content": content,
            "msg_type": msg_type,
            "reply_to": reply_to,
            "ttl_seconds": ttl_seconds,
        })
        return resp["msg_id"]

    def broadcast(self, content: dict, msg_type: str = "log") -> str:
        """Broadcast to all agents."""
        return self.send("broadcast", content, msg_type)

    def inbox(self, unread_only: bool = True, limit: int = 20) -> list[Message]:
        """Receive messages from inbox."""
        resp = self._req("GET", "/messages", params={
            "unread_only": str(unread_only).lower(),
            "limit": limit,
        })
        return [
            Message(
                msg_id=m["msg_id"],
                from_id=m["from_id"],
                msg_type=m["msg_type"],
                content=m["content"],
                timestamp=m["timestamp"],
                reply_to=m.get("reply_to"),
            )
            for m in resp.get("messages", [])
        ]

    def thread(self, msg_id: str) -> list[dict]:
        """Get a message and all its replies."""
        return self._req("GET", f"/messages/thread/{msg_id}").get("thread", [])

    # ── Filesystem ────────────────────────────────────────────────────────────

    def read(self, path: str) -> str:
        """Read a file. Returns content string."""
        return self._req("GET", "/fs/read", params={"path": path}).get("content", "")

    def write(self, path: str, content: str) -> bool:
        """Write a file. Returns True on success."""
        resp = self._req("POST", "/fs/write", body={"path": path, "content": content})
        return resp.get("success", False)

    def ls(self, path: str = "/agentOS/workspace") -> list[dict]:
        """List directory entries."""
        return self._req("GET", "/fs/list", params={"path": path}).get("entries", [])

    def read_context(self, path: str, top_k: int = 5) -> dict:
        """Read a file plus semantically related neighbor chunks."""
        return self._req("POST", "/fs/read_context", body={"path": path, "top_k": top_k})

    def batch_read(self, paths: list[str]) -> dict:
        """Read multiple files in one call."""
        return self._req("POST", "/fs/batch-read", body={"paths": paths})

    # ── Semantic search ───────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 10) -> list[SearchHit]:
        """Natural language search over indexed workspace."""
        resp = self._req("POST", "/semantic/search", body={"query": query, "top_k": top_k})
        return [
            SearchHit(
                file=r["file"],
                score=r["score"],
                preview=r["preview"],
                chunk_idx=r.get("chunk_idx", 0),
            )
            for r in resp.get("results", [])
        ]

    # ── Shell ─────────────────────────────────────────────────────────────────

    def shell(self, command: str, cwd: Optional[str] = None, timeout: int = 30) -> dict:
        """
        Run a shell command. Scoped to agent workspace if non-root.
        Returns: { stdout, stderr, exit_code, success, elapsed_seconds }
        """
        return self._req("POST", "/shell", body={
            "command": command,
            "cwd": cwd,
            "timeout": timeout,
        }, timeout=timeout + 5)

    # ── Session handoff ───────────────────────────────────────────────────────

    def handoff(
        self,
        summary: str,
        in_progress: Optional[list[str]] = None,
        next_steps: Optional[list[str]] = None,
        relevant_files: Optional[list[str]] = None,
        decisions_made: Optional[list[str]] = None,
        agent_id: str = "agent",
    ) -> dict:
        """Write a structured handoff for the next agent session."""
        return self._req("POST", "/agent/handoff", body={
            "agent_id": agent_id,
            "summary": summary,
            "in_progress": in_progress or [],
            "next_steps": next_steps or [],
            "relevant_files": relevant_files or [],
            "decisions_made": decisions_made or [],
        })

    def pickup(self) -> dict:
        """Get the last handoff + all changes since it was written."""
        return self._req("GET", "/agent/pickup")

    # ── Agents ────────────────────────────────────────────────────────────────

    def agents(self) -> list[dict]:
        """List all registered agents (requires admin capability)."""
        try:
            return self._req("GET", "/agents").get("agents", [])
        except HollowError as e:
            if e.status == 403:
                return []
            raise

    def agent(self, agent_id: str) -> dict:
        """Get a specific agent's record."""
        return self._req("GET", f"/agents/{agent_id}")

    def terminate(self, agent_id: str) -> bool:
        """Terminate an agent."""
        return self._req("DELETE", f"/agents/{agent_id}").get("ok", False)

    # ── Ollama direct ─────────────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        role: str = "general",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        timeout: int = 120,
    ) -> str:
        """Chat with a local model via role routing. Returns response text."""
        resp = self._req("POST", "/ollama/chat", body={
            "messages": messages,
            "role": role,
            "model": model,
            "temperature": temperature,
        }, timeout=timeout)
        return resp.get("response", "")

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


# ── Convenience: register + return scoped client ─────────────────────────────

def register(
    base_url: str,
    master_token: str,
    name: str,
    role: str = "worker",
    capabilities: Optional[list[str]] = None,
) -> tuple["Hollow", str]:
    """
    Register a new agent and return a scoped Hollow client for it.

    Returns (client, agent_id).

    Example:
        os, agent_id = hollow.register("http://localhost:7777", MASTER_TOKEN, "my-agent")
        result = os.task("Summarize the workspace", complexity=2)
        os.terminate(agent_id)
    """
    tmp = Hollow(base_url, master_token)
    resp = tmp._req("POST", "/agents/register", body={
        "name": name,
        "role": role,
        "capabilities": capabilities,
    })
    agent_token = resp["token"]
    agent_id = resp["agent_id"]
    client = Hollow(base_url, agent_token)
    client._agent_id = agent_id
    return client, agent_id
