"""COMMITMENT-DISPATCH-0 tests: apparatus verification around the real seam.

Not another LIFE episode. Each test pins one acceptance property:
exactly-once dispatch, crash reconciliation, refusal surfaces, selector
discipline, and the dispatcher's write boundary (never the completion
contract, authority class, H, M, or K).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

LIFE0_ROOT = Path(__file__).resolve().parent.parent
ADMIT = LIFE0_ROOT / "scripts" / "admit_commitment.py"
CLOSE = LIFE0_ROOT / "scripts" / "close_commitment.py"
DISPATCH = LIFE0_ROOT / "scripts" / "dispatch_commitment.py"

sys.path.insert(0, str(LIFE0_ROOT / "scripts"))
import dispatch_commitment as dc  # noqa: E402


def write_acceptance(tmp_path: Path, name: str, **overrides) -> Path:
    acc = {
        "objective": f"Test dispatch commitment {name}: build the dispatch test fixture",
        "completion_contract": {"predicates": [
            {"id": "p1", "verifiable": "predicate one holds"},
            {"id": "p2", "verifiable": "predicate two holds"},
        ]},
        "authority_class": "local-build",
        "scope": "test tree only",
        "accepted_by": "operator",
        "accepted_at": "2026-10-01T19:00:00-04:00",
        "origin": f"test {name}",
        "parent_commitment_id": None,
    }
    acc.update(overrides)
    p = tmp_path / f"acceptance-dispatch-{name}.json"
    p.write_text(json.dumps(acc), encoding="utf-8")
    return p


def admit(life0: Path, acceptance: Path) -> dict:
    r = subprocess.run(
        [sys.executable, str(ADMIT), "--life0", str(life0),
         "--acceptance", str(acceptance)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def run_dispatch(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(DISPATCH), *args],
                          capture_output=True, text=True)


def dispatch_json(*args) -> tuple[int, dict]:
    r = run_dispatch(*args)
    return r.returncode, json.loads(r.stdout)


def ledger_events(life0: Path, event: str | None = None) -> list:
    p = life0 / "state" / "commitment_dispatch.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            r = json.loads(line)
            if event is None or r.get("event") == event:
                out.append(r)
    return out


def evidence_for(*pred_ids: str) -> dict:
    return {pid: {"verdict": "verified", "evidence": f"e-{pid}"}
            for pid in pred_ids}


# 1. One eligible OPEN commitment launches exactly one chain.
def test_one_eligible_open_commitment_launches_exactly_one_chain(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "one"))
    need_id = out["need_id"]

    rc, v = dispatch_json("--life0", str(life0), "dispatch", "--need", need_id)
    assert rc == 0, v
    assert v["verdict"] == "DISPATCHED"
    assert v["dispatched_now"] is True
    assert v["authority_class"] == "local-build"

    dispatched = ledger_events(life0, "DISPATCHED")
    assert len(dispatched) == 1
    assert dispatched[0]["commitment_id"] == out["commitment_id"]

    chain_dir = life0 / "dispatch" / "chains" / v["chain_id"]
    binding_path = chain_dir / "CHAIN_BINDING.json"
    assert binding_path.exists()
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    assert binding["commitment_id"] == out["commitment_id"]
    assert binding["need_id"] == need_id
    assert binding["authority_class"] == "local-build"
    assert binding["current_obligation_projection"] == "OPEN"
    assert binding["auto_work_verdict"] == "ELIGIBLE"


# 2. Duplicate trigger does not duplicate the chain.
def test_duplicate_trigger_does_not_duplicate_chain(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "dup"))
    need_id = out["need_id"]

    rc1, v1 = dispatch_json("--life0", str(life0), "dispatch", "--need", need_id)
    assert v1["verdict"] == "DISPATCHED"
    rc2, v2 = dispatch_json("--life0", str(life0), "dispatch", "--need", need_id)
    assert rc2 == 0, v2
    assert v2["verdict"] == "ALREADY_DISPATCHED"
    assert v2["chain_id"] == v1["chain_id"]
    assert v2["dispatched_now"] is False

    # Recovery path agrees: no duplicate.
    rc3, v3 = dispatch_json("--life0", str(life0), "reconcile")
    assert v3["verdict"] == "ALREADY_DISPATCHED"

    assert len(ledger_events(life0, "DISPATCHED")) == 1
    assert len(list((life0 / "dispatch" / "chains").iterdir())) == 1


# 3. Crash after DISPATCH_INTENT reconciles correctly (completes, never relaunches).
def test_crash_after_intent_reconciles(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "crash"))
    need_id = out["need_id"]

    b = dc.bind(life0, need_id)  # the process "crashes" here
    assert b["bound"] is True
    nonce = b["nonce"]
    assert ledger_events(life0, "DISPATCH_INTENT")
    assert not ledger_events(life0, "DISPATCHED")

    out2 = dc.dispatch(life0, need_id)  # next run reconciles the open intent
    assert out2["verdict"] == "DISPATCHED", out2
    assert out2.get("recovered") is True

    dispatched = ledger_events(life0, "DISPATCHED")
    assert len(dispatched) == 1
    assert dispatched[0]["nonce"] == nonce  # same intent completed, not a new one


# 4. Closed commitment cannot launch.
def test_closed_commitment_cannot_launch(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "closed"))
    need_id = out["need_id"]

    ev = tmp_path / "evidence.json"
    ev.write_text(json.dumps(evidence_for("p1", "p2")), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(CLOSE), "--life0", str(life0), "--need", need_id,
         "--evidence", str(ev), "--closed-by", "test", "--how", "test"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

    v = dc.dispatch(life0, need_id)
    assert v["verdict"] == "OBLIGATION_TERMINAL"
    assert v["obligation_state"] == "SATISFIED"
    assert not ledger_events(life0, "DISPATCH_INTENT")
    assert not ledger_events(life0, "DISPATCHED")


# 5. Eligibility revoked before dispatch refuses.
def test_eligibility_revoked_before_dispatch_refuses(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "revoked"))
    need_id = out["need_id"]

    b = dc.bind(life0, need_id)
    assert b["bound"] is True

    # Revoke eligibility WITHOUT touching the acceptance record: the need
    # is no longer commitment-origin, so EXECUTION-0 no longer authorizes it.
    needs = life0 / "state" / "needs.jsonl"
    lines = []
    for line in needs.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if (r.get("event") == "NEED_CREATED"
                and r["need"]["need_id"] == need_id):
            r["need"]["source"] = "inbox"
        lines.append(json.dumps(r, sort_keys=True))
    needs.write_text("\n".join(lines) + "\n", encoding="utf-8")

    v = dc.launch(life0, need_id)
    assert v["verdict"] == "REFUSE", v
    assert "eligibility" in v["reason"].lower()
    refused = ledger_events(life0, "DISPATCH_REFUSED")
    assert len(refused) == 1
    assert not ledger_events(life0, "DISPATCHED")


# 6. Hash-bound commitment tampering refuses.
def test_hash_bound_commitment_tampering_refuses(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "tamper"))
    need_id = out["need_id"]
    commitment_id = out["commitment_id"]

    b = dc.bind(life0, need_id)
    assert b["bound"] is True

    reg = life0 / "state" / "commitments.jsonl"
    lines = []
    for line in reg.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if (r.get("event") == "COMMITMENT_ADMITTED"
                and r["commitment"]["commitment_id"] == commitment_id):
            r["commitment"]["objective"] = "Do something else entirely"
        lines.append(json.dumps(r, sort_keys=True))
    reg.write_text("\n".join(lines) + "\n", encoding="utf-8")

    v = dc.launch(life0, need_id)
    assert v["verdict"] == "REFUSE", v
    assert "acceptance_record_hash" in v["reason"]
    assert not ledger_events(life0, "DISPATCHED")


# 7. Unsupported authority class refuses (no silent widening, no mapping).
def test_unsupported_authority_class_refuses(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "wide",
                                        authority_class="external-propose"))
    need_id = out["need_id"]

    rc, v = dispatch_json("--life0", str(life0), "dispatch", "--need", need_id)
    assert rc == 2, v
    assert v["verdict"] == "REFUSE"
    assert "not supported" in v["reason"]
    assert not ledger_events(life0, "DISPATCHED")
    assert not (life0 / "dispatch" / "chains").exists()


# 8. Two OPEN commitments obey the deterministic selector.
def test_two_open_commitments_obey_deterministic_selector(tmp_path):
    life0 = tmp_path / "life0"
    first = admit(life0, write_acceptance(
        tmp_path, "first",
        objective="First test dispatch commitment: alpha work",
        accepted_at="2026-10-01T19:00:00-04:00"))
    second = admit(life0, write_acceptance(
        tmp_path, "second",
        objective="Second test dispatch commitment: beta work",
        accepted_at="2026-10-01T19:05:00-04:00"))

    rc, v = dispatch_json("--life0", str(life0), "reconcile")
    assert v["verdict"] == "DISPATCHED", v
    assert v["selected"]["need_id"] == first["need_id"]  # earliest created_at wins

    # Still OPEN (chain not run in this test): reconcile is idempotent, no fan-out.
    rc2, v2 = dispatch_json("--life0", str(life0), "reconcile")
    assert v2["verdict"] == "ALREADY_DISPATCHED"
    assert v2["selected"]["need_id"] == first["need_id"]
    assert len(ledger_events(life0, "DISPATCHED")) == 1

    # Once the first obligation is terminal, the second dispatches.
    ev = tmp_path / "evidence2.json"
    ev.write_text(json.dumps(evidence_for("p1", "p2")), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(CLOSE), "--life0", str(life0),
         "--need", first["need_id"], "--evidence", str(ev),
         "--closed-by", "test", "--how", "test"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    rc3, v3 = dispatch_json("--life0", str(life0), "reconcile")
    assert v3["verdict"] == "DISPATCHED", v3
    assert v3["selected"]["need_id"] == second["need_id"]
    assert len(ledger_events(life0, "DISPATCHED")) == 2


# 9. The dispatcher cannot mutate the completion contract, authority class,
#    H, M, or K: only its own ledger and chain dir may change.
def test_dispatcher_write_boundary(tmp_path):
    life0 = tmp_path / "life0"
    out = admit(life0, write_acceptance(tmp_path, "boundary"))
    need_id = out["need_id"]

    watched = [
        life0 / "state" / "commitments.jsonl",
        life0 / "state" / "needs.jsonl",
        life0 / "dispatch" / "staged" / need_id / "MISSION_PACKAGE.json",
    ]
    before = {str(p): p.read_bytes() for p in watched}
    before_files = {str(p) for p in life0.rglob("*") if p.is_file()}

    dc.dispatch(life0, need_id)
    dc.reconcile(life0)

    for p in watched:
        assert p.read_bytes() == before[str(p)], f"dispatcher mutated {p}"
    after_files = {str(p) for p in life0.rglob("*") if p.is_file()}
    new_files = after_files - before_files
    allowed_prefixes = (
        str(life0 / "state" / "commitment_dispatch.jsonl"),
        str(life0 / "dispatch" / "chains"),
    )
    for f in new_files:
        assert f.startswith(allowed_prefixes), f"dispatcher wrote outside its boundary: {f}"
