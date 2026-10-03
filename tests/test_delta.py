"""Delta detection per surface: D_t = E_t - E_{t-1}, mechanically."""
from __future__ import annotations

from life0.delta import detect_deltas, make_observation


def _snap(**over):
    base = {"github": {}, "fact0": {}, "inbox": {}}
    base.update(over)
    return base


def test_first_pulse_establishes_baseline_no_deltas():
    curr = _snap(github={"head_sha": "abc", "open_issues": []},
                 fact0={"evidence": {}}, inbox={"files": []})
    assert detect_deltas({}, curr) == []


def test_github_head_changed():
    prev = _snap(github={"repo": "o/r", "head_sha": "aaa", "open_issues": []})
    curr = _snap(github={"repo": "o/r", "head_sha": "bbb", "open_issues": []})
    ds = detect_deltas(prev, curr)
    assert len(ds) == 1
    d = ds[0]
    assert (d["source"], d["kind"]) == ("github", "head_changed")
    assert d["previous_state"] == {"head_sha": "aaa"}
    assert d["current_state"] == {"head_sha": "bbb"}
    # deterministic id: same delta, same id
    ds2 = detect_deltas(prev, curr)
    assert ds2[0]["delta_id"] == d["delta_id"]


def test_github_new_issue_and_closed():
    prev = _snap(github={"open_issues": [{"number": 1, "title": "old", "is_pull": False}]})
    curr = _snap(github={"open_issues": [{"number": 2, "title": "new", "is_pull": False}]})
    kinds = {(d["kind"], d["key"]) for d in detect_deltas(prev, curr)}
    assert ("new_issue", "issue-2") in kinds
    assert ("issue_closed", "issue-1") in kinds


def test_github_pulls_are_not_issues():
    prev = _snap(github={"open_issues": []})
    curr = _snap(github={"open_issues": [{"number": 9, "title": "pr", "is_pull": True}]})
    assert detect_deltas(prev, curr) == []


def test_ci_new_failure_and_recovery():
    def ci_run(conclusion, rid):
        return {"run_id": rid, "head_branch": "main", "conclusion": conclusion,
                "created_at": "2026-10-01T00:00:00Z"}
    prev = _snap(github={"ci": {"available": True, "runs": [ci_run("success", 1)]}})
    curr = _snap(github={"ci": {"available": True, "runs": [ci_run("failure", 2)]}})
    ds = detect_deltas(prev, curr)
    assert [d["kind"] for d in ds] == ["ci_new_failure"]
    assert ds[0]["current_state"]["run_id"] == 2
    rec = detect_deltas(curr, _snap(github={"ci": {"available": True,
                    "runs": [ci_run("success", 3)]}}))
    assert [d["kind"] for d in rec] == ["ci_recovered"]


def test_ci_unavailable_is_not_a_delta_or_error():
    prev = _snap(github={"ci": {"available": False, "reason": "no actions runs endpoint"}})
    curr = _snap(github={"ci": {"available": False, "reason": "no actions runs endpoint"}})
    assert detect_deltas(prev, curr) == []


def test_sensor_error_produces_no_deltas():
    prev = _snap(github={"head_sha": "aaa"})
    curr = _snap(github={"sensor_error": "boom"})
    assert detect_deltas(prev, curr) == []


def test_fact0_newly_stale_and_new_evidence():
    prev = _snap(fact0={"evidence": {
        "ev_a": {"currency": "CURRENT", "status": "CANDIDATE"},
        "ev_b": {"currency": "CURRENT", "status": "CANDIDATE"}}})
    curr = _snap(fact0={"evidence": {
        "ev_a": {"currency": "STALE", "status": "CANDIDATE"},
        "ev_b": {"currency": "CURRENT", "status": "CANDIDATE"},
        "ev_c": {"currency": "CURRENT", "status": "CANDIDATE", "trust_tier": "T"}}})
    kinds = {d["kind"]: d["key"] for d in detect_deltas(prev, curr)}
    assert kinds == {"evidence_stale": "ev_a", "new_evidence": "ev_c"}


def test_fact0_supersession():
    prev = _snap(fact0={"evidence": {"ev_a": {"currency": "CURRENT", "status": "CANDIDATE"}}})
    curr = _snap(fact0={"evidence": {"ev_a": {"currency": "CURRENT", "status": "SUPERSEDED"}}})
    ds = detect_deltas(prev, curr)
    assert [d["kind"] for d in ds] == ["evidence_superseded"]


def test_inbox_new_file_and_changed_hash():
    prev = _snap(inbox={"files": [{"name": "a.md", "sha256": "x" * 64, "bytes": 3}]})
    curr = _snap(inbox={"files": [{"name": "a.md", "sha256": "y" * 64, "bytes": 4},
                                  {"name": "b.md", "sha256": "z" * 64, "bytes": 1}]})
    kinds = {(d["kind"], d["key"]) for d in detect_deltas(prev, curr)}
    assert kinds == {("new_file", "a.md"), ("new_file", "b.md")}


def test_observation_record_shape():
    prev = _snap(inbox={"files": []})
    curr = _snap(inbox={"files": [{"name": "w.md", "sha256": "q" * 64, "bytes": 2}]})
    d = detect_deltas(prev, curr)[0]
    ob = make_observation(d)
    for field in ("observation_id", "source", "timestamp", "content_hash",
                  "previous_state", "current_state", "delta_kind", "delta_id"):
        assert field in ob, field
    assert ob["source"] == "inbox"
    assert len(ob["content_hash"]) == 64
