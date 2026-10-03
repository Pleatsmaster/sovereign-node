"""Regression tests for the 2026-10-01 LIFE-PRESSURE-002 incident.

The first live AUTO-CONTINUE-0 chain terminated correctly
(STOP_GOAL_SATISFIED, 8/8) but the executing agent (a) evaluated the
worker step before its file writes were visible on the shared
filesystem, recording a premature entry and a false --worker-status
failed entry, and (b) overrode the resulting STOP_STEP_FAILED /
STOP_NO_PROGRESS verdicts on private judgment and dispatched the
continuation anyway.

Two apparatus corrections, tested here:

  1. Snapshot publication barrier (publish_step_snapshot.py): the
     evaluator reads ONLY an atomically published, hash-verified
     snapshot. Without STEP_COMPLETED.json it refuses and writes
     nothing. Evaluation can no longer observe a half-written
     workspace.
  2. Dispatcher-issued eligibility tokens (authorize_dispatch.py):
     STOP is mechanically irreversible. After any STOP_* the parent's
     latest chain decision is not CONTINUE_ELIGIBLE and no input to
     the dispatcher can produce a token. A continuation step evaluated
     without a valid token is recorded STOP_UNAUTHORIZED_DISPATCH.

Deterministic, stdlib-only, no network, no subprocess, no model calls.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


acc = _load("classify_auto_continue_0", SCRIPTS / "classify_auto_continue_0.py")
pub = _load("publish_step_snapshot", SCRIPTS / "publish_step_snapshot.py")
disp = _load("authorize_dispatch", SCRIPTS / "authorize_dispatch.py")

CHAIN = "chain-test-001"
NEED = "TEST-NEED-001"
PREDS = ["d1_outcome_single_allowed", "d2_decision_validates",
         "d3_required_fields", "d4_sources_resolve",
         "d5_no_forbidden_mutation", "d6_rationale_exists",
         "d7_no_promotion", "d8_credit_none"]
GOOD_SOURCE = "namariel-live0/life-0/AUTO_WORK_0.md"  # exists under ~/workspace
BAD_SOURCE = "namariel-live0/life-0/NO_SUCH_FILE_missing.md"


def _freeze(body: dict) -> dict:
    body = dict(body)
    body["frozen_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in body.items() if k != "frozen_hash"},
                   sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body


def _contract(path: Path) -> Path:
    c = _freeze({
        "contract_id": "TEST-CHAIN-001",
        "status": "FROZEN",
        "authority_class": "INTERNAL_ANALYSIS",
        "originating_need_id": NEED,
        "chain_id": CHAIN,
        "max_depth": 5,
        "fan_out": 1,
        "bounds": {"immutable": True},
        "chain_budget_mutable": False,
        "completion_contract_mutable": False,
        "progress_invariant": "|D_{n+1}^{unsatisfied}| < |D_n^{unsatisfied}|",
        "done_predicates": [{"id": p} for p in PREDS],
        "gap_vocabulary": ["required_evidence_reference_missing"],
        "operation_vocabulary": ["RETRIEVE_AUTHORIZED_LOCAL_EVIDENCE"],
        "gap_policy": [{"predicate": "d4_sources_resolve",
                       "gap": "required_evidence_reference_missing",
                       "operation": "RETRIEVE_AUTHORIZED_LOCAL_EVIDENCE"}],
        "retry_policy": {"permitted_classes": []},
        "decision_schema": {
            "artifact": "work/EXPERIENCE_COMPRESSION_DECISION.json",
            "allowed_outcomes": ["NO_PERSISTENT_CHANGE"],
            "required_fields": ["outcome", "source_claims", "credit"],
            "credit_must_equal": "NONE",
        },
        "frozen_at": "2026-10-01T00:00:00+00:00",
        "frozen_by": "test",
    })
    path.write_text(json.dumps(c, indent=2, sort_keys=True) + "\n")
    return path


def _decision(source_claims) -> dict:
    return {
        "outcome": "NO_PERSISTENT_CHANGE",
        "source_claims": source_claims,
        "credit": "NONE",
        "promoted": False,
    }


@pytest.fixture()
def env(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "work").mkdir(parents=True)
    (pkg / "MISSION_PACKAGE.json").write_text("{}\n")
    snaps = tmp_path / "snaps"
    contract = _contract(tmp_path / "contract.json")
    ledger = pkg / "CHAIN_LEDGER.jsonl"
    return {"pkg": pkg, "snaps": snaps, "contract": contract, "ledger": ledger,
            "tmp": tmp_path}


def _stage(env, name: str, decision: dict, rationale: str = "why\n") -> Path:
    st = env["tmp"] / name
    st.mkdir()
    (st / "EXPERIENCE_COMPRESSION_DECISION.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n")
    (st / "RATIONALE.md").write_text(rationale)
    return st


def _publish(env, staging: Path, step: str, depth: int):
    rc = pub.cmd_publish(Namespace(
        staging=str(staging), snapshots_dir=str(env["snaps"]),
        step_id=step, depth=depth,
        expect=["EXPERIENCE_COMPRESSION_DECISION.json", "RATIONALE.md"],
        worker_status="completed"))
    assert rc == 0
    return env["snaps"] / f"s{depth:02d}"


def _evaluate(env, step: str, parent: str, depth: int, snap: Path,
              auth: Path | None = None):
    return acc.cmd_evaluate(Namespace(
        contract=str(env["contract"]), package=str(env["pkg"]),
        ledger=str(env["ledger"]), step_id=step, parent_step_id=parent,
        depth=depth, worker_status="completed", failure_class=None,
        snapshot=str(snap),
        dispatch_auth=str(auth) if auth else None,
        snapshots_dir=str(env["snaps"]) if depth >= 1 else None))


def _ledger_entries(env):
    return [json.loads(l) for l in
            env["ledger"].read_text().splitlines() if l.strip()]


# --- 1. the visibility race is closed ------------------------------------

def test_publish_refuses_incomplete_staging(env, capsys):
    st = _stage(env, "staging-s00", _decision([GOOD_SOURCE]))
    (st / "RATIONALE.md").unlink()  # worker still writing
    rc = pub.cmd_publish(Namespace(
        staging=str(st), snapshots_dir=str(env["snaps"]),
        step_id=f"{CHAIN}-s00", depth=0,
        expect=["EXPERIENCE_COMPRESSION_DECISION.json", "RATIONALE.md"],
        worker_status="completed"))
    assert rc == 2
    assert not (env["snaps"] / "s00").exists()


def test_evaluate_refuses_without_published_snapshot(env):
    # The 2026-10-01 defect: evaluation ran against files still landing.
    # Now there is no code path that evaluates without STEP_COMPLETED.json.
    rc = acc.cmd_evaluate(Namespace(
        contract=str(env["contract"]), package=str(env["pkg"]),
        ledger=str(env["ledger"]), step_id=f"{CHAIN}-s00",
        parent_step_id="none", depth=0, worker_status="completed",
        failure_class=None, snapshot=str(env["snaps"] / "s00"),
        dispatch_auth=None, snapshots_dir=None))
    assert rc == 2
    assert not env["ledger"].exists()  # refused: nothing written


def test_evaluate_detects_tampered_snapshot(env):
    snap = _publish(env, _stage(env, "staging-s00", _decision([GOOD_SOURCE])),
                    f"{CHAIN}-s00", 0)
    (snap / "RATIONALE.md").write_text("tampered after publish\n")
    rc = _evaluate(env, f"{CHAIN}-s00", "none", 0, snap)
    assert rc == 2
    assert not env["ledger"].exists()


# --- 2. STOP is mechanically irreversible ---------------------------------

def _stop_ledger(env):
    entry = {
        "chain_id": CHAIN, "step_id": f"{CHAIN}-s00", "parent_step_id": "none",
        "depth": 0, "evaluated_at": "2026-10-01T22:11:46+00:00",
        "worker_status": "completed", "failure_class": None,
        "contract_frozen_hash": "x",
        "predicates": {"satisfied": [p for p in PREDS if p != "d4_sources_resolve"],
                       "unsatisfied": ["d4_sources_resolve"]},
        "prev_unsatisfied_count": 1,
        "chain_decision": "STOP_NO_PROGRESS",
        "stop_reason": "unsatisfied predicate count did not strictly decrease",
    }
    env["ledger"].write_text(json.dumps(entry, sort_keys=True) + "\n")


def _eligible_classification(env, snap) -> Path:
    proposal = {
        "originating_need_id": NEED, "parent_step_id": f"{CHAIN}-s00",
        "chain_id": CHAIN, "current_depth": 1,
        "unsatisfied_done_predicate": "d4_sources_resolve",
        "observed_gap": "required_evidence_reference_missing",
        "proposed_operation": "RETRIEVE_AUTHORIZED_LOCAL_EVIDENCE",
        "expected_postcondition": "d4_sources_resolve becomes satisfied after "
                                 "RETRIEVE_AUTHORIZED_LOCAL_EVIDENCE",
        "authority_class": "INTERNAL_ANALYSIS", "bounds_changed": False,
    }
    ppath = env["pkg"] / "CHAIN_PROPOSAL_s01.json"
    ppath.write_text(json.dumps(proposal, indent=2, sort_keys=True) + "\n")
    rc = acc.cmd_classify(Namespace(
        contract=str(env["contract"]), proposal=str(ppath),
        ledger=str(env["ledger"]), write_verdict=str(env["pkg"]),
        parent_snapshot=str(snap)))
    assert rc == 0
    vpath = env["pkg"] / "AUTO_CONTINUE_0_CLASSIFICATION.json"
    assert json.loads(vpath.read_text())["verdict"] == "ELIGIBLE"
    return vpath


def test_dispatcher_refuses_after_stop(env):
    # Exact 2026-10-01 scenario: the agent's diagnosis was correct and the
    # proposal classifies ELIGIBLE, but the parent's latest decision is a
    # STOP. No token may exist.
    snap = _publish(env, _stage(env, "staging-s00", _decision([BAD_SOURCE])),
                    f"{CHAIN}-s00", 0)
    _stop_ledger(env)
    vpath = _eligible_classification(env, snap)
    rc = disp.cmd_authorize(Namespace(
        contract=str(env["contract"]),
        proposal=str(env["pkg"] / "CHAIN_PROPOSAL_s01.json"),
        ledger=str(env["ledger"]), classification=str(vpath),
        snapshots_dir=str(env["snaps"]), write_auth=str(env["snaps"])))
    assert rc == 3
    assert list(env["snaps"].glob("DISPATCH_AUTH_*.json")) == []


def test_evaluate_records_unauthorized_continuation(env):
    snap = _publish(env, _stage(env, "staging-s00", _decision([GOOD_SOURCE])),
                    f"{CHAIN}-s00", 0)
    rc = _evaluate(env, f"{CHAIN}-s00", "none", 0, snap)
    assert rc == 0
    # A depth-1 step evaluated with NO token: the violation is recorded,
    # the artifacts are never assessed, the chain terminates.
    snap1 = _publish(env, _stage(env, "staging-s01", _decision([GOOD_SOURCE])),
                     f"{CHAIN}-s01", 1)
    rc = _evaluate(env, f"{CHAIN}-s01", f"{CHAIN}-s00", 1, snap1, auth=None)
    assert rc == 0
    entries = _ledger_entries(env)
    assert entries[-1]["chain_decision"] == "STOP_UNAUTHORIZED_DISPATCH"
    assert entries[-1]["authorization"] == "FAILED"
    assert entries[-1]["predicates"] == {"satisfied": [], "unsatisfied": []}


def test_evaluate_rejects_mismatched_token_binding(env):
    snap0 = _publish(env, _stage(env, "staging-s00", _decision([BAD_SOURCE])),
                     f"{CHAIN}-s00", 0)
    rc = _evaluate(env, f"{CHAIN}-s00", "none", 0, snap0)
    assert rc == 0
    vpath = _eligible_classification(env, snap0)
    rc = disp.cmd_authorize(Namespace(
        contract=str(env["contract"]),
        proposal=str(env["pkg"] / "CHAIN_PROPOSAL_s01.json"),
        ledger=str(env["ledger"]), classification=str(vpath),
        snapshots_dir=str(env["snaps"]), write_auth=str(env["snaps"])))
    assert rc == 0
    auth_path = env["snaps"] / "DISPATCH_AUTH_s01.json"
    # Tamper: rebind the token to a different parent snapshot hash.
    tok = json.loads(auth_path.read_text())
    tok["parent_snapshot_hash"] = "0" * 64
    auth_path.write_text(json.dumps(tok, indent=2, sort_keys=True) + "\n")
    snap1 = _publish(env, _stage(env, "staging-s01", _decision([GOOD_SOURCE])),
                     f"{CHAIN}-s01", 1)
    rc = _evaluate(env, f"{CHAIN}-s01", f"{CHAIN}-s00", 1, snap1, auth_path)
    assert rc == 0
    assert _ledger_entries(env)[-1]["chain_decision"] == \
        "STOP_UNAUTHORIZED_DISPATCH"


# --- 3. the legitimate path still works ----------------------------------

def test_happy_path_two_step_chain(env):
    # s00: 7/8, d4 unsatisfied (mechanical gap, as on 2026-10-01).
    snap0 = _publish(env, _stage(env, "staging-s00", _decision([BAD_SOURCE])),
                     f"{CHAIN}-s00", 0)
    rc = _evaluate(env, f"{CHAIN}-s00", "none", 0, snap0)
    assert rc == 0
    assert _ledger_entries(env)[-1]["chain_decision"] == "CONTINUE_ELIGIBLE"

    vpath = _eligible_classification(env, snap0)
    rc = disp.cmd_authorize(Namespace(
        contract=str(env["contract"]),
        proposal=str(env["pkg"] / "CHAIN_PROPOSAL_s01.json"),
        ledger=str(env["ledger"]), classification=str(vpath),
        snapshots_dir=str(env["snaps"]), write_auth=str(env["snaps"])))
    assert rc == 0
    auth_path = env["snaps"] / "DISPATCH_AUTH_s01.json"
    tok = json.loads(auth_path.read_text())
    assert tok["step_id"] == f"{CHAIN}-s01"

    # A second token for the same parent: fan-out forbidden.
    rc = disp.cmd_authorize(Namespace(
        contract=str(env["contract"]),
        proposal=str(env["pkg"] / "CHAIN_PROPOSAL_s01.json"),
        ledger=str(env["ledger"]), classification=str(vpath),
        snapshots_dir=str(env["snaps"]), write_auth=str(env["snaps"])))
    assert rc == 3

    # s01: worker amends the s00 snapshot content, fixes the gap.
    st1 = env["tmp"] / "staging-s01"
    st1.mkdir()
    for f in snap0.iterdir():
        if f.name != "STEP_COMPLETED.json":
            shutil.copy(f, st1 / f.name)
    (st1 / "EXPERIENCE_COMPRESSION_DECISION.json").write_text(
        json.dumps(_decision([GOOD_SOURCE]), indent=2, sort_keys=True) + "\n")
    snap1 = _publish(env, st1, f"{CHAIN}-s01", 1)
    rc = _evaluate(env, f"{CHAIN}-s01", f"{CHAIN}-s00", 1, snap1, auth_path)
    assert rc == 0
    last = _ledger_entries(env)[-1]
    assert last["chain_decision"] == "STOP_GOAL_SATISFIED"
    assert last["predicates"]["unsatisfied"] == []
    assert last["snapshot_hash"] == json.loads(
        (snap1 / "STEP_COMPLETED.json").read_text())["snapshot_hash"]
