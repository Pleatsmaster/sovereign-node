"""LIFE-0 delta detector: D_t = E_t - E_{t-1}, computed mechanically.

Pure function of (previous snapshot, current snapshot). No LLM, no network,
no side effects. Every delta carries a substrate-minted deterministic id:
the same delta always yields the same id, which is what makes need dedup
across pulses exact. Wall-clock time is recorded, never hashed into the id.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _delta_id(source: str, kind: str, key: str, current_state: Any) -> str:
    core = {"source": source, "kind": kind, "key": key,
            "current": current_state}
    return "delta_" + hashlib.sha256(canonical(core).encode()).hexdigest()[:32]


def _mk(source: str, kind: str, key: str,
        previous_state: Any, current_state: Any) -> dict[str, Any]:
    return {
        "delta_id": _delta_id(source, kind, key, current_state),
        "source": source,
        "kind": kind,
        "key": key,
        "previous_state": previous_state,
        "current_state": current_state,
        "observed_at": utcnow_iso(),
    }


def detect_deltas(prev: dict[str, Any], curr: dict[str, Any]) -> list[dict[str, Any]]:
    """Diff two pulse snapshots. Surfaces with sensor_error on either side
    produce no deltas (a blind sensor is not evidence of change)."""
    deltas: list[dict[str, Any]] = []
    deltas.extend(_diff_github(prev.get("github", {}), curr.get("github", {})))
    deltas.extend(_diff_fact0(prev.get("fact0", {}), curr.get("fact0", {})))
    deltas.extend(_diff_inbox(prev.get("inbox", {}), curr.get("inbox", {})))
    return deltas


def _diff_github(prev: dict, curr: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if prev.get("sensor_error") or curr.get("sensor_error"):
        return out
    if not prev:  # first pulse: establish baseline, no deltas
        return out
    ph, ch = prev.get("head_sha"), curr.get("head_sha")
    if ph and ch and ph != ch:
        out.append(_mk("github", "head_changed", curr.get("repo", "?"),
                       {"head_sha": ph}, {"head_sha": ch}))
    prev_issues = {i["number"]: i for i in prev.get("open_issues", [])}
    for i in curr.get("open_issues", []):
        if i["number"] not in prev_issues and not i.get("is_pull"):
            out.append(_mk("github", "new_issue", f"issue-{i['number']}",
                           None, {"number": i["number"], "title": i["title"]}))
    for num, i in prev_issues.items():
        if num not in {x["number"] for x in curr.get("open_issues", [])}:
            out.append(_mk("github", "issue_closed", f"issue-{num}",
                           {"number": num, "title": i["title"]}, None))
    out.extend(_diff_ci(prev.get("ci", {}), curr.get("ci", {})))
    out.extend(_diff_ci_first_observation(prev, curr))
    return out


def _diff_ci_first_observation(prev: dict, curr: dict) -> list[dict[str, Any]]:
    """CI newly observable (was unavailable, now available): a currently
    failing default-branch run is a delta on first observation. Silence about
    a red branch is the worst failure mode of this sensor; a surface that was
    already available is baselined normally by _diff_ci."""
    out: list[dict[str, Any]] = []
    pc, cc = prev.get("ci", {}), curr.get("ci", {})
    if pc.get("available") or not cc.get("available"):
        return out
    branch = curr.get("default_branch", "main")
    latest = _latest_run_by_branch(cc).get(branch)
    if latest and latest.get("conclusion") == "failure":
        out.append(_mk("github", "ci_new_failure",
                       f"ci-{branch}-{latest.get('run_id')}",
                       {"available": False},
                       {"conclusion": "failure", "run_id": latest.get("run_id"),
                        "head_branch": branch, "html_url": latest.get("html_url")}))
    return out


def _latest_run_by_branch(ci: dict) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for r in ci.get("runs", []):
        b = r.get("head_branch") or "?"
        if b not in latest or (r.get("created_at") or "") > (latest[b].get("created_at") or ""):
            latest[b] = r
    return latest


def _diff_ci(prev: dict, curr: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not prev.get("available") or not curr.get("available"):
        return out  # surface unavailable is not a delta, never an error
    pl, cl = _latest_run_by_branch(prev), _latest_run_by_branch(curr)
    for branch, run in cl.items():
        p = pl.get(branch)
        if p is None:
            continue  # new branch tracking starts next pulse
        pc, cc = p.get("conclusion"), run.get("conclusion")
        if pc == cc:
            continue
        key = f"ci-{branch}-{run.get('run_id')}"
        if cc == "failure" and pc != "failure":
            out.append(_mk("github", "ci_new_failure", key,
                           {"conclusion": pc, "run_id": p.get("run_id")},
                           {"conclusion": cc, "run_id": run.get("run_id"),
                            "head_branch": branch, "html_url": run.get("html_url")}))
        elif pc == "failure" and cc == "success":
            out.append(_mk("github", "ci_recovered", key,
                           {"conclusion": pc, "run_id": p.get("run_id")},
                           {"conclusion": cc, "run_id": run.get("run_id"),
                            "head_branch": branch}))
    return out


def _diff_fact0(prev: dict, curr: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if prev.get("sensor_error") or curr.get("sensor_error"):
        return out
    if not prev:
        return out
    pe, ce = prev.get("evidence", {}), curr.get("evidence", {})
    for eid, cur in ce.items():
        if eid not in pe:
            out.append(_mk("fact0", "new_evidence", eid, None,
                           {"evidence_id": eid, "trust_tier": cur.get("trust_tier")}))
            continue
        p = pe[eid]
        if p.get("currency") == "CURRENT" and cur.get("currency") == "STALE":
            out.append(_mk("fact0", "evidence_stale", eid,
                           {"currency": "CURRENT"},
                           {"currency": "STALE", "evidence_id": eid}))
        if p.get("status") == "CANDIDATE" and cur.get("status") == "SUPERSEDED":
            out.append(_mk("fact0", "evidence_superseded", eid,
                           {"status": "CANDIDATE"},
                           {"status": "SUPERSEDED", "evidence_id": eid}))
    return out


def _diff_inbox(prev: dict, curr: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if prev.get("sensor_error") or curr.get("sensor_error"):
        return out
    if not prev:
        return out
    pf = {f["name"]: f for f in prev.get("files", [])}
    for f in curr.get("files", []):
        p = pf.get(f["name"])
        if p is None or p.get("sha256") != f.get("sha256"):
            out.append(_mk("inbox", "new_file", f["name"],
                           {"sha256": p.get("sha256")} if p else None,
                           {"name": f["name"], "sha256": f["sha256"],
                            "bytes": f["bytes"]}))
    return out


def make_observation(delta: dict[str, Any]) -> dict[str, Any]:
    """OBSERVATION record: source, timestamp, content/hash, previous_state,
    current_state. One per delta."""
    return {
        "observation_id": "obs_" + delta["delta_id"][len("delta_"):],
        "source": delta["source"],
        "timestamp": delta["observed_at"],
        "delta_kind": delta["kind"],
        "content_hash": hashlib.sha256(
            canonical(delta["current_state"]).encode()).hexdigest(),
        "previous_state": delta["previous_state"],
        "current_state": delta["current_state"],
        "delta_id": delta["delta_id"],
    }
