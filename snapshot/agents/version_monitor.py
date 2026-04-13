"""
Version Monitor — AgentOS Phase 4.

Auto-versioning pipeline: polls GitHub for new commits on installed wrappers
and the community store, fetches diffs, and calls Claude to regenerate the
wrapper when changes are detected.

No human required at any step. AI owns the full update lifecycle.

Architecture:
  1. Scan /agentOS/workspace/wrappers/ for installed wrappers
     AND /agentOS/store/data/ for all store wrappers
  2. For each wrapper: hit GitHub API to check latest commit
  3. If commit changed: fetch diff, call Claude to update wrapper
  4. New wrapper version uploaded to store automatically

Rate limiting:
  - Unauthenticated: 60 req/hr GitHub API. We batch checks and sleep between
    groups so a full 108-wrapper scan stays under the limit.
  - Set GITHUB_TOKEN env var to bump to 5000 req/hr (no throttling needed).

Cost model:
  - Polling: zero Claude calls, just HTTP to GitHub API
  - Update: one Claude Sonnet call per changed repo (only fires on real change)
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("agentos.version_monitor")

WRAPPERS_DIR = Path(os.getenv("HOLLOW_WRAPPERS_DIR", "/agentOS/workspace/wrappers"))
HOLLOW_STORE_URL = os.getenv("HOLLOW_STORE_URL", "http://host.docker.internal:7779")
_STORE_DATA_ENV = os.getenv("HOLLOW_STORE_DATA")
STORE_DATA_DIR = Path(_STORE_DATA_ENV) if _STORE_DATA_ENV else Path("/agentOS/store/data")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # Optional: bumps rate limit from 60→5000/hr

# How often to check for updates (seconds). Default 4 hours.
CHECK_INTERVAL = int(os.getenv("HOLLOW_VERSION_CHECK_INTERVAL", str(4 * 3600)))

# Max wrappers to check per run when unauthenticated (to stay under 60/hr rate limit).
# Each check is 1 GitHub API call; diffs add 1 more. Conservative: 25 per run.
# With GITHUB_TOKEN set, this limit is lifted (5000/hr).
_UNAUTH_BATCH_LIMIT = 25
# Seconds to sleep between batches of 10 when unauthenticated (spread load)
_BATCH_SLEEP = 2


# --------------------------------------------------------------------------- #
#  GitHub API helpers                                                          #
# --------------------------------------------------------------------------- #

def _github_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def _parse_repo_path(repo_url: str) -> Optional[str]:
    """Extract 'owner/repo' from a GitHub URL."""
    url = repo_url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    url = url.rstrip("/")
    if "github.com" not in url:
        return None
    parts = url.split("github.com/", 1)
    if len(parts) < 2:
        return None
    path = parts[1].strip("/")
    segments = path.split("/")
    if len(segments) >= 2:
        return f"{segments[0]}/{segments[1]}"
    return None


def get_latest_commit(repo_url: str) -> Optional[str]:
    """
    Query GitHub API for the latest commit SHA on the default branch.
    Returns 12-char short SHA or None on error.
    """
    repo_path = _parse_repo_path(repo_url)
    if not repo_path:
        return None
    try:
        import httpx
        r = httpx.get(
            f"https://api.github.com/repos/{repo_path}/commits",
            params={"per_page": 1},
            headers=_github_headers(),
            timeout=10,
            follow_redirects=True,
        )
        if r.status_code == 200:
            commits = r.json()
            if commits and isinstance(commits, list):
                return commits[0]["sha"][:12]
        elif r.status_code == 403:
            log.warning("GitHub rate limit hit for %s — retry later", repo_path)
        else:
            log.debug("GitHub API returned %d for %s", r.status_code, repo_path)
    except Exception as e:
        log.debug("get_latest_commit error for %s: %s", repo_url, e)
    return None


def get_commit_diff(repo_url: str, base_sha: str, head_sha: str) -> str:
    """
    Fetch the diff between two commits via GitHub compare API.
    Returns a summary string (file names + stats + first 3000 chars of patch).
    """
    repo_path = _parse_repo_path(repo_url)
    if not repo_path:
        return ""
    try:
        import httpx
        r = httpx.get(
            f"https://api.github.com/repos/{repo_path}/compare/{base_sha}...{head_sha}",
            headers=_github_headers(),
            timeout=15,
            follow_redirects=True,
        )
        if r.status_code != 200:
            return ""
        data = r.json()
        files = data.get("files", [])
        lines = [
            f"Commits ahead: {data.get('ahead_by', '?')}",
            f"Changed files: {len(files)}",
        ]
        patch_chars = 0
        for f in files[:15]:
            stat = f"  {f['filename']} (+{f.get('additions',0)} -{f.get('deletions',0)})"
            lines.append(stat)
            patch = f.get("patch", "")
            if patch and patch_chars < 3000:
                take = patch[:3000 - patch_chars]
                lines.append(f"  diff:\n{take}")
                patch_chars += len(take)
        return "\n".join(lines)
    except Exception as e:
        log.debug("get_commit_diff error: %s", e)
        return ""


# --------------------------------------------------------------------------- #
#  Wrapper update via Claude                                                   #
# --------------------------------------------------------------------------- #

def update_wrapper_from_diff(
    wrapper: dict,
    diff_summary: str,
    new_commit: str,
) -> Optional[dict]:
    """
    Given an existing wrapper and a diff summary, ask Claude to produce an
    updated wrapper JSON. Returns the new wrapper dict or None on failure.
    """
    existing = json.dumps({
        "capability_map": wrapper.get("capability_map", {}),
        "interface_spec": wrapper.get("interface_spec", {}),
    }, indent=2)

    prompt = (
        f"You are updating a Hollow app wrapper after a new commit was pushed to the repo.\n\n"
        f"Repository: {wrapper.get('repo_url', 'unknown')}\n"
        f"Previous commit: {wrapper.get('source_commit', '?')} → New commit: {new_commit}\n\n"
        f"What changed:\n{diff_summary}\n\n"
        f"Current wrapper:\n{existing}\n\n"
        f"Update the wrapper to reflect any meaningful changes:\n"
        f"- New capabilities added to the tool → add them\n"
        f"- Removed features → remove or update them\n"
        f"- Changed flags/args → update shell_templates and params\n"
        f"- Minor internal changes with no CLI impact → keep wrapper unchanged\n\n"
        f"Return ONLY the updated JSON with capability_map and interface_spec. "
        f"No explanation, no markdown fencing. If no meaningful CLI changes, "
        f"return the current wrapper unchanged."
    )

    raw_json = ""

    try:
        from agents.reasoning_layer import _get_claude_client, CLAUDE_SMART_MODEL, _strip_code_fences
        client = _get_claude_client()
        if client:
            msg = client.messages.create(
                model=CLAUDE_SMART_MODEL,
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_json = _strip_code_fences(msg.content[0].text.strip())
    except Exception as e:
        log.debug("Claude update_wrapper error: %s", e)

    # Ollama fallback
    if not raw_json:
        try:
            import httpx
            r = httpx.post(
                "http://localhost:11434/api/generate",
                json={"model": "qwen2.5:7b", "prompt": prompt, "stream": False},
                timeout=90,
            )
            if r.status_code == 200:
                raw_json = r.json().get("response", "")
                # Strip fences from Ollama output too
                raw_json = raw_json.strip()
                if raw_json.startswith("```"):
                    lines = raw_json.splitlines()
                    inner = []
                    for line in lines[1:]:
                        if line.strip() == "```":
                            break
                        inner.append(line)
                    raw_json = "\n".join(inner).strip()
        except Exception as e:
            log.debug("Ollama fallback error: %s", e)

    if not raw_json:
        return None

    try:
        updated = json.loads(raw_json)
        if "capability_map" in updated and "interface_spec" in updated:
            return updated
        log.debug("update_wrapper: response missing capability_map/interface_spec")
    except Exception as e:
        log.debug("update_wrapper JSON parse error: %s | raw: %s", e, raw_json[:200])

    return None


# --------------------------------------------------------------------------- #
#  Shared: check one wrapper file and optionally update it                    #
# --------------------------------------------------------------------------- #

def _check_one_wrapper(
    wrapper_file: Path,
    repo_name: str,
    results: dict,
    update: bool = True,
) -> bool:
    """
    Check a single wrapper.json for staleness and optionally update it.
    Returns True if a GitHub API call was made (for rate-limit accounting).
    """
    try:
        wrapper = json.loads(wrapper_file.read_text())
    except Exception:
        results["errors"] += 1
        return False

    repo_url = wrapper.get("repo_url", "")
    stored_commit = wrapper.get("source_commit", "")

    if not repo_url or not stored_commit or "github.com" not in repo_url:
        return False

    results["checked"] += 1

    latest_commit = get_latest_commit(repo_url)
    if not latest_commit:
        results["errors"] += 1
        return True  # API call was attempted

    if latest_commit.startswith(stored_commit) or stored_commit.startswith(latest_commit):
        log.debug("[version] %s is current (commit=%s)", repo_name, stored_commit)
        results["repos"].append({"name": repo_name, "status": "current", "commit": stored_commit})
        return True

    # Commit differs
    if not update:
        results["repos"].append({
            "name": repo_name,
            "status": "stale",
            "stored_commit": stored_commit,
            "latest_commit": latest_commit,
        })
        return True

    log.info("[version] %s is STALE: stored=%s latest=%s — updating",
             repo_name, stored_commit, latest_commit)

    diff_summary = get_commit_diff(repo_url, stored_commit, latest_commit)
    if not diff_summary:
        diff_summary = f"New commits since {stored_commit}"

    updated_data = update_wrapper_from_diff(wrapper, diff_summary, latest_commit)
    if not updated_data:
        log.warning("[version] %s: could not generate updated wrapper", repo_name)
        results["errors"] += 1
        results["repos"].append({"name": repo_name, "status": "update_failed"})
        return True

    new_wrapper = {
        **wrapper,
        "source_commit": latest_commit,
        "wrapped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "capability_map": updated_data["capability_map"],
        "interface_spec": updated_data.get("interface_spec", wrapper.get("interface_spec", {})),
    }
    tmp = wrapper_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(new_wrapper, indent=2))
    tmp.replace(wrapper_file)

    # Re-upload to store
    try:
        import httpx
        payload = {
            "repo_url": repo_url,
            "source_commit": latest_commit,
            "capability_map": updated_data["capability_map"],
            "interface_spec": updated_data.get("interface_spec", {}),
        }
        r = httpx.post(f"{HOLLOW_STORE_URL}/wrappers", json=payload, timeout=15)
        uploaded = r.status_code in (200, 201)
    except Exception:
        uploaded = False

    log.info("[version] %s updated: %s → %s (store_uploaded=%s)",
             repo_name, stored_commit, latest_commit, uploaded)
    results["updated"] += 1
    results["repos"].append({
        "name": repo_name,
        "status": "updated",
        "from": stored_commit,
        "to": latest_commit,
        "store_uploaded": uploaded,
    })
    return True


def _api_calls_remaining() -> int:
    """Estimate remaining GitHub API quota for this run."""
    if GITHUB_TOKEN:
        return 4990  # effectively unlimited with a token
    return _UNAUTH_BATCH_LIMIT


# --------------------------------------------------------------------------- #
#  Core check: scan installed wrappers and update if stale                    #
# --------------------------------------------------------------------------- #

def check_and_update_wrappers() -> dict:
    """
    Scan all installed wrappers, check GitHub for new commits,
    update any that are stale. Returns a summary dict.
    Respects GitHub API rate limits when unauthenticated.
    """
    results = {"checked": 0, "updated": 0, "errors": 0, "repos": [], "source": "installed"}

    if not WRAPPERS_DIR.exists():
        return results

    quota = _api_calls_remaining()
    api_calls = 0

    for i, wrapper_dir in enumerate(sorted(WRAPPERS_DIR.iterdir())):
        wrapper_file = wrapper_dir / "wrapper.json"
        if not wrapper_file.exists():
            continue

        if api_calls >= quota:
            log.warning("[version] GitHub rate limit reached — stopping early (%d/%d checked)",
                        results["checked"], results["checked"] + quota)
            break

        called = _check_one_wrapper(wrapper_file, wrapper_dir.name, results, update=True)
        if called:
            api_calls += 1
            # Small sleep every 10 calls when unauthenticated to spread load
            if not GITHUB_TOKEN and api_calls % 10 == 0:
                time.sleep(_BATCH_SLEEP)

    log.info("[version] installed check complete: checked=%d updated=%d errors=%d",
             results["checked"], results["updated"], results["errors"])
    return results


def check_and_update_store_wrappers(max_check: Optional[int] = None) -> dict:
    """
    Scan all store wrappers in STORE_DATA_DIR, check GitHub for new commits,
    update any that are stale. Returns a summary dict.

    max_check: cap on how many wrappers to process in one run (default: rate limit).
    """
    results = {"checked": 0, "updated": 0, "errors": 0, "repos": [], "source": "store"}

    if not STORE_DATA_DIR.exists():
        log.debug("[version] store data dir not found: %s", STORE_DATA_DIR)
        return results

    quota = min(max_check, _api_calls_remaining()) if max_check else _api_calls_remaining()
    api_calls = 0

    # Shuffle iteration order so a partial run covers different wrappers each time
    import random
    dirs = list(STORE_DATA_DIR.iterdir())
    random.shuffle(dirs)

    for wrapper_dir in dirs:
        wrapper_file = wrapper_dir / "wrapper.json"
        if not wrapper_file.exists():
            continue

        if api_calls >= quota:
            log.info("[version] store: quota reached (%d processed, %d remaining)",
                     results["checked"], len(dirs) - api_calls)
            break

        repo_name = wrapper_dir.name
        called = _check_one_wrapper(wrapper_file, repo_name, results, update=True)
        if called:
            api_calls += 1
            if not GITHUB_TOKEN and api_calls % 10 == 0:
                time.sleep(_BATCH_SLEEP)

    log.info("[version] store check complete: checked=%d updated=%d errors=%d",
             results["checked"], results["updated"], results["errors"])
    return results


def get_version_status(include_store: bool = True) -> dict:
    """
    Non-destructive scan: report which wrappers are stale/current without updating.
    Useful for dashboard status display. Returns:
      {stale: [...], current: [...], errors: [...], checked: int}
    Rate limited to _UNAUTH_BATCH_LIMIT checks when no GITHUB_TOKEN.
    """
    results: dict = {"checked": 0, "updated": 0, "errors": 0, "repos": []}
    quota = _api_calls_remaining()
    api_calls = 0

    # Check installed wrappers first
    if WRAPPERS_DIR.exists():
        for wrapper_dir in sorted(WRAPPERS_DIR.iterdir()):
            wrapper_file = wrapper_dir / "wrapper.json"
            if wrapper_file.exists() and api_calls < quota:
                called = _check_one_wrapper(wrapper_file, wrapper_dir.name, results, update=False)
                if called:
                    api_calls += 1

    # Check a sample of store wrappers
    if include_store and STORE_DATA_DIR.exists() and api_calls < quota:
        import random
        dirs = list(STORE_DATA_DIR.iterdir())
        random.shuffle(dirs)
        for wrapper_dir in dirs:
            if api_calls >= quota:
                break
            wrapper_file = wrapper_dir / "wrapper.json"
            if wrapper_file.exists():
                called = _check_one_wrapper(wrapper_file, wrapper_dir.name, results, update=False)
                if called:
                    api_calls += 1

    stale = [r for r in results["repos"] if r.get("status") == "stale"]
    current = [r for r in results["repos"] if r.get("status") == "current"]
    errors = results["errors"]

    return {
        "checked": results["checked"],
        "stale_count": len(stale),
        "current_count": len(current),
        "error_count": errors,
        "stale": stale,
        "github_token": bool(GITHUB_TOKEN),
    }


# --------------------------------------------------------------------------- #
#  Standalone run (for testing / cron)                                        #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [version_monitor] %(message)s")
    results = check_and_update_wrappers()
    print(json.dumps(results, indent=2))
