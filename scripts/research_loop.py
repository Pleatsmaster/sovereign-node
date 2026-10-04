#!/usr/bin/env python3
"""RESEARCH-LOOP-0: durable outer continuation over admitted commitments.

Uses existing projection, eligibility, priority, dispatch and agenda gates.
Does not admit work, change contracts, close obligations or execute resource
jobs directly. A worker command runs one existing bounded chain, as specified
by CHAIN_RUNBOOK_0. Its report never establishes satisfaction by itself.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
import agenda_common as agenda
import dispatch_commitment as dispatch
import execution_eligibility as eligibility
import propose_agenda
import resource_queue
from life0.needs import PRIORITIES, STANDING_OBLIGATIONS
from project_obligations import Projector

VERSION = "research-loop-0"
LEDGER = "state/research_loop.jsonl"
CONFIG = "state/research_loop_config.json"
CONTROLS = "state/research_loop_controls.jsonl"


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def records(path):
    """Malformed durable records are an integrity boundary, never ignored."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def event(life0, kind, **fields):
    previous = records(life0 / LEDGER)
    row = {"event": kind, "at": now(), "policy_version": VERSION,
           "seq": len(previous), "parent_hash": previous[-1]["hash"] if previous else None,
           **fields}
    row["hash"] = digest(row)
    append(life0 / LEDGER, row)
    return row


def verified_history(life0):
    history = records(life0 / LEDGER)
    parent = None
    for i, row in enumerate(history):
        body = {k: v for k, v in row.items() if k != "hash"}
        if row.get("seq") != i or row.get("parent_hash") != parent or digest(body) != row.get("hash"):
            raise ValueError("research ledger integrity failure")
        parent = row["hash"]
    return history


def worker_binding(argv):
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError("worker must be a nonempty JSON argv array")
    executable = shutil.which(argv[0])
    if executable is None:
        raise ValueError("worker executable unavailable")
    argv = [str(Path(executable).resolve()),
            *[str(Path(a).resolve()) if Path(a).is_file() else a for a in argv[1:]]]
    # Pin the executable and every existing file argument, including a script.
    # The runtime is trusted; this is identity binding, not an OS sandbox.
    files = {str(Path(a).resolve()): hashlib.sha256(Path(a).read_bytes()).hexdigest()
             for a in argv if Path(a).is_file()}
    return argv, files


def initialize(life0, argv, max_launches, max_seconds, timeout_seconds):
    if isinstance(max_launches, bool) or not isinstance(max_launches, int) or max_launches < 1:
        raise ValueError("max_launches must be a positive integer")
    if not all(math.isfinite(v) and v > 0 for v in (max_seconds, timeout_seconds)):
        raise ValueError("time bounds must be positive and finite")
    argv, files = worker_binding(argv)
    config = {"version": VERSION, "worker_argv": argv, "worker_files": files,
              "max_launches": max_launches, "max_seconds": max_seconds,
              "timeout_seconds": timeout_seconds, "api_budget_usd": 0,
              "mission": list(STANDING_OBLIGATIONS),
              "horizon_sha256": hashlib.sha256((life0 / "RESEARCH_HORIZON_0.md").read_bytes()).hexdigest()
              if (life0 / "RESEARCH_HORIZON_0.md").exists() else None}
    config["runtime_files"] = {
        str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
        for directory in (ROOT / "scripts", ROOT / "src")
        for p in directory.rglob("*.py")}
    if (life0 / CONFIG).exists() or (life0 / LEDGER).exists():
        raise ValueError("loop already initialized; budgets cannot reset on restart")
    path = life0 / CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as f:
        json.dump(config, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    event(life0, "LOOP_INITIALIZED", config_hash=digest(config))
    return config


def load_config(life0):
    config = json.loads((life0 / CONFIG).read_text())
    history = verified_history(life0)
    if not history or history[0].get("config_hash") != digest(config):
        raise ValueError("config differs from frozen initialization")
    for path, sha in {**config["runtime_files"], **config["worker_files"]}.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != sha:
            raise ValueError("worker file changed: " + path)
    horizon = life0 / "RESEARCH_HORIZON_0.md"
    sha = hashlib.sha256(horizon.read_bytes()).hexdigest() if horizon.exists() else None
    if sha != config["horizon_sha256"]:
        raise ValueError("research horizon changed since initialization")
    return config


def veto(life0, need_id=None):
    # HOLD and STOP remain dominant for this loop lifetime; no resume shortcut.
    for row in records(life0 / CONTROLS):
        if row.get("directive") in ("HOLD", "STOP") and row.get("need_id") in (None, need_id):
            return row
    return None


def frontier(life0):
    projection = Projector(str(life0)).project()
    history = verified_history(life0)
    owned = {r["need_id"]: r for r in history
             if r.get("event") in ("WORKER_INTENT", "LOCAL_RESULT")}
    dispatches = dispatch.fold_dispatch(life0)
    queue = resource_queue.fold_queue(life0)
    ready, blocked, done = [], [], []
    for nid, obligation in projection["obligations"].items():
        state = obligation["state"]
        if state in ("SATISFIED", "RETIRED"):
            done.append({"need_id": nid, "state": state})
            continue
        reason = None
        need = eligibility.find_need(life0, nid) or {}
        commitment = eligibility.find_commitment(life0, need.get("commitment_id")) or {}
        key = dispatch.key_for(need.get("commitment_id"), nid)
        prior = dispatches.get(key) or {}
        linked_jobs = [r for r in queue.values() if r.get("need_id") == nid or r.get("job_id") == nid]
        if state != "OPEN":
            reason = "OBLIGATION_" + state
        elif veto(life0, nid):
            reason = "OPERATOR_VETO"
        elif nid in owned:
            reason = owned[nid].get("status", "EXECUTION_UNCERTAIN")
        elif prior.get("event") in ("DISPATCHED", "DISPATCH_BLOCKED"):
            reason = "EXISTING_DISPATCH_OWNED_ELSEWHERE"
        elif linked_jobs or need.get("required_resource") or commitment.get("required_resource"):
            reason = "RESOURCE_QUEUE_PATH_REQUIRED"
        else:
            eligible = eligibility.check(life0, nid)
            if not eligible.get("eligible"):
                reason = eligible.get("blocking_condition", "NOT_AUTHORIZED")
            elif eligible["authority_class"] not in dispatch.SUPPORTED_AUTHORITY_CLASSES:
                reason = "UNSUPPORTED_AUTHORITY_CLASS"
        item = {"need_id": nid, "commitment_id": need.get("commitment_id"),
                "priority": need.get("priority", ""), "created_at": need.get("created_at", "")}
        if reason:
            blocked.append({**item, "reason": reason})
        else:
            ready.append(item)
    rank = {p: i for i, p in enumerate(PRIORITIES)}
    ready.sort(key=lambda r: (rank.get(r["priority"], 99), r["created_at"], r["need_id"]))
    return {"mission": list(STANDING_OBLIGATIONS), "ready": ready, "blocked": blocked,
            "done": done, "resource_queue": list(queue.values()),
            "agenda_gate": propose_agenda.gate(life0)}


def refresh(life0):
    result = frontier(life0)
    prior = [r for r in verified_history(life0) if r["event"] == "FRONTIER_REFRESHED"]
    if not prior or digest(prior[-1]["frontier"]) != digest(result):
        event(life0, "FRONTIER_REFRESHED", frontier=result)
    return result


def terminate(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def execute(life0, config, selected, remaining_seconds):
    nid = selected["need_id"]
    if veto(life0, nid):
        return {"status": "BLOCKED", "reason": "OPERATOR_VETO"}
    # Existing dispatcher owns admission binding and AUTO-WORK-0 gate.
    launched = dispatch.dispatch(life0, nid)
    if launched.get("verdict") != "DISPATCHED":
        return {"status": "BLOCKED", "reason": "DISPATCH_REFUSED", "dispatch": launched}
    binding_path = Path(launched["chain_binding"])
    workspace = life0 / "dispatch" / "research_loop" / nid
    workspace.mkdir(parents=True, exist_ok=True)
    binding = json.loads(binding_path.read_text())
    request = {"version": VERSION, "life0": str(life0.resolve()), "need_id": nid,
               "binding_path": str(binding_path.resolve()), "binding_hash": digest(binding),
               "workspace": str(workspace.resolve()), "report_path": str((workspace / "result.json").resolve()),
               "api_budget_usd": 0, "runbook": str((ROOT / "CHAIN_RUNBOOK_0.md").resolve())}
    request_path = workspace / "REQUEST.json"
    request_path.write_text(json.dumps(request, indent=2) + "\n")
    timeout = min(config["timeout_seconds"], remaining_seconds)
    # Reserve full timeout durably. A crashed launch consumes its reservation.
    load_config(life0)
    if veto(life0, nid) or not eligibility.check(life0, nid).get("eligible"):
        return {"status": "BLOCKED", "reason": "REVOKED_BEFORE_WORKER"}
    event(life0, "WORKER_INTENT", need_id=nid, request_hash=digest(request),
          binding_hash=digest(binding), reserved_seconds=timeout)
    # Check again after the durable boundary, immediately before Popen.
    if veto(life0, nid) or not eligibility.check(life0, nid).get("eligible"):
        return {"status": "BLOCKED", "reason": "REVOKED_BEFORE_WORKER"}
    started = time.monotonic()
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
    env.update({"SOVEREIGN_APIS_DISABLED": "1", "PYTHONUNBUFFERED": "1"})
    with (workspace / "stdout.log").open("wb") as stdout, (workspace / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen([*config["worker_argv"], str(request_path.resolve())],
                                   cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            reason = None
            while process.poll() is None:
                if veto(life0, nid):
                    reason = "OPERATOR_VETO"
                    break
                if not eligibility.check(life0, nid).get("eligible"):
                    # A worker may have just published a valid terminal record.
                    state = Projector(str(life0)).project()["obligations"][nid]["state"]
                    if state not in ("SATISFIED", "RETIRED"):
                        reason = "AUTHORIZATION_REVOKED"
                        break
                if time.monotonic() - started >= timeout:
                    reason = "WORKER_TIMEOUT"
                    break
                time.sleep(0.1)
            if reason:
                terminate(process)
                return {"status": "BLOCKED" if reason != "WORKER_TIMEOUT" else "FAIL",
                        "reason": reason, "returncode": process.returncode}
        finally:
            terminate(process)
    report_path = workspace / "result.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    projection = Projector(str(life0)).project()["obligations"][nid]["state"]
    if process.returncode != 0:
        return {"status": "FAIL", "reason": "WORKER_EXIT", "returncode": process.returncode,
                "report": report, "obligation_state": projection}
    if report.get("status") == "PASS" and projection == "SATISFIED":
        status = "PASS"
    elif report.get("status") in ("FAIL", "BLOCKED"):
        status = report["status"]
    else:
        status = "BLOCKED"
    return {"status": status, "report": report, "obligation_state": projection,
            "reason": report.get("reason") or "AUTHORITATIVE_COMPLETION_REQUIRED",
            "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest() if report_path.exists() else None}


def run(life0, watch=False, poll_seconds=30):
    state = life0 / "state"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "research_loop.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "ALREADY_RUNNING"}
        while True:
            config = load_config(life0)
            history = verified_history(life0)
            intents = [r for r in history if r["event"] == "WORKER_INTENT"]
            resolved = {r["need_id"] for r in history if r["event"] == "LOCAL_RESULT"}
            if any(r["need_id"] not in resolved for r in intents):
                return stop(life0, "EXECUTION_UNCERTAIN", refresh(life0))
            if veto(life0):
                return stop(life0, "OPERATOR_VETO", refresh(life0))
            reserved = sum(r["reserved_seconds"] for r in intents)
            if len(intents) >= config["max_launches"] or reserved >= config["max_seconds"]:
                return stop(life0, "BUDGET_EXHAUSTED", refresh(life0))
            current = refresh(life0)
            if not current["ready"]:
                packet = None
                if current["agenda_gate"].get("proceed"):
                    # Drafting packet only. No auto-acceptance, no invented job.
                    packet_path = life0 / "proposals/packets" / (current["agenda_gate"]["condition_set_hash"] + ".json")
                    packet = {"prepared": True, "packet_path": str(packet_path)} if packet_path.exists() else propose_agenda.prepare(life0)
                if not watch:
                    return stop(life0, "NO_EXECUTABLE_FRONTIER", current, agenda_packet=packet)
                previous_waits = [r for r in verified_history(life0) if r["event"] == "WAITING_FRONTIER"]
                if not previous_waits or previous_waits[-1].get("frontier_hash") != digest(current):
                    event(life0, "WAITING_FRONTIER", agenda_packet=packet, frontier_hash=digest(current))
                time.sleep(poll_seconds)
                continue
            selected = current["ready"][0]
            try:
                outcome = execute(life0, config, selected, config["max_seconds"] - reserved)
            except Exception as exc:
                # Do not disguise an exception after Popen as safe-to-retry.
                pending = [r for r in verified_history(life0) if r["event"] == "WORKER_INTENT" and r["need_id"] == selected["need_id"]]
                if pending:
                    event(life0, "EXECUTION_ERROR", need_id=selected["need_id"], error=str(exc))
                    return stop(life0, "EXECUTION_UNCERTAIN", refresh(life0))
                outcome = {"status": "BLOCKED", "reason": str(exc)}
            event(life0, "LOCAL_RESULT", need_id=selected["need_id"], **outcome)
            # Every PASS/FAIL/BLOCKED flows back to projection and selection.


def reconcile(life0, need_id, worker_stopped, reason):
    """Operator resolves an uncertain launch without retry or budget refund.

    No process liveness can be inferred from a missing report. The operator
    must establish that the old worker has stopped before using this command.
    """
    if not worker_stopped or not reason:
        raise ValueError("operator must confirm worker stopped and provide evidence/reason")
    with (life0 / "state/research_loop.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        history = verified_history(life0)
        intents = [r for r in history if r["event"] == "WORKER_INTENT" and r["need_id"] == need_id]
        results = [r for r in history if r["event"] == "LOCAL_RESULT" and r["need_id"] == need_id]
        if not intents or results:
            raise ValueError("no unresolved worker intent for need")
        state = Projector(str(life0)).project()["obligations"].get(need_id, {}).get("state")
        return event(life0, "LOCAL_RESULT", need_id=need_id,
                     status="PASS" if state == "SATISFIED" else "BLOCKED",
                     reason=reason, obligation_state=state,
                     reconciled_by="operator", worker_stopped=True)


def stop(life0, reason, current, **fields):
    result = {"status": reason, "frontier": current, **fields}
    event(life0, "LOOP_YIELDED", **result)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--life0", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init")
    init.add_argument("--worker-argv", required=True, help="JSON argv; REQUEST.json appended")
    init.add_argument("--max-launches", type=int, required=True)
    init.add_argument("--max-seconds", type=float, required=True)
    init.add_argument("--timeout-seconds", type=float, required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--watch", action="store_true")
    runner.add_argument("--poll-seconds", type=float, default=30)
    sub.add_parser("frontier")
    control = sub.add_parser("control")
    control.add_argument("directive", choices=("HOLD", "STOP"))
    control.add_argument("--need")
    control.add_argument("--reason", required=True)
    recovery = sub.add_parser("reconcile", help="resolve uncertain worker, never relaunch")
    recovery.add_argument("--need", required=True)
    recovery.add_argument("--worker-stopped", action="store_true", required=True)
    recovery.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    life0 = Path(args.life0).resolve()
    try:
        if args.cmd == "init":
            result = initialize(life0, json.loads(args.worker_argv), args.max_launches, args.max_seconds, args.timeout_seconds)
        elif args.cmd == "control":
            result = {"directive": args.directive, "need_id": args.need,
                      "reason": args.reason, "by": "operator", "at": now()}
            append(life0 / CONTROLS, result)
        elif args.cmd == "frontier":
            result = frontier(life0)
        elif args.cmd == "reconcile":
            result = reconcile(life0, args.need, args.worker_stopped, args.reason)
        else:
            if not math.isfinite(args.poll_seconds) or not 0.1 <= args.poll_seconds <= 60:
                raise ValueError("poll_seconds must be between 0.1 and 60")
            result = run(life0, args.watch, args.poll_seconds)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "INTEGRITY_OR_CONFIGURATION_BOUNDARY", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
