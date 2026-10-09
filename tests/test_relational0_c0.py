"""RELATIONAL-0 C0 acceptance tests: isolation of two persistent cells.

Threat model (tested): a cell worker (or pre-existing workspace content)
attempting to read the other cell's private data through any exposed interface.
Establishes isolation against THIS threat model, not universal hard isolation.

Patterns follow life-0/tests conventions: tmp_path isolated trees, no network,
no model calls except the marked real-worker test.
"""
import json
import os
import sys

import pytest

LIFE0 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LIFE0)

from cells import evidence, exchange, registry, view  # noqa: E402
from cells.policy import CELL_ACTIONS, get_policy, verify_um_root  # noqa: E402
from cells.run import run_cell_turn  # noqa: E402


@pytest.fixture()
def life0(tmp_path):
    d = str(tmp_path / "life0")
    os.makedirs(os.path.join(d, "state"))
    return d


@pytest.fixture()
def two_cells(life0):
    wa = os.path.join(life0, "cell_workspaces", "cell_a")
    wb = os.path.join(life0, "cell_workspaces", "cell_b")
    os.makedirs(wa)
    os.makedirs(wb)
    registry.register_cell(life0, "cell_a", "proc-hash-a", wa)
    registry.register_cell(life0, "cell_b", "proc-hash-b", wb)
    return {"cell_a": wa, "cell_b": wb}


def _act(kind, args=None):
    return {"kind": kind, "args": args or {}, "rationale": "test"}


def _stub(actions):
    it = iter(actions)

    def w(packet):
        try:
            return next(it)
        except StopIteration:
            return _act("stop", {"status": "complete"})
    return w


# --- C0: identity, separation, persistence ---------------------------------

def test_registry_identity_and_persistence(life0, two_cells):
    a = registry.get_cell(life0, "cell_a")
    b = registry.get_cell(life0, "cell_b")
    assert a["cell_id"] != b["cell_id"]
    assert a["owner"] == {"kind": "cell", "id": "cell_a"}
    assert "record_hash" in a
    # Persistence: re-read from disk.
    assert registry.get_cell(life0, "cell_a")["procedure_ref"] == "proc-hash-a"


def test_effective_history_separation(life0, two_cells):
    canary = "CANARY-A-9f2c41"
    ha = view.effective_history(
        life0, "cell_a",
        run_events=[{"owner": {"kind": "cell", "id": "cell_a"},
                     "note": canary}])
    hb = view.effective_history(life0, "cell_b", run_events=[])
    blob_a = json.dumps(ha)
    blob_b = json.dumps(hb)
    assert canary in blob_a
    assert canary not in blob_b


def test_packet_excludes_other_cell(life0, two_cells):
    canary = "CANARY-PACKET-77e0"
    ha = view.effective_history(
        life0, "cell_a",
        run_events=[{"owner": {"kind": "cell", "id": "cell_a"},
                     "note": canary}])
    pkt_b = view.build_packet("cell_b", "proc-hash-b", two_cells["cell_b"],
                              1, 4, [], objective="test")
    pkt_a = view.build_packet("cell_a", "proc-hash-a", two_cells["cell_a"],
                              1, 4, ha, objective="test")
    assert canary not in json.dumps(pkt_b)
    assert canary in json.dumps(pkt_a)
    # No residue injection, no foreign tools.
    assert "organism_residue" not in pkt_b
    assert set(pkt_b["workspace_rules"]["tools"]) == set(CELL_ACTIONS)


# --- C0: policy enforcement --------------------------------------------------

@pytest.mark.parametrize("kind", ["run", "search", "apply_patch", "git_diff",
                                  "status", "list_dir", "exec", ""])
def test_policy_rejects_non_cell_actions(kind):
    pol, PolicyError = get_policy()
    import types
    with pytest.raises(PolicyError):
        pol.validate(types.SimpleNamespace(kind=kind, args={}))


@pytest.mark.parametrize("bad_hash", [
    "../../etc/passwd", "a" * 63, "a" * 65, "ZZ" * 32, "", "a" * 64 + "/x",
])
def test_policy_rejects_malformed_evidence_hash(bad_hash):
    pol, PolicyError = get_policy()
    import types
    with pytest.raises(PolicyError):
        pol.validate(types.SimpleNamespace(
            kind="fetch_evidence", args={"hash": bad_hash}))


def test_fail_closed_identity():
    with pytest.raises(RuntimeError):
        verify_um_root("/nonexistent/baseline/tree")


# --- C0: adversarial filesystem ---------------------------------------------

def _plant_escape_routes(ws_b, life0):
    """Pre-existing workspace content per the threat model."""
    os.makedirs(ws_b, exist_ok=True)
    with open(os.path.join(ws_b, "link_out"), "w") as f:
        pass
    os.remove(os.path.join(ws_b, "link_out"))
    os.symlink("/etc/hostname", os.path.join(ws_b, "link_out"))
    os.symlink("/etc", os.path.join(ws_b, "dir_link_out"))


def test_adversarial_read_interfaces(life0, two_cells):
    _plant_escape_routes(two_cells["cell_b"], life0)
    secret = "SECRET-A-abcdef"
    with open(os.path.join(two_cells["cell_a"], "private.txt"), "w") as f:
        f.write(secret)
    attacks = [
        _act("read_file", {"path": "../cell_a/private.txt"}),
        _act("read_file", {"path": "/etc/hostname"}),
        _act("read_file", {"path": "link_out"}),
        _act("read_file", {"path": "dir_link_out/hostname"}),
        _act("read_file", {"path": two_cells["cell_a"] + "/private.txt"}),
        _act("fetch_evidence", {"hash": "b" * 64}),  # valid format, unauthorized
        _act("write_file", {"path": "../cell_a/evil.txt", "content": "x"}),
    ]
    turn = run_cell_turn(life0, "cell_b", _stub(attacks), "adversarial",
                         len(attacks) + 1, "obl-test",
                         os.path.join(life0, "runs", "adv"))
    disclosed = json.dumps(turn)
    assert secret not in disclosed
    # Every attack must be refused at the boundary (rejected or failed),
    # never executed successfully. The stub's trailing stop is legitimate.
    attack_kinds = {"read_file", "write_file", "fetch_evidence"}
    for s in turn["steps"]:
        if "rejected" in s:
            continue
        if s.get("action", {}).get("kind") in attack_kinds:
            assert s["result"]["ok"] is False, s


def test_unauthorized_hash_not_disclosed(life0, two_cells):
    digest = evidence.publish(life0, b"published-by-a")
    with pytest.raises(evidence.EvidenceError):
        evidence.fetch_evidence(life0, "cell_b", digest, [])


def test_corrupted_artifact_detected(life0, two_cells):
    data = b"integrity-test-bytes"
    digest = evidence.publish(life0, data)
    xrec = {"decision": "permitted", "received_by": "cell_b",
            "artifact_hash": digest}
    path = os.path.join(evidence.shared_root(life0), digest)
    with open(path, "wb") as f:
        f.write(b"tampered")
    with pytest.raises(evidence.EvidenceError, match="mismatch"):
        evidence.fetch_evidence(life0, "cell_b", digest, [xrec])


def test_crafted_digest_path_traversal_closed(life0):
    with pytest.raises(evidence.EvidenceError):
        evidence.fetch_evidence(life0, "cell_b", "a" * 63 + "/", [])


# --- C0: worker reuse, authority --------------------------------------------

def test_worker_reuse_no_crosstalk(life0, two_cells):
    seen = []

    def worker(packet):
        seen.append((packet["cell"]["cell_id"],
                     json.dumps(packet)))
        return _act("stop", {"status": "complete"})

    run_cell_turn(life0, "cell_a", worker, "reuse", 2, "obl-test",
                  os.path.join(life0, "runs", "ra"))
    run_cell_turn(life0, "cell_b", worker, "reuse", 2, "obl-test",
                  os.path.join(life0, "runs", "rb"))
    assert seen[0][0] == "cell_a" and seen[1][0] == "cell_b"
    assert "cell_a" not in seen[1][1].replace('"cell_id": "cell_b"', "")


def test_halted_before_start(life0, two_cells):
    dpath = os.path.join(life0, "state", "obligation_directives.jsonl")
    with open(dpath, "w") as f:
        f.write(json.dumps({"need_id": "obl-test", "at": 1,
                            "directive": "BLOCK"}) + "\n")
    turn = run_cell_turn(life0, "cell_a", _stub([]), "halted", 2,
                         "obl-test", os.path.join(life0, "runs", "h"))
    assert turn["started"] is False


def test_subprocess_worker_ipc(life0, two_cells, tmp_path):
    stub = tmp_path / "stub_worker.py"
    stub.write_text(
        "import json,sys\n"
        "msg=json.load(sys.stdin)\n"
        "p=msg.get('packet', msg)\n"
        "assert p['cell']['cell_id']=='cell_a'\n"
        "json.dump({'kind':'stop','args':{'status':'complete'},"
        "'rationale':'ok'}, sys.stdout)\n")
    turn = run_cell_turn(life0, "cell_a", [sys.executable, str(stub)],
                         "ipc", 2, "obl-test",
                         os.path.join(life0, "runs", "ipc"))
    assert turn["steps"] and \
        turn["steps"][-1]["action"]["kind"] == "stop"
