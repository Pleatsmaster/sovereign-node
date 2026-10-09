"""Cell act loop: drives a worker under the restricted cell contract.

Boundary order per action (binding): parse (Action.from_obj) -> authorize
(CellPolicy.validate) -> execute. Nothing executes before validation.
The worker shim's contract text is advisory; this is the enforcement.

Startup is fail-closed on module identity (cells.policy.verify_um_root):
if unified_machine did not load from the pinned baseline, nothing runs.

Commit phase: a cell's consequential result (artifact publication) requires
PermitCommit re-checked with FRESH halt state immediately before the write.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Dict, List, Optional

from . import evidence, exchange, registry, view
from .policy import CELL_ACTIONS, get_policy


def _load_um():
    from .policy import verify_um_root
    return verify_um_root()


def worker_call_subprocess(argv: List[str], packet: Dict[str, Any],
                           timeout: int,
                           env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    payload = {"mode": "act", "packet": packet}
    cp = subprocess.run(argv, input=json.dumps(payload), text=True,
                        capture_output=True, timeout=timeout, env=env)
    if cp.returncode != 0:
        raise RuntimeError(f"worker exited {cp.returncode}: "
                           f"{cp.stderr[:500]}")
    return json.loads(cp.stdout)


def _execute(um, cell_id: str, workspace, action, life0_dir: str,
             exchange_records: List[Dict[str, Any]]):
    """Execute an already-validated action. Never called pre-validation."""
    if action.kind == "read_file":
        return workspace.read_file(action.args["path"],
                                   action.args.get("start_line", 1),
                                   action.args.get("end_line"))
    if action.kind == "write_file":
        return workspace.write_file(action.args["path"],
                                    action.args.get("content", ""))
    if action.kind == "fetch_evidence":
        data = evidence.fetch_evidence(life0_dir, cell_id,
                                       action.args["hash"],
                                       exchange_records)
        ToolResult = um.types.ToolResult
        return ToolResult(True, "evidence retrieved",
                          {"bytes": len(data),
                           "text": data.decode("utf-8", errors="replace")})
    if action.kind == "stop":
        ToolResult = um.types.ToolResult
        return ToolResult(True, "worker stopped",
                          {"status": action.args.get("status", "complete")})
    raise RuntimeError(f"validated action has no executor: {action.kind}")


def run_cell_turn(life0_dir: str, cell_id: str, worker,
                  objective: str, budget: int, obligation_id: str,
                  run_dir: str,
                  exchange_records: Optional[List[Dict[str, Any]]] = None,
                  relationship: Optional[Dict[str, Any]] = None,
                  shared_evidence: Optional[List[str]] = None,
                  timeout: int = 120,
                  env: Optional[Dict[str, str]] = None,
                  pre_write_hook: Optional[Callable[[], None]] = None,
                  ) -> Dict[str, Any]:
    """Run one bounded cell turn. Returns the turn record.

    worker: either argv list (subprocess, JSON over stdin) or a callable
    taking the packet dict and returning an action dict.
    """
    um = _load_um()
    policy, PolicyError = get_policy()
    cell = registry.get_cell(life0_dir, cell_id)
    if cell is None:
        raise ValueError(f"unknown cell: {cell_id}")
    halt = exchange.read_halt_state(life0_dir, obligation_id)
    if halt["stopped"] or halt["revoked"]:
        return {"cell_id": cell_id, "started": False,
                "reason": "halted before start", "halt": halt}

    os.makedirs(run_dir, exist_ok=True)
    ws_root = cell["workspace_root"]
    os.makedirs(ws_root, exist_ok=True)
    workspace = um.workspace.Workspace(ws_root)
    ledger = um.ledger.Ledger(run_dir)
    exchange_records = exchange_records or []
    history: List[Dict[str, Any]] = []
    turn = {"cell_id": cell_id, "steps": [], "committed": [],
            "refused": [], "telemetry": []}

    for step in range(1, budget + 1):
        packet = view.build_packet(
            cell_id, cell["procedure_ref"], ws_root, step, budget,
            history, shared_evidence=shared_evidence, objective=objective)
        try:
            if callable(worker):
                raw = worker(packet)
            else:
                raw = worker_call_subprocess(worker, packet, timeout, env)
            action = um.types.Action.from_obj(raw)
            policy.validate(action)  # <-- the enforcement boundary
        except Exception as exc:  # noqa: BLE001 - malformed/rejected acts
            rec = {"step": step, "rejected": f"{type(exc).__name__}: {exc}"}
            history.append(rec)
            turn["steps"].append(rec)
            ledger.append("cell_act_rejected",
                          {"owner": {"kind": "cell", "id": cell_id}, **rec})
            continue
        try:
            result = _execute(um, cell_id, workspace, action, life0_dir,
                              exchange_records)
        except Exception as exc:  # noqa: BLE001 - tool-boundary refusal
            rec = {"step": step, "action": action.to_obj(),
                   "result": {"ok": False,
                              "message": f"tool refused: {type(exc).__name__}"}}
            history.append(rec)
            turn["steps"].append(rec)
            ledger.append("cell_act_failed",
                          {"owner": {"kind": "cell", "id": cell_id}, **rec})
            continue
        rec = {"step": step, "action": action.to_obj(),
               "result": result.to_obj()}
        history.append(rec)
        turn["steps"].append(rec)
        ledger.append("cell_act",
                      {"owner": {"kind": "cell", "id": cell_id}, **rec})
        if action.kind == "stop":
            break

    # Commit phase: consequential results go through the race-checked
    # commit (GO-2 step 2). Each result is fingerprinted, freshly
    # authorized, published, then re-validated; a concurrent revocation
    # voids the commit instead of producing an unauthorized result.
    pending = [s for s in turn["steps"]
               if s.get("action", {}).get("kind") == "write_file"
               and s.get("result", {}).get("ok")]
    turn["voided"] = []
    for s in pending:
        name = s["action"]["args"]["path"]
        with open(os.path.join(ws_root, name), "rb") as f:
            data = f.read()
        outcome = exchange.commit_result(
            life0_dir, cell_id, obligation_id,
            (relationship or {}).get("relationship_id"),
            name, data, ledger.append,
            pre_write_hook=pre_write_hook)
        turn["telemetry"].append(outcome["telemetry"])
        turn[{"committed": "committed", "refused": "refused",
              "voided": "voided"}[outcome["status"]]].append(outcome)
    turn["halt_at_end"] = exchange.read_halt_state(life0_dir, obligation_id)
    return turn
