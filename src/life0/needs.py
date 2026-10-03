"""LIFE-0 need formation and need queue.

A need MUST reference an observed delta. No evidence -> no task. No exceptions.
This is enforced mechanically: form_need() requires a delta dict and raises
otherwise.

Need formation in v0 is deterministic templating from the delta kind — the
pulse makes zero model calls. Worker interpretation of a need happens later,
inside the explicitly authorized mission, not in the pulse.

STANDING OBLIGATIONS (environmental constraints supplied by the operator,
NOT self-authored goals — that distinction is load-bearing):
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .delta import canonical, utcnow_iso

STANDING_OBLIGATIONS = (
    "1. Preserve the integrity of the LIVE substrate.",
    "2. Complete explicitly assigned work.",
    "3. Maintain factual awareness required by current work.",
    "4. Notice recurring operational pressure.",
    "5. Propose improvements when evidence justifies them.",
)

# Priority order, highest first. Mechanical mapping from delta kind.
PRIORITIES = (
    "blocking failure",
    "integrity/safety problem",
    "unfinished user work",
    "recurrent operational friction",
    "useful opportunity",
    "informational housekeeping",
    "accepted program commitment",
)

# delta kind -> (possible_need template, priority, obligation index)
NEED_TEMPLATES: dict[str, tuple[str, str, int]] = {
    "ci_new_failure": (
        "Diagnose and repair CI failure: run {run_id} on branch "
        "'{head_branch}' concluded '{conclusion}'. See {html_url}.",
        "blocking failure", 1,
    ),
    "new_file": (
        "Complete explicitly assigned operator work from inbox file "
        "'{name}' (sha256 {sha256}).",
        "unfinished user work", 2,
    ),
    "new_issue": (
        "Triage GitHub issue #{number}: {title}.",
        "useful opportunity", 5,
    ),
    "evidence_stale": (
        "Re-verify stale evidence {evidence_id}: factual awareness for "
        "current work requires knowing whether it is still current.",
        "informational housekeeping", 3,
    ),
    "evidence_superseded": (
        "Note supersession of evidence {evidence_id}; confirm downstream "
        "references point at the current identity.",
        "informational housekeeping", 3,
    ),
    "new_evidence": (
        "Review newly admitted candidate evidence {evidence_id} "
        "(tier {trust_tier}) for relevance to current work.",
        "informational housekeeping", 3,
    ),
    "head_changed": (
        "Mirror advanced {previous_head_sha} -> {head_sha}; confirm the organism's "
        "view of the lineage is consistent.",
        "informational housekeeping", 3,
    ),
}


def _need_id(delta: dict[str, Any]) -> str:
    """Substrate-minted deterministic id: same delta -> same need, so a
    persistent delta never spawns duplicate needs across pulses."""
    core = {"delta_id": delta["delta_id"], "kind": delta["kind"],
            "key": delta["key"]}
    return "need_" + hashlib.sha256(canonical(core).encode()).hexdigest()[:32]


def form_need(delta: dict[str, Any] | None) -> dict[str, Any]:
    """Build a need record from an observed delta. Raises on missing or
    malformed evidence — a need without a delta cannot exist."""
    if not isinstance(delta, dict) or not delta.get("delta_id"):
        raise ValueError("need formation requires an observed delta; got none")
    kind = delta.get("kind")
    if kind not in NEED_TEMPLATES:
        raise ValueError(f"no need template for delta kind: {kind!r}")
    template, priority, obligation = NEED_TEMPLATES[kind]
    cur = delta.get("current_state") or {}
    prev = delta.get("previous_state") or {}
    # previous-state fields are namespaced so they can never collide with
    # current-state fields (e.g. head_sha on both sides of a change).
    fields = {**cur, **{f"previous_{k}": v for k, v in prev.items()}}
    try:
        possible_need = template.format(**{k: fields.get(k, "?") for k in
                                           _template_fields(template)})
    except Exception as e:
        raise ValueError(f"need template render failed for {kind}: {e}")
    return {
        "need_id": _need_id(delta),
        "delta_id": delta["delta_id"],
        "source": delta["source"],
        "delta_kind": kind,
        "possible_need": possible_need,
        "evidence": {
            "observation": "see observations.jsonl",
            "delta_id": delta["delta_id"],
            "previous_state": delta.get("previous_state"),
            "current_state": delta.get("current_state"),
        },
        "priority": priority,
        "obligation": STANDING_OBLIGATIONS[obligation - 1],
        "status": "NEW",
        "created_at": utcnow_iso(),
    }


def _template_fields(template: str) -> list[str]:
    import string
    return [f[1] for f in string.Formatter().parse(template) if f[1]]


# -- need queue: append-only JSONL event log + fold ---------------------------

EVENT_NEED_CREATED = "NEED_CREATED"
EVENT_NEED_STATUS = "NEED_STATUS"

VALID_STATUSES = (
    "NEW",
    "STAGED_AWAITING_AUTHORIZATION",
    "AUTHORIZED_LAUNCHED",
    "DECLINED",
    "RESOLVED",
)

# Operator decisions recorded on status events. The future earned-autonomy path
# (repeated approval -> candidate standing authority -> falsification ->
# bounded autonomous dispatch) must be readable from these records, so every
# status event carries the operator's decision state explicitly instead of
# leaving it implied by the status string. The analysis itself is NOT built.
OPERATOR_DECISIONS = ("pending", "approved", "rejected")


def queue_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / "needs.jsonl"


def _append(state_dir: str | Path, event: dict[str, Any]) -> None:
    p = queue_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(canonical(event) + "\n")


def enqueue_need(state_dir: str | Path, need: dict[str, Any]) -> bool:
    """Append NEED_CREATED unless this need_id already exists. Returns True
    if the need was new."""
    if need["need_id"] in load_queue(state_dir):
        return False
    # The need is born undecided: the operator has not seen it yet.
    _append(state_dir, {"event": EVENT_NEED_CREATED, "at": utcnow_iso(),
                        "need": need,
                        "operator_decision": "pending", "decided_by": "pulse"})
    return True


def set_status(state_dir: str | Path, need_id: str, status: str,
               note: str = "",
               operator_decision: str | None = None,
               decided_by: str | None = None) -> None:
    """Append a NEED_STATUS event.

    operator_decision: pending | approved | rejected. The OPERATOR's decision
    on the staged need. Staging records "pending" (the operator has not
    decided yet); a later operator act appends "approved"/"rejected" with a
    reason + timestamp. A gate decline is recorded as "rejected" with
    decided_by="pulse" — the gate encodes operator policy, but no human
    decided that instance.
    decided_by: "pulse" | "operator" — who (or what) recorded this decision.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid need status: {status!r}")
    if operator_decision is not None and operator_decision not in OPERATOR_DECISIONS:
        raise ValueError(f"operator_decision must be one of {OPERATOR_DECISIONS}")
    ev: dict[str, Any] = {"event": EVENT_NEED_STATUS, "at": utcnow_iso(),
                          "need_id": need_id, "status": status, "note": note}
    if operator_decision is not None:
        ev["operator_decision"] = operator_decision
    if decided_by is not None:
        ev["decided_by"] = decided_by
    _append(state_dir, ev)


def load_queue(state_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Fold the event log: latest status per need wins. Returns needs ordered
    by priority (highest first), then creation time."""
    needs: dict[str, dict[str, Any]] = {}
    p = queue_path(state_dir)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                if ev.get("event") == EVENT_NEED_CREATED:
                    need = ev["need"]
                    # Decision fields on the creation event set the initial
                    # operator-decision state (pending: unseen by operator).
                    for f in ("operator_decision", "decided_by"):
                        if f in ev and f not in need:
                            need[f] = ev[f]
                    needs[need["need_id"]] = need
                elif ev.get("event") == EVENT_NEED_STATUS:
                    nid = ev["need_id"]
                    if nid in needs:
                        # Latest status event wins; the decision fields travel
                        # with the status so the queue always shows the current
                        # operator-decision state of each need.
                        update = {"status": ev["status"]}
                        for f in ("operator_decision", "decided_by"):
                            if f in ev:
                                update[f] = ev[f]
                        needs[nid] = {**needs[nid], **update}
    rank = {prio: i for i, prio in enumerate(PRIORITIES)}
    ordered = sorted(needs.values(),
                     key=lambda n: (rank.get(n["priority"], 99), n["created_at"]))
    return {n["need_id"]: n for n in ordered}
