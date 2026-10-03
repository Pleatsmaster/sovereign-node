"""COMMITMENT-EXECUTION-0 tests.

Standing authorization is mechanical: the six validity conditions are
checked by execution_eligibility.py; selection is deterministic; closure
requires every completion predicate verified with evidence, else refusal
with no state written.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

LIFE0_ROOT = Path(__file__).resolve().parent.parent
ADMIT = LIFE0_ROOT / "scripts" / "admit_commitment.py"
ELIG = LIFE0_ROOT / "scripts" / "execution_eligibility.py"
SELECT = LIFE0_ROOT / "scripts" / "select_obligation.py"
CLOSE = LIFE0_ROOT / "scripts" / "close_commitment.py"
PROJECTOR = LIFE0_ROOT / "scripts" / "project_obligations.py"

sys.path.insert(0, str(LIFE0_ROOT / "scripts"))
import execution_eligibility as elig  # noqa: E402


def write_acceptance(tmp_path: Path, name: str, **overrides) -> Path:
    acc = {
        "objective": f"Test commitment {name}: verify the execution machinery",
        "completion_contract": {"predicates": [
            {"id": "p1", "verifiable": "predicate one holds"},
            {"id": "p2", "verifiable": "predicate two holds"},
        ]},
        "authority_class": "local-build",
        "scope": "test tree only",
        "accepted_by": "operator",
        "accepted_at": "2026-10-01T18:47:00-04:00",
        "origin": f"test {name}",
        "parent_commitment_id": None,
    }
    acc.update(overrides)
    p = tmp_path / f"acceptance-{name}.json"
    p.write_text(json.dumps(acc), encoding="utf-8")
    return p


def admit(life0: Path, acceptance: Path) -> dict:
    r = subprocess.run(
        [sys.executable, str(ADMIT), "--life0", str(life0),
         "--acceptance", str(acceptance)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def run_script(script: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True)


def test_eligibility_authorized_for_valid_commitment(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "a"))
    r = run_script(ELIG, "--life0", str(life0), "--need", out["need_id"])
    assert r.returncode == 0, r.stdout + r.stderr
    v = json.loads(r.stdout)
    assert v["verdict"] == "EXECUTION_AUTHORIZED"
    assert v["authority_class"] == "local-build"
    assert v["accepted_by"] == "operator"


def test_eligibility_refuses_non_commitment_need(tmp_path):
    life0 = tmp_path / "life0"
    (life0 / "state").mkdir(parents=True)
    need = {"need_id": "need_delta1", "created_at": "2026-10-01T00:00:00+00:00",
            "source": "inbox", "priority": "unfinished user work",
            "possible_need": "x", "status": "NEW"}
    (life0 / "state" / "needs.jsonl").write_text(
        json.dumps({"event": "NEED_CREATED", "at": need["created_at"],
                    "need": need}) + "\n", encoding="utf-8")
    r = run_script(ELIG, "--life0", str(life0), "--need", "need_delta1")
    assert r.returncode == 2
    assert "not commitment-origin" in json.loads(r.stdout)["blocking_condition"]


def test_eligibility_refuses_unknown_need(tmp_path):
    life0 = tmp_path / "life0"
    (life0 / "state").mkdir(parents=True)
    r = run_script(ELIG, "--life0", str(life0), "--need", "need_nope")
    assert r.returncode == 2
    assert "no NEED_CREATED" in json.loads(r.stdout)["blocking_condition"]


def test_eligibility_refuses_agent_accepted_commitment(tmp_path):
    # Hand-crafted registry: admission would never write this; eligibility
    # must still refuse it at execution time.
    life0 = tmp_path / "life0"
    (life0 / "state").mkdir(parents=True)
    commitment = {
        "commitment_id": "commit_evil", "origin": "test",
        "accepted_by": "agent", "accepted_at": "2026-10-01T18:47:00-04:00",
        "objective": "Test commitment: agent tries to self-admit",
        "completion_contract": {"predicates": [
            {"id": "p1", "verifiable": "x"}]},
        "authority_class": "local-build", "scope": "test", "status": "OPEN",
        "parent_commitment_id": None,
    }
    (life0 / "state" / "commitments.jsonl").write_text(
        json.dumps({"event": "COMMITMENT_ADMITTED", "at": "2026-10-01T18:47:00+00:00",
                    "commitment": commitment}) + "\n", encoding="utf-8")
    need = {"need_id": "need_evil", "created_at": "2026-10-01T18:47:00+00:00",
            "commitment_id": "commit_evil", "source": "commitment",
            "origin": "commitment:commit_evil",
            "priority": "accepted program commitment",
            "possible_need": commitment["objective"], "status": "NEW"}
    (life0 / "state" / "needs.jsonl").write_text(
        json.dumps({"event": "NEED_CREATED", "at": need["created_at"],
                    "need": need}) + "\n", encoding="utf-8")
    r = run_script(ELIG, "--life0", str(life0), "--need", "need_evil")
    assert r.returncode == 2
    assert "not \"operator\"" in json.loads(r.stdout)["blocking_condition"]


def full_evidence():
    return {
        "p1": {"verdict": "verified", "evidence": "e1"},
        "p2": {"verdict": "verified", "evidence": "e2"},
    }


def write_evidence(tmp_path: Path, name: str, ev: dict) -> Path:
    p = tmp_path / f"evidence-{name}.json"
    p.write_text(json.dumps(ev), encoding="utf-8")
    return p


def close(life0: Path, need_id: str, evidence: Path):
    return run_script(CLOSE, "--life0", str(life0), "--need", need_id,
                      "--evidence", str(evidence),
                      "--closed-by", "test executor",
                      "--how", "test verification")


def package_status(life0: Path, need_id: str) -> str:
    pkg = json.loads((life0 / "dispatch" / "staged" / need_id /
                      "MISSION_PACKAGE.json").read_text(encoding="utf-8"))
    return pkg["status"]


def test_close_refuses_missing_predicate_evidence(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "b"))
    ev = write_evidence(tmp_path, "b", {"p1": {"verdict": "verified", "evidence": "e1"}})
    r = close(life0, out["need_id"], ev)
    assert r.returncode == 2
    assert json.loads(r.stdout)["missing"] == ["p2"]
    assert package_status(life0, out["need_id"]) == "STAGED_AWAITING_AUTHORIZATION"


def test_close_refuses_failed_predicate(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "c"))
    ev = write_evidence(tmp_path, "c", {
        "p1": {"verdict": "verified", "evidence": "e1"},
        "p2": {"verdict": "failed", "evidence": "did not hold"}})
    r = close(life0, out["need_id"], ev)
    assert r.returncode == 2
    assert json.loads(r.stdout)["failed"] == ["p2"]
    assert package_status(life0, out["need_id"]) == "STAGED_AWAITING_AUTHORIZATION"


def test_close_refuses_without_eligibility(tmp_path):
    life0 = tmp_path / "life0"
    (life0 / "state").mkdir(parents=True)
    ev = write_evidence(tmp_path, "d", full_evidence())
    r = close(life0, "need_nope", ev)
    assert r.returncode == 2
    out = json.loads(r.stdout)
    assert out["closed"] is False
    # No package and no eligibility: either refusal reason is correct.
    assert "no staged package" in out["reason"] or "AUTHORIZED" in out["reason"]


def test_close_all_verified_writes_closure_and_projects_satisfied(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "e"))
    ev = write_evidence(tmp_path, "e", full_evidence())
    r = close(life0, out["need_id"], ev)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["predicates_verified"] == 2

    pkg = json.loads((life0 / "dispatch" / "staged" / out["need_id"] /
                      "MISSION_PACKAGE.json").read_text(encoding="utf-8"))
    assert pkg["status"] == "CLOSED"
    assert pkg["closure"]["closed_by"] == "test executor"
    assert len(pkg["closure"]["predicates"]) == 2

    outp = tmp_path / "oblig.json"
    rp = run_script(PROJECTOR, "--life0", str(life0), "--out", str(outp))
    assert rp.returncode == 0, rp.stdout + rp.stderr
    proj = json.loads(outp.read_text(encoding="utf-8"))
    assert proj["obligations"][out["need_id"]]["state"] == "SATISFIED"
    assert proj["open_obligations"] == []


def test_close_is_idempotent(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "f"))
    ev = write_evidence(tmp_path, "f", full_evidence())
    r1 = close(life0, out["need_id"], ev)
    assert r1.returncode == 0
    r2 = close(life0, out["need_id"], ev)
    assert r2.returncode == 0
    assert json.loads(r2.stdout).get("already") is True


def test_eligibility_refuses_terminal_obligation(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "g"))
    ev = write_evidence(tmp_path, "g", full_evidence())
    assert close(life0, out["need_id"], ev).returncode == 0
    r = run_script(ELIG, "--life0", str(life0), "--need", out["need_id"])
    assert r.returncode == 2
    assert "not OPEN" in json.loads(r.stdout)["blocking_condition"]


def test_select_obligation_picks_oldest_first(tmp_path):
    life0 = tmp_path / "life0"
    a = admit(life0, write_acceptance(
        tmp_path, "h1", accepted_at="2026-10-01T18:40:00-04:00"))
    b = admit(life0, write_acceptance(
        tmp_path, "h2", accepted_at="2026-10-01T18:50:00-04:00"))
    r = run_script(SELECT, "--life0", str(life0))
    assert r.returncode == 0, r.stdout + r.stderr
    sel = json.loads(r.stdout)
    assert sel["verdict"] == "SELECTED"
    assert sel["selected"]["need_id"] == a["need_id"]

    # After closing the first, selection moves to the second.
    ev = write_evidence(tmp_path, "h1", full_evidence())
    assert close(life0, a["need_id"], ev).returncode == 0
    r2 = run_script(SELECT, "--life0", str(life0))
    assert json.loads(r2.stdout)["selected"]["need_id"] == b["need_id"]


def test_select_obligation_none_eligible(tmp_path):
    life0 = tmp_path / "life0"
    (life0 / "state").mkdir(parents=True)
    (life0 / "state" / "needs.jsonl").write_text("", encoding="utf-8")
    r = run_script(SELECT, "--life0", str(life0))
    assert r.returncode == 0
    assert json.loads(r.stdout)["verdict"] == "NO_ELIGIBLE_OBLIGATION"
