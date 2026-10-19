"""Outer-loop integration tests. All jobs are isolated test fixtures, not B1."""
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import admit_commitment as admission
import research_loop as loop
import dispatch_commitment as dispatch
import close_commitment
import resource_queue


def admit(life0, name, authority="local-build"):
    acceptance = {"objective": f"Verify isolated outer loop fixture {name}",
                  "completion_contract": {"predicates": [{"id": "p1", "verifiable": "fixture verified"}]},
                  "authority_class": authority, "scope": "isolated test workspace",
                  "accepted_by": "operator", "accepted_at": loop.now(), "origin": "test fixture"}
    path = life0.parent / (name + ".json")
    path.write_text(json.dumps(acceptance))
    result = admission.admit(life0, acceptance)
    assert result["admitted"], result
    return result["need_id"]


def setup(life0, tmp_path, behavior="normal", launches=8, seconds=20, timeout=2):
    # A trusted chain adapter fixture. PASS must use the real closure gate.
    worker = tmp_path / "worker.py"
    worker.write_text(f'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, {str(ROOT / "scripts")!r})
import close_commitment, research_loop
r = json.loads(Path(sys.argv[1]).read_text())
root = Path(r["life0"])
name = r["need_id"]
with (root / "executions.jsonl").open("a") as f:
    f.write(json.dumps({{"need_id": name, "apis_disabled": os.environ.get("SOVEREIGN_APIS_DISABLED")}}) + "\\n")
behavior = {behavior!r}
c = research_loop.eligibility.find_need(root, name)
commitment = research_loop.eligibility.find_commitment(root, c["commitment_id"])
objective = commitment["objective"]
status = "FAIL" if "fail" in objective else "BLOCKED" if "blocked" in objective else "PASS"
if behavior == "sleep":
    time.sleep(10)
if behavior == "hold":
    research_loop.append(root / research_loop.CONTROLS, {{"directive": "HOLD", "need_id": None}})
    time.sleep(10)
if behavior == "malformed":
    Path(r["report_path"]).write_text("{{")
    sys.exit(0)
if status == "PASS" and behavior != "lie":
    out = close_commitment.close(root, name, {{"p1": {{"verdict": "verified", "evidence": "isolated fixture"}}}}, "test worker", "fixture verification")
    assert out["closed"], out
Path(r["report_path"]).write_text(json.dumps({{"status": status, "reason": "fixture result"}}))
''')
    loop.initialize(life0, [sys.executable, str(worker)], launches, seconds, timeout)
    return worker


def executions(life0):
    return loop.records(life0 / "executions.jsonl")


def test_pass_fail_blocked_select_next_without_human_turn(tmp_path):
    life0 = tmp_path / "node"
    needs = [admit(life0, n) for n in ("pass-first", "fail-middle", "blocked-middle", "pass-last")]
    setup(life0, tmp_path)
    outcome = loop.run(life0)
    assert outcome["status"] == "NO_EXECUTABLE_FRONTIER"
    assert [r["need_id"] for r in executions(life0)] == needs
    assert all(r["apis_disabled"] == "1" for r in executions(life0))
    history = loop.verified_history(life0)
    results = [r for r in history if r["event"] == "LOCAL_RESULT"]
    assert [r["status"] for r in results] == ["PASS", "FAIL", "BLOCKED", "PASS"]
    assert len([r for r in history if r["event"] == "FRONTIER_REFRESHED"]) >= 5


def test_restart_and_replay_never_duplicate(tmp_path):
    life0 = tmp_path / "node"
    admit(life0, "one")
    setup(life0, tmp_path)
    loop.run(life0)
    before = len(executions(life0))
    loop.run(life0)
    loop.run(life0)
    assert len(executions(life0)) == before == 1


def test_crash_intent_is_uncertain_and_never_relaunched(tmp_path):
    life0 = tmp_path / "node"
    nid = admit(life0, "one")
    setup(life0, tmp_path)
    loop.event(life0, "WORKER_INTENT", need_id=nid, reserved_seconds=2)
    assert loop.run(life0)["status"] == "EXECUTION_UNCERTAIN"
    assert not executions(life0)


def test_existing_dispatched_open_job_does_not_starve_next(tmp_path):
    life0 = tmp_path / "node"
    first = admit(life0, "first")
    second = admit(life0, "second")
    assert dispatch.dispatch(life0, first)["verdict"] == "DISPATCHED"
    setup(life0, tmp_path)
    loop.run(life0)
    assert [r["need_id"] for r in executions(life0)] == [second]


def test_unsupported_authority_does_not_starve_ready_work(tmp_path):
    life0 = tmp_path / "node"
    admit(life0, "external", "external-propose")
    eligible = admit(life0, "internal")
    setup(life0, tmp_path)
    loop.run(life0)
    assert [r["need_id"] for r in executions(life0)] == [eligible]


def test_lifetime_budget_cannot_reset_on_restart(tmp_path):
    life0 = tmp_path / "node"
    admit(life0, "first")
    admit(life0, "second")
    setup(life0, tmp_path, launches=1)
    assert loop.run(life0)["status"] == "BUDGET_EXHAUSTED"
    assert loop.run(life0)["status"] == "BUDGET_EXHAUSTED"
    assert len(executions(life0)) == 1
    with pytest.raises(ValueError, match="cannot reset"):
        loop.initialize(life0, [sys.executable], 100, 100, 1)


def test_seconds_are_reserved_durably(tmp_path):
    life0 = tmp_path / "node"
    admit(life0, "first")
    admit(life0, "second")
    setup(life0, tmp_path, seconds=0.3, timeout=0.3)
    assert loop.run(life0)["status"] == "BUDGET_EXHAUSTED"
    assert len(executions(life0)) == 1
    assert loop.run(life0)["status"] == "BUDGET_EXHAUSTED"


def test_global_stop_blocks_launch(tmp_path):
    life0 = tmp_path / "node"
    admit(life0, "first")
    setup(life0, tmp_path)
    loop.append(life0 / loop.CONTROLS, {"directive": "STOP", "need_id": None})
    assert loop.run(life0)["status"] == "OPERATOR_VETO"
    assert not executions(life0)


def test_local_hold_allows_other_authorized_work(tmp_path):
    life0 = tmp_path / "node"
    first = admit(life0, "first")
    second = admit(life0, "second")
    setup(life0, tmp_path)
    loop.append(life0 / loop.CONTROLS, {"directive": "HOLD", "need_id": first})
    loop.run(life0)
    assert [r["need_id"] for r in executions(life0)] == [second]


def test_hold_during_worker_terminates_and_prevents_next(tmp_path):
    life0 = tmp_path / "node"
    admit(life0, "first")
    admit(life0, "second")
    setup(life0, tmp_path, behavior="hold")
    assert loop.run(life0)["status"] == "OPERATOR_VETO"
    assert len(executions(life0)) == 1
    assert any(r.get("reason") == "OPERATOR_VETO" for r in loop.verified_history(life0))


def test_timeout_fails_locally_then_selects_again(tmp_path):
    life0 = tmp_path / "node"
    needs = [admit(life0, n) for n in ("first", "second")]
    setup(life0, tmp_path, behavior="sleep", timeout=0.2)
    loop.run(life0)
    assert [r["need_id"] for r in executions(life0)] == needs
    assert [r["status"] for r in loop.verified_history(life0) if r["event"] == "LOCAL_RESULT"] == ["FAIL", "FAIL"]


def test_worker_pass_cannot_self_certify_completion(tmp_path):
    life0 = tmp_path / "node"
    admit(life0, "one")
    setup(life0, tmp_path, behavior="lie")
    loop.run(life0)
    results = [r for r in loop.verified_history(life0) if r["event"] == "LOCAL_RESULT"]
    assert results[0]["status"] == "BLOCKED"
    assert results[0]["obligation_state"] == "OPEN"


def test_worker_and_config_tampering_refuse(tmp_path):
    life0 = tmp_path / "node"
    admit(life0, "one")
    worker = setup(life0, tmp_path)
    original = worker.read_text()
    worker.write_text(original + "\n# changed")
    with pytest.raises(ValueError, match="worker file changed"):
        loop.run(life0)
    worker.write_text(original)
    config = json.loads((life0 / loop.CONFIG).read_text())
    config["max_launches"] = 100
    (life0 / loop.CONFIG).write_text(json.dumps(config))
    with pytest.raises(ValueError, match="config differs"):
        loop.run(life0)
    assert not executions(life0)


def test_ledger_tampering_refuses(tmp_path):
    life0 = tmp_path / "node"
    setup(life0, tmp_path)
    row = loop.records(life0 / loop.LEDGER)[0]
    row["config_hash"] = "forged"
    (life0 / loop.LEDGER).write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="integrity"):
        loop.run(life0)


def test_same_instance_lock_prevents_two_controllers(tmp_path):
    life0 = tmp_path / "node"
    setup(life0, tmp_path)
    with (life0 / "state/research_loop.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert loop.run(life0)["status"] == "ALREADY_RUNNING"


def test_resource_jobs_have_no_direct_execution_path(tmp_path):
    life0 = tmp_path / "node"
    nid = admit(life0, "resource")
    setup(life0, tmp_path)
    resource = {"event": "WAITING_RESOURCE", "key": "resource-job:pending",
                "job_id": "pending", "need_id": nid}
    loop.append(life0 / resource_queue.QUEUE_LEDGER, resource)
    before = (life0 / resource_queue.QUEUE_LEDGER).read_bytes()
    with patch.object(resource_queue, "dispatch_job", side_effect=AssertionError("direct dispatch forbidden")):
        assert loop.run(life0)["status"] == "NO_EXECUTABLE_FRONTIER"
    assert (life0 / resource_queue.QUEUE_LEDGER).read_bytes() == before
    assert not executions(life0)


def test_empty_frontier_does_not_manufacture_jobs(tmp_path):
    life0 = tmp_path / "node"
    setup(life0, tmp_path)
    assert loop.run(life0)["status"] == "NO_EXECUTABLE_FRONTIER"
    assert not (life0 / "state/commitments.jsonl").exists()
    assert not (life0 / "state/resource_queue.jsonl").exists()


def test_new_admitted_work_wakes_same_loop(tmp_path):
    life0 = tmp_path / "node"
    setup(life0, tmp_path)
    loop.run(life0)
    nid = admit(life0, "later")
    loop.run(life0)
    assert [r["need_id"] for r in executions(life0)] == [nid]


def test_frozen_horizon_change_refuses(tmp_path):
    life0 = tmp_path / "node"
    setup(life0, tmp_path)
    (life0 / "RESEARCH_HORIZON_0.md").write_text("new mission")
    with pytest.raises(ValueError, match="horizon changed"):
        loop.run(life0)


def test_nonfinite_bounds_refuse(tmp_path):
    with pytest.raises(ValueError, match="finite"):
        loop.initialize(tmp_path / "node", [sys.executable], 1, float("nan"), 1)


def test_operator_reconciliation_unblocks_other_work_without_retry(tmp_path):
    life0 = tmp_path / "node"
    first = admit(life0, "first")
    second = admit(life0, "second")
    setup(life0, tmp_path)
    loop.event(life0, "WORKER_INTENT", need_id=first, reserved_seconds=2)
    assert loop.run(life0)["status"] == "EXECUTION_UNCERTAIN"
    with pytest.raises(ValueError, match="confirm"):
        loop.reconcile(life0, first, False, "checked")
    loop.reconcile(life0, first, True, "operator verified worker stopped")
    loop.run(life0)
    assert [r["need_id"] for r in executions(life0)] == [second]
    assert sum(r["reserved_seconds"] for r in loop.verified_history(life0) if r["event"] == "WORKER_INTENT") == 4


def test_revoke_after_intent_before_spawn_blocks_worker(tmp_path):
    life0 = tmp_path / "node"
    nid = admit(life0, "one")
    setup(life0, tmp_path)
    original = loop.event
    def revoke_on_intent(root, kind, **fields):
        row = original(root, kind, **fields)
        if kind == "WORKER_INTENT":
            loop.append(root / loop.CONTROLS, {"directive": "STOP", "need_id": None})
        return row
    with patch.object(loop, "event", side_effect=revoke_on_intent):
        assert loop.run(life0)["status"] == "OPERATOR_VETO"
    assert not executions(life0)
    assert any(r.get("reason") == "REVOKED_BEFORE_WORKER" for r in loop.verified_history(life0))


def test_watch_wakes_on_real_admission_without_restart(tmp_path):
    life0 = tmp_path / "node"
    setup(life0, tmp_path, launches=1)
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/research_loop.py"),
                                "--life0", str(life0), "run", "--watch", "--poll-seconds", "0.1"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not any(r["event"] == "WAITING_FRONTIER" for r in loop.records(life0 / loop.LEDGER)):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        nid = admit(life0, "arrived")
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["status"] == "BUDGET_EXHAUSTED"
        assert [r["need_id"] for r in executions(life0)] == [nid]
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
