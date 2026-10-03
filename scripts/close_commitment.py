#!/usr/bin/env python3
"""COMMITMENT-EXECUTION-0: terminal closure for an executed commitment.

Records SATISFIED for a commitment whose frozen completion contract has
been verified predicate-by-predicate with evidence. The executor supplies
the evidence; the gate is mechanical:

  - execution eligibility must currently be EXECUTION_AUTHORIZED
    (no closure without standing authorization for an OPEN obligation);
  - EVERY contract predicate must carry verdict "verified" with evidence;
  - any missing or failed predicate -> refusal, no state written.

On all-pass, writes the closure into the staged MISSION_PACKAGE.json
(status CLOSED + closure record). The OBLIGATION-0 projector reads this
as closed_satisfied -> SATISFIED. Idempotent: closing an already-closed
package reports success without rewriting.

The evidence file is the executor's signed terminal report, e.g.:
  {"predicate_id": {"verdict": "verified", "evidence": "..."}, ...}

Exit codes: 0 = closed (or already closed); 2 = refused.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "scripts"))

import execution_eligibility as elig  # noqa: E402

POLICY_VERSION = "commitment-execution-0"


def _refuse(reason: str, **extra) -> dict:
    return {"closed": False, "reason": reason,
            "policy_version": POLICY_VERSION, **extra}


def close(life0: Path, need_id: str, evidence: dict, closed_by: str,
          how: str) -> dict:
    # 0. Idempotency: an already-closed package reports success without
    #    rewriting history. (Checked before eligibility: a closed package
    #    is terminal, so eligibility would correctly refuse it.)
    pkg_path = (life0 / "dispatch" / "staged" / need_id / "MISSION_PACKAGE.json")
    if not pkg_path.exists():
        return _refuse(f"no staged package for {need_id}")
    package = json.loads(pkg_path.read_text(encoding="utf-8"))
    if package.get("status") == "CLOSED":
        return {"closed": True, "already": True, "need_id": need_id,
                "policy_version": POLICY_VERSION}

    # 1. Standing authorization, re-verified at close time.
    verdict = elig.check(life0, need_id)
    if not verdict.get("eligible"):
        return _refuse(
            "execution eligibility is not EXECUTION_AUTHORIZED: "
            + verdict.get("blocking_condition", "unknown"))

    commitment = elig.find_commitment(life0, verdict["commitment_id"])
    predicates = (commitment.get("completion_contract") or {}).get("predicates", [])
    pred_ids = [p["id"] for p in predicates]

    # 2. Every predicate must be verified with evidence. No exceptions.
    missing = [pid for pid in pred_ids if pid not in evidence]
    if missing:
        return _refuse("missing evidence for predicates", missing=missing)
    failed = [pid for pid in pred_ids
              if evidence[pid].get("verdict") != "verified"
              or not evidence[pid].get("evidence")]
    if failed:
        return _refuse("predicates not verified", failed=failed)

    # 3. Write the closure.
    closure = {
        "closed_by": closed_by,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "how": how,
        "authority": ("COMMITMENT-EXECUTION-0: standing authorization from "
                      "valid COMMITMENT_ADMITTED; execution confined to the "
                      "declared authority class"),
        "authority_class": verdict["authority_class"],
        "execution_eligibility": "EXECUTION_AUTHORIZED",
        "predicates": [
            {"id": pid,
             "verdict": evidence[pid]["verdict"],
             "evidence": evidence[pid]["evidence"]}
            for pid in pred_ids
        ],
        "developmental_credit": "NONE",
    }
    package["status"] = "CLOSED"
    package["closure"] = closure
    tmp = pkg_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(package, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(pkg_path)

    return {"closed": True, "need_id": need_id,
            "commitment_id": verdict["commitment_id"],
            "predicates_verified": len(pred_ids),
            "policy_version": POLICY_VERSION}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Close an executed commitment after verifying its completion contract.")
    ap.add_argument("--life0", default=str(LIFE0_ROOT))
    ap.add_argument("--need", required=True, help="need_id to close")
    ap.add_argument("--evidence", required=True,
                    help="JSON file: predicate_id -> {verdict, evidence}")
    ap.add_argument("--closed-by", required=True,
                    help="who/what closed it (executor identity + authority ref)")
    ap.add_argument("--how", required=True,
                    help="how the completion was established")
    args = ap.parse_args(argv)

    try:
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps(_refuse(f"cannot read evidence file: {e}")))
        return 2

    result = close(Path(args.life0), args.need, evidence, args.closed_by, args.how)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("closed") else 2


if __name__ == "__main__":
    sys.exit(main())
