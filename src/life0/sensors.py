"""LIFE-0 sensors: bounded, deterministic, read-only snapshots of the three
permitted surfaces. No LLM, no spend, no writes to the surfaces themselves.

Each sensor returns a plain-JSON snapshot dict. Failures are recorded as
{"sensor_error": ...} inside the snapshot — a failed sensor never crashes
the pulse and never fabricates a delta.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

# The Hatch credential helper is machine-local. The GitHub sensor degrades
# gracefully without it (sensor_error in the snapshot), per this module's
# contract: "a failed sensor never crashes the pulse and never fabricates
# a delta."
try:
    sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
    from dynamic_credentials import add_surrogate_to_request, read_json_response
    _HAVE_CREDENTIAL_HELPER = True
except Exception:
    _HAVE_CREDENTIAL_HELPER = False

CRED = "custom.github"
ALLOWED = ("api.github.com",)
USER_AGENT = "namariel-life0/1.0"


def github_http_get(path: str, timeout_s: int = 15) -> tuple[int, Any]:
    """Real read-only GET against api.github.com. GET only, always."""
    if not _HAVE_CREDENTIAL_HELPER:
        return -1, {"transport_error": "credential helper unavailable: GitHub sensor degraded"}
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    add_surrogate_to_request(req, CRED, allowed_hosts=ALLOWED)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, read_json_response(resp)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = {}
        return e.code, body
    except Exception as e:  # timeout, DNS, TLS: sensor-level, not pulse-fatal
        return -1, {"transport_error": f"{type(e).__name__}: {e}"}


def sense_github(
    repo: str | None,
    fallback_name: str = "unified_machine",
    http_get: Callable[[str], tuple[int, Any]] = github_http_get,
) -> dict[str, Any]:
    """Snapshot the um-lineage mirror: default-branch HEAD, open issues,
    latest CI workflow runs. Read-only. Never touches the local checkout."""
    snap: dict[str, Any] = {"surface": "github"}
    try:
        if repo is None:
            status, me = http_get("/user")
            if status != 200:
                return {**snap, "sensor_error": f"GET /user -> HTTP {status}"}
            repo = f"{me['login']}/{fallback_name}"
        snap["repo"] = repo

        status, repo_info = http_get(f"/repos/{repo}")
        if status != 200:
            return {**snap, "sensor_error": f"GET /repos/{repo} -> HTTP {status}"}
        default_branch = repo_info.get("default_branch", "main")
        snap["default_branch"] = default_branch

        status, branch = http_get(f"/repos/{repo}/branches/{default_branch}")
        snap["head_sha"] = branch.get("commit", {}).get("sha") if status == 200 else None
        if status != 200:
            snap["head_error"] = f"HTTP {status}"

        status, issues = http_get(f"/repos/{repo}/issues?state=open&per_page=30")
        if status == 200:
            snap["open_issues"] = [
                {
                    "number": i.get("number"),
                    "title": i.get("title"),
                    "updated_at": i.get("updated_at"),
                    "is_pull": "pull_request" in i,
                }
                for i in issues
            ]
        else:
            snap["open_issues"] = []
            snap["issues_error"] = f"HTTP {status}"

        status, runs = http_get(f"/repos/{repo}/actions/runs?per_page=10")
        if status == 200:
            snap["ci"] = {
                "available": True,
                "runs": [
                    {
                        "run_id": r.get("id"),
                        "head_branch": r.get("head_branch"),
                        "conclusion": r.get("conclusion"),
                        "status": r.get("status"),
                        "created_at": r.get("created_at"),
                        "html_url": r.get("html_url"),
                    }
                    for r in runs.get("workflow_runs", [])
                ],
            }
        elif status == 404:
            # No Actions on this repo: surface unavailable, never an error.
            snap["ci"] = {"available": False, "reason": "no actions runs endpoint"}
        else:
            snap["ci"] = {"available": False, "reason": f"HTTP {status}"}
        return snap
    except Exception as e:
        return {**snap, "sensor_error": f"{type(e).__name__}: {e}"}


def sense_fact0(fact0_db: str | Path) -> dict[str, Any]:
    """Snapshot admitted evidence currency/status via the fact0 query
    interface (read-only). The pulse's own queries are logged by fact0's
    passive use-provenance with caller='life0-pulse' — observability working
    as designed, not a behavior change."""
    snap: dict[str, Any] = {"surface": "fact0"}
    try:
        # FACT-0 was built against the frozen v0.13 auth module
        # (AuthorityBoundaryError, RuntimeAuthority). Pin the frozen tree's
        # src ahead of any venv-installed namariel_live so the import below
        # resolves read-only against v0.13. The frozen tree is never written.
        sys.path.insert(0, str(Path.home() / "workspace/namariel-live0-v0.13/src"))
        sys.path.insert(0, str(Path.home() / "workspace/namariel-live0/fact-0/src"))
        from fact0 import get_envelope, list_current, open_store

        store = open_store(str(fact0_db))
        evidence: dict[str, Any] = {}
        for env in list_current(store, caller="life0-pulse"):
            eid = env["evidence_id"]
            evidence[eid] = {
                "currency": env["currency"]["state"],
                "status": env["status"],
                "trust_tier": env["trust"]["tier"],
            }
        # Superseded records are not in list_current; check history is out of
        # scope for v0 currency tracking — status transitions on current
        # evidence are what we diff. (Supersession appears as new ids.)
        _ = get_envelope  # referenced for interface completeness
        snap["evidence"] = evidence
        snap["count"] = len(evidence)
        return snap
    except Exception as e:
        return {**snap, "sensor_error": f"{type(e).__name__}: {e}"}


def sense_inbox(inbox_dir: str | Path) -> dict[str, Any]:
    """Snapshot the operator inbox. A new file (unseen name, or changed hash)
    is a delta. Files are placed here ONLY by the operator routing genuine
    work — never by the pulse, never manufactured."""
    snap: dict[str, Any] = {"surface": "inbox"}
    try:
        inbox = Path(inbox_dir)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "done").mkdir(exist_ok=True)
        files = []
        for p in sorted(inbox.iterdir()):
            if p.name == "done" or not p.is_file():
                continue
            if p.name.startswith(".") or p.name == "README.md":
                continue
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            files.append({"name": p.name, "sha256": digest, "bytes": p.stat().st_size})
        snap["files"] = files
        return snap
    except Exception as e:
        return {**snap, "sensor_error": f"{type(e).__name__}: {e}"}
