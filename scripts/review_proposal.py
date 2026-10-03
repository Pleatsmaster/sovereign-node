#!/usr/bin/env python3
"""AGENDA-PROPOSAL-0 human review: list, show, decide.

Decisions are the operator's acts:
  REJECTED — with a structured reason from the frozen enum (first-class
             evidence for why self-originated objectives die);
  ACCEPTED — builds the acceptance record from the proposal and runs it
             through admit_commitment.py unchanged (accepted_by="operator").

Only PROPOSED entries are decidable. The script never proposes, never
executes, never touches the projector.

Exit codes: 0 = ok; 2 = refused / failure. Reasons are JSON on stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "scripts"))

import agenda_common as ac  # noqa: E402
import admit_commitment  # noqa: E402

SCOPE_MAX_CHARS = 500


def _proposal_or_fail(life0: Path, proposal_id: str) -> tuple[dict, dict]:
    ledger = ac.load_proposal_ledger(life0)
    entry = ledger.get(proposal_id)
    if entry is None:
        return None, {"decided": False, "reason": "PROPOSAL_NOT_FOUND"}
    if entry["state"] != "PROPOSED":
        return None, {"decided": False, "reason": "PROPOSAL_NOT_LIVE",
                      "state": entry["state"]}
    return entry, None


def list_proposals(life0: Path, state: str | None) -> list[dict]:
    out = []
    for pid, entry in ac.load_proposal_ledger(life0).items():
        if state is None or entry["state"] == state:
            p = entry["proposal"]
            out.append({
                "proposal_id": pid,
                "state": entry["state"],
                "condition_set_hash": p.get("condition_set_hash"),
                "objective": p.get("objective"),
                "proposed_at": p.get("proposed_at"),
                "decision": entry.get("decision"),
            })
    out.sort(key=lambda e: e.get("proposed_at") or "")
    return out


def decide_rejected(life0: Path, proposal_id: str, reason: str,
                    note: str) -> dict:
    if reason not in ac.REJECTION_REASONS:
        return {"decided": False, "reason": "UNKNOWN_REJECTION_REASON",
                "supported": list(ac.REJECTION_REASONS)}
    entry, failure = _proposal_or_fail(life0, proposal_id)
    if failure is not None:
        return failure
    assert entry is not None
    ac.append_jsonl(
        life0 / "state" / "agenda_proposals.jsonl",
        {"event": "PROPOSAL_DECIDED", "at": ac.utcnow_iso(),
         "proposal_id": proposal_id, "verdict": "REJECTED",
         "reason": reason, "note": note or "",
         "decided_by": "operator",
         "policy_version": ac.POLICY_VERSION})
    return {"decided": True, "proposal_id": proposal_id,
            "verdict": "REJECTED", "reason": reason}


def _acceptance_for(proposal: dict, proposal_id: str) -> dict:
    conds = ", ".join(proposal["source_conditions"])
    scope = (f"Agenda proposal {proposal_id}: "
             f"{proposal['objective'][:160]}. Observed conditions: {conds}")
    if len(scope) > SCOPE_MAX_CHARS:
        scope = scope[:SCOPE_MAX_CHARS]
    return {
        "objective": proposal["objective"],
        "completion_contract": {
            "predicates": [
                {"id": p["id"], "verifiable": p["verifiable"]}
                for p in proposal["completion_contract"]["predicates"]
            ]
        },
        "authority_class": proposal["required_authority_class"],
        "scope": scope,
        "accepted_by": "operator",
        "accepted_at": ac.utcnow_iso(),
        "origin": f"agenda-proposal:{proposal_id}",
        "parent_commitment_id": None,
    }


def decide_accepted(life0: Path, proposal_id: str, note: str) -> dict:
    entry, failure = _proposal_or_fail(life0, proposal_id)
    if failure is not None:
        return failure
    assert entry is not None
    proposal = entry["proposal"]

    # The human's acceptance runs through the unchanged admission gate.
    acc = _acceptance_for(proposal, proposal_id)
    result = admit_commitment.admit(life0, acc)
    if not result.get("admitted"):
        return {"decided": False, "reason": "ADMISSION_REFUSED",
                "admission_reason": result.get("reason")}

    ac.append_jsonl(
        life0 / "state" / "agenda_proposals.jsonl",
        {"event": "PROPOSAL_DECIDED", "at": ac.utcnow_iso(),
         "proposal_id": proposal_id, "verdict": "ACCEPTED",
         "note": note or "", "decided_by": "operator",
         "commitment_id": result["commitment_id"],
         "need_id": result["need_id"],
         "policy_version": ac.POLICY_VERSION})
    return {"decided": True, "proposal_id": proposal_id,
            "verdict": "ACCEPTED",
            "commitment_id": result["commitment_id"],
            "need_id": result["need_id"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="AGENDA-PROPOSAL-0 human review of candidate objectives.")
    ap.add_argument("--life0", default=str(LIFE0_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list proposals")
    p_list.add_argument("--state", default=None,
                        choices=["PROPOSED", "ACCEPTED", "REJECTED",
                                 "EXPIRED"])

    p_show = sub.add_parser("show", help="show one proposal")
    p_show.add_argument("--proposal-id", required=True)

    p_dec = sub.add_parser("decide", help="accept or reject a proposal")
    p_dec.add_argument("--proposal-id", required=True)
    p_dec.add_argument("--verdict", required=True,
                       choices=["ACCEPTED", "REJECTED"])
    p_dec.add_argument("--reason", default=None,
                       help="required for REJECTED; one of the frozen enum")
    p_dec.add_argument("--note", default="")

    args = ap.parse_args(argv)
    life0 = Path(args.life0)
    try:
        if args.cmd == "list":
            print(json.dumps(list_proposals(life0, args.state), indent=2,
                             sort_keys=True))
            return 0
        if args.cmd == "show":
            entry = ac.load_proposal_ledger(life0).get(args.proposal_id)
            if entry is None:
                print(json.dumps({"found": False,
                                  "reason": "PROPOSAL_NOT_FOUND"}))
                return 2
            print(json.dumps(entry, indent=2, sort_keys=True))
            return 0
        if args.cmd == "decide":
            if args.verdict == "REJECTED":
                if not args.reason:
                    print(json.dumps({"decided": False,
                                      "reason": "REJECTION_REASON_REQUIRED",
                                      "supported": list(ac.REJECTION_REASONS)}))
                    return 2
                result = decide_rejected(life0, args.proposal_id,
                                         args.reason, args.note)
            else:
                result = decide_accepted(life0, args.proposal_id, args.note)
            print(json.dumps(result, sort_keys=True))
            return 0 if result.get("decided") else 2
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
