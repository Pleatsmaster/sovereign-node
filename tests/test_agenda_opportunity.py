"""AGENDA-OPPORTUNITY-0 tests: the second eye.

Each test pins one property of the opportunity half: the frozen horizon
parses exactly, the three predicates are deterministic observations (not
goals), transitions fire on witnessed change only, the gate unions both
eyes over one watermark/ledger, and proposal validation binds
opportunities to the horizon mechanically (HORIZON_BINDING_REQUIRED /
HORIZON_ID_CLOSED / HORIZON_ID_UNKNOWN / HORIZON_SCOPE_VIOLATION).
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

LIFE0_ROOT = Path(__file__).resolve().parent.parent
SCAN_OPP = LIFE0_ROOT / "scripts" / "scan_opportunities.py"
PROPOSE = LIFE0_ROOT / "scripts" / "propose_agenda.py"
REVIEW = LIFE0_ROOT / "scripts" / "review_proposal.py"

sys.path.insert(0, str(LIFE0_ROOT / "scripts"))
import agenda_common as ac  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def make_life0(tmp_path: Path, name: str) -> Path:
    """Minimal life-0 tree with the real frozen horizon and empty stores."""
    life0 = tmp_path / f"life0-{name}"
    (life0 / "state").mkdir(parents=True)
    shutil.copy(LIFE0_ROOT / "RESEARCH_HORIZON_0.md",
                life0 / "RESEARCH_HORIZON_0.md")
    (life0 / "state" / "consequences.jsonl").write_text("", encoding="utf-8")
    (life0 / "state" / "sensors.json").write_text(
        json.dumps({"fact0": {"available": True}}), encoding="utf-8")
    return life0


def write_consequence(life0: Path, mission_id: str, artifacts: list):
    rec = {"mission_id": mission_id, "artifact_produced": artifacts,
           "recorded_at": "2026-10-01T20:00:00+00:00",
           "recorded_by": "test"}
    with open(life0 / "state" / "consequences.jsonl", "a",
              encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def write_bindings(life0: Path, rows: list[dict]):
    with open(life0 / "state" / "horizon_bindings.jsonl", "w",
              encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def write_links(life0: Path, rows: list[dict]):
    with open(life0 / "state" / "evidence_links.jsonl", "w",
              encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def write_sensors(life0: Path, doc: dict):
    (life0 / "state" / "sensors.json").write_text(
        json.dumps(doc), encoding="utf-8")


def write_residues(res_dir: Path, residues: list[dict]):
    res_dir.mkdir(parents=True, exist_ok=True)
    with open(res_dir / "residue.jsonl", "w", encoding="utf-8") as f:
        for r in residues:
            f.write(json.dumps(r) + "\n")


def make_residue(rid: str, ts: float, status: str = "active") -> dict:
    return {"id": rid, "ts": ts, "status": status,
            "condition": "WHEN test", "content": "DO test",
            "derived_from": ["um-op-001"],
            "scope": "Applies to test missions."}


def make_ledger(um_missions: Path, mission: str,
                events: list[tuple[float, str]]):
    d = um_missions / f".state-{mission}"
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(d / "ledger.sqlite3")
    con.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL, "
                "kind TEXT, payload_json TEXT, parent_hash TEXT, "
                "event_hash TEXT)")
    for i, (ts, kind) in enumerate(events):
        con.execute("INSERT INTO events (seq, ts, kind) VALUES (?,?,?)",
                    (i, ts, kind))
    con.commit()
    con.close()


def run_opp(life0: Path, um_missions: Path, res_dir: Path
            ) -> tuple[int, dict]:
    env = {**os.environ, "UM_RESIDUE_DIR": str(res_dir)}
    r = subprocess.run(
        [sys.executable, str(SCAN_OPP), "--life0", str(life0),
         "--um-missions", str(um_missions)],
        capture_output=True, text=True, env=env)
    return r.returncode, json.loads(r.stdout)


def opp_scan(life0: Path) -> dict:
    rec = ac.latest_opportunity_scan(life0)
    assert rec is not None, "expected an opportunity scan record"
    return rec


def gate(life0: Path) -> dict:
    r = subprocess.run([sys.executable, str(PROPOSE), "--life0", str(life0),
                        "gate"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def draft_opp_proposal(life0: Path, opp_id: str, horizon_ids: list,
                       **overrides) -> dict:
    scan = ac.combined_scan(life0)
    assert scan is not None
    p = {
        "proposal_id": "",
        "condition_set_hash": scan["condition_set_hash"],
        "source_conditions": [opp_id],
        "evidence_refs": [{"kind": "condition", "condition_id": opp_id}],
        "objective": ("Evaluate the newly testable residue against a matched "
                      "control to determine whether it causally changes the "
                      "later decision"),
        "why_now": (f"Observed {opp_id} in the latest scan: the residue only "
                    "now became testable and the window is current."),
        "expected_consequence": "A recorded verdict on the residue.",
        "completion_contract": {"predicates": [
            {"id": "verdict-recorded",
             "verifiable": "a lifecycle verdict exists in the research record"}]},
        "required_authority_class": "local-build",
        "predicted_cost": "one local-build chain",
        "known_risks": ["the pivot may not qualify",
                        "the bearing on the horizon may be nil"],
        "why_existing_obligations_do_not_cover_it": (
            "The obligation projection is empty."),
        "horizon_ids": horizon_ids,
        "generator_model": "test-harness",
    }
    p.update(overrides)
    return p


def record_proposal(life0: Path, proposal: dict) -> tuple[int, dict]:
    pf = life0 / "draft.json"
    pf.write_text(json.dumps(proposal), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(PROPOSE), "--life0", str(life0), "record",
         "--proposal", str(pf)], capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout)


# --------------------------------------------------------------------------
# Horizon
# --------------------------------------------------------------------------

def test_horizon_parses_frozen_document(tmp_path):
    life0 = make_life0(tmp_path, "horizon")
    h = ac.load_horizon(life0)
    assert sorted(h) == ["RH0", "RH1", "RH2", "RH3", "RH4", "RH5"]
    assert h["RH0"]["status"] == "CLOSED_BASELINE"
    assert h["RH3"]["status"] == "OPEN_PARTIAL"
    for q in ("RH1", "RH2", "RH4", "RH5"):
        assert h[q]["status"] == "OPEN"
        assert len(h[q]["question"]) > 50
    # RH5's elaboration (Evidence must distinguish) is kept, not truncated.
    assert "C increases" in h["RH5"]["question"]
    open_qs = ac.open_horizon_questions(life0)
    assert [q["id"] for q in open_qs] == ["RH1", "RH2", "RH3", "RH4", "RH5"]


def test_horizon_missing_fail_closed(tmp_path):
    life0 = make_life0(tmp_path, "nohorizon")
    (life0 / "RESEARCH_HORIZON_0.md").unlink()
    umm = tmp_path / "umm"; umm.mkdir()
    res = tmp_path / "res"; res.mkdir()
    rc, out = run_opp(life0, umm, res)
    assert rc == 0
    assert out["opportunity_count"] == 0
    assert any("HORIZON" in e for e in out["source_errors"])


# --------------------------------------------------------------------------
# Scanner: observations only, deterministic
# --------------------------------------------------------------------------

def _three_eye_setup(tmp_path, name):
    """One scan baseline, then state arranged so all three predicates fire."""
    life0 = make_life0(tmp_path, name)
    umm = tmp_path / f"umm-{name}"; umm.mkdir()
    res = tmp_path / f"res-{name}"
    write_residues(res, [make_residue("r_test1", 1700000000.0)])
    write_sensors(life0, {"github": {"ci": {"available": False}}})
    rc, _ = run_opp(life0, umm, res)
    assert rc == 0
    # Now: later ledger, flipped sensor, bound artifact.
    make_ledger(umm, "um-op-002", [(1700001000.0, "act_completed")])
    write_sensors(life0, {"github": {"ci": {"available": True}}})
    write_consequence(life0, "m-test-1", ["results/run1.json"])
    write_bindings(life0, [{"mission_id": "m-test-1",
                             "horizon_ids": ["RH1"],
                             "bound_by": "operator"}])
    return life0, umm, res


def test_scanner_emits_observations_only(tmp_path):
    life0, umm, res = _three_eye_setup(tmp_path, "obs")
    rc, out = run_opp(life0, umm, res)
    assert rc == 0
    assert out["opportunity_count"] == 3
    rec = opp_scan(life0)
    sources = {c["source"] for c in rec["conditions"]}
    assert sources == set(ac.OPPORTUNITY_SOURCES)
    for c in rec["conditions"]:
        assert set(c.keys()) == {"condition_id", "source", "observed_at",
                                 "condition", "evidence", "scan_id"}
        assert c["source"] in ac.OPPORTUNITY_SOURCES
        joined = json.dumps(c).lower()
        assert "goal" not in joined and "priority" not in joined
        assert "fix " not in joined and "must " not in joined


def test_scan_is_deterministic(tmp_path):
    life0, umm, res = _three_eye_setup(tmp_path, "det")
    run_opp(life0, umm, res)
    conds2 = opp_scan(life0)["conditions"]
    ids1 = sorted(c["condition_id"] for c in conds2)
    assert len(ids1) == 3
    # Third scan: the two transition predicates stay silent (already
    # witnessed); the state-based artifact predicate still reports the
    # still-unlinked artifact, with a stable condition id.
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 1
    c3 = opp_scan(life0)["conditions"][0]
    assert c3["source"] == "unlinked_result_artifact"
    assert c3["condition_id"] in ids1
    # Same world, fresh tree: identical condition ids (deterministic).
    life0b, ummb, resb = _three_eye_setup(tmp_path, "detb")
    run_opp(life0b, ummb, resb)
    ids2 = sorted(c["condition_id"] for c in opp_scan(life0b)["conditions"])
    assert ids1 == ids2


# --------------------------------------------------------------------------
# Predicate 1: unlinked_result_artifact
# --------------------------------------------------------------------------

def test_unlinked_artifact_fires_with_binding(tmp_path):
    life0 = make_life0(tmp_path, "art1")
    umm = tmp_path / "umm-art1"; umm.mkdir()
    res = tmp_path / "res-art1"; res.mkdir()
    write_consequence(life0, "m-a", ["results/a.json"])
    write_bindings(life0, [{"mission_id": "m-a", "horizon_ids": ["RH1", "RH2"],
                             "bound_by": "operator"}])
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 1
    c = opp_scan(life0)["conditions"][0]
    assert c["source"] == "unlinked_result_artifact"
    assert c["evidence"]["horizon_scope"] == ["RH1", "RH2"]
    assert c["evidence"]["unlinked_horizon"] == ["RH1", "RH2"]


def test_unlinked_artifact_silent_without_binding(tmp_path):
    life0 = make_life0(tmp_path, "art2")
    umm = tmp_path / "umm-art2"; umm.mkdir()
    res = tmp_path / "res-art2"; res.mkdir()
    write_consequence(life0, "m-a", ["results/a.json"])
    # No binding: however suggestive, no opportunity.
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 0


def test_unlinked_artifact_partial_link(tmp_path):
    life0 = make_life0(tmp_path, "art3")
    umm = tmp_path / "umm-art3"; umm.mkdir()
    res = tmp_path / "res-art3"; res.mkdir()
    write_consequence(life0, "m-a", ["results/a.json"])
    write_bindings(life0, [{"mission_id": "m-a", "horizon_ids": ["RH1", "RH2"],
                             "bound_by": "operator"}])
    write_links(life0, [{"artifact": "results/a.json", "horizon_id": "RH1",
                         "linked_by": "operator"}])
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 1
    c = opp_scan(life0)["conditions"][0]
    assert c["evidence"]["unlinked_horizon"] == ["RH2"]
    assert c["evidence"]["horizon_scope"] == ["RH1", "RH2"]


def test_unlinked_artifact_silent_when_fully_linked(tmp_path):
    life0 = make_life0(tmp_path, "art4")
    umm = tmp_path / "umm-art4"; umm.mkdir()
    res = tmp_path / "res-art4"; res.mkdir()
    write_consequence(life0, "m-a", ["results/a.json"])
    write_bindings(life0, [{"mission_id": "m-a", "horizon_ids": ["RH1"],
                             "bound_by": "operator"}])
    write_links(life0, [{"artifact": "results/a.json", "horizon_id": "RH1",
                         "linked_by": "operator"}])
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 0


# --------------------------------------------------------------------------
# Predicate 2: testable_residue_pending
# --------------------------------------------------------------------------

def test_residue_transition_fires_on_second_scan(tmp_path):
    life0 = make_life0(tmp_path, "res1")
    umm = tmp_path / "umm-res1"; umm.mkdir()
    res = tmp_path / "res-res1"
    write_residues(res, [make_residue("r_a", 1700000000.0)])
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 0  # baseline silent
    make_ledger(umm, "um-op-009", [(1700001000.0, "act_completed")])
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 1
    c = opp_scan(life0)["conditions"][0]
    assert c["source"] == "testable_residue_pending"
    assert c["evidence"]["residue_id"] == "r_a"
    assert c["evidence"]["horizon_scope"] == ["RH1", "RH3"]
    assert "test harness" in c["evidence"]["note"]


def test_residue_no_refire_when_already_testable(tmp_path):
    life0 = make_life0(tmp_path, "res2")
    umm = tmp_path / "umm-res2"; umm.mkdir()
    res = tmp_path / "res-res2"
    write_residues(res, [make_residue("r_a", 1700000000.0)])
    make_ledger(umm, "um-op-009", [(1700001000.0, "act_completed")])
    run_opp(life0, umm, res)  # baseline: already testable, silent
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 0


def test_residue_nested_ledger_discovery(tmp_path):
    """Mission ledgers nested under per-mission dirs must be discovered.

    Regression: the first live scan missed 72/78 ledgers because discovery
    only matched the ledger-root top level.
    """
    life0 = make_life0(tmp_path, "res4")
    umm = tmp_path / "umm-res4"; umm.mkdir()
    res = tmp_path / "res-res4"
    write_residues(res, [make_residue("r_a", 1700000000.0)])
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 0  # baseline silent
    nested = umm / "m025-allocation-pattern-analysis" / ".state-um-op-025"
    nested.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(nested / "ledger.sqlite3")
    con.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL, "
                "kind TEXT, payload_json TEXT, parent_hash TEXT, "
                "event_hash TEXT)")
    con.execute("INSERT INTO events (seq, ts, kind) VALUES (0, 1700001000.0, 'act_completed')")
    con.commit()
    con.close()
    rc, out = run_opp(life0, umm, res)
    assert rc == 0
    conds = opp_scan(life0)["conditions"]
    assert any(c["evidence"]["residue_id"] == "r_a" and
               "um-op-025" in c["evidence"]["later_missions"]
               for c in conds), "nested ledger was not discovered"


def test_residue_inactive_never_fires(tmp_path):
    life0 = make_life0(tmp_path, "res3")
    umm = tmp_path / "umm-res3"; umm.mkdir()
    res = tmp_path / "res-res3"
    write_residues(res, [make_residue("r_a", 1700000000.0, status="retired")])
    run_opp(life0, umm, res)
    make_ledger(umm, "um-op-009", [(1700001000.0, "act_completed")])
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 0


# --------------------------------------------------------------------------
# Predicate 3: resource_became_available
# --------------------------------------------------------------------------

def test_resource_flip_fires_false_to_true(tmp_path):
    life0 = make_life0(tmp_path, "rsrc1")
    umm = tmp_path / "umm-rsrc1"; umm.mkdir()
    res = tmp_path / "res-rsrc1"; res.mkdir()
    write_sensors(life0, {"github": {"ci": {"available": False}}})
    run_opp(life0, umm, res)  # baseline
    write_sensors(life0, {"github": {"ci": {"available": True}}})
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 1
    c = opp_scan(life0)["conditions"][0]
    assert c["source"] == "resource_became_available"
    assert c["condition"] == "resource became available: github.ci"
    assert c["evidence"]["horizon_scope"] == ["RH1", "RH2", "RH3",
                                              "RH4", "RH5"]


def test_resource_stable_does_not_fire(tmp_path):
    life0 = make_life0(tmp_path, "rsrc2")
    umm = tmp_path / "umm-rsrc2"; umm.mkdir()
    res = tmp_path / "res-rsrc2"; res.mkdir()
    write_sensors(life0, {"github": {"ci": {"available": True}}})
    run_opp(life0, umm, res)
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 0


def test_resource_new_sensor_not_a_transition(tmp_path):
    life0 = make_life0(tmp_path, "rsrc3")
    umm = tmp_path / "umm-rsrc3"; umm.mkdir()
    res = tmp_path / "res-rsrc3"; res.mkdir()
    write_sensors(life0, {"fact0": {"available": True}})
    run_opp(life0, umm, res)
    # A brand-new sensor at true was never witnessed at false.
    write_sensors(life0, {"fact0": {"available": True},
                          "gpu": {"pool": {"available": True}}})
    rc, out = run_opp(life0, umm, res)
    assert rc == 0 and out["opportunity_count"] == 0


# --------------------------------------------------------------------------
# Gate: one mechanism, two eyes
# --------------------------------------------------------------------------

def _opp_only_state(tmp_path, name) -> tuple[Path, str]:
    life0 = make_life0(tmp_path, name)
    umm = tmp_path / f"umm-{name}"; umm.mkdir()
    res = tmp_path / f"res-{name}"; res.mkdir()
    write_sensors(life0, {"github": {"ci": {"available": False}}})
    run_opp(life0, umm, res)
    write_sensors(life0, {"github": {"ci": {"available": True}}})
    rc, _ = run_opp(life0, umm, res)
    assert rc == 0
    rec = opp_scan(life0)
    assert rec["conditions"], "fixture must yield an opportunity"
    return life0, rec["conditions"][0]["condition_id"]


def test_gate_proceeds_on_opportunity_alone(tmp_path):
    life0, _ = _opp_only_state(tmp_path, "gate1")
    g = gate(life0)
    assert g["proceed"] is True and g["reason"] == "PROCEED"
    assert g["opportunity_count"] == 1 and g["pressure_count"] == 0
    assert g["condition_count"] == 1


def test_gate_combines_pressure_and_opportunity(tmp_path):
    life0, _ = _opp_only_state(tmp_path, "gate2")
    # Hand-write one pressure scan record (the pressure scanner's own shape).
    cond = {"condition_id": "cond_test_deadbeef", "source": "test_suite_status",
            "observed_at": "2026-10-01T20:00:00+00:00",
            "condition": "test suite failing: 1 failed",
            "evidence": {"failed": ["t"]}, "scan_id": "scan_x"}
    ac.append_jsonl(life0 / "state" / "condition_scans.jsonl",
                    {"event": "SCAN_RECORDED", "scan_id": "scan_x",
                     "scanned_at": "2026-10-01T20:00:00+00:00",
                     "conditions": [cond],
                     "condition_set_hash": ac.condition_set_hash([cond])})
    g = gate(life0)
    assert g["proceed"] is True
    assert g["pressure_count"] == 1 and g["opportunity_count"] == 1
    assert g["condition_count"] == 2


def test_no_conditions_no_model_stage(tmp_path):
    life0 = make_life0(tmp_path, "gate3")
    g = gate(life0)
    assert g["proceed"] is False and g["reason"] == "NO_CONDITIONS"


# --------------------------------------------------------------------------
# Validation: horizon binding is mechanical
# --------------------------------------------------------------------------

def test_record_requires_horizon_binding_for_opportunity(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "val1")
    rc, rec = record_proposal(life0, draft_opp_proposal(life0, oid, []))
    assert rc == 2 and rec["reason"] == "HORIZON_BINDING_REQUIRED"


def test_record_refuses_closed_horizon_id(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "val2")
    rc, rec = record_proposal(life0, draft_opp_proposal(life0, oid, ["RH0"]))
    assert rc == 2 and rec["reason"] == "HORIZON_ID_CLOSED"


def test_record_refuses_unknown_horizon_id(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "val3")
    rc, rec = record_proposal(life0, draft_opp_proposal(life0, oid, ["RH99"]))
    assert rc == 2 and rec["reason"] == "HORIZON_ID_UNKNOWN"


def test_record_refuses_out_of_scope_binding(tmp_path):
    # The fixture opportunity is a resource flip: scope is all OPEN ids,
    # so bind RH1 (valid) vs an artifact-scoped check via a second fixture.
    life0 = make_life0(tmp_path, "val4")
    umm = tmp_path / "umm-val4"; umm.mkdir()
    res = tmp_path / "res-val4"; res.mkdir()
    write_consequence(life0, "m-a", ["results/a.json"])
    write_bindings(life0, [{"mission_id": "m-a", "horizon_ids": ["RH1"],
                             "bound_by": "operator"}])
    run_opp(life0, umm, res)
    oid = opp_scan(life0)["conditions"][0]["condition_id"]
    # RH2 is OPEN but outside this artifact's frozen scope [RH1].
    rc, rec = record_proposal(life0, draft_opp_proposal(life0, oid, ["RH2"]))
    assert rc == 2 and rec["reason"] == "HORIZON_SCOPE_VIOLATION"


def test_record_accepts_valid_opportunity_proposal(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "val5")
    rc, rec = record_proposal(life0, draft_opp_proposal(life0, oid, ["RH1"]))
    assert rc == 0 and rec["recorded"] is True, rec
    stored = ac.load_proposal_ledger(life0)[rec["proposal_id"]]["proposal"]
    assert stored["horizon_ids"] == ["RH1"]
    assert stored["source_conditions"] == [oid]


def test_rejected_opportunity_blocks_regeneration(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "val6")
    rc, rec = record_proposal(life0, draft_opp_proposal(life0, oid, ["RH2"]))
    assert rc == 0
    pid = rec["proposal_id"]
    r = subprocess.run(
        [sys.executable, str(REVIEW), "--life0", str(life0), "decide",
         "--proposal-id", pid, "--verdict", "REJECTED",
         "--reason", "DUPLICATE"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    g = gate(life0)
    assert g["proceed"] is False
    assert g["reason"] in ("CONDITION_STATE_UNCHANGED", "ALREADY_COVERED")
    # The constraint holds independently of the watermark: with the
    # watermark removed, the condition-bound uniqueness still blocks.
    (life0 / "state" / "agenda_watermark.json").unlink()
    g = gate(life0)
    assert g["proceed"] is False and g["reason"] == "ALREADY_COVERED"


def test_no_proposal_advances_shared_watermark(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "val7")
    csh = ac.combined_scan(life0)["condition_set_hash"]
    r = subprocess.run(
        [sys.executable, str(PROPOSE), "--life0", str(life0),
         "record-no-proposal", "--condition-set-hash", csh,
         "--note", "resource flip has no defensible horizon bearing"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    g = gate(life0)
    assert g["proceed"] is False
    assert g["reason"] == "CONDITION_STATE_UNCHANGED"


def test_invented_opportunity_refused(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "val8")
    rc, rec = record_proposal(
        life0, draft_opp_proposal(life0, oid, ["RH1"],
                                 source_conditions=["cond_opp_ffffffff"]))
    assert rc == 2 and rec["reason"] == "UNOBSERVED_CONDITION"


# --------------------------------------------------------------------------
# Write boundary
# --------------------------------------------------------------------------

def test_scanner_write_boundary(tmp_path):
    life0 = make_life0(tmp_path, "wb")
    before = {p.name for p in (life0 / "state").iterdir()}
    umm = tmp_path / "umm-wb"; umm.mkdir()
    res = tmp_path / "res-wb"
    write_residues(res, [make_residue("r_test1", 1700000000.0)])
    rc, _ = run_opp(life0, umm, res)
    assert rc == 0
    after = {p.name for p in (life0 / "state").iterdir()}
    assert after - before == {"opportunity_scans.jsonl"}


# --------------------------------------------------------------------------
# Instrumentation debt: generator identity + prompt hash (not machinery)
# --------------------------------------------------------------------------

def _recorded_proposal(life0: Path, pid: str) -> dict:
    for line in (life0 / "state" / "agenda_proposals.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r.get("event") == "PROPOSAL_RECORDED" and r["proposal"]["proposal_id"] == pid:
            return r["proposal"]
    raise AssertionError("proposal not recorded")


def _with_prompt(life0: Path) -> Path:
    shutil.copy(LIFE0_ROOT / "OPPORTUNITY_PROMPT_0.md",
                life0 / "OPPORTUNITY_PROMPT_0.md")
    return life0


def test_record_stamps_generator_model_and_prompt_hash(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "instr1")
    _with_prompt(life0)
    draft = draft_opp_proposal(life0, oid, ["RH1"], generator_model="muse-spark-1.3")
    rc, rec = record_proposal(life0, draft)
    assert rc == 0 and rec["recorded"] is True
    stored = _recorded_proposal(life0, rec["proposal_id"])
    assert stored["generator_model"] == "muse-spark-1.3"
    assert stored["generator_prompt_sha256"] == ac.prompt_sha256(life0)
    assert len(stored["generator_prompt_sha256"]) == 64


def test_record_refuses_missing_generator_model(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "instr2")
    _with_prompt(life0)
    draft = draft_opp_proposal(life0, oid, ["RH1"])
    del draft["generator_model"]
    rc, rec = record_proposal(life0, draft)
    assert rc == 2 and rec["reason"] in ("SCHEMA_MISMATCH", "GENERATOR_MODEL_MISSING")


def test_record_refuses_empty_generator_model(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "instr3")
    _with_prompt(life0)
    draft = draft_opp_proposal(life0, oid, ["RH1"], generator_model="  ")
    rc, rec = record_proposal(life0, draft)
    assert rc == 2 and rec["reason"] == "GENERATOR_MODEL_MISSING"


def test_prompt_hash_binds_to_record_time_bytes(tmp_path):
    life0, oid = _opp_only_state(tmp_path, "instr4")
    _with_prompt(life0)
    prompt = life0 / "OPPORTUNITY_PROMPT_0.md"
    before = ac.prompt_sha256(life0)
    # Simulate a prompt edit between prepare and record: the recorder must
    # bind to the bytes on disk at record time, not any earlier copy.
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    after = ac.prompt_sha256(life0)
    assert before != after
    draft = draft_opp_proposal(life0, oid, ["RH1"], generator_model="test-harness")
    rc, rec = record_proposal(life0, draft)
    assert rc == 0
    stored = _recorded_proposal(life0, rec["proposal_id"])
    assert stored["generator_prompt_sha256"] == after


def test_prepare_packet_carries_prompt_hash(tmp_path):
    life0, _ = _opp_only_state(tmp_path, "instr5")
    _with_prompt(life0)
    r = subprocess.run(
        [sys.executable, str(PROPOSE), "--life0", str(life0), "prepare"],
        capture_output=True, text=True)
    assert r.returncode == 0
    prep = json.loads(r.stdout)
    pkt = json.loads((life0 / prep["packet_path"]).read_text(encoding="utf-8")) \
        if not prep["packet_path"].startswith("/") else \
        json.loads(Path(prep["packet_path"]).read_text(encoding="utf-8"))
    assert pkt["proposal_prompt_sha256"] == ac.prompt_sha256(life0)
