"""End-to-end pulse: mocked GitHub, tmp fact0 db, tmp inbox. No network."""
from __future__ import annotations

import json
from pathlib import Path

from fact0 import EvidenceStore, FactLedger
from fact0.admission import admit
from namariel_live.auth import RuntimeAuthority  # noqa: F401  (import surface check)

from life0.needs import load_queue, set_status
from life0.pulse import consequence_schema, pulse_stats, run_pulse


def make_github(head_sha="aaa", issues=(), ci_available=False):
    def fake(path):
        if path == "/user":
            return 200, {"login": "testlogin"}
        if path == "/repos/testlogin/unified_machine":
            return 200, {"default_branch": "main"}
        if path == "/repos/testlogin/unified_machine/branches/main":
            return 200, {"commit": {"sha": head_sha}}
        if path == "/repos/testlogin/unified_machine/issues?state=open&per_page=30":
            return 200, [
                {"number": n, "title": t, "updated_at": "2026-10-01T00:00:00Z"}
                for n, t in issues
            ]
        if path == "/repos/testlogin/unified_machine/actions/runs?per_page=10":
            if ci_available:
                return 200, {"workflow_runs": []}
            return 404, {"message": "Not Found"}
        raise AssertionError(f"unexpected path: {path}")
    return fake


def make_cfg(tmp_path, fact0_db):
    inbox = tmp_path / "inbox"
    (inbox / "done").mkdir(parents=True)
    return {
        "github_repo": None,
        "github_repo_fallback_name": "unified_machine",
        "fact0_db": str(fact0_db),
        "inbox_dir": str(inbox),
        "state_dir": str(tmp_path / "state"),
        "dispatch_dir": str(tmp_path / "staged"),
    }


def make_fact0_db(path):
    store = EvidenceStore(FactLedger(path))
    admit(store, RuntimeAuthority.live(), artifact_bytes=b"bytes",
          artifact_name="e.json", media_type="application/json",
          provenance_claim={"source_description": "t", "method": "t",
                            "collected_by": "t",
                            "source_checked_at": "2026-10-01"})
    return store


def test_first_pulse_baseline_no_action(tmp_path):
    cfg = make_cfg(tmp_path, tmp_path / "fact0.db")
    make_fact0_db(cfg["fact0_db"])
    r = run_pulse(cfg, github_fetch=make_github())
    assert r["result"] == "NO_ACTION"
    assert r["surfaces"] == {"github": "ok", "fact0": "ok", "inbox": "ok"}
    log = (Path(cfg["state_dir"]) / "pulse_log.jsonl").read_text()
    assert '"result":"NO_ACTION"' in log
    assert (Path(cfg["state_dir"]) / "sensors.json").exists()


def test_inbox_file_becomes_staged_need_and_moves_to_done(tmp_path):
    cfg = make_cfg(tmp_path, tmp_path / "fact0.db")
    make_fact0_db(cfg["fact0_db"])
    run_pulse(cfg, github_fetch=make_github())  # baseline
    (Path(cfg["inbox_dir"]) / "work.md").write_text("do the thing", encoding="utf-8")
    r = run_pulse(cfg, github_fetch=make_github())
    assert r["result"] == "STAGED"
    assert r["needs_created"] == 1 and r["needs_staged"] == 1
    q = load_queue(cfg["state_dir"])
    assert len(q) == 1
    need = next(iter(q.values()))
    assert need["status"] == "STAGED_AWAITING_AUTHORIZATION"
    assert need["priority"] == "unfinished user work"
    pkg = Path(cfg["dispatch_dir"]) / need["need_id"] / "MISSION_PACKAGE.json"
    assert json.loads(pkg.read_text())["launch_authorized"] is False
    # processed file retired to done/; inbox holds no new files now
    assert (Path(cfg["inbox_dir"]) / "done" / "work.md").exists()
    assert not (Path(cfg["inbox_dir"]) / "work.md").exists()


def test_persistent_delta_does_not_restage(tmp_path):
    cfg = make_cfg(tmp_path, tmp_path / "fact0.db")
    make_fact0_db(cfg["fact0_db"])
    run_pulse(cfg, github_fetch=make_github())
    # CI failure appears and stays failing across pulses
    def ci_fail(path):
        status, body = make_github()(path)
        if path.endswith("actions/runs?per_page=10"):
            return 200, {"workflow_runs": [
                {"id": 7, "head_branch": "main", "conclusion": "failure",
                 "status": "completed", "created_at": "2026-10-01T01:00:00Z",
                 "html_url": "https://example.invalid/7"}]}
        return status, body
    r1 = run_pulse(cfg, github_fetch=ci_fail)
    assert r1["needs_staged"] == 1
    # next pulse: same failing run still latest -> delta detector needs a
    # *change*; a static failure is not a new delta -> NO_ACTION, no duplicates
    r2 = run_pulse(cfg, github_fetch=ci_fail)
    assert r2["result"] == "NO_ACTION"
    assert len(load_queue(cfg["state_dir"])) == 1


def test_github_head_change_creates_informational_need(tmp_path):
    cfg = make_cfg(tmp_path, tmp_path / "fact0.db")
    make_fact0_db(cfg["fact0_db"])
    run_pulse(cfg, github_fetch=make_github(head_sha="aaa"))
    r = run_pulse(cfg, github_fetch=make_github(head_sha="bbb"))
    assert r["result"] == "STAGED"
    need = next(iter(load_queue(cfg["state_dir"]).values()))
    assert need["priority"] == "informational housekeeping"
    assert "aaa" in need["possible_need"] and "bbb" in need["possible_need"]


def test_pulse_never_manufactures_inbox_files(tmp_path):
    cfg = make_cfg(tmp_path, tmp_path / "fact0.db")
    make_fact0_db(cfg["fact0_db"])
    run_pulse(cfg, github_fetch=make_github())
    run_pulse(cfg, github_fetch=make_github(head_sha="zzz"))
    inbox_files = [p.name for p in Path(cfg["inbox_dir"]).iterdir()
                   if p.is_file() and p.name != "README.md"]
    assert inbox_files == []


def test_consequence_schema_is_defined_not_built(tmp_path):
    s = consequence_schema()
    assert s["schema"] == "life0.consequence"
    assert "operator_accepted" in s["required_fields"]
    assert "failure_removed" in s["required_fields"]
    assert not (Path(tmp_path) / "state" / "consequences.jsonl").exists()


def test_sensor_exception_yields_pulse_invalid_not_no_action(tmp_path):
    """A sensor raising an exception -> PULSE_INVALID, recorded with which
    surface failed. It must NEVER be counted as NO_ACTION, and denominator
    queries must exclude it."""
    cfg = make_cfg(tmp_path, tmp_path / "fact0.db")
    make_fact0_db(cfg["fact0_db"])

    def boom(path):
        raise RuntimeError("simulated github outage")

    r = run_pulse(cfg, github_fetch=boom)
    assert r["result"] == "PULSE_INVALID"
    assert r["result"] != "NO_ACTION"
    assert r["degraded_surfaces"] == ["github"]
    assert r["surfaces"]["github"] == "error"
    # recorded in the pulse log with the failing surface named
    log = (Path(cfg["state_dir"]) / "pulse_log.jsonl").read_text()
    assert '"result":"PULSE_INVALID"' in log
    assert "github" in log

    # The blind surface produced no deltas (failure is not evidence of change)
    obs_path = Path(cfg["state_dir"]) / "observations.jsonl"
    assert not obs_path.exists() or obs_path.read_text().strip() == ""

    # Denominator hygiene: the invalid pulse is excluded from the NO_ACTION
    # base rate. One valid NO_ACTION pulse before it...
    run_pulse(cfg, github_fetch=make_github())
    stats = pulse_stats(cfg["state_dir"])
    assert stats["total_pulses"] == 2
    assert stats["invalid_pulses"] == 1
    assert stats["valid_pulses"] == 1
    assert stats["no_action_pulses"] == 1
    # denominator is 1 (valid only), not 2
    assert stats["no_action_rate"] == 1.0


def test_degraded_pulse_still_processes_healthy_surfaces(tmp_path):
    """A failed surface marks the pulse PULSE_INVALID, but deltas from
    healthy surfaces are still processed — each surface's deltas are
    independent, and a GitHub outage must not swallow an operator's inbox."""
    cfg = make_cfg(tmp_path, tmp_path / "fact0.db")
    make_fact0_db(cfg["fact0_db"])
    run_pulse(cfg, github_fetch=make_github())  # baseline
    (Path(cfg["inbox_dir"]) / "urgent.md").write_text("operator work", encoding="utf-8")

    def boom(path):
        raise ConnectionError("simulated github outage")

    r = run_pulse(cfg, github_fetch=boom)
    assert r["result"] == "PULSE_INVALID"
    assert r["degraded_surfaces"] == ["github"]
    assert r["needs_created"] == 1 and r["needs_staged"] == 1
    assert (Path(cfg["inbox_dir"]) / "done" / "urgent.md").exists()


def test_staged_records_carry_decision_and_consequence_fields(tmp_path):
    """Staged need + package carry detection / operator decision /
    consequence slots for the future earned-autonomy analysis (not built)."""
    cfg = make_cfg(tmp_path, tmp_path / "fact0.db")
    make_fact0_db(cfg["fact0_db"])
    run_pulse(cfg, github_fetch=make_github())
    (Path(cfg["inbox_dir"]) / "work.md").write_text("do the thing", encoding="utf-8")
    r = run_pulse(cfg, github_fetch=make_github())
    assert r["result"] == "STAGED"
    need = next(iter(load_queue(cfg["state_dir"]).values()))
    # detection: the need points back at its delta
    assert need["delta_id"] and need["source"] == "inbox"
    # operator decision: pending, recorded by the pulse at staging time
    assert need["operator_decision"] == "pending"
    assert need["decided_by"] == "pulse"
    # later operator act is recordable on the same fields
    set_status(cfg["state_dir"], need["need_id"], "AUTHORIZED_LAUNCHED",
               operator_decision="approved", decided_by="operator",
               note="Stephan: go")
    need2 = load_queue(cfg["state_dir"])[need["need_id"]]
    assert need2["operator_decision"] == "approved"
    assert need2["decided_by"] == "operator"
    # the staged package carries the same decision slot + consequence slot
    pkg = json.loads((Path(cfg["dispatch_dir"]) / need["need_id"]
                      / "MISSION_PACKAGE.json").read_text())
    assert pkg["operator_decision"]["decision"] == "pending"
    assert pkg["operator_decision"]["decided_by"] == "pulse"
    assert pkg["consequence"] is None


def test_operator_decision_values_are_enforced(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        set_status(tmp_path, "need_x", "DECLINED",
                   operator_decision="maybe", decided_by="operator")
