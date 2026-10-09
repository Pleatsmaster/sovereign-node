"""Persistent experience residue (R_t): the organism's acquired reusable store.

Not conversation memory. Not a transcript archive. A small, versioned,
append-only store of things the organism has learned from doing work:
procedures that succeeded, tool facts discovered through experience,
recurring failure conditions, and (later) executable skills.

The closed loop:

    mission -> outcome -> worker proposal -> mechanical admission
        -> persistent R -> retrieval into a later mission's packet

Write path: after a mission reaches a terminal status, the organism gives
the worker exactly one opportunity to propose a reusable lesson
(REUSE_CANDIDATE) or decline (NO_REUSABLE_LESSON). The operator never
writes the lesson. Admission is mechanical: schema-valid, evidence
references resolve to real ledger events of that mission, one residue per
mission, length caps, no silent forking of active residues. Admitted
records are versioned (parent_hash chain) and reversible (retirement is an
appended record, never a mutation).

Read path: before each mission step, up to RETRIEVAL_LIMIT most-recent
active residues are placed in the worker packet under "organism_residue",
clearly delimited as acquired organism state. Injected ids are recorded in
the mission ledger for provenance.

The store lives outside any git worktree (default ~/.unified-machine/residue,
override with UM_RESIDUE_DIR) so that R_t survives lineage checkouts and
persists across organism generations: learning (R_t -> R_t+1) is a faster
timescale than development (U_n -> U_n+1).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KINDS = ("procedure", "tool_fact", "failure_pattern", "skill")
STATUSES = ("candidate", "active", "retired")

MAX_CONDITION = 500
MAX_CONTENT = 2000
MAX_SCOPE = 500
MAX_EVIDENCE_REFS = 12
RETRIEVAL_LIMIT = 5
# Ledger events offered to the worker as citable evidence for a proposal.
MAX_PROPOSAL_EVENTS = 150
MAX_EVENT_PAYLOAD = 400

FILENAME = "residue.jsonl"


def default_residue_dir() -> Path:
    env = os.environ.get("UM_RESIDUE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".unified-machine" / "residue"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(record: Dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "record_hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _normalize_condition(condition: str) -> str:
    return " ".join(condition.casefold().split())


PROPOSAL_CONTRACT = r"""
You are the cognitive worker inside a persistent autonomous research machine.
The mission described below has just finished. You have exactly ONE opportunity
to propose a reusable lesson for the organism's persistent residue store -- or
to decline.

A reusable lesson is NOT a summary of what happened. It is an operational rule
the machine can apply in future work. Good candidates:

- WHEN <observable task condition> DO <concrete procedure naming tools/actions>
- a tool or interface fact discovered through experience (what worked, what did not, exact forms)
- a recurring failure condition with its observable signature and the recovery that worked

Propose at most one lesson. It MUST be:

1. Grounded: cite specific ledger events below as "seq:<n>". Every citation must
   resolve to a real event. The admission rule rejects proposals whose evidence
   does not exist.
2. Operationally specific: name tools, actions, observable conditions. Generic
   advice ("read sources before writing") is rejected as non-operational unless
   it names the concrete mechanism that was missing.
3. Non-obvious to the base model: something the machine learned by doing, not by
   being told.

Respond with EXACTLY one of the following.

(a) If nothing from this mission is worth persisting:

NO_REUSABLE_LESSON
<one sentence saying why>

(b) Otherwise, a single JSON object (a fenced ```json block is tolerated):

{
  "kind": "procedure|tool_fact|failure_pattern|skill",
  "condition": "WHEN ... -- observable task features (max 500 chars)",
  "lesson": "DO ... / the fact / the pattern and its signature (max 2000 chars)",
  "scope": "where this applies and where it does not (max 500 chars)",
  "evidence": ["seq:12", "seq:15"],
  "supersedes": "<residue id, ONLY if this replaces an existing active residue listed below>"
}

Rules: evidence must be a non-empty list (max 12) of seq references to the
ledger events below. Do not invent seq numbers. Do not propose more than one
lesson. The operator does not write lessons; your proposal is admitted only
through the mechanical admission rule.
""".strip()


class ResidueStore:
    """Append-only JSONL store. Records are immutable; status changes and
    retirements are new appended records sharing the residue id. The current
    state of an id is its latest record."""

    def __init__(self, directory: Optional[Path] = None):
        self.directory = Path(directory) if directory else default_residue_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / FILENAME
        self.path.touch(exist_ok=True)

    def _read_all(self) -> List[Dict[str, Any]]:
        records = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def current(self) -> Dict[str, Dict[str, Any]]:
        """Latest record per residue id, in first-seen order."""
        state: Dict[str, Dict[str, Any]] = {}
        for record in self._read_all():
            state[record["id"]] = record
        return state

    def active(self, limit: int = RETRIEVAL_LIMIT) -> List[Dict[str, Any]]:
        """Active residues, most recently created first, bounded."""
        actives = [r for r in self.current().values() if r.get("status") == "active"]
        actives.sort(key=lambda r: (r.get("ts", 0), r.get("id", "")), reverse=True)
        return actives[: max(0, limit)]

    def has_derived_from(self, mission_id: str) -> bool:
        return any(
            mission_id in (r.get("derived_from") or [])
            for r in self.current().values()
        )

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(record)
        record["record_hash"] = _record_hash(record)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(canonical_json(record) + "\n")
        return record

    def retire(self, residue_id: str, reason: str, actor: str = "operator") -> Dict[str, Any]:
        """Reversible deactivation: appends a retirement record. Never mutates."""
        state = self.current()
        prev = state.get(residue_id)
        if prev is None:
            raise KeyError(f"unknown residue id: {residue_id}")
        if prev.get("status") == "retired":
            raise ValueError(f"residue already retired: {residue_id}")
        record = {
            "id": residue_id,
            "derived_from": prev.get("derived_from", []),
            "condition": prev.get("condition", ""),
            "content": prev.get("content", ""),
            "kind": prev.get("kind", "procedure"),
            "scope": prev.get("scope", ""),
            "status": "retired",
            "created_by": f"retirement:{actor}",
            "evidence": [],
            "parent_hash": prev.get("record_hash"),
            "retire_reason": (reason or "")[:MAX_SCOPE],
            "ts": time.time(),
        }
        return self.append(record)


def _unfence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.startswith("json"):
            t = t[4:].lstrip()
    return t


def parse_proposal(text: str) -> Tuple[str, Any]:
    """Parse raw worker proposal text.

    Returns (verdict, payload) where verdict is one of:
      "none"         -> payload is the worker's one-line reason (str)
      "candidate"    -> payload is the proposed object (dict)
      "unparseable"  -> payload is a raw excerpt (str)
    """
    t = (text or "").strip()
    if t.startswith("NO_REUSABLE_LESSON"):
        return ("none", t[len("NO_REUSABLE_LESSON"):].strip()[:MAX_SCOPE])
    try:
        obj = json.loads(_unfence(t))
    except Exception:
        return ("unparseable", t[:2000])
    if not isinstance(obj, dict):
        return ("unparseable", t[:2000])
    return ("candidate", obj)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def build_proposal_packet(
    *,
    mission_id: str,
    objective: str,
    status: str,
    steps: int,
    failed_acts: int,
    acceptance_ok: Optional[bool],
    stop_reason: str,
    report: str,
    ledger: Any,
    store: "ResidueStore",
) -> Dict[str, Any]:
    """Assemble the evidence bundle the worker proposes from.

    Ledger events are compacted to (seq, kind, truncated payload); the worker
    cites them as "seq:<n>". mission_started is always included; otherwise the
    most recent MAX_PROPOSAL_EVENTS events are offered.
    """
    events = ledger.events(limit=MAX_PROPOSAL_EVENTS + 8)
    compacted = [
        {
            "seq": e["seq"],
            "kind": e["kind"],
            "payload": _truncate(
                json.dumps(e["payload"], ensure_ascii=False), MAX_EVENT_PAYLOAD
            ),
        }
        for e in events
    ]
    started = [c for c in compacted if c["kind"] == "mission_started"]
    rest = [c for c in compacted if c["kind"] != "mission_started"]
    offered = started + rest[-MAX_PROPOSAL_EVENTS:]
    return {
        "mode": "residue_proposal",
        "proposal_contract": PROPOSAL_CONTRACT,
        "mission": {"id": mission_id, "objective": objective},
        "outcome": {
            "status": status,
            "steps": steps,
            "failed_acts": failed_acts,
            "acceptance_ok": acceptance_ok,
            "stop_reason": stop_reason,
        },
        "active_residues": [
            {"id": r["id"], "kind": r["kind"], "condition": r["condition"]}
            for r in store.active(limit=RETRIEVAL_LIMIT)
        ],
        "report": _truncate(report or "", 8000),
        "ledger_events": offered,
    }


def admit_candidate(
    store: ResidueStore,
    proposal: Dict[str, Any],
    *,
    mission_id: str,
    ledger: Any,
    worker_label: str,
) -> Tuple[bool, Any]:
    """Mechanical admission rule. Returns (True, record) or (False, reason).

    Checks, in order: schema/kind, length caps, evidence references resolve to
    real events of this mission's ledger, one residue per mission, no silent
    forking of an active residue (a normalized-condition match requires an
    explicit supersedes link). The worker proposes; only this rule admits.
    """
    if not isinstance(proposal, dict):
        return (False, "proposal is not an object")

    kind = proposal.get("kind")
    if kind not in KINDS:
        return (False, f"kind must be one of {KINDS}, got {kind!r}")

    condition = proposal.get("condition")
    if not isinstance(condition, str) or not condition.strip():
        return (False, "condition must be a non-empty string")
    if len(condition) > MAX_CONDITION:
        return (False, f"condition exceeds {MAX_CONDITION} chars")

    lesson = proposal.get("lesson")
    if not isinstance(lesson, str) or not lesson.strip():
        return (False, "lesson must be a non-empty string")
    if len(lesson) > MAX_CONTENT:
        return (False, f"lesson exceeds {MAX_CONTENT} chars")

    scope = proposal.get("scope", "")
    if not isinstance(scope, str):
        return (False, "scope must be a string")
    if len(scope) > MAX_SCOPE:
        return (False, f"scope exceeds {MAX_SCOPE} chars")

    evidence = proposal.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return (False, "evidence must be a non-empty list of seq references")
    if len(evidence) > MAX_EVIDENCE_REFS:
        return (False, f"evidence exceeds {MAX_EVIDENCE_REFS} references")
    try:
        known_seqs = {e["seq"] for e in ledger.events(limit=100000)}
    except Exception as exc:
        return (False, f"ledger unreadable: {type(exc).__name__}")
    for ref in evidence:
        if not isinstance(ref, str) or not ref.startswith("seq:"):
            return (False, f"evidence reference must look like 'seq:<n>', got {ref!r}")
        try:
            seq = int(ref.split(":", 1)[1])
        except ValueError:
            return (False, f"evidence reference not an integer seq: {ref!r}")
        if seq not in known_seqs:
            return (False, f"evidence seq {seq} not found in this mission's ledger")

    if store.has_derived_from(mission_id):
        return (False, f"mission {mission_id} already yielded a residue (one per mission)")

    supersedes = proposal.get("supersedes")
    if supersedes is not None and not isinstance(supersedes, str):
        return (False, "supersedes must be a residue id string")
    active = store.current()
    norm = _normalize_condition(condition)
    dup_id = next(
        (
            rid
            for rid, r in active.items()
            if r.get("status") == "active" and _normalize_condition(r.get("condition", "")) == norm
        ),
        None,
    )
    parent_hash: Optional[str] = None
    if supersedes:
        prev = active.get(supersedes)
        if prev is None or prev.get("status") != "active":
            return (False, f"supersedes target {supersedes!r} is not an active residue")
        parent_hash = prev.get("record_hash")
    elif dup_id is not None:
        return (
            False,
            f"duplicate of active residue {dup_id}; set supersedes to version it explicitly",
        )

    record = {
        "id": "r_" + uuid.uuid4().hex[:12],
        "derived_from": [mission_id],
        "condition": condition.strip(),
        "content": lesson.strip(),
        "kind": kind,
        "scope": scope.strip(),
        "status": "active",
        "created_by": f"worker:{worker_label}/mission:{mission_id}",
        "evidence": list(evidence),
        "parent_hash": parent_hash,
        "ts": time.time(),
    }
    return (True, store.append(record))
