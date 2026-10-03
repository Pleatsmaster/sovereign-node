"""AGENDA-PROPOSAL-0 tests: apparatus verification around the real seam.

Each test pins one acceptance property: observations-only scanning,
deterministic condition-set hashing, the four-conjunct gate, the
constitutional constraints (no sensorium widening, condition-bound
uniqueness, first-class rejection evidence), strict proposal validation,
accept-to-admission, expiry, and the write boundary.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

LIFE0_ROOT = Path(__file__).resolve().parent.parent
SCAN = LIFE0_ROOT / "scripts" / "scan_conditions.py"
PROPOSE = LIFE0_ROOT / "scripts" / "propose_agenda.py"
REVIEW = LIFE0_ROOT / "scripts" / "review_proposal.py"
ADMIT = LIFE0_ROOT / "scripts" / "admit_commitment.py"

sys.path.insert(0, str(LIFE0_ROOT / "scripts"))
import agenda_common as ac  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def make_life0(tmp_path: Path, name: str, with_condition: bool = True) -> Path:
    """Minimal life-0 tree: records with a dangling ref, a tiny pytest dir,
    and a small invariants file. All scanner inputs are explicit overrides."""
    life0 = tmp_path / f"life0-{name}"
    (life0 / "state").mkdir(parents=True)
    (life0 / "records").mkdir(parents=True)
    if with_condition:
        (life0 / "records" / "note.md").write_text(
            "# field note\n\nSee ~/workspace/nonexistent-dir-xyz/report.md "
            "for the raw dump.\n\nRelated admission: commit_aaaaaaaaaaaaaaaa "
            "(never registered).\n",
            encoding="utf-8")
    else:
        (life0 / "records" / "note.md").write_text(
            "# field note\n\nNothing referenced here.\n", encoding="utf-8")

    tests_dir = life0 / "fixture_tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_passes():\n    assert True\n\n"
        "def test_fails():\n    assert False, 'boom'\n",
        encoding="utf-8")

    inv_file = life0 / "invariants.md"
    target = life0 / "guard.txt"
    target.write_text("guarded\n", encoding="utf-8")
    import hashlib
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    inv_file.write_text(
        "# invariants\n\n```invariants\n" + json.dumps([
            {"id": "INV-GUARD", "description": "guard file intact",
             "check": "file-sha256", "path": "guard.txt",
             "expected_sha256": digest},
            {"id": "INV-GUARD-PRESENT", "description": "guard file present",
             "check": "file-exists", "path": "guard.txt"},
        ]) + "\n```\n",
        encoding="utf-8")
    return life0


def run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args],
                          capture_output=True, text=True)


def scan(life0: Path, **kw) -> tuple[int, dict]:
    args = [str(SCAN), "--life0", str(life0),
            "--tests-dir", str(kw.get("tests_dir", life0 / "fixture_tests")),
            "--invariants", str(kw.get("invariants", life0 / "invariants.md"))]
    r = run(*args)
    return r.returncode, json.loads(r.stdout)


def gate(life0: Path) -> dict:
    r = run(str(PROPOSE), "--life0", str(life0), "gate")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def latest_scan_record(life0: Path) -> dict:
    return ac.latest_scan(life0)


def draft_proposal(life0: Path, **overrides) -> dict:
    scan_rec = latest_scan_record(life0)
    conds = scan_rec["conditions"]
    assert conds, "fixture must produce at least one condition"
    c0 = conds[0]["condition_id"]
    p = {
        "proposal_id": "",
        "condition_set_hash": scan_rec["condition_set_hash"],
        "source_conditions": [c0],
        "evidence_refs": [{"kind": "condition", "condition_id": c0}],
        "objective": ("Reconcile the dangling evidence reference recorded in "
                      "records/note.md so later retrieval cannot silently "
                      "miss it"),
        "why_now": (f"Observed {c0} in the latest scan: the reference is "
                    "dangling now and every later read risks the same miss."),
        "expected_consequence": ("The record either resolves to a real path "
                                 "or is explicitly marked retired."),
        "completion_contract": {"predicates": [
            {"id": "refs-resolve",
             "verifiable": ("every path referenced in records/note.md exists "
                            "or the line is marked retired")}]},
        "required_authority_class": "local-build",
        "predicted_cost": "one local-build chain, tens of worker steps",
        "known_risks": ["the condition may be misdiagnosed",
                        "the missing path may be intentionally absent"],
        "why_existing_obligations_do_not_cover_it": (
            "The obligation projection is empty; no admitted commitment "
            "covers evidence hygiene."),
        # Pressure-only proposal: no horizon binding.
        "horizon_ids": [],
        "generator_model": "test-harness",
    }
    p.update(overrides)
    return p


def record_proposal(life0: Path, proposal: dict) -> tuple[int, dict]:
    pf = life0 / "draft.json"
    pf.write_text(json.dumps(proposal), encoding="utf-8")
    r = run(str(PROPOSE), "--life0", str(life0), "record",
            "--proposal", str(pf))
    return r.returncode, json.loads(r.stdout)


def no_proposal(life0: Path, h: str, note: str = "nothing worth doing") -> dict:
    r = run(str(PROPOSE), "--life0", str(life0), "record-no-proposal",
            "--condition-set-hash", h, "--note", note)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


# --------------------------------------------------------------------------
# 1. Scanner emits observations only — no goal-shaped fields, fixed templates
# --------------------------------------------------------------------------

def test_scanner_emits_observations_only(tmp_path):
    life0 = make_life0(tmp_path, "obs")
    rc, v = scan(life0)
    assert rc == 0, v
    assert v["condition_count"] >= 3  # failing test + missing path + dangling id
    rec = latest_scan_record(life0)
    allowed = {"condition_id", "source", "observed_at", "condition",
               "evidence", "scan_id"}
    forbidden = {"action", "fix", "goal", "should", "recommendation", "priority"}
    for c in rec["conditions"]:
        assert set(c.keys()) == allowed
        assert not (forbidden & set(c["evidence"].keys()))
        assert c["source"] in ac.CONDITION_SOURCES
    texts = [c["condition"] for c in rec["conditions"]]
    assert any(t == "records reference missing path: "
                  "~/workspace/nonexistent-dir-xyz/report.md" for t in texts)
    assert any(t.startswith("dangling reference with no registry entry: "
                            "commit_aaaaaaaaaaaaaaaa") for t in texts)
    assert any(t.endswith("test_sample.py::test_fails") and
               t.startswith("life-0 test failing: ") for t in texts)
    # Positive control: the passing invariants must not fire.
    assert not any((c["evidence"].get("invariant_id") or "").startswith("INV-GUARD")
                   for c in rec["conditions"])


# --------------------------------------------------------------------------
# 2. Condition-set hash is deterministic over observation content
# --------------------------------------------------------------------------

def test_condition_set_hash_deterministic(tmp_path):
    life0 = make_life0(tmp_path, "hash")
    rc, v1 = scan(life0)
    assert rc == 0
    rc, v2 = scan(life0)
    assert rc == 0
    assert v1["condition_set_hash"] == v2["condition_set_hash"]
    assert v1["scan_id"] != v2["scan_id"]  # volatile ids differ; hash does not

    # Reordering the stored conditions cannot change the hash.
    rec = latest_scan_record(life0)
    conds = rec["conditions"]
    assert ac.condition_set_hash(conds) == ac.condition_set_hash(
        list(reversed(conds)))

    # A genuinely new condition changes the hash.
    extra = dict(conds[0])
    extra["condition_id"] = "cond_test_deadbeef"
    assert ac.condition_set_hash(conds + [extra]) != ac.condition_set_hash(conds)


# --------------------------------------------------------------------------
# 3. Gate blocks when OPEN obligations exist
# --------------------------------------------------------------------------

def _admit_commitment(life0: Path) -> dict:
    acc = {
        "objective": "Test agenda gate commitment: keep the projection non-empty",
        "completion_contract": {"predicates": [
            {"id": "p1", "verifiable": "predicate one holds"}]},
        "authority_class": "local-build",
        "scope": "test tree only",
        "accepted_by": "operator",
        "accepted_at": "2026-10-01T19:00:00-04:00",
        "origin": "test agenda gate",
        "parent_commitment_id": None,
    }
    af = life0 / "acceptance.json"
    af.write_text(json.dumps(acc), encoding="utf-8")
    r = run(str(ADMIT), "--life0", str(life0), "--acceptance", str(af))
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_gate_blocks_when_open_obligations_exist(tmp_path):
    life0 = make_life0(tmp_path, "gate-open")
    _admit_commitment(life0)
    rc, v = scan(life0)
    assert rc == 0 and v["condition_count"] >= 1
    g = gate(life0)
    assert g["proceed"] is False
    assert g["reason"] == "OPEN_OBLIGATIONS_EXIST"
    assert g["open_obligations"]


# --------------------------------------------------------------------------
# 4. Unchanged condition state does not re-evaluate
# --------------------------------------------------------------------------

def test_gate_blocks_unchanged_condition_state(tmp_path):
    life0 = make_life0(tmp_path, "gate-unchanged")
    rc, v = scan(life0)
    assert rc == 0
    g = gate(life0)
    assert g["proceed"] is True, g
    no_proposal(life0, v["condition_set_hash"])
    g2 = gate(life0)
    assert g2["proceed"] is False
    assert g2["reason"] == "CONDITION_STATE_UNCHANGED"
    assert g2["last_outcome"] == "NO_PROPOSAL"


# --------------------------------------------------------------------------
# 5. Constraint 3: a rejected proposal for an unchanged state never regenerates
# --------------------------------------------------------------------------

def test_rejected_proposal_blocks_regeneration(tmp_path):
    life0 = make_life0(tmp_path, "gate-reject")
    rc, v = scan(life0)
    assert rc == 0
    assert gate(life0)["proceed"] is True

    rc, rec = record_proposal(life0, draft_proposal(life0))
    assert rc == 0 and rec["recorded"] is True, rec
    pid = rec["proposal_id"]

    r = run(str(REVIEW), "--life0", str(life0), "decide",
            "--proposal-id", pid, "--verdict", "REJECTED",
            "--reason", "NOT_WORTH_DOING", "--note", "test rejection")
    assert r.returncode == 0, r.stdout + r.stderr

    # Rescan: same world, same hash. Regeneration is blocked.
    rc, v2 = scan(life0)
    assert rc == 0
    assert v2["condition_set_hash"] == v["condition_set_hash"]

    g = gate(life0)
    assert g["proceed"] is False
    assert g["reason"] in ("CONDITION_STATE_UNCHANGED", "ALREADY_COVERED")

    # The constraint holds independently of the watermark: with the
    # watermark removed, the condition-bound uniqueness still blocks.
    (life0 / "state" / "agenda_watermark.json").unlink()
    g = gate(life0)
    assert g["proceed"] is False
    assert g["reason"] == "ALREADY_COVERED"
    assert g["covering_proposal_id"] == pid
    assert g["covering_state"] == "REJECTED"

    # And the recorder itself refuses a fresh draft for the same state.
    rc, rec2 = record_proposal(life0, draft_proposal(
        life0, objective="A different objective for the same dangling reference "
                         "that should never become a second proposal"))
    assert rc == 2
    assert rec2["reason"] == "GATE_BLOCKED"


# --------------------------------------------------------------------------
# 6. Constraint 2: the model cannot widen the sensorium
# --------------------------------------------------------------------------

def test_record_refuses_unobserved_condition(tmp_path):
    life0 = make_life0(tmp_path, "unobserved")
    rc, v = scan(life0)
    assert rc == 0
    assert gate(life0)["proceed"] is True
    rc, rec = record_proposal(
        life0, draft_proposal(life0, source_conditions=["cond_test_ffffffff"]))
    assert rc == 2
    assert rec["reason"] == "UNOBSERVED_CONDITION"
    assert rec["unobserved"] == ["cond_test_ffffffff"]


# --------------------------------------------------------------------------
# 7-8. Strict validation: authority, evidence, grounding, no extra fields
# --------------------------------------------------------------------------

def test_record_refuses_wrong_authority_class(tmp_path):
    life0 = make_life0(tmp_path, "authority")
    scan(life0)
    assert gate(life0)["proceed"] is True
    rc, rec = record_proposal(
        life0, draft_proposal(life0, required_authority_class="external-propose"))
    assert rc == 2
    assert rec["reason"] == "AUTHORITY_CLASS_UNSUPPORTED"


def test_record_refuses_manufactured_urgency_and_extras(tmp_path):
    life0 = make_life0(tmp_path, "grounding")
    scan(life0)
    assert gate(life0)["proceed"] is True

    # why_now cites nothing observed -> manufactured urgency.
    rc, rec = record_proposal(
        life0, draft_proposal(life0, why_now="This feels urgent and important."))
    assert rc == 2
    assert rec["reason"] == "WHY_NOW_UNGROUNDED"

    # Free-form importance scoring is not part of the schema.
    bad = draft_proposal(life0)
    bad["importance_score"] = 9
    rc, rec = record_proposal(life0, bad)
    assert rc == 2
    assert rec["reason"] == "SCHEMA_MISMATCH"
    assert rec["extra"] == ["importance_score"]

    # Evidence must resolve.
    rc, rec = record_proposal(life0, draft_proposal(life0, evidence_refs=[]))
    assert rc == 2
    assert rec["reason"] == "EVIDENCE_REFS_EMPTY"

    rc, rec = record_proposal(
        life0, draft_proposal(
            life0, evidence_refs=[{"kind": "file",
                                   "path": "records/does-not-exist.md"}]))
    assert rc == 2
    assert rec["reason"] == "EVIDENCE_UNRESOLVABLE"

    # Vague objective.
    rc, rec = record_proposal(life0, draft_proposal(life0, objective="fix stuff"))
    assert rc == 2
    assert rec["reason"] == "OBJECTIVE_VAGUE"


# --------------------------------------------------------------------------
# 9. Rejection reasons are first-class and enumerated
# --------------------------------------------------------------------------

def test_rejection_reasons_enum(tmp_path):
    assert ac.REJECTION_REASONS == (
        "NOT_WORTH_DOING", "DUPLICATE", "OBJECTIVE_VAGUE",
        "NO_MEASURABLE_END_STATE", "EVIDENCE_INSUFFICIENT",
        "WRONG_AUTHORITY_CLASS", "MISDIAGNOSED_CONDITION", "OTHER")

    life0 = make_life0(tmp_path, "reasons")

    def fresh_condition_state(i: int) -> dict:
        # Each iteration gets a genuinely distinct condition state: a new
        # dangling path in the records. One live proposal per condition set.
        note = life0 / "records" / "note.md"
        with note.open("a", encoding="utf-8") as f:
            f.write(f"\nAlso see ~/workspace/nonexistent-dir-xyz/part-{i}.md.\n")
        rc, v = scan(life0)
        assert rc == 0, v
        return v

    for i, reason in enumerate(ac.REJECTION_REASONS):
        v = fresh_condition_state(i)
        assert gate(life0)["proceed"] is True, gate(life0)
        rc, rec = record_proposal(
            life0, draft_proposal(
                life0,
                objective=(f"Reconcile dangling reference part-{i} in "
                           f"records/note.md so later retrieval cannot "
                           f"silently miss it")))
        assert rc == 0, rec
        pid = rec["proposal_id"]
        r = run(str(REVIEW), "--life0", str(life0), "decide",
                "--proposal-id", pid, "--verdict", "REJECTED",
                "--reason", reason)
        assert r.returncode == 0, r.stdout + r.stderr
        assert json.loads(r.stdout)["reason"] == reason

    # Unknown reasons are refused, not recorded.
    v = fresh_condition_state(99)
    assert gate(life0)["proceed"] is True
    rc, rec = record_proposal(
        life0, draft_proposal(
            life0, objective="Reconcile dangling reference part-99 for the bogus "
                             "reason test case"))
    assert rc == 0, rec
    r = run(str(REVIEW), "--life0", str(life0), "decide",
            "--proposal-id", rec["proposal_id"], "--verdict", "REJECTED",
            "--reason", "BOGUS_REASON")
    assert r.returncode == 2
    assert json.loads(r.stdout)["reason"] == "UNKNOWN_REJECTION_REASON"


# --------------------------------------------------------------------------
# 10. ACCEPTED drives the unchanged admission gate
# --------------------------------------------------------------------------

def test_accepted_proposal_drives_admission(tmp_path):
    life0 = make_life0(tmp_path, "accept")
    rc, v = scan(life0)
    assert rc == 0
    assert gate(life0)["proceed"] is True

    rc, rec = record_proposal(life0, draft_proposal(life0))
    assert rc == 0, rec
    pid = rec["proposal_id"]

    r = run(str(REVIEW), "--life0", str(life0), "decide",
            "--proposal-id", pid, "--verdict", "ACCEPTED",
            "--note", "worth doing")
    assert r.returncode == 0, r.stdout + r.stderr
    decided = json.loads(r.stdout)
    assert decided["verdict"] == "ACCEPTED"
    assert decided["commitment_id"].startswith("commit_")

    # The admission wrote through the normal machinery.
    commits = ac.read_jsonl(life0 / "state" / "commitments.jsonl")
    admitted = [c for c in commits
                if c.get("event") == "COMMITMENT_ADMITTED"
                and c["commitment"]["commitment_id"] == decided["commitment_id"]]
    assert len(admitted) == 1
    c = admitted[0]["commitment"]
    assert c["accepted_by"] == "operator"
    assert c["authority_class"] == "local-build"
    assert admitted[0]["commitment"]["origin"] == f"agenda-proposal:{pid}"

    needs = [n for n in ac.read_jsonl(life0 / "state" / "needs.jsonl")
             if n.get("event") == "NEED_CREATED"]
    assert any(n["need"]["need_id"] == decided["need_id"]
               and n["need"]["source"] == "commitment" for n in needs)

    # The new obligation is OPEN, so the agenda gate now stands down.
    g = gate(life0)
    assert g["proceed"] is False
    assert g["reason"] == "OPEN_OBLIGATIONS_EXIST"


# --------------------------------------------------------------------------
# 11. Expiry is deterministic
# --------------------------------------------------------------------------

def test_stale_proposals_expire(tmp_path):
    life0 = make_life0(tmp_path, "expiry", with_condition=False)
    # Seed a PROPOSED entry 8 days old directly (deterministic clock control).
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    stale = {
        "proposal_id": "prop_staletest01",
        "condition_set_hash": "deadbeef",
        "source_conditions": ["cond_ref_12345678"],
        "evidence_refs": [{"kind": "condition",
                           "condition_id": "cond_ref_12345678"}],
        "objective": "A stale proposal that nobody reviewed in time at all",
        "why_now": "cond_ref_12345678 was observed eight days ago",
        "expected_consequence": "nothing",
        "completion_contract": {"predicates": [
            {"id": "p", "verifiable": "p holds"}]},
        "required_authority_class": "local-build",
        "predicted_cost": "low",
        "known_risks": ["stale"],
        "why_existing_obligations_do_not_cover_it": "none did",
        "state": "PROPOSED",
        "proposed_at": old,
        "proposed_by": "agenda-proposal-0",
    }
    ac.append_jsonl(life0 / "state" / "agenda_proposals.jsonl",
                    {"event": "PROPOSAL_RECORDED", "at": old,
                     "proposal": stale,
                     "policy_version": ac.POLICY_VERSION})
    rc, v = scan(life0)
    assert rc == 0, v
    assert v["expired_proposals"] == ["prop_staletest01"]
    ledger = ac.load_proposal_ledger(life0)
    assert ledger["prop_staletest01"]["state"] == "EXPIRED"


# --------------------------------------------------------------------------
# 12. Write boundary: scan/propose touch only their own state
# --------------------------------------------------------------------------

def test_stage1_and_recorder_write_boundary(tmp_path):
    life0 = make_life0(tmp_path, "boundary")
    before = {p.name for p in (life0 / "state").iterdir()}

    rc, v = scan(life0)
    assert rc == 0
    assert gate(life0)["proceed"] is True
    r = run(str(PROPOSE), "--life0", str(life0), "prepare")
    assert r.returncode == 0, r.stdout + r.stderr
    no_proposal(life0, v["condition_set_hash"])

    after = {p.name for p in (life0 / "state").iterdir()}
    new_files = after - before
    assert new_files <= {"condition_scans.jsonl", "agenda_proposals.jsonl",
                         "agenda_watermark.json"}, new_files
    # The recorder never touches the obligation stores.
    assert not (life0 / "state" / "commitments.jsonl").exists()
    assert not (life0 / "state" / "needs.jsonl").exists()
    pkt = life0 / "proposals" / "packets" / f"{v['condition_set_hash']}.json"
    assert pkt.exists()


# --------------------------------------------------------------------------
# 13. NO_PROPOSAL advances the watermark and stays silent downstream
# --------------------------------------------------------------------------

def test_no_proposal_advances_watermark(tmp_path):
    life0 = make_life0(tmp_path, "noprop")
    rc, v = scan(life0)
    assert rc == 0
    out = no_proposal(life0, v["condition_set_hash"], note="nothing deserves work")
    assert out["outcome"] == "NO_PROPOSAL"
    wm = ac.read_watermark(life0)
    assert wm["last_evaluated_condition_set_hash"] == v["condition_set_hash"]
    assert wm["outcome"] == "NO_PROPOSAL"
    # No proposal entry was created — only the evaluation event.
    assert ac.load_proposal_ledger(life0) == {}
    assert gate(life0)["reason"] == "CONDITION_STATE_UNCHANGED"


# --------------------------------------------------------------------------
# 14. Duplicate record is idempotent
# --------------------------------------------------------------------------

def test_duplicate_record_is_idempotent(tmp_path):
    life0 = make_life0(tmp_path, "idedup")
    scan(life0)
    assert gate(life0)["proceed"] is True
    rc, rec1 = record_proposal(life0, draft_proposal(life0))
    assert rc == 0 and rec1["recorded"] is True
    rc, rec2 = record_proposal(life0, draft_proposal(life0))
    assert rc == 0
    assert rec2["duplicate"] is True
    assert rec2["proposal_id"] == rec1["proposal_id"]
    recorded = [r for r in ac.read_jsonl(life0 / "state" / "agenda_proposals.jsonl")
                if r.get("event") == "PROPOSAL_RECORDED"]
    assert len(recorded) == 1
