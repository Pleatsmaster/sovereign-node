#!/usr/bin/env python3
"""COMMITMENT-ADMISSION-0: deterministic admission of human-accepted program
commitments into durable LIFE obligation state.

Reads one acceptance record (JSON), validates it against the frozen rules in
COMMITMENT_ADMISSION_0.md, and on success:

  1. appends COMMITMENT_ADMITTED to state/commitments.jsonl (append-only);
  2. appends NEED_CREATED to state/needs.jsonl (source "commitment");
  3. stages an inert mission package under dispatch/staged/<need_id>/
     (STAGED_AWAITING_AUTHORIZATION, worker_command null, launch_authorized
     false) — the same inert shape the pulse produces for delta-needs.

Makes zero model calls, spends nothing, launches nothing. Admission is a
recording act, not a granting act: the machinery has no code path that
upgrades, widens, or invents an authority class.

Exit codes: 0 = admitted; 2 = rejected (validation failure or duplicate).
The reason is printed as JSON on stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "src"))

from life0.gate import FORBIDDEN_TARGETS  # noqa: E402  (single source: pulse gate list)

ADMISSION_POLICY_VERSION = "commitment-admission-0"

# Closed authority-class enum. Admission RECORDS the human-stated class; it
# never grants. There is deliberately no mapping that upgrades a class.
AUTHORITY_CLASSES = (
    "local-read",        # read/derive within the workspace; no writes, no external acts
    "local-build",       # write code/artifacts, run tests; no external actuation
    "external-propose",  # may prepare external actions; execution needs separate authorization
)

OBJECTIVE_MIN_CHARS = 20   # "improve Namariel" is 17: fails structurally
SCOPE_MAX_CHARS = 500

PACKAGE_SCHEMA = "life0.mission_package"
PACKAGE_VERSION = 1
STATUS_STAGED = "STAGED_AWAITING_AUTHORIZATION"
COMMITMENT_PRIORITY = "accepted program commitment"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha16(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:16]


def sha32(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:32]


def _reject(reason: str) -> dict:
    return {"admitted": False, "reason": reason,
            "policy_version": ADMISSION_POLICY_VERSION}


def validate_acceptance(acc: dict, life0_root: Path) -> tuple[dict | None, dict | None]:
    """Return (commitment_record, None) on success, (None, rejection) on failure.

    The commitment_record is fully formed but not yet persisted.
    """
    if not isinstance(acc, dict):
        return None, _reject("acceptance record must be a JSON object")

    required = ("objective", "completion_contract", "authority_class",
                "scope", "accepted_by", "accepted_at", "origin")
    for field in required:
        if not acc.get(field):
            return None, _reject(f"missing required field: {field}")

    # Exclusion 2: no model may originate the acceptance event. The human's
    # acceptance is identified by accepted_by == "operator", exactly.
    if acc["accepted_by"] != "operator":
        return None, _reject(
            "accepted_by must be \"operator\": no agent-generated objective "
            f"can self-admit (got {acc['accepted_by']!r})")

    try:
        accepted_at = datetime.fromisoformat(acc["accepted_at"])
    except (ValueError, TypeError):
        return None, _reject("accepted_at must be a valid ISO-8601 timestamp")

    # Exclusion 1: no vague goals. Concreteness is enforced structurally:
    # a minimum-length objective PLUS a completion contract of verifiable
    # predicates. The human supplies the judgment; the machinery enforces form.
    objective = acc["objective"].strip()
    if len(objective) < OBJECTIVE_MIN_CHARS:
        return None, _reject(
            f"objective too vague ({len(objective)} chars < "
            f"{OBJECTIVE_MIN_CHARS}): concrete objectives only")

    contract = acc["completion_contract"]
    predicates = contract.get("predicates") if isinstance(contract, dict) else None
    if not isinstance(predicates, list) or not predicates:
        return None, _reject(
            "completion_contract must carry at least one verifiable predicate")
    for p in predicates:
        if not isinstance(p, dict) or not p.get("id") or not p.get("verifiable"):
            return None, _reject(
                "each completion predicate needs a non-empty id and verifiable condition")

    # Exclusion 3: no admission may widen authority, H, M, or K. The class
    # must be one of the closed enum and is recorded verbatim.
    if acc["authority_class"] not in AUTHORITY_CLASSES:
        return None, _reject(
            f"unknown authority_class {acc['authority_class']!r}: must be one of "
            f"{list(AUTHORITY_CLASSES)}; admission cannot widen authority")

    scope = acc["scope"].strip()
    if not (1 <= len(scope) <= SCOPE_MAX_CHARS):
        return None, _reject(
            f"scope must be 1..{SCOPE_MAX_CHARS} chars")

    # Forbidden targets: same list the pulse gate enforces.
    haystack = f"{objective}\n{scope}\n{acc['origin']}".lower()
    for target in FORBIDDEN_TARGETS:
        if target in haystack:
            return None, _reject(
                f"commitment references forbidden target: {target}")

    parent = acc.get("parent_commitment_id")
    commitment_id = "commit_" + sha16(objective, "operator",
                                      accepted_at.isoformat())
    if parent is not None:
        if not commitment_exists(life0_root, parent):
            return None, _reject(
                f"parent_commitment_id {parent!r} not found in the registry")

    commitment = {
        "commitment_id": commitment_id,
        "origin": acc["origin"],
        "accepted_by": "operator",
        "accepted_at": accepted_at.isoformat(),
        "objective": objective,
        "completion_contract": {"predicates": [
            {"id": p["id"], "verifiable": p["verifiable"]} for p in predicates]},
        "authority_class": acc["authority_class"],  # recorded verbatim: never upgraded
        "scope": scope,
        "status": "OPEN",
        "parent_commitment_id": parent,
    }
    return commitment, None


def commitment_exists(life0_root: Path, commitment_id: str) -> bool:
    reg = life0_root / "state" / "commitments.jsonl"
    if not reg.exists():
        return False
    with reg.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = rec.get("commitment") or {}
            if c.get("commitment_id") == commitment_id:
                return True
    return False


def need_exists(life0_root: Path, need_id: str) -> bool:
    p = life0_root / "state" / "needs.jsonl"
    if not p.exists():
        return False
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") == "NEED_CREATED" and \
                    (rec.get("need") or {}).get("need_id") == need_id:
                return True
    return False


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _objective_text(commitment: dict, mission_id: str) -> str:
    preds = "\n".join(
        f"- [{p['id']}] {p['verifiable']}"
        for p in commitment["completion_contract"]["predicates"])
    return f"""# {mission_id} — STAGED, NOT AUTHORIZED

**Status: STAGED_AWAITING_AUTHORIZATION. This mission has not been launched.
Launching it — supplying a worker command, spending budget, invoking a
worker — requires an explicit operator act. Nothing in this package can
execute itself.**

## Commitment

- commitment_id: {commitment['commitment_id']}
- accepted_by: {commitment['accepted_by']} (human acceptance event — see origin)
- accepted_at: {commitment['accepted_at']}
- origin: {commitment['origin']}
- authority_class: {commitment['authority_class']} (recorded, not granted)
- scope: {commitment['scope']}

## Objective

{commitment['objective']}

## Completion contract (human-stated, verifiable)

{preds}
"""


def admit(life0_root: Path, acc: dict) -> dict:
    """Validate and admit. Returns the result dict (admitted True/False)."""
    commitment, rejection = validate_acceptance(acc, life0_root)
    if rejection is not None:
        return rejection
    assert commitment is not None
    commitment_id = commitment["commitment_id"]

    # Idempotency: the same acceptance re-submitted is refused, never duplicated.
    if commitment_exists(life0_root, commitment_id):
        return _reject(f"already admitted: {commitment_id}")

    now = utcnow_iso()
    state_dir = life0_root / "state"
    dispatch_dir = life0_root / "dispatch" / "staged"

    # 1. Registry append (append-only).
    _append_jsonl(state_dir / "commitments.jsonl",
                  {"event": "COMMITMENT_ADMITTED", "at": now,
                   "commitment": commitment})

    # 2. Need (the obligation handle OBLIGATION-0 projects).
    need_id = "need_" + sha32("commitment-need", commitment_id)
    mission_id = f"life0-{need_id}"
    need = {
        "need_id": need_id,
        "created_at": now,
        "commitment_id": commitment_id,
        "source": "commitment",
        "origin": f"commitment:{commitment_id}",
        "delta_id": None,
        "priority": COMMITMENT_PRIORITY,
        "possible_need": commitment["objective"],
        "obligation": "N. Complete explicitly accepted program commitment.",
        "status": "NEW",
        "evidence": {
            "commitment_id": commitment_id,
            "acceptance_ref": commitment["origin"],
        },
    }
    if not need_exists(life0_root, need_id):
        _append_jsonl(state_dir / "needs.jsonl",
                      {"event": "NEED_CREATED", "at": now,
                       "decided_by": "admission",
                       "operator_decision": "pending",
                       "need": need})
        _append_jsonl(state_dir / "needs.jsonl",
                      {"event": "NEED_STATUS", "at": now,
                       "decided_by": "admission",
                       "need_id": need_id,
                       "status": STATUS_STAGED,
                       "operator_decision": "pending",
                       "note": ("staged at admission; launch requires "
                                "explicit operator act")})

    # 3. Inert staged package (mirrors the pulse's stage_mission shape).
    gate_record = {
        "decision": "STAGE",
        "reason": (
            f"commitment {commitment_id} passed the admission gate: human "
            f"acceptance by operator, concrete objective with verifiable "
            f"completion contract, authority_class "
            f"'{commitment['authority_class']}' recorded (not granted), "
            "no forbidden targets."
        ),
        "policy_version": ADMISSION_POLICY_VERSION,
    }
    pkg_dir = dispatch_dir / need_id
    if not pkg_dir.exists():
        pkg_dir.mkdir(parents=True)
        package = {
            "schema": PACKAGE_SCHEMA,
            "schema_version": PACKAGE_VERSION,
            # NOTE: no worker_command, no executable plan. The worker command
            # is supplied by the operator at explicit authorization time.
            "worker_command": None,
            "launch_authorized": False,
            "status": STATUS_STAGED,
            "mission_id": mission_id,
            "need_id": need_id,
            "commitment_id": commitment_id,
            "delta_id": None,
            "priority": COMMITMENT_PRIORITY,
            "obligation": need["obligation"],
            "objective_path": "OBJECTIVE.md",
            "budget_suggested": 16,
            "acceptance": commitment["completion_contract"]["predicates"],
            "staged_at": now,
            "gate": gate_record,
            "operator_decision": {
                "decision": "pending",
                "decided_by": "admission",
                "reason": "staged; launch requires explicit operator act",
                "at": None,
            },
            "consequence": None,
            "commitment": commitment,
        }
        (pkg_dir / "MISSION_PACKAGE.json").write_text(
            json.dumps(package, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        (pkg_dir / "OBJECTIVE.md").write_text(
            _objective_text(commitment, mission_id), encoding="utf-8")
        (pkg_dir / "STAGE_RECORD.json").write_text(
            json.dumps({
                "need_id": need_id,
                "mission_id": mission_id,
                "commitment_id": commitment_id,
                "gate": gate_record,
                "staged_at": now,
                "note": ("Staged by COMMITMENT-ADMISSION-0. Launch requires "
                         "an explicit operator act; this package cannot "
                         "launch itself."),
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {"admitted": True, "commitment_id": commitment_id,
            "need_id": need_id, "mission_id": mission_id,
            "policy_version": ADMISSION_POLICY_VERSION}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Admit a human-accepted program commitment as a LIFE obligation.")
    ap.add_argument("--life0", default=str(LIFE0_ROOT),
                    help="path to the life-0 directory")
    ap.add_argument("--acceptance", required=True,
                    help="path to the acceptance record JSON file")
    args = ap.parse_args(argv)

    try:
        acc = json.loads(Path(args.acceptance).read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps(_reject(f"cannot read acceptance file: {e}")))
        return 2

    result = admit(Path(args.life0), acc)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("admitted") else 2


if __name__ == "__main__":
    sys.exit(main())
