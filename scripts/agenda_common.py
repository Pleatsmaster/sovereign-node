#!/usr/bin/env python3
"""AGENDA-PROPOSAL-0 shared deterministic helpers.

Ledger conventions (append-only, matching the rest of life-0):
  state/condition_scans.jsonl  — SCAN_RECORDED events (pressure eye)
  state/opportunity_scans.jsonl — SCAN_RECORDED events (opportunity eye)
  state/agenda_proposals.jsonl — PROPOSAL_RECORDED / PROPOSAL_NO_PROPOSAL /
                                  PROPOSAL_DECIDED / PROPOSAL_EXPIRED events
  state/agenda_watermark.json  — last evaluated condition state (plain JSON)
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

POLICY_VERSION = "agenda-opportunity-0"

# Frozen candidate schema field list. The recorder accepts exactly these keys.
# AGENDA-OPPORTUNITY-0 adds horizon_ids: required (non-empty, OPEN/OPEN_PARTIAL,
# within the cited opportunities' horizon_scope) when any source condition is
# an opportunity; pressure-only proposals carry none.
# Instrumentation (not developmental machinery): generator_model is REQUIRED —
# the drafter's model identity string, so future comparative lineage can
# distinguish history-driven generator change from a changed worker or prompt.
# generator_prompt_sha256 is stamped by the recorder from disk at record time
# (never trusted from the draft).
PROPOSAL_SCHEMA_FIELDS = (
    "proposal_id",
    "condition_set_hash",
    "source_conditions",
    "evidence_refs",
    "objective",
    "why_now",
    "expected_consequence",
    "completion_contract",
    "required_authority_class",
    "predicted_cost",
    "known_risks",
    "why_existing_obligations_do_not_cover_it",
    "horizon_ids",
    "generator_model",
)

# Frozen structured rejection reasons (AGENDA_PROPOSAL_0.md).
REJECTION_REASONS = (
    "NOT_WORTH_DOING",
    "DUPLICATE",
    "OBJECTIVE_VAGUE",
    "NO_MEASURABLE_END_STATE",
    "EVIDENCE_INSUFFICIENT",
    "WRONG_AUTHORITY_CLASS",
    "MISDIAGNOSED_CONDITION",
    "OTHER",
)

PROPOSAL_AUTHORITY_CLASSES = ("local-build",)  # the demonstrated class only

EXPIRY_DAYS = 7
OBJECTIVE_MIN_CHARS = 20  # mirrors COMMITMENT-ADMISSION-0

CONDITION_SOURCES = (
    "test_suite_status",
    "evidence_store_integrity",
    "unresolved_or_stale_references",
    "declared_repo_invariants",
)

# AGENDA-OPPORTUNITY-0: the second eye. Observation-only records, same
# six-key shape as pressures; the source distinguishes the kind.
OPPORTUNITY_SOURCES = (
    "unlinked_result_artifact",
    "testable_residue_pending",
    "resource_became_available",
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha_hex(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()


def prompt_sha256(life0: Path) -> str | None:
    """SHA-256 of the live Stage-2 prompt file, or None if unreadable.

    Instrumentation: binds each recorded proposal to the exact prompt bytes
    the drafter was instructed to follow, so a silent prompt edit is
    detectable from the lineage alone.
    """
    try:
        return hashlib.sha256(
            (life0 / "OPPORTUNITY_PROMPT_0.md").read_bytes()).hexdigest()
    except OSError:
        return None


def condition_id_for(source: str, condition: str) -> str:
    short = {"test_suite_status": "test",
             "evidence_store_integrity": "evi",
             "unresolved_or_stale_references": "ref",
             "declared_repo_invariants": "inv",
             "unlinked_result_artifact": "opp",
             "testable_residue_pending": "opp",
             "resource_became_available": "opp"}.get(source, "gen")
    return f"cond_{short}_{sha_hex(source, condition)[:8]}"


def condition_set_hash(conditions: list[dict]) -> str:
    """Deterministic over the observation content only.

    Volatile fields (observed_at, scan_id) are excluded so that an
    unchanged world hashes identically across scans.
    """
    normalized = sorted(
        ({"condition_id": c["condition_id"],
          "source": c["source"],
          "condition": c["condition"],
          "evidence": c["evidence"]} for c in conditions),
        key=lambda c: c["condition_id"])
    return sha_hex(canonical(normalized))


def proposal_id_for(condition_set_hash: str, objective: str) -> str:
    return "prop_" + sha_hex(condition_set_hash, objective.strip())[:12]


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(canonical(record) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def latest_scan(life0: Path) -> dict | None:
    """Most recent SCAN_RECORDED event, or None."""
    scans = [r for r in read_jsonl(life0 / "state" / "condition_scans.jsonl")
             if r.get("event") == "SCAN_RECORDED"]
    return scans[-1] if scans else None


def latest_opportunity_scan(life0: Path) -> dict | None:
    """Most recent SCAN_RECORDED event from the opportunity scanner."""
    scans = [r for r in read_jsonl(life0 / "state" / "opportunity_scans.jsonl")
             if r.get("event") == "SCAN_RECORDED"]
    return scans[-1] if scans else None


def combined_scan(life0: Path) -> dict | None:
    """Union of the latest pressure and opportunity scans.

    Returns None when neither scanner has ever recorded. The combined
    condition_set_hash is deterministic over observation content only;
    with zero opportunities it equals the pressure-only hash, so
    pressure-only states hash identically across the policy bump.
    """
    p = latest_scan(life0)
    o = latest_opportunity_scan(life0)
    if p is None and o is None:
        return None
    pressures = (p or {}).get("conditions") or []
    opportunities = (o or {}).get("conditions") or []
    dedup: dict[str, dict] = {}
    for c in pressures + opportunities:
        dedup[c["condition_id"]] = c
    conditions = [dedup[k] for k in sorted(dedup)]
    return {
        "pressures": pressures,
        "opportunities": opportunities,
        "conditions": conditions,
        "condition_set_hash": condition_set_hash(conditions),
        "pressure_scan_id": (p or {}).get("scan_id"),
        "opportunity_scan_id": (o or {}).get("scan_id"),
    }


# --------------------------------------------------------------------------
# Research horizon (operator-frozen, model-immutable)
# --------------------------------------------------------------------------

HORIZON_OPEN_STATUSES = ("OPEN", "OPEN_PARTIAL")

_HORIZON_ID_RE = re.compile(r"^RH(\d+)\s*[—–-]\s*(.+)$")
_HORIZON_STATUS_RE = re.compile(r"^STATUS:\s*([A-Z_]+)\s*$")


def load_horizon(life0: Path) -> dict[str, dict]:
    """Parse RESEARCH_HORIZON_0.md into {id: {id, title, status, question}}.

    The file is operator-frozen; the parser is strict and fail-closed:
    any structural problem yields {} and callers treat a missing horizon
    as a source error, never as an empty horizon.
    """
    try:
        text = (life0 / "RESEARCH_HORIZON_0.md").read_text(encoding="utf-8")
    except OSError:
        return {}
    lines = text.splitlines()
    out: dict[str, dict] = {}
    i = 0
    while i < len(lines):
        m = _HORIZON_ID_RE.match(lines[i])
        if not m:
            i += 1
            continue
        qid = f"RH{m.group(1)}"
        title = m.group(2).strip()
        status = None
        j = i + 1
        while j < min(i + 5, len(lines)):
            sm = _HORIZON_STATUS_RE.match(lines[j])
            if sm:
                status = sm.group(1)
                break
            j += 1
        if status is None:
            return {}
        # Question: everything after the STATUS line up to the next
        # question block, the rules section, or a separator — kept
        # verbatim, including subsection labels (Disposition, Required
        # distinction, Established background, ...). Lossless by design:
        # the parser never decides what counts as "the question".
        qlines: list[str] = []
        k = j + 1
        while k < len(lines):
            ln = lines[k]
            if (_HORIZON_ID_RE.match(ln) or ln.startswith("HORIZON RULES")
                    or ln.startswith("---")):
                break
            qlines.append(ln)
            k += 1
        question = "\n".join(qlines).strip()
        if not question:
            return {}
        out[qid] = {"id": qid, "title": title, "status": status,
                    "question": question}
        i = k
    return out if out else {}


def open_horizon_questions(life0: Path) -> list[dict]:
    """OPEN/OPEN_PARTIAL questions, sorted by id, for the proposal packet."""
    horizon = load_horizon(life0)
    return [horizon[qid] for qid in sorted(horizon)
            if horizon[qid]["status"] in HORIZON_OPEN_STATUSES]


def read_horizon_bindings(life0: Path) -> list[dict]:
    """Operator-frozen mission/chain → RH* bindings. Missing file → []."""
    return read_jsonl(life0 / "state" / "horizon_bindings.jsonl")


def read_evidence_links(life0: Path) -> list[dict]:
    """Operator-recorded artifact → horizon-question evaluations."""
    return read_jsonl(life0 / "state" / "evidence_links.jsonl")


def load_proposal_ledger(life0: Path) -> dict[str, dict]:
    """Fold agenda_proposals.jsonl events into current proposal states.

    Returns {proposal_id: {"state": ..., "proposal": {...}, ...}}.
    NO_PROPOSAL outcomes are returned separately (keyed by condition hash).
    """
    proposals: dict[str, dict] = {}
    for r in read_jsonl(life0 / "state" / "agenda_proposals.jsonl"):
        ev = r.get("event")
        if ev == "PROPOSAL_RECORDED":
            p = r["proposal"]
            proposals[p["proposal_id"]] = {
                "state": "PROPOSED",
                "proposal": p,
                "proposed_at": r.get("at"),
            }
        elif ev == "PROPOSAL_DECIDED":
            pid = r["proposal_id"]
            if pid in proposals:
                proposals[pid]["state"] = r["verdict"]
                proposals[pid]["decision"] = {
                    "verdict": r["verdict"],
                    "reason": r.get("reason"),
                    "note": r.get("note"),
                    "decided_by": r.get("decided_by"),
                    "decided_at": r.get("at"),
                    "commitment_id": r.get("commitment_id"),
                    "need_id": r.get("need_id"),
                }
        elif ev == "PROPOSAL_EXPIRED":
            pid = r["proposal_id"]
            if pid in proposals and proposals[pid]["state"] == "PROPOSED":
                proposals[pid]["state"] = "EXPIRED"
                proposals[pid]["expired_at"] = r.get("at")
    return proposals


def no_proposal_hashes(life0: Path) -> set[str]:
    return {r["condition_set_hash"]
            for r in read_jsonl(life0 / "state" / "agenda_proposals.jsonl")
            if r.get("event") == "PROPOSAL_NO_PROPOSAL"}


def read_watermark(life0: Path) -> dict:
    p = life0 / "state" / "agenda_watermark.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_watermark(life0: Path, watermark: dict) -> None:
    p = life0 / "state" / "agenda_watermark.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(canonical(watermark) + "\n", encoding="utf-8")
