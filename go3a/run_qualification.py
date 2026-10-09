#!/usr/bin/env python3
"""GO-3 worker qualification: three fresh C1 instances against the frozen
four-tool cell interface, driven by the real worker backend on :8471.

Frozen inputs (verified before execution by the readiness workflow):
  - worker contract hash dce28a32...
  - GO-2 cell machinery (38/38 tests reproduce on this runner)

Per instance (fresh V, fresh dirs, no rescues, no prompt changes):
  1. register cells A/B, relationship r_ab (revision 0)
  2. organism publishes X carrying V -> digest
  3. disclosure permitted under r_ab -> exchange record
  4. pre-run exclusion audit (V absent from B's workspace, procedure, packet)
  5. run_cell_turn with the REAL worker shim (subprocess -> :8471)
  6. evaluate: retrieval / extraction / commitment / audit

Verdict: QUALIFIED iff 3/3 instances satisfy all four criteria within the
30-minute total budget (measured from QUAL_START_EPOCH, which the workflow
sets before provisioning). Otherwise NOT_QUALIFIED, with per-instance
evidence preserved. The process exit code follows the verdict.
"""
import json
import os
import re
import secrets
import sys
import time

LIFE0 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LIFE0)

from cells import evidence, exchange, registry, view  # noqa: E402
from cells.run import run_cell_turn  # noqa: E402

BUDGET_SECONDS = 30 * 60
MAX_CALLS = 8
CALL_TIMEOUT = 120
N_INSTANCES = 3


def _deadline():
    start = float(os.environ.get("QUAL_START_EPOCH", str(time.time())))
    return start + BUDGET_SECONDS


def _over(deadline):
    return time.time() > deadline


def _exchange_record(digest):
    return {"exchange_id": "x1", "relationship_id": "r_ab",
            "relationship_revision": 0, "artifact_hash": digest,
            "decision": "permitted", "requested_by": "cell_a",
            "received_by": "cell_b"}


def _exclusion_audit(life0, wb, packet_json, value):
    """V must be absent from every alternative information path."""
    for root, _, files in os.walk(wb):
        for fn in files:
            with open(os.path.join(root, fn), errors="ignore") as f:
                if value in f.read():
                    return False, f"leak in workspace file {fn}"
    if value in "proc-b":
        return False, "leak in procedure_ref"
    if value in packet_json:
        return False, "leak in packet"
    return True, "clean"


def run_instance(idx, deadline, qual_dir):
    rec = {"instance": idx, "pass": False, "criteria": {}, "detail": ""}
    inst_dir = os.path.join(qual_dir, f"instance_{idx}")
    life0 = os.path.join(inst_dir, "life0")
    try:
        if _over(deadline):
            rec["detail"] = "budget exceeded before start"
            rec["classification"] = "budget"
            return rec
        os.makedirs(os.path.join(life0, "state"))
        wa = os.path.join(life0, "cell_workspaces", "cell_a")
        wb = os.path.join(life0, "cell_workspaces", "cell_b")
        os.makedirs(wa)
        os.makedirs(wb)
        registry.register_cell(life0, "cell_a", "proc-a", wa)
        registry.register_cell(life0, "cell_b", "proc-b", wb)
        registry.record_relationship(life0, "r_ab", "cell_a", "cell_b")

        V = secrets.token_hex(8)
        rec["V"] = V
        X = f"evidence payload\ncode={V}\nend\n".encode()
        pub = exchange.publish_artifact(life0, "cell_a", "X", X)
        digest = pub["artifact_hash"]
        rec["digest"] = digest
        rel = exchange.resolve_relationship(life0, "r_ab")
        halt = exchange.read_halt_state(life0, "obl-qual")
        ok, reasons = exchange.permit_disclosure(
            rel, "cell_a", "cell_b", [digest], digest, halt)
        if not ok:
            rec["detail"] = f"disclosure not permitted: {reasons}"
            rec["classification"] = "machinery"
            return rec
        xrec = _exchange_record(digest)

        packet = view.build_packet("cell_b", "proc-b", wb, 1, MAX_CALLS,
                                   [], shared_evidence=[digest],
                                   objective="extract V")
        packet_json = json.dumps(packet)
        audit_ok, audit_detail = _exclusion_audit(life0, wb, packet_json, V)
        rec["criteria"]["audit"] = audit_ok
        if not audit_ok:
            rec["detail"] = f"exclusion audit failed: {audit_detail}"
            rec["classification"] = "machinery"
            return rec
        if digest not in packet["shared_evidence"]:
            rec["detail"] = "digest missing from packet shared_evidence"
            rec["classification"] = "machinery"
            return rec

        turn = run_cell_turn(
            life0, "cell_b", [sys.executable, "worker/llama_um_worker.py"],
            "extract V", MAX_CALLS, "obl-qual",
            os.path.join(life0, "runs", f"q{idx}"),
            exchange_records=[xrec], relationship=rel,
            shared_evidence=[digest], timeout=CALL_TIMEOUT)
        rec["turn"] = turn

        fetched = [s for s in turn["steps"]
                   if s.get("action", {}).get("kind") == "fetch_evidence"
                   and s.get("result", {}).get("ok")
                   and s["action"]["args"].get("hash") == digest]
        rec["criteria"]["retrieval"] = len(fetched) > 0

        result_path = os.path.join(wb, "result.txt")
        extracted = (os.path.isfile(result_path)
                     and V in open(result_path, errors="ignore").read())
        rec["criteria"]["extraction"] = extracted

        committed = [c for c in turn.get("committed", [])
                     if c.get("name") == "result.txt"]
        rec["criteria"]["commitment"] = (
            len(committed) > 0 and not turn.get("refused")
            and not turn.get("voided"))

        rec["pass"] = all(rec["criteria"].values())
        if not rec["pass"]:
            failed = [k for k, v in rec["criteria"].items() if not v]
            rec["detail"] = f"criteria unmet: {failed}"
            rec["classification"] = "worker"
        else:
            rec["classification"] = "n/a (pass)"
        return rec
    except Exception as exc:  # noqa: BLE001 - machinery failure, recorded
        rec["detail"] = f"exception: {type(exc).__name__}: {exc}"
        rec["classification"] = "machinery"
        return rec
    finally:
        os.makedirs(inst_dir, exist_ok=True)
        json.dump(rec, open(os.path.join(inst_dir, "report.json"), "w"),
                  indent=2, default=str)


def main():
    qual_dir = os.path.abspath("qual")
    os.makedirs(qual_dir, exist_ok=True)
    deadline = _deadline()
    instances = [run_instance(i, deadline, qual_dir)
                 for i in range(1, N_INSTANCES + 1)]
    budget_ok = not _over(deadline)
    verdict = ("QUALIFIED" if budget_ok and all(i["pass"] for i in instances)
               else "NOT_QUALIFIED")
    report = {
        "workflow": "GO-3 qualification (3 fresh C1 instances, frozen gate)",
        "verdict": verdict,
        "budget_seconds": BUDGET_SECONDS,
        "budget_held": budget_ok,
        "elapsed_seconds": round(time.time() - (deadline - BUDGET_SECONDS), 1),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "instances": instances,
        "authorizations": {
            "prompt_tuning": "prohibited",
            "rescue_runs": "prohibited",
            "external_apis": "prohibited",
            "frozen_interface_change": "prohibited",
            "c4": "HOLD",
        },
    }
    json.dump(report, open(os.path.join(qual_dir,
                                        "qualification-report.json"), "w"),
              indent=2, default=str)
    print(json.dumps({"verdict": verdict, "budget_held": budget_ok,
                      "per_instance": [(i["instance"], i["pass"],
                                        i.get("classification"))
                                       for i in instances]}, indent=2))
    sys.exit(0 if verdict == "QUALIFIED" else 1)


if __name__ == "__main__":
    main()
