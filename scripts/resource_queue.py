#!/usr/bin/env python3
"""RESOURCE-GATED QUEUED EXECUTION (resource_queue.py).

Extends the COMMITMENT-DISPATCH-0 pattern to resource-blocked jobs: an
already-authorized job waits durably until a mechanical resource probe
reports availability, then dispatches automatically with no second human
authorization.

Durable states per idempotency key (append-only state/resource_queue.jsonl):
  (none) -> WAITING_RESOURCE -> READY -> DISPATCH_INTENT -> DISPATCHED
    -> terminal (via job completion record)
  DISPATCH_REFUSED is supersedable by a fresh intent (new nonce).
  DISPATCHED and REVOKED are terminal for the key.
  A crash between INTENT and DISPATCHED reconciles (completes the open
  intent); it never relaunches. Restart means reconcile, not relaunch.

Invariants:
  - GO persists across temporary resource unavailability. A temporary
    external boundary suspends execution; it does not erase authorization
    and does not require another human contact event.
  - resource_available + valid queued authorization => automatic execution.
  - Probes are mechanical and deterministic: stdlib only, no network,
    no subprocess, no model calls. A probe returns True (available) or
    False (unavailable), or raises ResourceSignalMissing when no
    deterministic signal exists for the resource on this box. Unknown is
    never coerced into available or unavailable; the job keeps waiting.
  - HOLD/STOP: revoke(job_id) writes REVOKED (terminal). Revocation is
    checked before every transition out of WAITING_RESOURCE.

This module writes ONLY to state/resource_queue.jsonl and to
dispatch/resource_jobs/<job_id>/. It never mutates the job's frozen
packet, the authorization record, H, M, or K.

No developmental credit: this is continuity plumbing for
already-authorized work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

POLICY_VERSION = "resource-queue-0"

QUEUE_LEDGER = "state/resource_queue.jsonl"
REVOCATION_LEDGER = "state/resource_queue_revocations.jsonl"


class ResourceSignalMissing(Exception):
    """No deterministic signal exists for the resource on this box.

    Carries observed_interfaces (what was inspected) and
    required_condition (what would be needed). The caller must not
    invent an availability heuristic.
    """

    def __init__(self, resource_id: str, observed_interfaces: list,
                 required_condition: str):
        super().__init__(f"BLOCKED_RESOURCE_SIGNAL_MISSING: {resource_id}")
        self.resource_id = resource_id
        self.observed_interfaces = observed_interfaces
        self.required_condition = required_condition


def _probe_grokbot() -> bool:
    """Mechanical probe for grokbot execution-seat availability.

    Inspected 2026-10-02 on the analysis box: no grokbot CLI, no config
    file, no API endpoint, no shared filesystem and no network path to
    Grok Bot's box (separate machine; everything known about its side
    arrives relayed through the operator). Weekly usage state is known
    only via operator relay. There is no deterministic signal to bind.
    """
    raise ResourceSignalMissing(
        resource_id="grokbot",
        observed_interfaces=[
            "no grokbot CLI on PATH",
            "no grokbot config file (~/.config, ~/.grok*, home dotfiles)",
            "no grokbot API endpoint or skill on this box",
            "no shared filesystem with Grok Bot's box (separate machine)",
            "no network path to Grok Bot's box reachable from this box",
            "usage/quota state arrives only via operator relay (chat), "
            "not a machine-readable signal",
        ],
        required_condition=(
            "mechanically establish execution resource availability: a "
            "deterministic, machine-readable signal on a box this scheduler "
            "can read (file, exit code, or local API) reporting grokbot "
            "weekly-usage window open/closed"
        ),
    )


# resource_id -> probe callable. Probes take no arguments and return bool.
# Register new resources here; each probe documents what it inspects.
PROBES = {
    "grokbot": _probe_grokbot,
}


def probe_resource(resource_id: str) -> bool:
    """Mechanically check one resource. No model calls, ever."""
    probe = PROBES.get(resource_id)
    if probe is None:
        raise ResourceSignalMissing(
            resource_id=resource_id,
            observed_interfaces=[f"no probe registered for {resource_id!r}"],
            required_condition="register a mechanical probe for the resource",
        )
    return bool(probe())


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def h(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_for(job_id: str) -> str:
    return f"resource-job:{job_id}"


def append_record(life0: Path, record: dict, ledger: str = QUEUE_LEDGER) -> None:
    path = life0 / ledger
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def fold_queue(life0: Path) -> dict:
    """Latest queue record per idempotency key."""
    path = life0 / QUEUE_LEDGER
    latest: dict = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        k = r.get("key")
        if k:
            latest[k] = r
    return latest


def is_revoked(life0: Path, job_id: str) -> dict | None:
    """Return the revocation record if job_id was revoked, else None."""
    path = life0 / REVOCATION_LEDGER
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("job_id") == job_id and r.get("event") == "REVOKED":
            return r
    return None


def revoke(life0: Path, job_id: str, reason: str, by: str = "operator") -> dict:
    """HOLD/STOP a queued job. Terminal for the key. Checked before every
    transition out of WAITING_RESOURCE."""
    key = key_for(job_id)
    record = {"event": "REVOKED", "at": utcnow_iso(), "key": key,
              "job_id": job_id, "by": by, "reason": reason,
              "policy_version": POLICY_VERSION}
    append_record(life0, record, REVOCATION_LEDGER)
    # Also fold into the queue ledger so the key's latest state is terminal.
    append_record(life0, record)
    return {"revoked": True, "job_id": job_id, "key": key, "reason": reason}


def enqueue(life0: Path, job_spec: dict) -> dict:
    """Place an authorized job into WAITING_RESOURCE.

    job_spec must carry: job_id, authorization {authorized_by, at, scope},
    packet {label_path, label_sha256, ...}, required_resource,
    resource_reason. Verifies the label file hash at enqueue time.
    """
    job_id = job_spec.get("job_id")
    if not job_id:
        return {"enqueued": False, "reason": "job_spec missing job_id"}
    auth = job_spec.get("authorization") or {}
    if auth.get("authorized_by") != "operator":
        return {"enqueued": False, "job_id": job_id,
                "reason": "authorization.authorized_by must be 'operator'"}
    packet = job_spec.get("packet") or {}
    label_path = packet.get("label_path")
    label_sha256 = packet.get("label_sha256")
    if not label_path or not label_sha256:
        return {"enqueued": False, "job_id": job_id,
                "reason": "packet.label_path and packet.label_sha256 required"}
    lp = Path(label_path)
    if not lp.exists():
        return {"enqueued": False, "job_id": job_id,
                "reason": f"label file not found: {label_path}"}
    if sha256_file(lp) != label_sha256:
        return {"enqueued": False, "job_id": job_id,
                "reason": "label_sha256 mismatch at enqueue: refusing to queue "
                          "a packet that does not match its frozen hash"}
    required_resource = job_spec.get("required_resource")
    if not required_resource:
        return {"enqueued": False, "job_id": job_id,
                "reason": "required_resource missing"}

    key = key_for(job_id)
    latest = fold_queue(life0).get(key)
    if latest and latest.get("event") in ("DISPATCHED", "REVOKED"):
        return {"enqueued": False, "job_id": job_id, "key": key,
                "reason": f"key terminal ({latest.get('event')})"}
    if latest and latest.get("event") == "WAITING_RESOURCE":
        return {"enqueued": False, "job_id": job_id, "key": key,
                "reason": "already WAITING_RESOURCE"}

    record = {"event": "WAITING_RESOURCE", "at": utcnow_iso(), "key": key,
              "job_id": job_id, "policy_version": POLICY_VERSION,
              "authorization": auth, "packet": packet,
              "required_resource": required_resource,
              "resource_reason": job_spec.get("resource_reason", ""),
              "next_job": job_spec.get("next_job"),
              "execution_box": job_spec.get("execution_box", ""),
              "queue_nonce": secrets.token_hex(16)}
    append_record(life0, record)
    return {"enqueued": True, "job_id": job_id, "key": key,
            "state": "WAITING_RESOURCE"}


def _refuse(life0: Path, key: str, job_id: str, reason: str) -> dict:
    record = {"event": "DISPATCH_REFUSED", "at": utcnow_iso(), "key": key,
              "job_id": job_id, "reason": reason}
    append_record(life0, record)
    return {"dispatched": False, "verdict": "REFUSE", "job_id": job_id,
            "key": key, "reason": reason}


def _verify_binding(life0: Path, job_id: str, waiting: dict) -> tuple[dict | None, str]:
    """Re-verify everything bound at enqueue. Any mismatch -> refuse."""
    rev = is_revoked(life0, job_id)
    if rev:
        return None, f"job revoked ({rev.get('reason')})"

    packet = waiting.get("packet") or {}
    lp = Path(packet.get("label_path", ""))
    if not lp.exists():
        return None, f"label file vanished since enqueue: {packet.get('label_path')}"
    if sha256_file(lp) != packet.get("label_sha256"):
        return None, ("label_sha256 mismatch: frozen packet changed since "
                      "enqueue; fail closed, do not repair")

    binding = {
        "policy_version": POLICY_VERSION,
        "job_id": job_id,
        "authorization": waiting.get("authorization"),
        "packet": packet,
        "packet_verified_at": utcnow_iso(),
        "required_resource": waiting.get("required_resource"),
        "execution_box": waiting.get("execution_box", ""),
        "next_job": waiting.get("next_job"),
        "acceptable_actions_binding": (
            "execution-box: bind from live residue store per 031A precedent; "
            "persist the exact bound value and its provenance before outcome "
            "observation"),
        "queue_nonce": waiting.get("queue_nonce"),
        "dispatch_nonce": secrets.token_hex(16),
    }
    return binding, ""


def _write_handoff(life0: Path, job_id: str, binding: dict) -> Path:
    """Materialize the complete execution handoff for the execution box.
    First-delivery-complete: everything the execution seat needs, no
    follow-up queries."""
    job_dir = life0 / "dispatch" / "resource_jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    handoff = {
        "job_id": job_id,
        "policy_version": POLICY_VERSION,
        "authorization": binding["authorization"],
        "packet": binding["packet"],
        "binding": binding,
        "binding_hash": h(binding),
        "execution_instructions": [
            "Bind acceptable_actions from the live residue store.",
            "Persist the exact bound value and its provenance BEFORE "
            "observing the outcome.",
            "Run the assay exactly as frozen (no M2 patch, no relabeling, "
            "no pivot/residue substitution, no rescue if null).",
            "Route the verdict only to the packet's verdict_space.",
        ],
        "materialized_at": utcnow_iso(),
    }
    # Ship the frozen label bytes alongside the handoff.
    label_src = Path(binding["packet"]["label_path"])
    (job_dir / "label.jsonl").write_bytes(label_src.read_bytes())
    outp = job_dir / "HANDOFF.json"
    tmp = outp.with_suffix(".tmp")
    tmp.write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.rename(tmp, outp)
    return outp


def dispatch_job(life0: Path, job_id: str) -> dict:
    """Drive one job WAITING_RESOURCE -> READY -> DISPATCH_INTENT -> DISPATCHED.

    Called only after the resource probe reports available. Re-verifies the
    frozen packet before launch; any mismatch fails closed.
    """
    key = key_for(job_id)
    latest = fold_queue(life0).get(key)
    if not latest:
        return {"dispatched": False, "verdict": "REFUSE", "job_id": job_id,
                "key": key, "reason": "no queue record for job"}
    event = latest.get("event")
    if event == "DISPATCHED":
        return {"dispatched": False, "verdict": "ALREADY_DISPATCHED",
                "job_id": job_id, "key": key}
    if event == "REVOKED":
        return {"dispatched": False, "verdict": "REVOKED", "job_id": job_id,
                "key": key}
    if event == "DISPATCH_INTENT":
        # Crash recovery: complete the open intent, never relaunch.
        out = _complete_intent(life0, job_id, key, latest)
        out["recovered"] = True
        return out
    if event != "WAITING_RESOURCE":
        return {"dispatched": False, "verdict": "REFUSE", "job_id": job_id,
                "key": key,
                "reason": f"unexpected state {event}; refusing"}

    binding, err = _verify_binding(life0, job_id, latest)
    if binding is None:
        return _refuse(life0, key, job_id, err)

    # WAITING_RESOURCE -> READY (resource already proven available by caller).
    append_record(life0, {"event": "READY", "at": utcnow_iso(), "key": key,
                          "job_id": job_id,
                          "resource": latest.get("required_resource")})
    # READY -> DISPATCH_INTENT.
    intent = {"event": "DISPATCH_INTENT", "at": utcnow_iso(), "key": key,
              "job_id": job_id, "binding": binding}
    append_record(life0, intent)
    out = _complete_intent(life0, job_id, key, intent)
    return out


def _complete_intent(life0: Path, job_id: str, key: str, intent: dict) -> dict:
    """DISPATCH_INTENT -> DISPATCHED: materialize the handoff. Idempotent:
    completing an already-completed intent returns ALREADY_DISPATCHED."""
    latest = fold_queue(life0).get(key)
    if latest and latest.get("event") == "DISPATCHED":
        return {"dispatched": False, "verdict": "ALREADY_DISPATCHED",
                "job_id": job_id, "key": key,
                "handoff": latest.get("handoff")}
    binding = intent.get("binding") or {}
    handoff_path = _write_handoff(life0, job_id, binding)
    record = {"event": "DISPATCHED", "at": utcnow_iso(), "key": key,
              "job_id": job_id,
              "handoff": str(handoff_path),
              "nonce": binding.get("dispatch_nonce"),
              "binding_hash": h(binding)}
    append_record(life0, record)
    return {"dispatched": True, "verdict": "DISPATCHED", "job_id": job_id,
            "key": key, "handoff": str(handoff_path),
            "binding_hash": record["binding_hash"]}


def mark_terminal(life0: Path, job_id: str, verdict: str,
                  result_ref: str = "") -> dict:
    """Record a job's terminal verdict. verdict must be in the job's
    verdict_space. Releases the next job per frozen ordering (eligible
    for authorization, never auto-enqueued)."""
    key = key_for(job_id)
    latest = fold_queue(life0).get(key)
    if not latest or latest.get("event") != "DISPATCHED":
        return {"terminal": False, "job_id": job_id, "key": key,
                "reason": "job is not DISPATCHED"}
    intent = None
    for r in _all_records(life0):
        if r.get("key") == key and r.get("event") == "DISPATCH_INTENT":
            intent = r
    verdict_space = ((intent or {}).get("binding") or {}).get("packet", {}).get(
        "verdict_space") or []
    if verdict_space and verdict not in verdict_space:
        return {"terminal": False, "job_id": job_id, "key": key,
                "reason": f"verdict {verdict!r} not in {verdict_space}"}
    record = {"event": "TERMINAL", "at": utcnow_iso(), "key": key,
              "job_id": job_id, "verdict": verdict,
              "result_ref": result_ref}
    append_record(life0, record)
    out = {"terminal": True, "job_id": job_id, "key": key, "verdict": verdict}
    # Release the next job per frozen ordering: eligible for authorization,
    # never auto-enqueued, never altered. The next_job relation was bound
    # into the dispatch intent from the WAITING_RESOURCE record.
    intent = None
    for r in _all_records(life0):
        if r.get("key") == key and r.get("event") == "DISPATCH_INTENT":
            intent = r
    nxt = ((intent or {}).get("binding") or {}).get("next_job") or {}
    if nxt.get("job_id"):
        rel = {"event": "NEXT_JOB_RELEASED", "at": utcnow_iso(),
               "key": key_for(nxt["job_id"]), "job_id": nxt["job_id"],
               "released_by": job_id,
               "relation": nxt.get("relation", ""),
               "note": "eligible for operator authorization per frozen "
                       "ordering; not enqueued, not altered"}
        append_record(life0, rel)
        out["next_job_released"] = nxt["job_id"]
    return out


def tick(life0: Path) -> dict:
    """Hourly tick: for each WAITING_RESOURCE job, mechanically check its
    resource. Unavailable -> preserve state, exit successfully. Available
    -> drive to DISPATCHED with no second human authorization. Signal
    missing -> record one observation, keep waiting, stay silent after.
    No model calls."""
    folded = fold_queue(life0)
    results = []
    for key, rec in sorted(folded.items()):
        if rec.get("event") != "WAITING_RESOURCE":
            continue
        job_id = rec.get("job_id")
        resource_id = rec.get("required_resource")
        try:
            available = probe_resource(resource_id)
        except ResourceSignalMissing as e:
            # Record once per job under a separate observation key; the job
            # key's latest event stays WAITING_RESOURCE so the tick keeps
            # checking. Subsequent ticks stay silent.
            sig_key = f"resource-signal:{job_id}"
            already = fold_queue(life0).get(sig_key) is not None
            if not already:
                append_record(life0, {
                    "event": "RESOURCE_SIGNAL_MISSING",
                    "at": utcnow_iso(), "key": sig_key, "job_id": job_id,
                    "resource_id": e.resource_id,
                    "observed_interfaces": e.observed_interfaces,
                    "required_condition": e.required_condition,
                })
            results.append({"job_id": job_id, "tick": "SIGNAL_MISSING",
                            "resource": resource_id})
            continue
        if not available:
            results.append({"job_id": job_id, "tick": "WAITING_RESOURCE",
                            "resource": resource_id})
            continue
        out = dispatch_job(life0, job_id)
        out["tick"] = "DISPATCHED" if out.get("dispatched") else out.get("verdict")
        results.append(out)
    return {"tick": "ok", "jobs": results}


def _all_records(life0: Path) -> list:
    path = life0 / QUEUE_LEDGER
    recs = []
    if not path.exists():
        return recs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return recs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RESOURCE-GATED QUEUED EXECUTION")
    ap.add_argument("--life0", required=True, help="life-0 directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("enqueue", help="place an authorized job in WAITING_RESOURCE")
    e.add_argument("--job-spec", required=True, help="path to job spec JSON")

    sub.add_parser("tick", help="hourly tick: probe resources, dispatch what is ready")

    d = sub.add_parser("dispatch", help="drive one job forward (resource already verified)")
    d.add_argument("--job", required=True)

    t = sub.add_parser("mark-terminal", help="record a job terminal verdict")
    t.add_argument("--job", required=True)
    t.add_argument("--verdict", required=True)
    t.add_argument("--result-ref", default="")

    r = sub.add_parser("revoke", help="HOLD/STOP a queued job (terminal)")
    r.add_argument("--job", required=True)
    r.add_argument("--reason", required=True)

    s = sub.add_parser("show", help="latest queue state per key")
    s.add_argument("--job", default=None)

    args = ap.parse_args(argv)
    life0 = Path(args.life0)

    if args.cmd == "enqueue":
        spec = json.loads(Path(args.job_spec).read_text(encoding="utf-8"))
        out = enqueue(life0, spec)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("enqueued") else 2
    if args.cmd == "tick":
        out = tick(life0)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    if args.cmd == "dispatch":
        out = dispatch_job(life0, args.job)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("verdict") in ("DISPATCHED", "ALREADY_DISPATCHED") else 2
    if args.cmd == "mark-terminal":
        out = mark_terminal(life0, args.job, args.verdict, args.result_ref)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("terminal") else 2
    if args.cmd == "revoke":
        out = revoke(life0, args.job, args.reason)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    if args.cmd == "show":
        folded = fold_queue(life0)
        if args.job:
            folded = {k: v for k, v in folded.items() if v.get("job_id") == args.job}
        slim = {k: {"event": v.get("event"), "job_id": v.get("job_id"),
                    "at": v.get("at")}
                for k, v in folded.items()}
        print(json.dumps(slim, indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
