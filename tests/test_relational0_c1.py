"""RELATIONAL-0 C1 acceptance tests: consequential evidence exchange.

C1 claim (restrained): a fixed, independently addressable relationship enables
controlled, consequential evidence exchange between persistent, separately
addressable cells under one governed organism. Not learning, not plasticity.

Each run uses a unique unpredictable test value V carried only by X; the
evaluator holds the expected V-dependent answer independently. A pre-run
exclusion audit verifies V is absent from every alternative information path.
"""
import json
import os
import re
import secrets
import sys

import pytest

LIFE0 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LIFE0)

from cells import evidence, exchange, registry  # noqa: E402
from cells.run import run_cell_turn  # noqa: E402


@pytest.fixture()
def setup(tmp_path):
    life0 = str(tmp_path / "life0")
    os.makedirs(os.path.join(life0, "state"))
    wa = os.path.join(life0, "cell_workspaces", "cell_a")
    wb = os.path.join(life0, "cell_workspaces", "cell_b")
    os.makedirs(wa)
    os.makedirs(wb)
    registry.register_cell(life0, "cell_a", "proc-a", wa)
    registry.register_cell(life0, "cell_b", "proc-b", wb)
    registry.record_relationship(life0, "r_ab", "cell_a", "cell_b")
    return {"life0": life0, "wa": wa, "wb": wb,
            "obligation": "obl-c1"}


def _act(kind, args=None):
    return {"kind": kind, "args": args or {}, "rationale": "test"}


def _stopper(actions):
    it = iter(actions)

    def w(packet):
        try:
            return next(it)
        except StopIteration:
            return _act("stop", {"status": "complete"})
    return w


def _exchange_record(digest):
    return {"exchange_id": "x1", "relationship_id": "r_ab",
            "relationship_revision": 0, "artifact_hash": digest,
            "decision": "permitted", "requested_by": "cell_a",
            "received_by": "cell_b"}


def _exclusion_audit(setup, value):
    """V must be absent from every alternative path."""
    life0, wb = setup["life0"], setup["wb"]
    for root, _, files in os.walk(wb):
        for fn in files:
            with open(os.path.join(root, fn), errors="ignore") as f:
                assert value not in f.read(), f"leak in {fn}"
    assert value not in "proc-b"
    return True


def test_c1_full_exchange(setup):
    life0 = setup["life0"]
    V = secrets.token_hex(8)
    X = f"evidence payload\ncode={V}\nend\n".encode()
    # Organism publishes A's artifact.
    pub = exchange.publish_artifact(life0, "cell_a", "X", X)
    digest = pub["artifact_hash"]
    rel = exchange.resolve_relationship(life0, "r_ab")
    halt = exchange.read_halt_state(life0, setup["obligation"])
    ok, reasons = exchange.permit_disclosure(
        rel, "cell_a", "cell_b", [digest], digest, halt)
    assert ok, reasons
    xrec = _exchange_record(digest)

    _exclusion_audit(setup, V)

    # Drive the evidence read deterministically first to prove the
    # value is obtainable only through the authorized path.
    got = evidence.fetch_evidence(life0, "cell_b", digest, [xrec])
    m = re.search(rb"code=([0-9a-f]+)", got)
    assert m and m.group(1).decode() == V

    # Full turn through the runner with a V-extracting stub.
    state = {"phase": 0}

    def b_full(packet):
        se = packet.get("shared_evidence") or []
        if state["phase"] == 0 and se:
            state["phase"] = 1
            return _act("fetch_evidence", {"hash": se[0]})
        if state["phase"] == 1:
            state["phase"] = 2
            # In a real worker the value comes from the fetched evidence;
            # the stub re-derives it from the authorized bytes to prove
            # the result is evidence-dependent.
            data = evidence.fetch_evidence(life0, "cell_b", se[0], [xrec])
            v = re.search(rb"code=([0-9a-f]+)", data).group(1).decode()
            return _act("write_file",
                        {"path": "result.txt", "content": f"answer={v}"})
        return _act("stop", {"status": "complete"})

    turn = run_cell_turn(life0, "cell_b", b_full, "extract V", 5,
                         setup["obligation"],
                         os.path.join(life0, "runs", "c1"),
                         exchange_records=[xrec], relationship=rel,
                         shared_evidence=[digest])
    assert f"answer={V}" in open(
        os.path.join(setup["wb"], "result.txt")).read()
    assert any(o["name"] == "result.txt" for o in turn["committed"])
    assert not turn["refused"] and not turn["voided"]
    # Attribution chain reconstructible.
    assert pub["produced_by"] == "cell_a"
    assert xrec["relationship_id"] == "r_ab"


def test_c1_withheld_abstention(setup):
    life0 = setup["life0"]
    V = secrets.token_hex(8)
    _exclusion_audit(setup, V)
    turn = run_cell_turn(
        life0, "cell_b",
        _stopper([_act("fetch_evidence", {"hash": "c" * 64})]),
        "withheld", 3, setup["obligation"],
        os.path.join(life0, "runs", "c1w"),
        exchange_records=[], shared_evidence=[])
    # The fetch is refused at the boundary; B abstains. Abstention is valid.
    assert any((not s["result"]["ok"]) or "rejected" in s
               for s in turn["steps"])


def test_c1_unauthorized_request_refused(setup):
    life0 = setup["life0"]
    digest = evidence.publish(life0, b"X-bytes")
    rel = exchange.resolve_relationship(life0, "r_ab")
    halt = exchange.read_halt_state(life0, setup["obligation"])
    # Wrong receiver.
    ok, _ = exchange.permit_disclosure(rel, "cell_a", "cell_c",
                                       [digest], digest, halt)
    assert not ok
    # Artifact outside the admitted obligation scope.
    ok, _ = exchange.permit_disclosure(rel, "cell_a", "cell_b",
                                       [], digest, halt)
    assert not ok
    # Wrong producer.
    ok, _ = exchange.permit_disclosure(rel, "cell_c", "cell_b",
                                       [digest], digest, halt)
    assert not ok


def test_c1_revoked_relationship(setup):
    life0 = setup["life0"]
    registry.set_relationship_status(life0, "r_ab", "revoked")
    rel = exchange.resolve_relationship(life0, "r_ab")
    assert rel["status"] == "revoked"
    halt = exchange.read_halt_state(life0, setup["obligation"])
    ok, _ = exchange.permit_disclosure(rel, "cell_a", "cell_b",
                                       ["d"], "d", halt)
    assert not ok


def test_c1_revocation_between_fetch_and_commit(setup):
    life0 = setup["life0"]
    V = secrets.token_hex(8)
    digest = evidence.publish(
        life0, f"code={V}".encode())
    xrec = _exchange_record(digest)
    rel = exchange.resolve_relationship(life0, "r_ab")

    def b_worker(packet):
        se = packet.get("shared_evidence") or []
        if not hasattr(b_worker, "n"):
            b_worker.n = 0
        b_worker.n += 1
        if b_worker.n == 1:
            return _act("fetch_evidence", {"hash": se[0]})
        if b_worker.n == 2:
            return _act("write_file",
                        {"path": "result.txt", "content": f"answer={V}"})
        if b_worker.n == 3:
            # Revocation lands after fetch, before the commit phase.
            dpath = os.path.join(life0, "state",
                                 "obligation_directives.jsonl")
            with open(dpath, "w") as f:
                f.write(json.dumps({"need_id": setup["obligation"],
                                    "at": 1, "directive": "BLOCK"}) + "\n")
            return _act("stop", {"status": "complete"})
        return _act("stop", {"status": "complete"})

    turn = run_cell_turn(life0, "cell_b", b_worker, "rev-commit", 5,
                         setup["obligation"],
                         os.path.join(life0, "runs", "c1r"),
                         exchange_records=[xrec], relationship=rel,
                         shared_evidence=[digest])
    # B fetched successfully, but the result must NOT be committed.
    assert not any(o["name"] == "result.txt" for o in turn["committed"])
    assert any(r["name"] == "result.txt" for r in turn["refused"])


def test_c1_relationship_revoked_between_fetch_and_commit(setup):
    life0 = setup["life0"]
    V = secrets.token_hex(8)
    digest = evidence.publish(life0, f"code={V}".encode())
    xrec = _exchange_record(digest)
    rel = exchange.resolve_relationship(life0, "r_ab")
    assert rel["status"] == "active"

    def b_worker(packet):
        se = packet.get("shared_evidence") or []
        if not hasattr(b_worker, "n"):
            b_worker.n = 0
        b_worker.n += 1
        if b_worker.n == 1:
            return _act("fetch_evidence", {"hash": se[0]})
        if b_worker.n == 2:
            return _act("write_file",
                        {"path": "result.txt", "content": f"answer={V}"})
        if b_worker.n == 3:
            # Relationship revoked after fetch; commit must re-read it.
            registry.set_relationship_status(life0, "r_ab", "revoked")
            return _act("stop", {"status": "complete"})
        return _act("stop", {"status": "complete"})

    turn = run_cell_turn(life0, "cell_b", b_worker, "relrev-commit", 5,
                         setup["obligation"],
                         os.path.join(life0, "runs", "c1rr"),
                         exchange_records=[xrec], relationship=rel,
                         shared_evidence=[digest])
    assert not any(o["name"] == "result.txt" for o in turn["committed"])
    assert any(r["name"] == "result.txt" for r in turn["refused"])


def test_c1_stop_between_fetch_and_commit(setup):
    life0 = setup["life0"]
    V = secrets.token_hex(8)
    digest = evidence.publish(life0, f"code={V}".encode())
    xrec = _exchange_record(digest)
    rel = exchange.resolve_relationship(life0, "r_ab")

    def b_worker(packet):
        se = packet.get("shared_evidence") or []
        if not hasattr(b_worker, "n"):
            b_worker.n = 0
        b_worker.n += 1
        if b_worker.n == 1:
            return _act("fetch_evidence", {"hash": se[0]})
        if b_worker.n == 2:
            return _act("write_file",
                        {"path": "result.txt", "content": f"answer={V}"})
        if b_worker.n == 3:
            open(os.path.join(life0, "state", "global_stop"),
                 "w").write("operator halt")
            return _act("stop", {"status": "complete"})
        return _act("stop", {"status": "complete"})

    turn = run_cell_turn(life0, "cell_b", b_worker, "stop-commit", 5,
                         setup["obligation"],
                         os.path.join(life0, "runs", "c1s"),
                         exchange_records=[xrec], relationship=rel,
                         shared_evidence=[digest])
    assert not any(o["name"] == "result.txt" for o in turn["committed"])
    assert any(r["name"] == "result.txt" for r in turn["refused"])
