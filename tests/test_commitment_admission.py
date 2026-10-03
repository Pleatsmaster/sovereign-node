"""COMMITMENT-ADMISSION-0 tests.

Validates the frozen rules: human-only acceptance, concrete objectives with
verifiable completion contracts, closed authority classes, no forbidden
targets, idempotent admission, and the end-to-end path admitted -> staged
need -> OBLIGATION-0 projects OPEN (via the real, untouched projector).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

LIFE0_ROOT = Path(__file__).resolve().parent.parent
ADMIT = LIFE0_ROOT / "scripts" / "admit_commitment.py"
PROJECTOR = LIFE0_ROOT / "scripts" / "project_obligations.py"


def write_acceptance(tmp_path: Path, **overrides) -> Path:
    acc = {
        "objective": "Design, implement, and validate COMMITMENT-ADMISSION-0",
        "completion_contract": {"predicates": [
            {"id": "mechanism-validates",
             "verifiable": "admit_commitment.py rejects bad acceptances"},
            {"id": "registry-append-only",
             "verifiable": "state/commitments.jsonl has exactly one entry"},
        ]},
        "authority_class": "local-build",
        "scope": "life-0 tree only: scripts, tests, docs, state",
        "accepted_by": "operator",
        "accepted_at": "2026-10-01T18:40:21-04:00",
        "origin": "test acceptance",
        "parent_commitment_id": None,
    }
    acc.update(overrides)
    p = tmp_path / "acceptance.json"
    p.write_text(json.dumps(acc), encoding="utf-8")
    return p


def run_admit(life0: Path, acceptance: Path):
    return subprocess.run(
        [sys.executable, str(ADMIT), "--life0", str(life0),
         "--acceptance", str(acceptance)],
        capture_output=True, text=True)


def read_jsonl(path: Path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def test_valid_admission_creates_registry_need_and_package(tmp_path):
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path)
    r = run_admit(life0, acc)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["admitted"] is True
    assert out["commitment_id"].startswith("commit_")
    assert out["need_id"].startswith("need_")
    assert out["mission_id"] == f"life0-{out['need_id']}"

    # Registry: exactly one append-only record.
    regs = read_jsonl(life0 / "state" / "commitments.jsonl")
    assert len(regs) == 1
    assert regs[0]["event"] == "COMMITMENT_ADMITTED"
    c = regs[0]["commitment"]
    assert c["commitment_id"] == out["commitment_id"]
    assert c["accepted_by"] == "operator"
    assert c["status"] == "OPEN"
    assert c["authority_class"] == "local-build"

    # Need: exactly one NEED_CREATED, source commitment.
    needs = read_jsonl(life0 / "state" / "needs.jsonl")
    created = [e for e in needs if e["event"] == "NEED_CREATED"]
    assert len(created) == 1
    n = created[0]["need"]
    assert n["need_id"] == out["need_id"]
    assert n["source"] == "commitment"
    assert n["origin"] == f"commitment:{out['commitment_id']}"
    assert n["priority"] == "accepted program commitment"
    statuses = [e for e in needs if e["event"] == "NEED_STATUS"]
    assert len(statuses) == 1
    assert statuses[0]["status"] == "STAGED_AWAITING_AUTHORIZATION"

    # Staged package: inert shape, launch impossible.
    pkg_dir = life0 / "dispatch" / "staged" / out["need_id"]
    pkg = json.loads((pkg_dir / "MISSION_PACKAGE.json").read_text(encoding="utf-8"))
    assert pkg["status"] == "STAGED_AWAITING_AUTHORIZATION"
    assert pkg["worker_command"] is None
    assert pkg["launch_authorized"] is False
    assert pkg["gate"]["decision"] == "STAGE"
    assert pkg["gate"]["policy_version"] == "commitment-admission-0"
    assert pkg["commitment"]["commitment_id"] == out["commitment_id"]
    assert (pkg_dir / "OBJECTIVE.md").exists()
    assert (pkg_dir / "STAGE_RECORD.json").exists()


def test_duplicate_admission_refused_idempotent(tmp_path):
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path)
    r1 = run_admit(life0, acc)
    assert r1.returncode == 0
    r2 = run_admit(life0, acc)
    assert r2.returncode == 2
    out2 = json.loads(r2.stdout)
    assert "already admitted" in out2["reason"]
    assert len(read_jsonl(life0 / "state" / "commitments.jsonl")) == 1
    created = [e for e in read_jsonl(life0 / "state" / "needs.jsonl")
               if e["event"] == "NEED_CREATED"]
    assert len(created) == 1


def test_rejects_agent_originated_acceptance(tmp_path):
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path, accepted_by="agent")
    r = run_admit(life0, acc)
    assert r.returncode == 2
    assert "self-admit" in json.loads(r.stdout)["reason"]
    assert not (life0 / "state" / "commitments.jsonl").exists()


@pytest.mark.parametrize("bad_by", ["model", "Muse", "OPERATOR", ""])
def test_rejects_any_non_operator_accepted_by(tmp_path, bad_by):
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path, accepted_by=bad_by)
    r = run_admit(life0, acc)
    assert r.returncode == 2


def test_rejects_vague_objective(tmp_path):
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path, objective="improve Namariel")
    r = run_admit(life0, acc)
    assert r.returncode == 2
    assert "vague" in json.loads(r.stdout)["reason"]


def test_rejects_empty_completion_contract(tmp_path):
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path,
                           completion_contract={"predicates": []})
    r = run_admit(life0, acc)
    assert r.returncode == 2
    acc2 = write_acceptance(tmp_path,
                            completion_contract={"predicates": [{"id": "x"}]})
    r2 = run_admit(life0, acc2)
    assert r2.returncode == 2  # predicate without a verifiable condition


def test_rejects_unknown_authority_class(tmp_path):
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path, authority_class="root-everything")
    r = run_admit(life0, acc)
    assert r.returncode == 2
    assert "cannot widen authority" in json.loads(r.stdout)["reason"]


def test_rejects_forbidden_target(tmp_path):
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path,
                           scope="life-0 tree and the operator .ssh directory")
    r = run_admit(life0, acc)
    assert r.returncode == 2
    assert "forbidden target" in json.loads(r.stdout)["reason"]


def test_rejects_unknown_parent_commitment(tmp_path):
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path, parent_commitment_id="commit_deadbeef")
    r = run_admit(life0, acc)
    assert r.returncode == 2
    assert "parent_commitment_id" in json.loads(r.stdout)["reason"]


def test_admission_records_authority_verbatim_never_widens(tmp_path):
    """The admitted record's authority_class equals the human-stated class;
    the enum is closed and there is no upgrade path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("admit_commitment", ADMIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(mod.AUTHORITY_CLASSES) == {"local-read", "local-build",
                                         "external-propose"}
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path, authority_class="local-read")
    r = run_admit(life0, acc)
    assert r.returncode == 0
    c = read_jsonl(life0 / "state" / "commitments.jsonl")[0]["commitment"]
    assert c["authority_class"] == "local-read"


def test_admitted_need_projects_open_via_untouched_projector(tmp_path):
    """End-to-end: admitted commitment -> staged need -> OBLIGATION-0 OPEN."""
    life0 = tmp_path / "life0"
    acc = write_acceptance(tmp_path)
    r = run_admit(life0, acc)
    assert r.returncode == 0
    need_id = json.loads(r.stdout)["need_id"]

    out = tmp_path / "obligations.json"
    rp = subprocess.run(
        [sys.executable, str(PROJECTOR), "--life0", str(life0),
         "--out", str(out)],
        capture_output=True, text=True)
    assert rp.returncode == 0, rp.stdout + rp.stderr
    proj = json.loads(out.read_text(encoding="utf-8"))
    assert proj["open_obligations"] == [need_id]
    obl = proj["obligations"][need_id]
    assert obl["state"] == "OPEN"
    assert obl["determining_event"]["type"] == "need_created"
