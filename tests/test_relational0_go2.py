"""RELATIONAL-0 GO-2 acceptance tests: operational integration.

Three workstreams, in order. Each produces evidence; a failure stops the
sequence and is classified (integration / governance / worker-capability).

Step 1: the admitted-obligation path launches a cell turn; unauthorized
        dispatch is refused. Uses the REAL admission and dispatch-intent
        scripts; only the AUTO-WORK-0-gated launch staging is replicated
        mechanically (documented below).
Step 2: a revocation racing the commit window cannot produce an unauthorized
        committed result (detection-and-void, honestly scoped).
Step 3: the worker interface is frozen; a real worker gets exactly the
        four-tool contract.
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time

import pytest

LIFE0_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LIFE0_ROOT)
REAL_SCRIPTS = os.path.join(LIFE0_ROOT, "scripts")

from cells import evidence, exchange, registry  # noqa: E402
from cells.run import run_cell_turn  # noqa: E402


def _load_script(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REAL_SCRIPTS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_life0(tmp_path, name):
    life0 = tmp_path / f"life0-{name}"
    (life0 / "state").mkdir(parents=True)
    (life0 / "dispatch" / "staged").mkdir(parents=True)
    (life0 / "dispatch" / "chains").mkdir(parents=True)
    # Real scripts, symlinked: same code, isolated state.
    os.symlink(REAL_SCRIPTS, life0 / "scripts")
    return life0


def _admit(life0):
    acc = {
        "objective": "GO-2 cell integration test obligation",
        "completion_contract": {"predicates": [
            {"id": "cell-turn-runs", "verifiable": "report exists"},
        ]},
        "authority_class": "local-build",
        "scope": "test tree only",
        "accepted_by": "operator",
        "accepted_at": "2026-10-09T12:00:00-04:00",
        "origin": "go2 test",
        "parent_commitment_id": None,
    }
    acc_path = life0 / "acceptance.json"
    acc_path.write_text(json.dumps(acc), encoding="utf-8")
    cp = subprocess.run(
        [sys.executable, os.path.join(REAL_SCRIPTS, "admit_commitment.py"),
         "--life0", str(life0), "--acceptance", str(acc_path)],
        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return json.loads(cp.stdout)


def _dispatch_intent(life0, need_id):
    dc = _load_script("dispatch_commitment")
    out = dc.dispatch(life0, need_id)
    assert out["verdict"] == "DISPATCHED", out
    chain_id = out["chain_id"]
    bpath = life0 / "dispatch" / "chains" / chain_id / "CHAIN_BINDING.json"
    binding = json.loads(bpath.read_text(encoding="utf-8"))
    return binding, bpath, chain_id


def _stub_worker_script(path):
    path.write_text(
        "import json,sys\n"
        "msg=json.load(sys.stdin)\n"
        "p=msg.get('packet',msg)\n"
        "n=getattr(sys,'_n',0)+1; sys._n=n\n"
        "open('/tmp/go2_stub_count','a').write(str(n)+'\\n')\n"
        "if n==1:\n"
        "    json.dump({'kind':'write_file','args':{'path':'hello.txt',"
        "'content':'cell-turn-ok'},'rationale':'t'},sys.stdout)\n"
        "else:\n"
        "    json.dump({'kind':'stop','args':{'status':'complete'},"
        "'rationale':'t'},sys.stdout)\n")


def _um_root():
    return os.environ.get(
        "RELATIONAL0_UM_ROOT", "/home/hatch/workspace/unified_machine_v0")


def _worker_shim():
    return os.environ.get(
        "RELATIONAL0_WORKER_SHIM",
        "/home/hatch/workspace/llama_um_worker.py")


def _host_config(tmp_path, worker_argv):
    cfg = {
        "worker_argv": worker_argv,
        "um_root": _um_root(),
        "default_bounds": {"max_seconds": 300, "max_steps": 4,
                           "finalization_reserve_seconds": 30},
        "proxy_env": {},
        "acceptance_checkers": {},
    }
    p = tmp_path / "chain_adapter_config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


def _request(life0, need_id, binding_path, binding_hash, workspace,
             report_path, cell_id):
    req = {
        "life0": str(life0),
        "need_id": need_id,
        "binding_path": str(binding_path),
        "binding_hash": binding_hash,
        "workspace": str(workspace),
        "report_path": str(report_path),
        "api_budget_usd": 0,
        "cell_id": cell_id,
    }
    p = life0 / "REQUEST.json"
    p.write_text(json.dumps(req), encoding="utf-8")
    return str(p)


def _binding_hash(binding_path):
    import hashlib
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    return hashlib.sha256(json.dumps(
        binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------- step 1

def test_step1_admitted_obligation_launches_cell_turn(tmp_path, monkeypatch):
    life0 = _make_life0(tmp_path, "go2a")
    adm = _admit(life0)
    need_id = adm["need_id"]
    binding, bpath, chain_id = _dispatch_intent(life0, need_id)

    ws = life0 / "cell_workspaces" / "cell_a"
    ws.mkdir(parents=True)
    sys.path.insert(0, LIFE0_ROOT)
    from cells import registry as reg
    reg.register_cell(str(life0), "cell_a", "proc-a", str(ws))

    stub = tmp_path / "stub.py"
    _stub_worker_script(stub)
    try:
        os.remove("/tmp/go2_stub_count")
    except OSError:
        pass
    cfg = _host_config(tmp_path, [sys.executable, str(stub)])
    monkeypatch.setenv("CHAIN_ADAPTER_CONFIG", cfg)
    report_path = life0 / "report.json"
    req_path = _request(life0, need_id, bpath, _binding_hash(bpath),
                        ws, report_path, "cell_a")

    import chain_adapter
    rc = chain_adapter.main(["chain_adapter.py", req_path])
    assert rc == 0, f"adapter returned {rc}"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["kind"] == "cell_turn"
    assert report["launched"] is True
    assert report["cell_id"] == "cell_a"
    # The stub ran through the dispatched path (worker executions happened).
    assert os.path.exists("/tmp/go2_stub_count")
    assert (ws / "hello.txt").read_text() == "cell-turn-ok"


def test_step1_unauthorized_dispatch_refused(tmp_path, monkeypatch):
    life0 = _make_life0(tmp_path, "go2b")
    adm = _admit(life0)
    need_id = adm["need_id"]
    binding, bpath, chain_id = _dispatch_intent(life0, need_id)
    ws = life0 / "cell_workspaces" / "cell_a"
    ws.mkdir(parents=True)
    sys.path.insert(0, LIFE0_ROOT)
    from cells import registry as reg
    reg.register_cell(str(life0), "cell_a", "proc-a", str(ws))
    stub = tmp_path / "stub.py"
    _stub_worker_script(stub)
    cfg = _host_config(tmp_path, [sys.executable, str(stub)])
    monkeypatch.setenv("CHAIN_ADAPTER_CONFIG", cfg)
    import chain_adapter
    binding_hash = _binding_hash(bpath)

    def run_request(cell_id, workspace, bhash):
        rp = life0 / "report.json"
        req = {"life0": str(life0), "need_id": need_id,
               "binding_path": str(bpath), "binding_hash": bhash,
               "workspace": str(workspace), "report_path": str(rp),
               "api_budget_usd": 0, "cell_id": cell_id}
        pp = life0 / "REQ.json"
        pp.write_text(json.dumps(req))
        return chain_adapter.main(["chain_adapter.py", str(pp)])

    other_ws = life0 / "elsewhere"
    other_ws.mkdir()
    # Tampered binding hash.
    assert run_request("cell_a", ws, "0" * 64) == 2
    # Unknown cell.
    assert run_request("cell_zzz", ws, binding_hash) == 2
    # Workspace mismatch: request names a different dir than the registry.
    assert run_request("cell_a", other_ws, binding_hash) == 2
    # Revoked before launch: zero worker executions.
    (life0 / "state" / "obligation_directives.jsonl").write_text(
        json.dumps({"need_id": need_id, "at": 1,
                    "directive": "BLOCK"}) + "\n")
    rp = life0 / "report.json"
    req = {"life0": str(life0), "need_id": need_id,
           "binding_path": str(bpath), "binding_hash": binding_hash,
           "workspace": str(ws), "report_path": str(rp),
           "api_budget_usd": 0, "cell_id": "cell_a"}
    pp = life0 / "REQ2.json"
    pp.write_text(json.dumps(req))
    assert chain_adapter.main(["chain_adapter.py", str(pp)]) == 0
    report = json.loads(rp.read_text())
    assert report["launched"] is False


# ---------------------------------------------------------------- step 2

def _cell_setup(tmp_path):
    life0 = str(tmp_path / "life0")
    os.makedirs(os.path.join(life0, "state"))
    wa = os.path.join(life0, "ws", "cell_a")
    os.makedirs(wa)
    from cells import registry as reg
    reg.register_cell(life0, "cell_a", "proc-a", wa)
    return life0, wa


def test_step2_racing_revocation_voids_commit(tmp_path):
    life0, wa = _cell_setup(tmp_path)

    def racer():
        time.sleep(0.05)
        with open(os.path.join(life0, "state",
                               "obligation_directives.jsonl"), "w") as f:
            f.write(json.dumps({"need_id": "obl", "at": 1,
                                "directive": "BLOCK"}) + "\n")

    t = threading.Thread(target=racer)
    t.start()
    out = exchange.commit_result(
        life0, "cell_a", "obl", None, "result.txt", b"data",
        lambda k, p: None,
        pre_write_hook=lambda: time.sleep(0.2))
    t.join()
    assert out["status"] == "voided", out["status"]
    # Artifact quarantined out of the trusted store.
    digest = out["artifact_hash"]
    assert not os.path.exists(
        os.path.join(evidence.shared_root(life0), digest))
    assert os.path.exists(os.path.join(
        life0, "state", "shared_artifacts_quarantine", digest))


def test_step2_quiet_window_commits(tmp_path):
    life0, wa = _cell_setup(tmp_path)
    out = exchange.commit_result(
        life0, "cell_a", "obl", None, "result.txt", b"data",
        lambda k, p: None)
    assert out["status"] == "committed", out
    digest = out["artifact"]["artifact_hash"]
    assert os.path.exists(os.path.join(evidence.shared_root(life0), digest))


def test_step2_preexisting_revocation_refuses(tmp_path):
    life0, wa = _cell_setup(tmp_path)
    with open(os.path.join(life0, "state",
                           "obligation_directives.jsonl"), "w") as f:
        f.write(json.dumps({"need_id": "obl", "at": 1,
                            "directive": "RETIRE"}) + "\n")
    out = exchange.commit_result(
        life0, "cell_a", "obl", None, "result.txt", b"data",
        lambda k, p: None)
    assert out["status"] == "refused"


# ---------------------------------------------------------------- step 3

def test_step3_cell_contract_frozen():
    import hashlib
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "w", _worker_shim())
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    contract = w.CELL_ACTION_CONTRACT + "\n\n" + w.CELL_EXAMPLE
    h = hashlib.sha256(contract.encode()).hexdigest()
    frozen = open(os.path.join(
        LIFE0_ROOT, "cells", "CELL_CONTRACT_FROZEN.md")).read()
    assert h in frozen, "worker contract drifted from frozen interface"
    assert h == "dce28a328c306e34f5b0f28dfe9e6be2bd70e4aef93da8ff49227ff788d728d3"
    # Exactly the four tools; the organism's extra tools are absent.
    assert "fetch_evidence" in contract
    assert '"kind": "read_file|write_file|fetch_evidence|stop"' in contract
    for extra in ["\"search\"", "\"run\"", "\"apply_patch\"", "\"git_diff\""]:
        assert extra not in contract, extra
    assert set(w.CELL_ACTION_SCHEMA["properties"]["kind"]["enum"]) == {
        "read_file", "write_file", "fetch_evidence", "stop"}
