#!/usr/bin/env python3
"""COMMITMENT-DISPATCH-0 mechanical dispatcher.

Bridges an already-authorized commitment (EXECUTION_AUTHORIZED under
COMMITMENT-EXECUTION-0) into the existing AUTO-WORK-0 / AUTO-CONTINUE-0
chain machinery. Deliberately boring: no reasoning, no reinterpretation
of the commitment, no new objective, no policy layer.

The dispatcher verifies, binds, and records. It does not choose.

Binding (all re-verified before launch; any mismatch -> REFUSE):
  commitment_id, need_id, acceptance_record_hash, completion_contract_hash,
  authority_class (passed through unchanged -- never translated),
  eligibility_artifact_hash, current_obligation_projection (must be OPEN),
  chain_bounds hash, dispatch_nonce.

Durable states per idempotency key (append-only state/commitment_dispatch.jsonl):
  (none) -> DISPATCH_INTENT -> DISPATCHED -> terminal (via OBLIGATION-0 projection)
  DISPATCH_REFUSED is supersedable by a fresh intent (new nonce);
  DISPATCHED and DISPATCH_BLOCKED are terminal for the key.
  A crash between INTENT and DISPATCHED reconciles (completes), never relaunches.

Supported authority classes: exactly the classes already demonstrated by the
existing AUTO-WORK-0 / AUTO-CONTINUE-0 path: {"local-build"}. Anything else
refuses; this implementation must not be used to generalize undeclared classes.

The dispatcher writes ONLY to state/commitment_dispatch.jsonl and to
dispatch/chains/<chain_id>/CHAIN_BINDING.json. It never mutates the
completion contract, the authority class, H, M, or K.

Deterministic: stdlib only. No network, no subprocess, no model calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "scripts"))
sys.path.insert(0, str(LIFE0_ROOT / "src"))

import execution_eligibility as elig_mod  # noqa: E402
import select_obligation as select_mod  # noqa: E402
from classify_auto_work_0 import classify as auto_work_classify  # noqa: E402
from life0.gate import FORBIDDEN_TARGETS  # noqa: E402  (single source: pulse gate list)
from project_obligations import Projector  # noqa: E402

POLICY_VERSION = "commitment-dispatch-0"

# Authority classes the existing AUTO-WORK-0 / AUTO-CONTINUE-0 path has
# demonstrated. local-build ran live (LIFE-PRESSURE-002 chain, 2026-10-01).
# This set is closed by this record; widening it is an authority-class
# expansion and needs the human.
SUPPORTED_AUTHORITY_CLASSES = ("local-build",)

# Frozen AUTO-CONTINUE-0 chain bounds (AUTO_CONTINUE_0.md). Bound by hash
# into every dispatch; a swapped dispatcher config between intent and
# launch is detected as a mismatch and refused.
CHAIN_BOUNDS = {
    "max_depth": 5,
    "fan_out": 1,
    "same_need_required": True,
    "contract_immutable": True,
    "budget_immutable": True,
    "retry": "contract_gated",
}

DISPATCH_LEDGER = "state/commitment_dispatch.jsonl"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def h(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_for(commitment_id: str, need_id: str) -> str:
    return f"commitment-dispatch:{commitment_id}:{need_id}"


def chain_id_for(key: str) -> str:
    return "chain_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def append_record(life0: Path, record: dict) -> None:
    path = life0 / DISPATCH_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def fold_dispatch(life0: Path) -> dict:
    """Latest dispatch record per idempotency key."""
    path = life0 / DISPATCH_LEDGER
    latest: dict = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        k = r.get("key")
        if k:
            latest[k] = r
    return latest


def commitment_record(life0: Path, commitment_id: str) -> dict | None:
    path = life0 / "state" / "commitments.jsonl"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("event") == "COMMITMENT_ADMITTED":
            c = r.get("commitment") or {}
            if c.get("commitment_id") == commitment_id:
                return c
    return None


def need_commitment_id(life0: Path, need_id: str) -> str | None:
    path = life0 / "state" / "needs.jsonl"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("event") == "NEED_CREATED":
            n = r.get("need") or {}
            if n.get("need_id") == need_id:
                return n.get("commitment_id")
    return None


def projection_state(life0: Path, need_id: str) -> str | None:
    proj = Projector(str(life0)).project()
    o = (proj.get("obligations") or {}).get(need_id)
    return o.get("state") if o else None


def compute_binding(life0: Path, need_id: str, commitment_id: str) -> tuple[dict | None, str]:
    """Verify eligibility and compute the full binding. Returns (binding, error)."""
    verdict = elig_mod.check(life0, need_id)
    if not verdict.get("eligible"):
        return None, ("execution eligibility is not EXECUTION_AUTHORIZED: "
                      + verdict.get("blocking_condition", "unknown"))

    commitment = commitment_record(life0, commitment_id)
    if commitment is None:
        return None, f"no COMMITMENT_ADMITTED record for {commitment_id}"

    authority_class = commitment.get("authority_class")
    if authority_class not in SUPPORTED_AUTHORITY_CLASSES:
        return None, (f"authority class {authority_class!r} is not supported by "
                      f"COMMITMENT-DISPATCH-0 (supported: {list(SUPPORTED_AUTHORITY_CLASSES)})")

    state = projection_state(life0, need_id)
    if state != "OPEN":
        return None, f"obligation projects {state}, not OPEN"

    binding = {
        "policy_version": POLICY_VERSION,
        "commitment_id": commitment_id,
        "need_id": need_id,
        "acceptance_record_hash": h(commitment),
        "completion_contract_hash": h(commitment.get("completion_contract") or {}),
        "authority_class": authority_class,  # passed through unchanged; never translated
        "eligibility_artifact_hash": h(verdict),
        "eligibility_verdict": verdict.get("verdict"),
        "current_obligation_projection": state,
        "chain_bounds": dict(CHAIN_BOUNDS),
        "chain_bounds_hash": h(CHAIN_BOUNDS),
        "dispatch_nonce": secrets.token_hex(16),
    }
    return binding, ""


def bind(life0: Path, need_id: str) -> dict:
    """Write DISPATCH_INTENT for one need. Verifies eligibility and bindings."""
    commitment_id = need_commitment_id(life0, need_id)
    if not commitment_id:
        return {"bound": False, "need_id": need_id,
                "reason": "not a commitment-origin need (no commitment_id on NEED_CREATED)"}
    key = key_for(commitment_id, need_id)
    latest = fold_dispatch(life0).get(key)
    if latest and latest.get("event") == "DISPATCHED":
        return {"bound": False, "need_id": need_id, "key": key,
                "reason": "already DISPATCHED (idempotency key terminal)"}
    if latest and latest.get("event") == "DISPATCH_BLOCKED":
        return {"bound": False, "need_id": need_id, "key": key,
                "reason": "already DISPATCH_BLOCKED (terminal for this key)"}
    if latest and latest.get("event") == "DISPATCH_INTENT":
        return {"bound": False, "need_id": need_id, "key": key,
                "reason": "open DISPATCH_INTENT already exists; launch or reconcile it"}

    binding, err = compute_binding(life0, need_id, commitment_id)
    if binding is None:
        return {"bound": False, "need_id": need_id, "key": key, "reason": err}

    record = {"event": "DISPATCH_INTENT", "at": utcnow_iso(), "key": key,
              "need_id": need_id, "commitment_id": commitment_id,
              "binding": binding}
    append_record(life0, record)
    return {"bound": True, "need_id": need_id, "key": key,
            "commitment_id": commitment_id, "nonce": binding["dispatch_nonce"]}


def _refuse(life0: Path, key: str, need_id: str, commitment_id: str | None,
            reason: str) -> dict:
    record = {"event": "DISPATCH_REFUSED", "at": utcnow_iso(), "key": key,
              "need_id": need_id, "commitment_id": commitment_id, "reason": reason}
    append_record(life0, record)
    return {"launched": False, "verdict": "REFUSE", "need_id": need_id,
            "key": key, "reason": reason}


def launch(life0: Path, need_id: str) -> dict:
    """Complete an open DISPATCH_INTENT: re-verify everything, run the
    AUTO-WORK-0 entry gate, and on ELIGIBLE write DISPATCHED."""
    commitment_id = need_commitment_id(life0, need_id)
    if not commitment_id:
        return {"launched": False, "verdict": "REFUSE", "need_id": need_id,
                "reason": "not a commitment-origin need"}
    key = key_for(commitment_id, need_id)
    latest = fold_dispatch(life0).get(key)
    if not latest or latest.get("event") != "DISPATCH_INTENT":
        return {"launched": False, "verdict": "REFUSE", "need_id": need_id,
                "key": key, "reason": "no open DISPATCH_INTENT for this key"}

    intent_binding = latest.get("binding") or {}

    # Re-verify every bound field. Any mismatch -> REFUSE.
    commitment = commitment_record(life0, commitment_id)
    if commitment is None:
        return _refuse(life0, key, need_id, commitment_id,
                       "commitment record vanished between intent and launch")
    if h(commitment) != intent_binding.get("acceptance_record_hash"):
        return _refuse(life0, key, need_id, commitment_id,
                       "acceptance_record_hash mismatch: commitment record changed since intent")
    if h(commitment.get("completion_contract") or {}) != intent_binding.get("completion_contract_hash"):
        return _refuse(life0, key, need_id, commitment_id,
                       "completion_contract_hash mismatch: completion contract changed since intent")
    if commitment.get("authority_class") != intent_binding.get("authority_class"):
        return _refuse(life0, key, need_id, commitment_id,
                       "authority_class mismatch since intent")
    if intent_binding.get("authority_class") not in SUPPORTED_AUTHORITY_CLASSES:
        return _refuse(life0, key, need_id, commitment_id,
                       "authority class not supported by COMMITMENT-DISPATCH-0")
    if h(CHAIN_BOUNDS) != intent_binding.get("chain_bounds_hash"):
        return _refuse(life0, key, need_id, commitment_id,
                       "chain_bounds_hash mismatch: dispatcher bounds changed since intent")

    verdict = elig_mod.check(life0, need_id)
    if not verdict.get("eligible"):
        return _refuse(life0, key, need_id, commitment_id,
                       "execution eligibility revoked before dispatch: "
                       + verdict.get("blocking_condition", "unknown"))
    if h(verdict) != intent_binding.get("eligibility_artifact_hash"):
        return _refuse(life0, key, need_id, commitment_id,
                       "eligibility_artifact_hash mismatch since intent")

    state = projection_state(life0, need_id)
    if state != "OPEN":
        return _refuse(life0, key, need_id, commitment_id,
                       f"obligation projects {state}, not OPEN")

    # AUTO-WORK-0 entry gate (existing machinery, read-only).
    pkg_dir = life0 / "dispatch" / "staged" / need_id
    aw = auto_work_classify(pkg_dir)
    aw_verdict = aw.get("verdict")
    if aw_verdict == "UNUSABLE":
        return _refuse(life0, key, need_id, commitment_id,
                       f"AUTO-WORK-0 classifier unusable: {aw.get('error')}")
    if aw_verdict != "ELIGIBLE":
        record = {"event": "DISPATCH_BLOCKED", "at": utcnow_iso(), "key": key,
                  "need_id": need_id, "commitment_id": commitment_id,
                  "reason": "AUTO-WORK-0 entry gate INELIGIBLE",
                  "failed_criteria": aw.get("failed_criteria", [])}
        append_record(life0, record)
        return {"launched": False, "verdict": "BLOCKED", "need_id": need_id,
                "key": key, "reason": "AUTO-WORK-0 entry gate INELIGIBLE",
                "failed_criteria": aw.get("failed_criteria", [])}

    # All green: instantiate the chain.
    chain_id = chain_id_for(key)
    chain_dir = life0 / "dispatch" / "chains" / chain_id
    chain_dir.mkdir(parents=True, exist_ok=True)
    binding = dict(intent_binding)
    binding["auto_work_verdict"] = aw_verdict
    binding["auto_work_criteria_hash"] = h([
        {"id": c.get("id"), "passed": c.get("passed")}
        for c in aw.get("criteria", [])])
    binding["chain_id"] = chain_id
    binding_path = chain_dir / "CHAIN_BINDING.json"
    tmp = binding_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.rename(tmp, binding_path)

    record = {"event": "DISPATCHED", "at": utcnow_iso(), "key": key,
              "need_id": need_id, "commitment_id": commitment_id,
              "chain_id": chain_id, "nonce": intent_binding.get("dispatch_nonce"),
              "binding_hash": h(binding)}
    append_record(life0, record)
    return {"launched": True, "verdict": "DISPATCHED", "need_id": need_id,
            "key": key, "commitment_id": commitment_id, "chain_id": chain_id,
            "chain_binding": str(binding_path),
            "authority_class": binding["authority_class"]}


def dispatch(life0: Path, need_id: str) -> dict:
    """Drive one need's dispatch state forward exactly one step.

    The dispatcher receives exactly one selected commitment; it never
    ranks or fans out.
    """
    # A terminal obligation never dispatches, regardless of ledger state.
    state = projection_state(life0, need_id)
    if state is not None and state != "OPEN":
        return {"verdict": "OBLIGATION_TERMINAL", "need_id": need_id,
                "obligation_state": state}

    commitment_id = need_commitment_id(life0, need_id)
    key = key_for(commitment_id, need_id) if commitment_id else None
    latest = fold_dispatch(life0).get(key) if key else None
    event = (latest or {}).get("event")

    if event == "DISPATCHED":
        return {"verdict": "ALREADY_DISPATCHED", "need_id": need_id, "key": key,
                "commitment_id": commitment_id,
                "chain_id": latest.get("chain_id"), "dispatched_now": False}
    if event == "DISPATCH_BLOCKED":
        return {"verdict": "ALREADY_BLOCKED", "need_id": need_id, "key": key,
                "dispatched_now": False}
    if event == "DISPATCH_INTENT":
        # Crash recovery: complete the open intent, never relaunch.
        out = launch(life0, need_id)
        out["dispatched_now"] = out.get("verdict") == "DISPATCHED"
        out["recovered"] = True
        return out
    # No record, or a supersedable REFUSED: bind, then launch.
    b = bind(life0, need_id)
    if not b.get("bound"):
        return {"verdict": "REFUSE", "need_id": need_id,
                "key": b.get("key"), "reason": b.get("reason"),
                "dispatched_now": False}
    out = launch(life0, need_id)
    out["dispatched_now"] = out.get("verdict") == "DISPATCHED"
    return out


def reconcile(life0: Path) -> dict:
    """Recovery path: select the one deterministic eligible obligation and
    drive its dispatch forward. Uses the existing selector unchanged."""
    sel = select_mod.select(life0)
    if sel.get("verdict") != "SELECTED":
        return {"verdict": "NO_ELIGIBLE_OBLIGATION",
                "selector": sel.get("verdict")}
    need_id = sel["selected"]["need_id"]
    out = dispatch(life0, need_id)
    out["selected"] = sel["selected"]
    return out


# ---------------------------------------------------------------------------
# Chain-contract materialization (deterministic derivation for the executor).
# ---------------------------------------------------------------------------

# Frozen AUTO-CONTINUE-0 vocabularies/policies, copied from the frozen
# LIFE-PRESSURE-002 contract (the demonstrated instance). Not chosen per
# commitment; identical for every commitment chain.
_FROZEN_GAP_VOCABULARY = [
    "required_evidence_reference_missing",
    "required_schema_field_absent",
    "validation_failed",
    "cited_local_artifact_unavailable",
    "specified_comparison_incomplete",
    "deterministic_checker_failed",
    "done_predicate_false",
]
_FROZEN_OPERATION_VOCABULARY = [
    "RETRIEVE_AUTHORIZED_LOCAL_EVIDENCE",
    "AMEND_DECISION_ARTIFACT",
    "RUN_DETERMINISTIC_VALIDATION",
    "REEVALUATE_COMPLETION_CONTRACT",
]
_FROZEN_GAP_POLICY = [
    {"gap": "required_evidence_reference_missing",
     "operation": "RETRIEVE_AUTHORIZED_LOCAL_EVIDENCE", "predicate": None},
    {"gap": "required_schema_field_absent",
     "operation": "AMEND_DECISION_ARTIFACT", "predicate": None},
    {"gap": "validation_failed",
     "operation": "AMEND_DECISION_ARTIFACT", "predicate": None},
    {"gap": "cited_local_artifact_unavailable",
     "operation": "RETRIEVE_AUTHORIZED_LOCAL_EVIDENCE", "predicate": None},
    {"gap": "specified_comparison_incomplete",
     "operation": "AMEND_DECISION_ARTIFACT", "predicate": None},
    {"gap": "deterministic_checker_failed",
     "operation": "RUN_DETERMINISTIC_VALIDATION", "predicate": None},
    {"gap": "done_predicate_false",
     "operation": "REEVALUATE_COMPLETION_CONTRACT", "predicate": None},
]
_FROZEN_BOUNDS = {
    "all_done_predicates_true": "STOP",
    "ambiguous_necessity": "STOP",
    "external_effects": "forbidden",
    "h_mutation": "forbidden",
    "k_mutation": "forbidden",
    "m_mutation": "forbidden",
    "new_capability": "forbidden",
    "promotion": "forbidden",
    "semantic_expansion": "STOP",
}


def materialize_chain_contract(binding: dict) -> dict:
    """Deterministically derive the AUTO-CONTINUE-0 chain contract from a
    dispatch binding. No judgment: bounds/vocabularies are the frozen
    AUTO-CONTINUE-0 constants; done_predicates are the commitment's frozen
    completion contract, verbatim."""
    commitment_id = binding["commitment_id"]
    contract = {
        "contract_id": "chain-contract-" + commitment_id.replace("commit_", ""),
        "chain_id": binding["chain_id"],
        "originating_need_id": binding["need_id"],
        "authority_class": binding["authority_class"],
        "max_depth": CHAIN_BOUNDS["max_depth"],
        "fan_out": CHAIN_BOUNDS["fan_out"],
        "bounds": dict(_FROZEN_BOUNDS),
        "chain_budget_mutable": False,
        "completion_contract_mutable": False,
        "decision_schema": {
            "allowed_outcomes": ["CONTINUE_ELIGIBLE", "STOP_GOAL_SATISFIED",
                                 "STOP_MAX_DEPTH", "STOP_NO_PROGRESS",
                                 "STOP_STEP_FAILED", "STOP_UNAUTHORIZED_DISPATCH"],
            "artifact": "decision artifact under the chain work dir",
            "credit_must_equal": "NONE",
            "required_fields": ["outcome", "predicates", "evidence"],
        },
        "done_predicates": [
            {"id": p["id"], "verifiable": p["verifiable"]}
            for p in (binding.get("completion_contract_predicates") or [])
        ],
        "gap_policy": [dict(g) for g in _FROZEN_GAP_POLICY],
        "gap_vocabulary": list(_FROZEN_GAP_VOCABULARY),
        "operation_vocabulary": list(_FROZEN_OPERATION_VOCABULARY),
        "progress_invariant": ("|D_{n+1}^{unsatisfied}| < |D_n^{unsatisfied}| "
                               "or concrete blocker against an existing d_i; "
                               "D never enlarges"),
        "retry_policy": {"permitted_classes": []},
        "status": "FROZEN",
        "frozen_at": utcnow_iso(),
        "frozen_by": "commitment-dispatch-0 materialization",
        "origin": f"derived from {commitment_id} acceptance; bounds/vocabularies are frozen AUTO-CONTINUE-0 constants",
    }
    contract["frozen_hash"] = hashlib.sha256(canonical(
        {k: v for k, v in contract.items() if k != "frozen_hash"})).hexdigest()
    return contract


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="COMMITMENT-DISPATCH-0 mechanical dispatcher")
    ap.add_argument("--life0", required=True, help="life-0 directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dispatch", help="drive one need's dispatch forward one step")
    d.add_argument("--need", required=True)

    sub.add_parser("reconcile", help="recovery: select the deterministic eligible obligation and drive it")

    m = sub.add_parser("materialize-contract",
                       help="derive the AUTO-CONTINUE-0 chain contract from a CHAIN_BINDING.json")
    m.add_argument("--binding", required=True)
    m.add_argument("--out", required=True)

    args = ap.parse_args(argv)
    life0 = Path(args.life0)

    if args.cmd == "dispatch":
        out = dispatch(life0, args.need)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("verdict") in ("DISPATCHED", "ALREADY_DISPATCHED") else 2
    if args.cmd == "reconcile":
        out = reconcile(life0)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("verdict") in ("DISPATCHED", "ALREADY_DISPATCHED",
                                           "NO_ELIGIBLE_OBLIGATION") else 2
    if args.cmd == "materialize-contract":
        binding = json.loads(Path(args.binding).read_text(encoding="utf-8"))
        # The executor passes the predicates through the binding file; the
        # dispatcher module reads them from the commitment record for safety.
        if not binding.get("completion_contract_predicates"):
            c = commitment_record(life0, binding["commitment_id"]) or {}
            binding["completion_contract_predicates"] = (
                (c.get("completion_contract") or {}).get("predicates") or [])
        contract = materialize_chain_contract(binding)
        outp = Path(args.out)
        tmp = outp.with_suffix(".tmp")
        tmp.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        os.rename(tmp, outp)
        print(json.dumps({"materialized": str(outp),
                          "frozen_hash": contract["frozen_hash"],
                          "done_predicates": len(contract["done_predicates"])},
                         indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
