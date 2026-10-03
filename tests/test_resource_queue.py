"""RESOURCE-GATED QUEUED EXECUTION tests: apparatus verification around the seam.

Each test pins one acceptance property: durable WAITING_RESOURCE state,
mechanical resource gating (no model calls), exactly-once dispatch, crash
reconciliation, fail-closed packet verification, revocation, and the
missing-signal observation. Unknown is never coerced into available.
"""
import json
import sys
from pathlib import Path

import pytest

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "scripts"))
import resource_queue as rq  # noqa: E402


def write_label(tmp_path: Path, name: str = "label") -> Path:
    p = tmp_path / f"{name}.jsonl"
    p.write_text(json.dumps({"candidate_id": "r_test",
                             "pivot_id": "um-op-031:p01"}) + "\n",
                 encoding="utf-8")
    return p


def job_spec(label: Path, **overrides) -> dict:
    spec = {
        "job_id": "assay-031C",
        "authorization": {
            "authorized_by": "operator",
            "at": "2026-10-01T23:02:00-04:00",
            "scope": "GO - execute 031C unchanged when usage window opens",
        },
        "packet": {
            "label_path": str(label),
            "label_sha256": rq.sha256_file(label),
            "verdict_space": ["VALID_INERT", "CAUSAL_USEFUL",
                              "CAUSAL_UNRESOLVED", "INVALID"],
        },
        "required_resource": "grokbot",
        "resource_reason": "grokbot_weekly_usage_exhausted",
        "next_job": {"job_id": "assay-031D",
                     "relation": "frozen preregistered ordering"},
        "execution_box": "cap-execution-box",
    }
    spec.update(overrides)
    return spec


def test_enqueue_writes_waiting_resource(tmp_path):
    label = write_label(tmp_path)
    out = rq.enqueue(tmp_path, job_spec(label))
    assert out["enqueued"] is True
    assert out["state"] == "WAITING_RESOURCE"
    assert out["key"] == "resource-job:assay-031C"
    folded = rq.fold_queue(tmp_path)
    rec = folded[out["key"]]
    assert rec["event"] == "WAITING_RESOURCE"
    assert rec["required_resource"] == "grokbot"


def test_enqueue_refuses_hash_mismatch(tmp_path):
    label = write_label(tmp_path)
    spec = job_spec(label)
    spec["packet"]["label_sha256"] = "0" * 64
    out = rq.enqueue(tmp_path, spec)
    assert out["enqueued"] is False
    assert "mismatch" in out["reason"]


def test_enqueue_requires_operator_authorization(tmp_path):
    label = write_label(tmp_path)
    spec = job_spec(label)
    spec["authorization"]["authorized_by"] = "worker"
    out = rq.enqueue(tmp_path, spec)
    assert out["enqueued"] is False


def test_enqueue_idempotent_when_waiting(tmp_path):
    label = write_label(tmp_path)
    assert rq.enqueue(tmp_path, job_spec(label))["enqueued"] is True
    out = rq.enqueue(tmp_path, job_spec(label))
    assert out["enqueued"] is False
    assert "already WAITING_RESOURCE" in out["reason"]


def test_tick_preserves_waiting_when_resource_unavailable(tmp_path, monkeypatch):
    label = write_label(tmp_path)
    rq.enqueue(tmp_path, job_spec(label))
    monkeypatch.setitem(rq.PROBES, "grokbot", lambda: False)
    out = rq.tick(tmp_path)
    assert out["tick"] == "ok"
    assert out["jobs"][0]["tick"] == "WAITING_RESOURCE"
    folded = rq.fold_queue(tmp_path)
    assert folded["resource-job:assay-031C"]["event"] == "WAITING_RESOURCE"


def test_tick_dispatches_when_resource_available(tmp_path, monkeypatch):
    label = write_label(tmp_path)
    rq.enqueue(tmp_path, job_spec(label))
    monkeypatch.setitem(rq.PROBES, "grokbot", lambda: True)
    out = rq.tick(tmp_path)
    job = out["jobs"][0]
    assert job["verdict"] == "DISPATCHED"
    assert job["dispatched"] is True
    handoff = Path(job["handoff"])
    assert handoff.exists()
    assert (handoff.parent / "label.jsonl").exists()
    folded = rq.fold_queue(tmp_path)
    assert folded["resource-job:assay-031C"]["event"] == "DISPATCHED"


def test_tick_records_signal_missing_once_and_keeps_waiting(tmp_path):
    label = write_label(tmp_path)
    rq.enqueue(tmp_path, job_spec(label))
    # Real grokbot probe raises ResourceSignalMissing (no deterministic signal).
    out1 = rq.tick(tmp_path)
    assert out1["jobs"][0]["tick"] == "SIGNAL_MISSING"
    recs = rq._all_records(tmp_path)
    assert sum(1 for r in recs
               if r["event"] == "RESOURCE_SIGNAL_MISSING") == 1
    # Job key's latest event is still WAITING_RESOURCE: the tick keeps it.
    assert rq.fold_queue(tmp_path)["resource-job:assay-031C"]["event"] == \
        "WAITING_RESOURCE"
    out2 = rq.tick(tmp_path)
    recs2 = rq._all_records(tmp_path)
    assert sum(1 for r in recs2
               if r["event"] == "RESOURCE_SIGNAL_MISSING") == 1
    assert out2["jobs"][0]["tick"] == "SIGNAL_MISSING"


def test_dispatch_is_exactly_once(tmp_path, monkeypatch):
    label = write_label(tmp_path)
    rq.enqueue(tmp_path, job_spec(label))
    monkeypatch.setitem(rq.PROBES, "grokbot", lambda: True)
    first = rq.dispatch_job(tmp_path, "assay-031C")
    assert first["verdict"] == "DISPATCHED"
    second = rq.dispatch_job(tmp_path, "assay-031C")
    assert second["verdict"] == "ALREADY_DISPATCHED"
    assert second["dispatched"] is False


def test_crash_recovery_completes_intent(tmp_path, monkeypatch):
    label = write_label(tmp_path)
    rq.enqueue(tmp_path, job_spec(label))
    key = rq.key_for("assay-031C")
    # Simulate a crash between READY and DISPATCHED: hand-write an intent.
    rq.append_record(tmp_path, {"event": "READY", "at": rq.utcnow_iso(),
                                "key": key, "job_id": "assay-031C",
                                "resource": "grokbot"})
    rq.append_record(tmp_path, {"event": "DISPATCH_INTENT", "at": rq.utcnow_iso(),
                                "key": key, "job_id": "assay-031C",
                                "binding": {"job_id": "assay-031C",
                                           "packet": job_spec(label)["packet"],
                                           "authorization": job_spec(label)["authorization"],
                                           "dispatch_nonce": "abc"}})
    monkeypatch.setitem(rq.PROBES, "grokbot", lambda: True)
    out = rq.dispatch_job(tmp_path, "assay-031C")
    assert out["recovered"] is True
    assert out["verdict"] == "DISPATCHED"


def test_dispatch_refuses_on_packet_hash_change(tmp_path, monkeypatch):
    label = write_label(tmp_path)
    rq.enqueue(tmp_path, job_spec(label))
    # Mutate the frozen packet after enqueue: fail closed, do not repair.
    label.write_text(json.dumps({"candidate_id": "r_TAMPERED"}) + "\n",
                     encoding="utf-8")
    monkeypatch.setitem(rq.PROBES, "grokbot", lambda: True)
    out = rq.dispatch_job(tmp_path, "assay-031C")
    assert out["verdict"] == "REFUSE"
    assert "mismatch" in out["reason"]


def test_revoke_is_terminal(tmp_path, monkeypatch):
    label = write_label(tmp_path)
    rq.enqueue(tmp_path, job_spec(label))
    rq.revoke(tmp_path, "assay-031C", "operator HOLD")
    monkeypatch.setitem(rq.PROBES, "grokbot", lambda: True)
    out = rq.tick(tmp_path)
    # Revoked job is not WAITING_RESOURCE anymore, so tick skips it.
    assert out["jobs"] == []
    d = rq.dispatch_job(tmp_path, "assay-031C")
    assert d["verdict"] == "REVOKED"


def test_mark_terminal_validates_verdict_and_releases_next(tmp_path, monkeypatch):
    label = write_label(tmp_path)
    rq.enqueue(tmp_path, job_spec(label))
    monkeypatch.setitem(rq.PROBES, "grokbot", lambda: True)
    rq.dispatch_job(tmp_path, "assay-031C")
    bad = rq.mark_terminal(tmp_path, "assay-031C", "MADE_UP")
    assert bad["terminal"] is False
    good = rq.mark_terminal(tmp_path, "assay-031C", "VALID_INERT",
                            result_ref="batch-031C")
    assert good["terminal"] is True
    assert good["next_job_released"] == "assay-031D"
    # 031D is released as eligible, NOT enqueued: its key carries only the
    # NEXT_JOB_RELEASED observation, never a WAITING_RESOURCE record.
    folded = rq.fold_queue(tmp_path)
    assert folded["resource-job:assay-031D"]["event"] == "NEXT_JOB_RELEASED"
    assert not any(r.get("event") == "WAITING_RESOURCE"
                   and r.get("job_id") == "assay-031D"
                   for r in rq._all_records(tmp_path))


def test_probe_registry_is_mechanical():
    # Probes must be stdlib-only: no network/model SDK imports anywhere in
    # the module, and probe functions take no arguments (nothing to smuggle
    # a model call through).
    import ast
    tree = ast.parse(Path(rq.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = {"requests", "urllib3", "httpx", "openai", "anthropic", "boto3"}
    assert not (imported & banned), f"mechanical probes must not import: {imported & banned}"
    for name, fn in rq.PROBES.items():
        params = list(__import__("inspect").signature(fn).parameters)
        assert params == [], f"probe {name} must take no arguments"
