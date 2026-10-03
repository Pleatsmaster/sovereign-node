"""Need formation: evidence enforcement, deterministic ids, queue dedup/order."""
from __future__ import annotations

import pytest

from life0.delta import detect_deltas
from life0.needs import (
    PRIORITIES,
    enqueue_need,
    form_need,
    load_queue,
    set_status,
)


def _delta(kind="new_file", key="w.md"):
    return {
        "delta_id": "delta_" + "a" * 32,
        "source": "inbox",
        "kind": kind,
        "key": key,
        "previous_state": None,
        "current_state": {"name": "w.md", "sha256": "q" * 64, "bytes": 2},
        "observed_at": "2026-10-01T00:00:00+00:00",
    }


def test_need_requires_evidence_no_exceptions():
    with pytest.raises(ValueError, match="requires an observed delta"):
        form_need(None)
    with pytest.raises(ValueError, match="requires an observed delta"):
        form_need({})
    with pytest.raises(ValueError, match="requires an observed delta"):
        form_need({"kind": "new_file"})  # no delta_id: not an observed delta


def test_need_unknown_kind_rejected():
    d = _delta(kind="alien_event")
    with pytest.raises(ValueError, match="no need template"):
        form_need(d)


def test_need_record_shape_and_deterministic_id():
    n1 = form_need(_delta())
    n2 = form_need(_delta())
    assert n1["need_id"] == n2["need_id"]  # substrate-minted, deterministic
    assert n1["need_id"].startswith("need_")
    assert n1["delta_id"] == "delta_" + "a" * 32
    assert n1["priority"] == "unfinished user work"
    assert n1["status"] == "NEW"
    assert "w.md" in n1["possible_need"]
    assert n1["obligation"].startswith("2.")


def test_priority_ordering():
    assert PRIORITIES == (
        "blocking failure",
        "integrity/safety problem",
        "unfinished user work",
        "recurrent operational friction",
        "useful opportunity",
        "informational housekeeping",
        "accepted program commitment",
    )


def test_queue_dedup_and_status_fold(tmp_path):
    n = form_need(_delta())
    assert enqueue_need(tmp_path, n) is True
    assert enqueue_need(tmp_path, n) is False  # same delta: no duplicate
    q = load_queue(tmp_path)
    assert list(q) == [n["need_id"]]
    assert q[n["need_id"]]["status"] == "NEW"
    set_status(tmp_path, n["need_id"], "STAGED_AWAITING_AUTHORIZATION")
    q2 = load_queue(tmp_path)
    assert q2[n["need_id"]]["status"] == "STAGED_AWAITING_AUTHORIZATION"
    with pytest.raises(ValueError, match="invalid need status"):
        set_status(tmp_path, n["need_id"], "LAUNCHED")  # not a real status


def test_queue_ordered_by_priority(tmp_path):
    n_low = form_need(_delta(kind="new_evidence", key="ev1"))
    n_high = form_need(_delta(kind="ci_new_failure", key="ci"))
    # enqueue low-priority first; fold must still order high-priority first
    enqueue_need(tmp_path, n_low)
    enqueue_need(tmp_path, n_high)
    ordered = list(load_queue(tmp_path))
    assert ordered[0] == n_high["need_id"]
    assert ordered[1] == n_low["need_id"]
