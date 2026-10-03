#!/usr/bin/env python3
"""AUTO-CONTINUE-0 mechanical classifier.

Determines whether a proposed continuation step may be dispatched under
the AUTO-CONTINUE-0 standing authority (temporal extension of
AUTO-WORK-0; no authority expansion).

Two subcommands:

  classify  --contract C --proposal P --ledger L
      Pre-dispatch check of a continuation proposal. Verifies the frozen
      chain contract (hash), the proposal's mechanical form, vocabulary
      membership, depth/bounds/fan-out, parent completion, retry
      semantics, and that the targeted done predicate is currently
      unsatisfied. Writes nothing except with --write-verdict.

  evaluate  --contract C --package PKG --ledger L --step-id S \
            --parent-step-id P --depth N --worker-status completed|failed \
            --snapshot SNAP [--failure-class F] \
            [--dispatch-auth AUTH --snapshots-dir SD]   (depth >= 1)
      Post-step mechanical evaluation of the frozen completion contract
      D_0 against the step's PUBLISHED SNAPSHOT (never the live workspace).
      Without a verifiable STEP_COMPLETED.json the evaluator refuses and
      writes nothing. For depth >= 1 a dispatcher-issued eligibility token
      (--dispatch-auth) bound to the exact parent snapshot is required;
      without it the step is recorded STOP_UNAUTHORIZED_DISPATCH.
      Appends one entry to the cumulative chain ledger and reports the
      chain decision: CONTINUE_ELIGIBLE | STOP_GOAL_SATISFIED
      | STOP_NO_PROGRESS | STOP_STEP_FAILED | STOP_UNAUTHORIZED_DISPATCH.

  publish_step_snapshot.py and authorize_dispatch.py are companion scripts:
  the first closes the worker-output visibility race (atomic publish
  barrier), the second makes STOP mechanically irreversible (the
  dispatcher, not the agent's judgment, decides whether a next step may
  exist).

Deterministic: stdlib only. No network, no subprocess, no model calls.
Fail-closed: any criterion that cannot be verified -> INELIGIBLE / STOP.

Constitutional invariants enforced:
  - continuation only across mechanically observable gaps (vocabulary);
  - every step independently INTERNAL_ANALYSIS; bounds_changed=false;
  - frozen D_0; |D_{n+1}^{unsatisfied}| < |D_n^{unsatisfied}| or a
    concrete blocker; D never enlarged (proposal keys fixed);
  - one child per parent (no fan-out); depth <= max_depth;
  - failed steps retry only within contract retry_policy classes.

Exit codes: 0 = completed (verdict in the JSON output);
            2 = unusable input (contract/proposal/ledger/package unreadable).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Companion script: snapshot publication barrier (single source of truth
# for snapshot integrity verification).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_step_snapshot import verify_snapshot  # noqa: E402

AUTHORITY = "AUTO-CONTINUE-0"

PROPOSAL_FIELDS = (
    "originating_need_id",
    "parent_step_id",
    "chain_id",
    "current_depth",
    "unsatisfied_done_predicate",
    "observed_gap",
    "proposed_operation",
    "expected_postcondition",
    "authority_class",
    "bounds_changed",
)

# Proposal keys outside this set are rejected: the proposal cannot smuggle
# a new objective, a new predicate, or widened bounds.
EXPANSION_MARKERS = (
    "new_predicate", "new_objective", "additional_goal", "extend",
    "widen", "extra_",
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def verdict(ok: bool, reasons: list[str], extra: dict | None = None) -> dict:
    v = {
        "authority": AUTHORITY,
        "verdict": "ELIGIBLE" if ok else "INELIGIBLE",
        "reasons": reasons,
        "evaluated_at": utcnow_iso(),
    }
    if extra:
        v.update(extra)
    return v


def emit(v: dict, write_dir: Path | None, filename: str) -> None:
    if write_dir is not None:
        write_dir.mkdir(parents=True, exist_ok=True)
        (write_dir / filename).write_text(
            json.dumps(v, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    json.dump(v, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


# ---------------------------------------------------------------- contract

def load_contract(path: Path):
    """Returns (contract, error). Verifies frozen hash."""
    c = load_json(path)
    if c is None:
        return None, "contract unreadable"
    if c.get("status") != "FROZEN":
        return None, "contract status is not FROZEN"
    frozen = c.get("frozen_hash")
    if not isinstance(frozen, str):
        return None, "contract carries no frozen_hash"
    body = {k: v for k, v in c.items() if k != "frozen_hash"}
    if hashlib.sha256(canonical(body)).hexdigest() != frozen:
        return None, "contract frozen_hash does not verify"
    return c, None


def load_ledger(path: Path):
    """Returns (entries, error). Missing ledger -> ([], None) only when
    explicitly allowed by the caller; classify requires it to exist."""
    if not path.exists():
        return None, "ledger not found"
    entries = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            return None, f"ledger line {i} is not valid JSON"
    return entries, None


def latest_evaluation(entries: list[dict], chain_id: str):
    for e in reversed(entries):
        if e.get("chain_id") == chain_id and "predicates" in e:
            return e
    return None


# ---------------------------------------------------------------- classify

def cmd_classify(args) -> int:
    reasons: list[str] = []
    contract, err = load_contract(Path(args.contract))
    if err:
        emit(verdict(False, [err]), None, "")
        return 2
    proposal = load_json(Path(args.proposal))
    if proposal is None:
        emit(verdict(False, ["proposal unreadable"]), None, "")
        return 2
    entries, err = load_ledger(Path(args.ledger))
    if err:
        emit(verdict(False, [err]), None, "")
        return 2

    def deny(r: str):
        reasons.append(r)

    # 1. Fixed proposal form. No prose-justification field, no expansion keys.
    if not isinstance(proposal, dict):
        deny("proposal is not an object")
    else:
        missing = [f for f in PROPOSAL_FIELDS if f not in proposal]
        if missing:
            deny(f"proposal missing fields: {missing}")
        extra_keys = [k for k in proposal if k not in PROPOSAL_FIELDS]
        if extra_keys:
            deny(f"proposal carries non-mechanical keys: {extra_keys}")
        low_keys = " ".join(str(k).lower() for k in proposal)
        if any(m in low_keys for m in EXPANSION_MARKERS):
            deny("proposal key suggests objective expansion")

    chain_id = proposal.get("chain_id")
    if chain_id != contract.get("chain_id"):
        deny("chain_id does not match frozen contract")
    if proposal.get("originating_need_id") != contract.get("originating_need_id"):
        deny("originating_need_id does not match frozen contract")

    # 2. Same authority class, independently; bounds immutable.
    if proposal.get("authority_class") != "INTERNAL_ANALYSIS":
        deny("authority_class is not INTERNAL_ANALYSIS")
    if proposal.get("authority_class") != contract.get("authority_class"):
        deny("authority_class differs from frozen contract")
    if proposal.get("bounds_changed") is not False:
        deny("bounds_changed must be false")

    # 3. Depth: 1..max_depth, monotone from ledger.
    depth = proposal.get("current_depth")
    max_depth = contract.get("max_depth")
    if not isinstance(depth, int) or not isinstance(max_depth, int):
        deny("current_depth/max_depth not integers")
    else:
        if not (1 <= depth <= max_depth):
            deny(f"current_depth {depth} outside [1, {max_depth}]")
        chain_entries = [e for e in entries if e.get("chain_id") == chain_id]
        max_seen = max([e.get("depth", -1) for e in chain_entries], default=-1)
        if max_seen != depth - 1:
            deny(f"depth not monotone: ledger max depth {max_seen}, "
                 f"proposal depth {depth}")

    # 4. Vocabulary membership; operation bound to the predicate's policy.
    pred = proposal.get("unsatisfied_done_predicate")
    pred_ids = [d.get("id") for d in contract.get("done_predicates", [])]
    if pred not in pred_ids:
        deny(f"unsatisfied_done_predicate {pred!r} not in frozen D_0")
    if proposal.get("observed_gap") not in contract.get("gap_vocabulary", []):
        deny(f"observed_gap {proposal.get('observed_gap')!r} not in gap vocabulary")
    op = proposal.get("proposed_operation")
    if op not in contract.get("operation_vocabulary", []):
        deny(f"proposed_operation {op!r} not in operation vocabulary")
    policy_ops = {
        g.get("operation")
        for g in contract.get("gap_policy", [])
        if g.get("predicate") == pred
    }
    if pred in pred_ids and op not in policy_ops:
        deny(f"operation {op!r} not permitted by gap policy for {pred}")

    # 5. Expected postcondition must name the targeted predicate.
    epc = proposal.get("expected_postcondition")
    if not isinstance(epc, str) or not epc.strip():
        deny("expected_postcondition empty")
    elif isinstance(pred, str) and pred not in epc:
        deny("expected_postcondition does not name the targeted predicate")

    # 6. Targeted predicate must be currently unsatisfied (mechanical).
    latest = latest_evaluation(entries, chain_id) if isinstance(chain_id, str) else None
    if latest is None:
        deny("no predicate evaluation on record for this chain")
    elif pred in pred_ids and pred not in latest["predicates"].get("unsatisfied", []):
        deny(f"predicate {pred} is not unsatisfied on record")

    # 7. Parent step completed; retry only within permitted classes.
    parent_id = proposal.get("parent_step_id")
    parent = next(
        (e for e in entries
         if e.get("chain_id") == chain_id and e.get("step_id") == parent_id),
        None,
    )
    if parent is None:
        deny(f"parent step {parent_id!r} has no ledger record")
    else:
        if parent.get("worker_status") == "failed":
            permitted = contract.get("retry_policy", {}).get("permitted_classes", [])
            if parent.get("failure_class") not in permitted:
                deny("parent step failed and its failure class is not a "
                     "permitted retry class: chain terminates")
        elif parent.get("worker_status") != "completed":
            deny("parent step has no completed/failed outcome on record")

    # 8. No fan-out: this parent has no child yet; this step id unused.
    if isinstance(chain_id, str) and isinstance(parent_id, str):
        siblings = [
            e for e in entries
            if e.get("chain_id") == chain_id
            and e.get("parent_step_id") == parent_id
        ]
        if siblings:
            deny("parent already has a continuation: fan-out forbidden")
        child_id = f"{chain_id}-s{depth:02d}" if isinstance(depth, int) else None
        if child_id and any(
            e.get("chain_id") == chain_id and e.get("step_id") == child_id
            for e in entries
        ):
            deny(f"step id {child_id} already recorded")

    # 9. Parent-snapshot binding. When --parent-snapshot is given, the
    # verdict is bound to the exact published snapshot the dispatcher
    # must later require; an unverifiable snapshot fails the proposal.
    parent_snapshot_hash = None
    if args.parent_snapshot:
        rec, serr = verify_snapshot(Path(args.parent_snapshot))
        if serr:
            deny(f"parent snapshot unverifiable: {serr}")
        elif rec.get("step_id") != parent_id:
            deny("parent snapshot step_id does not match proposal "
                 "parent_step_id")
        else:
            parent_snapshot_hash = rec["snapshot_hash"]

    ok = not reasons
    extra = {"chain_id": chain_id, "proposal_depth": depth,
             "target_predicate": pred, "parent_step_id": parent_id,
             "parent_snapshot_hash": parent_snapshot_hash} if ok else None
    out_path = Path(args.write_verdict) if args.write_verdict else None
    emit(verdict(ok, reasons, extra), out_path,
         "AUTO_CONTINUE_0_CLASSIFICATION.json")
    return 0


# ---------------------------------------------------------------- evaluate

ALLOWED_PACKAGE_FILES = {
    "MISSION_PACKAGE.json",
    "OBJECTIVE.md",
    "STAGE_RECORD.json",
    "AUTO_WORK_0_CLASSIFICATION.json",
    "AUTO_WORK_0_DISPATCH.json",
    "AUTO_CONTINUE_0_CLASSIFICATION.json",
    "CHAIN_LEDGER.jsonl",
    "work/EXPERIENCE_COMPRESSION_DECISION.json",
    "work/RATIONALE.md",
}


def is_proposal_file(rel: str) -> bool:
    """CHAIN_PROPOSAL_sNN.json — mechanical continuation proposals, additive."""
    base = rel.rsplit("/", 1)[-1]
    return (base.startswith("CHAIN_PROPOSAL_s") and base.endswith(".json")
            and base[len("CHAIN_PROPOSAL_s"):-len(".json")].isdigit())


def resolve_source(ref: str, package: Path, life0: Path, workspace: Path):
    p = Path(ref)
    if p.is_absolute():
        try:
            p.relative_to(workspace)
        except ValueError:
            return None
        return p if p.is_file() else None
    for base in (package, life0, workspace):
        cand = base / ref
        if cand.is_file():
            return cand
    return None


def evaluate_predicates(contract: dict, package: Path,
                        decision_path: Path, rationale_path: Path):
    """Assess D_0 against a PUBLISHED SNAPSHOT's artifacts.

    decision_path / rationale_path point inside the snapshot directory,
    never the live workspace. The package dir is still scanned for d5
    (package integrity) and used as a resolve base for d4.
    """
    schema = contract.get("decision_schema", {})
    allowed = set(schema.get("allowed_outcomes", []))
    required = schema.get("required_fields", [])
    credit_must = schema.get("credit_must_equal", "NONE")

    decision = load_json(decision_path) if decision_path.is_file() else None

    life0 = package.parent.parent.parent  # dispatch/staged/<need> -> life-0
    workspace = Path.home() / "workspace"

    sat, unsat = [], []

    def check(pid: str, cond: bool):
        (sat if cond else unsat).append(pid)

    # d1: exactly one allowed top-level outcome
    check("d1_outcome_single_allowed",
          isinstance(decision, dict)
          and isinstance(decision.get("outcome"), str)
          and decision["outcome"] in allowed)
    # d2: decision file exists and validates
    check("d2_decision_validates", isinstance(decision, dict))
    # d3: required fields exist
    check("d3_required_fields",
          isinstance(decision, dict)
          and all(f in decision for f in required))
    # d4: source claims resolve to existing history
    if isinstance(decision, dict) and isinstance(decision.get("source_claims"), list):
        check("d4_sources_resolve",
              all(isinstance(r, str) and resolve_source(r, package, life0, workspace)
                  for r in decision["source_claims"]))
    else:
        check("d4_sources_resolve",
              isinstance(decision, dict)
              and not isinstance(decision.get("source_claims"), list)
              and "source_claims" not in (decision or {}))
        # absent source_claims with a decision present counts as vacuous only
        # when the key is missing entirely; a present non-list fails d3 first.
        if isinstance(decision, dict) and "source_claims" not in decision:
            pass
    # d5: no forbidden mutation — package contains only allowlisted files
    if package.is_dir():
        found = set()
        for f in package.rglob("*"):
            if f.is_file():
                try:
                    found.add(str(f.relative_to(package)))
                except ValueError:
                    found.add(f.name)
        check("d5_no_forbidden_mutation",
              all(f in ALLOWED_PACKAGE_FILES or is_proposal_file(f)
                  for f in found))
    else:
        check("d5_no_forbidden_mutation", False)
    # d6: rationale exists and non-empty
    check("d6_rationale_exists",
          rationale_path.is_file() and rationale_path.stat().st_size > 0)
    # d7: promotion remains false
    check("d7_no_promotion",
          isinstance(decision, dict) and decision.get("promoted") is not True
          and decision.get("promotion") is not True)
    # d8: credit remains NONE
    check("d8_credit_none",
          isinstance(decision, dict)
          and decision.get("credit") == credit_must
          and all(decision.get(k, credit_must) == credit_must
                  for k in ("lineage_credit", "developmental_credit")))
    return sat, unsat


def verify_dispatch_auth(args, contract: dict, entries: list[dict]):
    """Mechanical eligibility-token check for a continuation step.

    Returns None when the step carries a valid dispatcher-issued token
    bound to the exact parent snapshot, else a failure reason string.
    """
    package = Path(args.package)
    if not args.dispatch_auth or not args.snapshots_dir:
        return ("depth >= 1 requires --dispatch-auth and --snapshots-dir: "
                "no eligibility token presented")
    auth = load_json(Path(args.dispatch_auth))
    if not isinstance(auth, dict):
        return "dispatch auth artifact unreadable"
    if auth.get("authority") != AUTHORITY:
        return "dispatch auth authority mismatch"
    if auth.get("chain_id") != contract["chain_id"]:
        return "dispatch auth chain_id mismatch"
    if auth.get("step_id") != args.step_id:
        return "dispatch auth not issued for this step_id"
    if auth.get("parent_step_id") != args.parent_step_id:
        return "dispatch auth not issued for this parent_step_id"

    # The token must bind the exact parent snapshot the classifier saw.
    parent_snap = Path(args.snapshots_dir) / f"s{args.depth - 1:02d}"
    prec, perr = verify_snapshot(parent_snap)
    if perr:
        return f"parent snapshot unverifiable: {perr}"
    if prec.get("step_id") != args.parent_step_id:
        return "parent snapshot step_id mismatch"
    if auth.get("parent_snapshot_hash") != prec["snapshot_hash"]:
        return ("dispatch auth not bound to the published parent snapshot "
                "(parent_snapshot_hash mismatch)")

    # The token must bind the exact proposal and classification bytes.
    proposal_path = package / f"CHAIN_PROPOSAL_s{args.depth:02d}.json"
    if not proposal_path.is_file():
        return "proposal file absent for auth binding check"
    if auth.get("proposal_hash") != sha256_file(proposal_path):
        return "dispatch auth proposal_hash mismatch: proposal changed after authorization"
    class_path = package / "AUTO_CONTINUE_0_CLASSIFICATION.json"
    if not class_path.is_file():
        return "classification verdict absent for auth binding check"
    if auth.get("classification_hash") != sha256_file(class_path):
        return ("dispatch auth classification_hash mismatch: verdict changed "
                "after authorization")
    return None


def append_entry(ledger_path: Path, entry: dict):
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def cmd_evaluate(args) -> int:
    contract, err = load_contract(Path(args.contract))
    if err:
        emit({"authority": AUTHORITY, "error": err,
              "evaluated_at": utcnow_iso()}, None, "")
        return 2
    package = Path(args.package)
    ledger_path = Path(args.ledger)
    chain_id = contract["chain_id"]

    # Snapshot barrier: the evaluator reads ONLY the published snapshot.
    # No STEP_COMPLETED.json -> refuse, write nothing. There is no code
    # path that evaluates a half-written workspace.
    snap = Path(args.snapshot)
    srec, serr = verify_snapshot(snap)
    if serr:
        emit({"authority": AUTHORITY, "error": f"snapshot refused: {serr}",
              "evaluated_at": utcnow_iso()}, None, "")
        return 2
    if srec.get("step_id") != args.step_id:
        emit({"authority": AUTHORITY,
              "error": "snapshot step_id does not match --step-id",
              "evaluated_at": utcnow_iso()}, None, "")
        return 2

    entries: list[dict] = []
    if ledger_path.exists():
        entries, err = load_ledger(ledger_path)
        if err:
            emit({"authority": AUTHORITY, "error": err,
                  "evaluated_at": utcnow_iso()}, None, "")
            return 2

    prev = latest_evaluation(entries, chain_id)
    prev_unsat = set(prev["predicates"]["unsatisfied"]) if prev else set(
        d.get("id") for d in contract.get("done_predicates", []))

    worker_status = args.worker_status

    # Eligibility-token gate: a continuation step without a valid
    # dispatcher-issued token is recorded STOP_UNAUTHORIZED_DISPATCH.
    # The violation is written to the ledger (visible) and the chain
    # terminates; the step's artifacts are never assessed.
    if args.depth >= 1:
        auth_failure = verify_dispatch_auth(args, contract, entries)
        if auth_failure:
            entry = {
                "chain_id": chain_id,
                "step_id": args.step_id,
                "parent_step_id": args.parent_step_id,
                "depth": args.depth,
                "evaluated_at": utcnow_iso(),
                "authorization": "FAILED",
                "auth_failure": auth_failure,
                "worker_status": worker_status,
                "failure_class": args.failure_class,
                "contract_frozen_hash": contract["frozen_hash"],
                "predicates": {"satisfied": [], "unsatisfied": []},
                "prev_unsatisfied_count": len(prev_unsat),
                "chain_decision": "STOP_UNAUTHORIZED_DISPATCH",
                "stop_reason": ("continuation lacks a valid mechanical "
                                "eligibility token: " + auth_failure +
                                "; chain terminates"),
            }
            append_entry(ledger_path, entry)
            emit({"authority": AUTHORITY, "chain_id": chain_id,
                  "step_id": args.step_id,
                  "chain_decision": "STOP_UNAUTHORIZED_DISPATCH",
                  "stop_reason": entry["stop_reason"],
                  "ledger": str(ledger_path),
                  "evaluated_at": entry["evaluated_at"]}, None, "")
            return 0

    decision_path = snap / "EXPERIENCE_COMPRESSION_DECISION.json"
    rationale_path = snap / "RATIONALE.md"
    sat, unsat = evaluate_predicates(contract, package,
                                     decision_path, rationale_path)
    curr_unsat = set(unsat)

    if worker_status == "failed":
        chain_decision, stop_reason = "STOP_STEP_FAILED", (
            f"worker step failed (class={args.failure_class}); "
            "retry only within contract retry_policy.permitted_classes")
    elif not curr_unsat:
        chain_decision, stop_reason = "STOP_GOAL_SATISFIED", \
            "all frozen done predicates satisfied"
    elif len(curr_unsat) < len(prev_unsat):
        chain_decision, stop_reason = "CONTINUE_ELIGIBLE", (
            f"unsatisfied predicates decreased {len(prev_unsat)} -> "
            f"{len(curr_unsat)}; a continuation may be proposed")
    else:
        chain_decision, stop_reason = "STOP_NO_PROGRESS", (
            "unsatisfied predicate count did not strictly decrease and no "
            "concrete blocker was produced")

    entry = {
        "chain_id": chain_id,
        "step_id": args.step_id,
        "parent_step_id": args.parent_step_id,
        "depth": args.depth,
        "evaluated_at": utcnow_iso(),
        "worker_status": worker_status,
        "failure_class": args.failure_class,
        "contract_frozen_hash": contract["frozen_hash"],
        "snapshot_hash": srec["snapshot_hash"],
        "predicates": {"satisfied": sorted(sat), "unsatisfied": sorted(unsat)},
        "prev_unsatisfied_count": len(prev_unsat),
        "chain_decision": chain_decision,
        "stop_reason": stop_reason,
    }
    append_entry(ledger_path, entry)

    emit({"authority": AUTHORITY, "chain_id": chain_id,
          "step_id": args.step_id, "chain_decision": chain_decision,
          "stop_reason": stop_reason,
          "satisfied": sorted(sat), "unsatisfied": sorted(unsat),
          "ledger": str(ledger_path),
          "evaluated_at": entry["evaluated_at"]}, None, "")
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="AUTO-CONTINUE-0 classifier")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify", help="pre-dispatch continuation check")
    c.add_argument("--contract", required=True)
    c.add_argument("--proposal", required=True)
    c.add_argument("--ledger", required=True)
    c.add_argument("--write-verdict", default=None,
                   help="directory receiving AUTO_CONTINUE_0_CLASSIFICATION.json")
    c.add_argument("--parent-snapshot", default=None,
                   help="published snapshot dir of the parent step; the "
                        "verdict is bound to its snapshot_hash")

    e = sub.add_parser("evaluate", help="post-step D_0 evaluation + ledger")
    e.add_argument("--contract", required=True)
    e.add_argument("--package", required=True)
    e.add_argument("--ledger", required=True)
    e.add_argument("--step-id", required=True)
    e.add_argument("--parent-step-id", required=True)
    e.add_argument("--depth", type=int, required=True)
    e.add_argument("--worker-status", required=True,
                   choices=("completed", "failed"))
    e.add_argument("--failure-class", default=None)
    e.add_argument("--snapshot", required=True,
                   help="published snapshot dir for this step; the evaluator "
                        "reads ONLY the snapshot, never the live workspace")
    e.add_argument("--dispatch-auth", default=None,
                   help="dispatcher-issued eligibility token (required when "
                        "depth >= 1)")
    e.add_argument("--snapshots-dir", default=None,
                   help="snapshots root (required when depth >= 1, to locate "
                        "the parent snapshot)")

    args = ap.parse_args()
    if args.cmd == "classify":
        return cmd_classify(args)
    return cmd_evaluate(args)


if __name__ == "__main__":
    sys.exit(main())
