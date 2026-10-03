#!/usr/bin/env python3
"""COMMITMENT-EXECUTION-0: mechanical standing-authorization check.

For a need_id, verifies the six validity conditions under which a valid
COMMITMENT_ADMITTED event constitutes standing authorization to execute
inside the declared authority class:

  1. a COMMITMENT_ADMITTED record exists for the need's commitment
  2. accepted_by == "operator" (exactly)
  3. the completion contract validates structurally
  4. the authority class is in the closed enum
  5. the scope passes the forbidden-target gate
  6. the obligation currently projects OPEN (OBLIGATION-0)

Read-only: makes zero model calls, spends nothing, launches nothing,
writes nothing.

Exit codes: 0 = EXECUTION_AUTHORIZED; 2 = NOT_AUTHORIZED (the blocking
condition is printed as JSON on stdout).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "src"))
sys.path.insert(0, str(LIFE0_ROOT / "scripts"))

from life0.gate import FORBIDDEN_TARGETS  # noqa: E402
from project_obligations import Projector  # noqa: E402

import admit_commitment as admission  # noqa: E402

POLICY_VERSION = "commitment-execution-0"


def load_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def find_need(life0: Path, need_id: str):
    for e in load_jsonl(life0 / "state" / "needs.jsonl"):
        if e.get("event") == "NEED_CREATED" and \
                (e.get("need") or {}).get("need_id") == need_id:
            return e["need"]
    return None


def find_commitment(life0: Path, commitment_id: str):
    for e in load_jsonl(life0 / "state" / "commitments.jsonl"):
        if e.get("event") == "COMMITMENT_ADMITTED" and \
                (e.get("commitment") or {}).get("commitment_id") == commitment_id:
            return e["commitment"]
    return None


def check(life0: Path, need_id: str) -> dict:
    """Return the eligibility verdict dict (never raises on bad records)."""
    need = find_need(life0, need_id)
    if need is None:
        return _blocked(f"no NEED_CREATED for {need_id}")
    if need.get("source") != "commitment":
        return _blocked(
            f"need {need_id} is not commitment-origin "
            f"(source={need.get('source')!r}): standing authorization applies "
            "only to admitted commitments")
    commitment_id = need.get("commitment_id")
    c = find_commitment(life0, commitment_id)
    if c is None:
        return _blocked(
            f"no COMMITMENT_ADMITTED record for {commitment_id}")

    # 2. Human acceptance only.
    if c.get("accepted_by") != "operator":
        return _blocked(
            f"accepted_by is {c.get('accepted_by')!r}, not \"operator\"")

    # 3. Completion contract validates structurally.
    preds = ((c.get("completion_contract") or {}).get("predicates"))
    if not isinstance(preds, list) or not preds or not all(
            isinstance(p, dict) and p.get("id") and p.get("verifiable")
            for p in preds):
        return _blocked("completion contract fails structural validation")

    # 4. Authority class recognized (closed enum; recorded, never granted).
    if c.get("authority_class") not in admission.AUTHORITY_CLASSES:
        return _blocked(
            f"unrecognized authority_class {c.get('authority_class')!r}")

    # 5. Scope passes the forbidden-target gate.
    haystack = f"{c.get('objective', '')}\n{c.get('scope', '')}\n{c.get('origin', '')}".lower()
    for target in FORBIDDEN_TARGETS:
        if target in haystack:
            return _blocked(f"scope references forbidden target: {target}")

    # 6. Obligation currently projects OPEN (terminal dominates).
    try:
        proj = Projector(str(life0)).project()
        state = (proj["obligations"].get(need_id) or {}).get("state")
    except Exception as e:
        return _blocked(f"obligation projection failed: {e}")
    if state != "OPEN":
        return _blocked(
            f"obligation projects {state}, not OPEN: standing authorization "
            "covers execution of open obligations only")

    return {
        "eligible": True,
        "verdict": "EXECUTION_AUTHORIZED",
        "need_id": need_id,
        "commitment_id": commitment_id,
        "authority_class": c["authority_class"],
        "scope": c["scope"],
        "completion_contract": c["completion_contract"],
        "accepted_by": "operator",
        "policy_version": POLICY_VERSION,
        "standing_authorization": (
            "COMMITMENT-EXECUTION-0: valid COMMITMENT_ADMITTED = standing "
            "authorization to execute inside the declared authority class; "
            "no second mission-by-mission authorization required."),
    }


def _blocked(reason: str) -> dict:
    return {"eligible": False, "verdict": "NOT_AUTHORIZED",
            "blocking_condition": reason, "policy_version": POLICY_VERSION}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check COMMITMENT-EXECUTION-0 standing authorization for a need.")
    ap.add_argument("--life0", default=str(LIFE0_ROOT))
    ap.add_argument("--need", required=True, help="need_id to check")
    args = ap.parse_args(argv)
    verdict = check(Path(args.life0), args.need)
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["eligible"] else 2


if __name__ == "__main__":
    sys.exit(main())
