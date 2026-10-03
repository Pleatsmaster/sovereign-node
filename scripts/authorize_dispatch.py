#!/usr/bin/env python3
"""AUTO-CONTINUE-0 dispatcher: the mechanical eligibility token.

The run agent NEVER decides whether a continuation may be dispatched.
It obtains a DISPATCH_AUTH_sNN.json token from this script, or there is
no next step. In particular:

  STOP verdict  =>  dispatcher incapable of dispatching the next step.

This is a property of the executable topology, not an instruction to the
agent. After any STOP_* the parent's latest chain decision is not
CONTINUE_ELIGIBLE, and no input to this script can satisfy the interface:
there is no flag, no override, no agent judgment that produces a token.

The token binds, by hash, the exact proposal bytes, the exact classifier
verdict bytes, and the exact parent snapshot the classifier was bound
to. The post-step evaluator re-verifies every binding; a step evaluated
without a valid token is recorded STOP_UNAUTHORIZED_DISPATCH and the
chain terminates.

Deterministic: stdlib only. No network, no subprocess, no model calls.
Fail-closed: anything unverifiable -> refusal, exit 3, no token written.

Exit codes: 0 = authorized (token written, details on stdout);
            2 = unusable input (contract/proposal/ledger/classification
                unreadable);
            3 = refused (mechanical gate failed; reason in the JSON output).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify_auto_continue_0 as acc  # noqa: E402
from publish_step_snapshot import verify_snapshot  # noqa: E402

AUTHORITY = "AUTO-CONTINUE-0"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def refuse(reason: str, detail: str = "") -> int:
    print(json.dumps({"authorized": False, "authority": AUTHORITY,
                      "reason": reason, "detail": detail,
                      "evaluated_at": utcnow_iso()},
                     indent=2, sort_keys=True))
    return 3


def cmd_authorize(args) -> int:
    contract, err = acc.load_contract(Path(args.contract))
    if err:
        print(json.dumps({"authorized": False, "error": err}),
              file=sys.stderr)
        return 2
    proposal = acc.load_json(Path(args.proposal))
    if not isinstance(proposal, dict):
        print(json.dumps({"authorized": False,
                           "error": "proposal unreadable"}), file=sys.stderr)
        return 2
    classification = acc.load_json(Path(args.classification))
    if not isinstance(classification, dict):
        print(json.dumps({"authorized": False,
                           "error": "classification unreadable"}),
              file=sys.stderr)
        return 2
    entries, err = acc.load_ledger(Path(args.ledger))
    if err:
        print(json.dumps({"authorized": False, "error": err}),
              file=sys.stderr)
        return 2

    chain_id = proposal.get("chain_id")
    parent_id = proposal.get("parent_step_id")
    depth = proposal.get("current_depth")
    pred = proposal.get("unsatisfied_done_predicate")

    # 1. The classifier verdict must be ELIGIBLE and bound to this proposal
    #    and to the exact parent snapshot.
    if classification.get("verdict") != "ELIGIBLE":
        return refuse("classifier verdict is not ELIGIBLE",
                      f"verdict={classification.get('verdict')}")
    bindings = [
        ("chain_id", classification.get("chain_id"), chain_id),
        ("parent_step_id", classification.get("parent_step_id"), parent_id),
        ("proposal_depth", classification.get("proposal_depth"), depth),
        ("target_predicate", classification.get("target_predicate"), pred),
    ]
    for name, got, want in bindings:
        if got != want:
            return refuse("classifier verdict not bound to this proposal",
                          f"{name}: verdict has {got!r}, proposal has {want!r}")
    cpsh = classification.get("parent_snapshot_hash")
    if not isinstance(cpsh, str):
        return refuse("classifier verdict carries no parent snapshot binding",
                      "run classify with --parent-snapshot")

    parent_snap = Path(args.snapshots_dir) / f"s{depth - 1:02d}" \
        if isinstance(depth, int) else None
    if parent_snap is None or not parent_snap.is_dir():
        return refuse("parent snapshot absent")
    prec, perr = verify_snapshot(parent_snap)
    if perr:
        return refuse("parent snapshot unverifiable", perr)
    if prec.get("step_id") != parent_id:
        return refuse("parent snapshot step_id mismatch")
    if prec["snapshot_hash"] != cpsh:
        return refuse("classifier bound to a different parent snapshot",
                      "parent_snapshot_hash mismatch")

    # 2. THE terminal gate: the parent's latest chain decision must be
    #    CONTINUE_ELIGIBLE. Any STOP_* — including STOP_NO_PROGRESS and
    #    STOP_STEP_FAILED — refuses here. There is no override path.
    parent_entries = [e for e in entries
                      if e.get("chain_id") == chain_id
                      and e.get("step_id") == parent_id]
    if not parent_entries:
        return refuse("parent step has no ledger record")
    latest_parent = parent_entries[-1]  # ledger is append-only chronological
    if latest_parent.get("chain_decision") != "CONTINUE_ELIGIBLE":
        return refuse("STOP_IS_TERMINAL",
                      f"parent latest chain_decision="
                      f"{latest_parent.get('chain_decision')}; "
                      f"stop_reason={latest_parent.get('stop_reason')}")
    if pred not in latest_parent.get("predicates", {}).get("unsatisfied", []):
        return refuse("target predicate not unsatisfied on record")

    # 3. One child per parent: no existing token, no recorded child step.
    child_id = f"{chain_id}-s{depth:02d}"
    auth_dir = Path(args.write_auth)
    auth_dir.mkdir(parents=True, exist_ok=True)
    for tok in sorted(auth_dir.glob("DISPATCH_AUTH_*.json")):
        t = acc.load_json(tok)
        if isinstance(t, dict) and t.get("parent_step_id") == parent_id:
            return refuse("parent already has a dispatch token",
                          f"fan-out forbidden: {tok.name}")
    if any(e.get("chain_id") == chain_id and e.get("step_id") == child_id
           for e in entries):
        return refuse("child step already recorded", child_id)
    auth_path = auth_dir / f"DISPATCH_AUTH_s{depth:02d}.json"
    if auth_path.exists():
        return refuse("dispatch token already exists", auth_path.name)

    # 4. Issue the token, bound by hash to the exact input bytes.
    token = {
        "authority": AUTHORITY,
        "chain_id": chain_id,
        "step_id": child_id,
        "parent_step_id": parent_id,
        "parent_snapshot_hash": prec["snapshot_hash"],
        "proposal_hash": acc.sha256_file(Path(args.proposal)),
        "classification_hash": acc.sha256_file(Path(args.classification)),
        "authorized_at": utcnow_iso(),
    }
    auth_path.write_text(json.dumps(token, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")

    print(json.dumps({"authorized": True, "authority": AUTHORITY,
                      "token": str(auth_path), "step_id": child_id,
                      "parent_step_id": parent_id,
                      "parent_snapshot_hash": prec["snapshot_hash"],
                      "evaluated_at": token["authorized_at"]},
                     indent=2, sort_keys=True))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="issue a mechanical dispatch eligibility token")
    ap.add_argument("--contract", required=True)
    ap.add_argument("--proposal", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--classification", required=True,
                    help="AUTO_CONTINUE_0_CLASSIFICATION.json artifact")
    ap.add_argument("--snapshots-dir", required=True)
    ap.add_argument("--write-auth", required=True,
                    help="directory receiving DISPATCH_AUTH_sNN.json")
    return cmd_authorize(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
