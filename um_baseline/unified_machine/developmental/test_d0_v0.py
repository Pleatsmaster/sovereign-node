"""D0-v0 no-API gate: test_d0_v0.py

Every test is deterministic, runs no model, launches nothing, and touches
no network. Failure anywhere blocks the retrospective, not just the freeze.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from d0 import ledger as L
from d0 import request as R
from d0 import vocabulary as V
from d0 import version_space as VS

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


REAL_VOCAB = V.load_vocabulary(HERE / "vocabularies" / "acquisition_v1.json")


def full_state(**kw):
    base = {
        "artifact_written": False,
        "broad_search_count": 0,
        "consecutive_broad_searches_without_new_source": 0,
        "coverage_complete": False,
        "coverage_fraction": 0.0,
        "distinct_sources_discovered": 0,
        "distinct_sources_read": 0,
        "enumeration_performed": False,
        "evidence_requirement_coverage": "complete",
        "n_empty_query_searches": 0,
        "n_required_sources": 6,
        "n_searches": 0,
        "n_sources_read": 0,
        "proposed_action_class": "READ_SOURCE",
        "proposed_action_role": "direct_acquisition",
        "proposed_query_empty": False,
        "steps_remaining": 8,
        "steps_used": 0,
        "task_names_file": False,
    }
    base.update(kw)
    return base


def ev(eid, state, effect):
    return {
        "evidence_id": eid,
        "state_before": state,
        "observed_effect": list(effect),
        "cost": None,
        "source_episode": None,
        "provenance": None,
    }


TINY = {
    "vocabulary_id": "test-tiny",
    "version": 1,
    "k_max": 2,
    "consistency_rule": {
        "name": "test-one-sided",
        "version": 1,
        "effect_complete_token": "ACQUIRE_COMPLETE_SOURCE_SET",
    },
    "predicates": [
        {"id": "p01", "field": "a", "op": "==", "value": True, "readable": "a"},
        {"id": "p02", "field": "a", "op": "==", "value": False, "readable": "not a"},
        {"id": "p03", "field": "b", "op": ">", "value": 0, "readable": "b>0"},
    ],
    "exclusive_groups": [["p01", "p02"]],
}


def tiny_vocab():
    tmp = Path(tempfile.mkdtemp(prefix="d0tiny-"))
    p = tmp / "tiny.json"
    p.write_text(json.dumps(TINY, indent=2))
    v = V.load_vocabulary(p)
    return v, tmp


def tiny_state(a, b):
    return {"a": a, "b": b}


# ---------------- vocabulary ----------------

@test
def test_vocab_loads_39_predicates_in_order():
    assert len(REAL_VOCAB["predicates"]) == 39
    assert [p["id"] for p in REAL_VOCAB["predicates"]] == [
        f"p{i:02d}" for i in range(1, 40)
    ]


@test
def test_vocab_hash_stable_64hex():
    h1 = V.vocabulary_hash(REAL_VOCAB)
    h2 = V.vocabulary_hash(V.load_vocabulary(HERE / "vocabularies" / "acquisition_v1.json"))
    assert h1 == h2 and len(h1) == 64 and all(c in "0123456789abcdef" for c in h1)


@test
def test_vocab_rejects_unknown_op():
    bad = json.loads(json.dumps({"predicates": REAL_VOCAB["predicates"],
                                 "k_max": 2, "exclusive_groups": []}))
    bad["predicates"][0]["op"] = "eq"
    tmp = Path(tempfile.mkdtemp(prefix="d0bad-"))
    p = tmp / "bad.json"
    p.write_text(json.dumps(bad))
    try:
        V.load_vocabulary(p)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_vocab_exclusive_groups_reference_known_ids():
    ids = {p["id"] for p in REAL_VOCAB["predicates"]}
    for g in REAL_VOCAB["exclusive_groups"]:
        assert len(g) >= 2
        for pid in g:
            assert pid in ids


@test
def test_hypothesis_count_is_782():
    # 1 TRUE + 39 singletons + C(39,2)=741 pairs + 1 NULL
    hyps = V.enumerate_hypotheses(REAL_VOCAB)
    assert len(hyps) == 1 + 39 + 39 * 38 // 2 + 1 == 782


@test
def test_hypothesis_canonical_order():
    hyps = V.enumerate_hypotheses(REAL_VOCAB)
    assert hyps[0]["hid"] == "TRUE"
    assert hyps[1]["hid"] == "C:p01"
    assert hyps[-1]["hid"] == "NULL"
    hyps2 = V.enumerate_hypotheses(REAL_VOCAB)
    assert [h["hid"] for h in hyps] == [h["hid"] for h in hyps2]


@test
def test_hypothesis_no_duplicate_ids():
    hyps = V.enumerate_hypotheses(REAL_VOCAB)
    hids = [h["hid"] for h in hyps]
    assert len(set(hids)) == len(hids)


@test
def test_region_count_is_781():
    # R:TRUE + 39 + 741 (no R:NULL)
    assert len(V.enumerate_regions(REAL_VOCAB)) == 781


# ---------------- predicate evaluation ----------------

@test
def test_fires_semantics_on_synthetic_states():
    v = REAL_VOCAB
    s_search0 = full_state(n_searches=0)
    s_search1 = full_state(n_searches=1)
    h_true = {"hid": "TRUE", "pids": []}
    h_null = {"hid": "NULL", "pids": None}
    h_p11 = {"hid": "C:p11", "pids": ["p11"]}
    assert V.fires(h_true, s_search0, v) is True
    assert V.fires(h_null, s_search0, v) is False
    assert V.fires(h_p11, s_search0, v) is True
    assert V.fires(h_p11, s_search1, v) is False


@test
def test_eval_all_ops():
    cases = [
        ("==", 3, 3, True), ("!=", 3, 4, True),
        (">", 3, 2, True), ("<", 3, 4, True),
        (">=", 3, 3, True), ("<=", 3, 3, True),
        ("in", "x", ["x", "y"], True), ("in", "z", ["x", "y"], False),
        ("==", 3, 4, False),
    ]
    for op, actual, value, expect in cases:
        pred = {"id": "px", "field": "f", "op": op, "value": value}
        assert V.eval_predicate(pred, {"f": actual}) is expect, op


@test
def test_eval_missing_field_raises():
    try:
        V.eval_predicate({"id": "px", "field": "nope", "op": "==", "value": 1}, {})
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


# ---------------- consistency ----------------

@test
def test_firing_at_complete_eliminates():
    v = REAL_VOCAB
    hyps = V.enumerate_hypotheses(v)
    s = full_state(n_searches=1, coverage_complete=False)
    e = ev("e1", s, ["ACQUIRE_COMPLETE_SOURCE_SET"])
    survivors, eliminated = VS.apply_evidence(hyps, [e], v)
    dead = {d["hid"] for d in eliminated}
    # every hypothesis firing at s must be dead
    for h in hyps:
        if V.fires(h, s, v):
            assert h["hid"] in dead, h["hid"]
    assert "NULL" not in dead


@test
def test_firing_at_other_retains_everything():
    v = REAL_VOCAB
    hyps = V.enumerate_hypotheses(v)
    e = ev("e1", full_state(), ["OTHER"])
    survivors, eliminated = VS.apply_evidence(hyps, [e], v)
    assert eliminated == [] and len(survivors) == 782


@test
def test_quiet_at_complete_is_retained():
    v = REAL_VOCAB
    hyps = V.enumerate_hypotheses(v)
    # n_searches=1: C:p11 (n_searches==0) is quiet -> must survive a COMPLETE
    e = ev("e1", full_state(n_searches=1), ["ACQUIRE_COMPLETE_SOURCE_SET"])
    survivors, eliminated = VS.apply_evidence(hyps, [e], v)
    assert "C:p11" in {h["hid"] for h in survivors}
    assert "NULL" in {h["hid"] for h in survivors}


@test
def test_h1_fragment_killed_by_targeted_search_complete():
    # mirrors ex-002 shape: a second targeted search closed the gap
    v = REAL_VOCAB
    hyps = V.enumerate_hypotheses(v)
    e1 = ev("e1", full_state(), ["OTHER"])
    e2 = ev("e2", full_state(
        n_searches=1, coverage_fraction=0.167, distinct_sources_discovered=1,
        proposed_action_class="SEARCH_CONTENT",
        proposed_action_role="targeted_search",
        steps_used=1, steps_remaining=7), ["ACQUIRE_COMPLETE_SOURCE_SET"])
    survivors, eliminated = VS.apply_evidence(hyps, [e1], v)
    survivors, eliminated = VS.apply_evidence(survivors, [e2], v)
    dead = {d["hid"] for d in eliminated}
    assert "C:p02|p35" in dead  # fires at e2 (coverage incomplete + targeted_search)
    assert "C:p11" not in dead  # quiet at e2 (n_searches=1)


@test
def test_null_survives_any_evidence():
    v = REAL_VOCAB
    hyps = V.enumerate_hypotheses(v)
    e = ev("e1", full_state(), ["ACQUIRE_COMPLETE_SOURCE_SET", "OTHER"])
    survivors, eliminated = VS.apply_evidence(hyps, [e], v)
    assert "NULL" in {h["hid"] for h in survivors}


@test
def test_evidence_input_order_irrelevant():
    v = REAL_VOCAB
    hyps = V.enumerate_hypotheses(v)
    e1 = ev("e2", full_state(n_searches=1), ["ACQUIRE_COMPLETE_SOURCE_SET"])
    e2 = ev("e1", full_state(), ["OTHER"])
    sa1, ea1 = VS.apply_evidence(hyps, [e1], v)
    sa, ea2 = VS.apply_evidence(sa1, [e2], v)
    sb1, eb1 = VS.apply_evidence(hyps, [e2], v)
    sb, eb2 = VS.apply_evidence(sb1, [e1], v)
    assert [h["hid"] for h in sa] == [h["hid"] for h in sb]
    assert sorted(d["hid"] for d in ea1 + ea2) == sorted(d["hid"] for d in eb1 + eb2)


# ---------------- elimination provenance ----------------

@test
def test_eliminated_by_carries_evidence_id():
    v = REAL_VOCAB
    hyps = V.enumerate_hypotheses(v)
    e = ev("ex-9", full_state(n_searches=0), ["ACQUIRE_COMPLETE_SOURCE_SET"])
    survivors, eliminated = VS.apply_evidence(hyps, [e], v)
    assert len(eliminated) > 0
    for d in eliminated:
        assert set(d.keys()) == {"hid", "eliminated_by"}
        assert d["eliminated_by"] == "ex-9"


@test
def test_survivor_order_stays_canonical():
    v = REAL_VOCAB
    hyps = V.enumerate_hypotheses(v)
    order = [h["hid"] for h in hyps]
    e = ev("e1", full_state(n_searches=3), ["ACQUIRE_COMPLETE_SOURCE_SET"])
    survivors, eliminated = VS.apply_evidence(hyps, [e], v)
    got = [h["hid"] for h in survivors]
    assert got == [h for h in order if h in set(got)]


# ---------------- disagreement ----------------

@test
def test_prediction_in_region_three_valued():
    v, tmp = tiny_vocab()
    try:
        h_p1 = {"hid": "C:p01", "pids": ["p01"]}
        h_null = {"hid": "NULL", "pids": None}
        # subset -> FIRES
        assert V.prediction_in_region(h_p1, ["p01", "p03"], v) == "FIRES"
        # exclusive with a region predicate -> QUIET
        assert V.prediction_in_region(h_p1, ["p02"], v) == "QUIET"
        assert V.prediction_in_region(h_null, ["p02"], v) == "QUIET"
        # neither -> UNDETERMINED
        assert V.prediction_in_region(h_p1, ["p03"], v) == "UNDETERMINED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_region_satisfied():
    v, tmp = tiny_vocab()
    try:
        assert V.region_satisfied(["p01", "p03"], tiny_state(True, 5), v) is True
        assert V.region_satisfied(["p01", "p03"], tiny_state(False, 5), v) is False
        assert V.region_satisfied(["p02"], tiny_state(True, 0), v) is False
        assert V.region_satisfied([], tiny_state(True, 0), v) is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_disagreement_found_synthetic():
    # e1 observes a=True,b=0 with OTHER: first eligible = R:p01, pair (C:p01, C:p02)
    v, tmp = tiny_vocab()
    try:
        hyps = V.enumerate_hypotheses(v)
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        survivors, eliminated = VS.apply_evidence(hyps, [e], v)
        dis = VS.find_disagreement(survivors, [e], v)
        assert dis is not None
        assert dis["region"]["rid"] == "R:p01"
        # TRUE (empty conjunction) is FIRES-determined in every region and is
        # first in canonical order; C:p02 is the first QUIET (exclusive p01/p02)
        assert dis["hypothesis_ids"] == ["TRUE", "C:p02"]
        assert dis["predictions"] == {"TRUE": "INTERVENE", "C:p02": "QUIET"}
        assert dis["ordinary_work"] == "observed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_observed_regions_rank_first():
    # e1 observes a=False: R:p01 (unknown) precedes R:p02 (observed) canonically,
    # but the observed region must win the ranking.
    v, tmp = tiny_vocab()
    try:
        hyps = V.enumerate_hypotheses(v)
        e = ev("e1", tiny_state(False, 0), ["OTHER"])
        survivors, eliminated = VS.apply_evidence(hyps, [e], v)
        dis = VS.find_disagreement(survivors, [e], v)
        assert dis["region"]["rid"] == "R:p02"
        assert dis["ordinary_work"] == "observed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_region_with_complete_observation_excluded():
    # a COMPLETE observed inside R:p01 removes it from eligibility
    v, tmp = tiny_vocab()
    try:
        hyps = V.enumerate_hypotheses(v)
        e = ev("e1", tiny_state(True, 0), ["ACQUIRE_COMPLETE_SOURCE_SET"])
        survivors, eliminated = VS.apply_evidence(hyps, [e], v)
        regions = VS.eligible_regions(survivors, [e], v)
        assert all(r["region"]["rid"] != "R:p01" for r in regions)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_contradictory_region_never_eligible():
    # R:p01|p02 is unsatisfiable: no experience can be gathered there
    v, tmp = tiny_vocab()
    try:
        hyps = V.enumerate_hypotheses(v)
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        survivors, eliminated = VS.apply_evidence(hyps, [e], v)
        regions = VS.eligible_regions(survivors, [e], v)
        assert all(r["region"]["rid"] != "R:p01|p02" for r in regions)
        # R:TRUE is likewise never eligible (not an actionable experience target)
        assert all(r["region"]["rid"] != "R:TRUE" for r in regions)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_no_disagreement_when_only_null_and_contradictory_survive():
    v, tmp = tiny_vocab()
    try:
        hyps = V.enumerate_hypotheses(v)
        e1 = ev("e1", tiny_state(True, 5), ["ACQUIRE_COMPLETE_SOURCE_SET"])
        e2 = ev("e2", tiny_state(False, 5), ["ACQUIRE_COMPLETE_SOURCE_SET"])
        survivors, eliminated = VS.apply_evidence(hyps, [e1], v)
        survivors, eliminated = VS.apply_evidence(survivors, [e2], v)
        got = {h["hid"] for h in survivors}
        assert got == {"NULL", "C:p01|p02"}
        assert VS.find_disagreement(survivors, [e1, e2], v) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- ledger ----------------

def _tiny_materialization(v, tmpdir, evidence):
    src = Path(tmpdir) / "evidence.jsonl"
    src.write_text("\n".join(json.dumps({
        "evidence_id": e["evidence_id"],
        "state_before": e["state_before"],
        "observed_effect": e["observed_effect"],
    }) for e in evidence))
    out = Path(tmpdir) / "developmental"
    return L.build(evidence, v, Path(tmpdir) / "tiny.json", [src], out), out


@test
def test_build_writes_three_materialization_files():
    v, tmp = tiny_vocab()
    try:
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        _, out = _tiny_materialization(v, tmp, [e])
        assert (out / "state.json").exists()
        assert (out / "version_space.jsonl").exists()
        assert (out / "history.jsonl").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_history_initial_record_has_enumeration_provenance():
    v, tmp = tiny_vocab()
    try:
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        _, out = _tiny_materialization(v, tmp, [e])
        recs = [json.loads(l) for l in (out / "history.jsonl").read_text().splitlines()]
        init = recs[0]
        assert init["record_seq"] == 0
        assert init["transition"] == "initial_construction"
        assert init["from"] == "EMPTY" and init["to"] == "D0"
        assert init["initial_hypothesis_count"] == 8
        assert init["predicate_enumeration_order"] == ["p01", "p02", "p03"]
        assert init["k_max"] == 2
        assert init["consistency_rule"] == "test-one-sided"
        assert init["conjunction_enumeration_order"]
        assert init["pruning_canonicalization"]
        # initial construction is f(EMPTY, EMPTY, V): no evidence embedded
        assert "evidence_application_order" not in init
        assert recs[1]["transition"] == "experience_admitted"
        assert recs[1]["experience"] == "e1"
        assert recs[1]["record_seq"] == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_version_space_rederivable_from_recorded_orders():
    # The materialized version space must be exactly re-derivable from the
    # enumeration orders recorded in history record 0 (proof of derivation).
    v, tmp = tiny_vocab()
    try:
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        _, out = _tiny_materialization(v, tmp, [e])
        recs = [json.loads(l) for l in (out / "history.jsonl").read_text().splitlines()]
        init = recs[0]
        assert init["predicate_enumeration_order"] == [p["id"] for p in v["predicates"]]
        hyps = V.enumerate_hypotheses(v)  # deterministic from vocab
        materialized = [json.loads(l)["hid"]
                        for l in (out / "version_space.jsonl").read_text().splitlines()]
        # every materialized survivor is a hypothesis, in canonical order
        canon = [h["hid"] for h in hyps]
        assert materialized == [h for h in canon if h in set(materialized)]
        assert len(materialized) == 8  # OTHER eliminates nothing
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_every_history_record_carries_source_identity():
    v, tmp = tiny_vocab()
    try:
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        _, out = _tiny_materialization(v, tmp, [e])
        for line in (out / "history.jsonl").read_text().splitlines():
            rec = json.loads(line)
            assert "source_identity" in rec
            si = rec["source_identity"]
            assert si["d0_code_hash"]
            assert si["vocabulary_hash"]
            assert si["vocabulary_id"] == "test-tiny"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_load_rejects_tampered_materialization():
    v, tmp = tiny_vocab()
    try:
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        _, out = _tiny_materialization(v, tmp, [e])
        p = out / "version_space.jsonl"
        with p.open("a") as f:
            f.write(json.dumps({"hid": "FORGED", "predicates": ["p01"]}) + "\n")
        try:
            L.load_materialization(out)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_incremental_update_byte_matches_full_rebuild():
    v, tmp = tiny_vocab()
    try:
        e1 = ev("e1", tiny_state(True, 0), ["OTHER"])
        e2 = ev("e2", tiny_state(False, 1), ["ACQUIRE_COMPLETE_SOURCE_SET"])
        _, full = _tiny_materialization(v, tmp, [e1, e2])
        full_bytes = {p.name: p.read_bytes() for p in full.iterdir()}
        inc = Path(tmp) / "inc"
        src = Path(tmp) / "evidence.jsonl"
        L.build([], v, Path(tmp) / "tiny.json", [src], inc)
        L.update(inc, [e1], v, Path(tmp) / "tiny.json", [src])
        L.update(inc, [e2], v, Path(tmp) / "tiny.json", [src])
        inc_bytes = {p.name: p.read_bytes() for p in inc.iterdir()}
        assert inc_bytes == full_bytes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_update_rejects_duplicate_evidence():
    v, tmp = tiny_vocab()
    try:
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        _, out = _tiny_materialization(v, tmp, [e])
        try:
            L.update(out, [e], v, Path(tmp) / "tiny.json", [Path(tmp) / "evidence.jsonl"])
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_update_rejects_vocabulary_change():
    v, tmp = tiny_vocab()
    try:
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        _, out = _tiny_materialization(v, tmp, [e])
        v2 = dict(v)
        v2["vocabulary_id"] = "other"
        try:
            L.update(out, [ev("e2", tiny_state(False, 0), ["OTHER"])], v2,
                     Path(tmp) / "tiny.json", [Path(tmp) / "evidence.jsonl"])
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_destructive_rebuild_byte_matches():
    v, tmp = tiny_vocab()
    try:
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        _, out = _tiny_materialization(v, tmp, [e])
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        shutil.rmtree(out)
        L.build([e], v, Path(tmp) / "tiny.json", [Path(tmp) / "evidence.jsonl"], out)
        after = {p.name: p.read_bytes() for p in out.iterdir()}
        assert before == after
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- request ----------------

@test
def test_build_request_returns_none_when_no_disagreement():
    v, tmp = tiny_vocab()
    try:
        e1 = ev("e1", tiny_state(True, 5), ["ACQUIRE_COMPLETE_SOURCE_SET"])
        e2 = ev("e2", tiny_state(False, 5), ["ACQUIRE_COMPLETE_SOURCE_SET"])
        state, out = _tiny_materialization(v, tmp, [e1, e2])
        loaded = L.load_materialization(out)
        assert R.build_request(loaded, v, [e1, e2]) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_request_validates_against_referenced_state():
    v, tmp = tiny_vocab()
    try:
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        _, out = _tiny_materialization(v, tmp, [e])
        loaded = L.load_materialization(out)
        req = R.build_request(loaded, v, [e])
        assert req is not None
        assert req["disagreement"]["region"]["rid"] == "R:p01"
        assert R.validate_request(req, out, v, [e]) == "REQUEST_VALID"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_request_id_is_deterministic():
    v, tmp = tiny_vocab()
    try:
        e = ev("e1", tiny_state(True, 0), ["OTHER"])
        _, out = _tiny_materialization(v, tmp, [e])
        loaded = L.load_materialization(out)
        r1 = R.build_request(loaded, v, [e])
        r2 = R.build_request(loaded, v, [e])
        assert r1["request_id"] == r2["request_id"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _tampered_request():
    v, tmp = tiny_vocab()
    e = ev("e1", tiny_state(True, 0), ["OTHER"])
    _, out = _tiny_materialization(v, tmp, [e])
    loaded = L.load_materialization(out)
    req = R.build_request(loaded, v, [e])
    return v, tmp, e, out, req


@test
def test_tampered_hypothesis_ids_rejected():
    v, tmp, e, out, req = _tampered_request()
    try:
        bad = json.loads(json.dumps(req))
        bad["disagreement"]["hypothesis_ids"] = ["C:p01", "NULL"]
        try:
            R.validate_request(bad, out, v, [e])
            raise AssertionError("expected RequestInvalid")
        except R.RequestInvalid:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_tampered_region_rejected():
    v, tmp, e, out, req = _tampered_request()
    try:
        bad = json.loads(json.dumps(req))
        bad["disagreement"]["region"]["rid"] = "R:p03"
        try:
            R.validate_request(bad, out, v, [e])
            raise AssertionError("expected RequestInvalid")
        except R.RequestInvalid:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_wrong_state_hash_rejected():
    v, tmp, e, out, req = _tampered_request()
    try:
        bad = json.loads(json.dumps(req))
        bad["developmental_state_hash"] = "0" * 64
        try:
            R.validate_request(bad, out, v, [e])
            raise AssertionError("expected RequestInvalid")
        except R.RequestInvalid:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_wrong_vocabulary_hash_rejected():
    v, tmp, e, out, req = _tampered_request()
    try:
        bad = json.loads(json.dumps(req))
        bad["derivation"]["vocabulary_hash"] = "0" * 64
        try:
            R.validate_request(bad, out, v, [e])
            raise AssertionError("expected RequestInvalid")
        except R.RequestInvalid:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_claimed_constructible_capsule_rejected_in_v0():
    v, tmp, e, out, req = _tampered_request()
    try:
        bad = json.loads(json.dumps(req))
        bad["reachability"]["capsule"] = {"status": "constructible", "spec": {}, "reason": None}
        try:
            R.validate_request(bad, out, v, [e])
            raise AssertionError("expected RequestInvalid")
        except R.RequestInvalid:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def test_corrupt_request_id_rejected():
    v, tmp, e, out, req = _tampered_request()
    try:
        bad = json.loads(json.dumps(req))
        bad["request_id"] = "f" * 64
        try:
            R.validate_request(bad, out, v, [e])
            raise AssertionError("expected RequestInvalid")
        except R.RequestInvalid:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    passed = 0
    failed = []
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append((fn.__name__, repr(exc)))
    print(f"{passed}/{len(TESTS)} tests passed")
    for name, err in failed:
        print(f"FAIL {name}: {err}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
