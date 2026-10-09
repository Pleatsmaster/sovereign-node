"""Cell dispatch branch of the SOVEREIGN execution path (GO-2 step 1).

Called from chain_adapter.main() AFTER the full REQUEST.json and binding
validation, when the request carries cell_id. This is the attach seam:
admission -> dispatch -> binding validation (all unchanged, in the adapter)
-> cell turn (here).

Fail-closed dispatch integrity:
- cell_id must be registered; unknown cells are refused.
- REQUEST.json's workspace must equal the registry's workspace_root for the
  cell. A dispatch naming any other workspace for a registered cell is
  refused: the registry, not the request, is authoritative for cell identity.
- Pre-start halt check mirrors the chain path: revoked/stopped obligations
  launch zero worker executions.

This module never touches admission, dispatch authorization, STOP semantics,
or revocation rules; it only reads them. No CHAIN_LEDGER.jsonl writes: the
cell turn's record lives in its run ledger (sqlite) and the report file.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

from . import exchange, registry
from .run import run_cell_turn


class CellDispatchError(Exception):
    pass


def _write_report(report_path: str, payload: Dict[str, Any]) -> None:
    tmp = report_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.rename(tmp, report_path)


def run_cell_dispatch(host_cfg: Dict[str, Any], req: Dict[str, Any],
                      env: Dict[str, str]) -> int:
    """Execute one admitted cell turn. Returns process exit code."""
    life0 = req["life0_dir"]
    cell_id = req["cell_id"]
    cell = registry.get_cell(life0, cell_id)
    if cell is None:
        raise CellDispatchError(f"unknown cell_id: {cell_id}")

    ws_root = os.path.realpath(cell["workspace_root"])
    req_ws = os.path.realpath(req["workspace"])
    if ws_root != req_ws:
        raise CellDispatchError(
            f"cell workspace mismatch: request names {req_ws}, "
            f"registry records {ws_root}; refusing")

    halt = exchange.read_halt_state(life0, req["need_id"])
    base_report = {
        "kind": "cell_turn",
        "cell_id": cell_id,
        "need_id": req["need_id"],
        "chain_id": req["chain_id"],
        "commitment_id": req["commitment_id"],
        "binding_sha256": req["binding_sha256"],
        "at": time.time(),
    }
    if halt["stopped"] or halt["revoked"]:
        base_report.update({"launched": False, "reason": "halted before start",
                            "halt": halt})
        _write_report(req["report_path"], base_report)
        return 0

    run_dir = os.path.join(ws_root, "cell-runs", req["chain_id"])
    turn = run_cell_turn(
        life0_dir=life0,
        cell_id=cell_id,
        worker=req["worker_argv"],
        objective=req["objective"],
        budget=req["bounds"]["max_steps"],
        obligation_id=req["need_id"],
        run_dir=run_dir,
        exchange_records=[],
        relationship=None,
        shared_evidence=[],
        timeout=120,
    )
    base_report.update({"launched": True, "turn": turn, "halt_at_end": halt})
    _write_report(req["report_path"], base_report)
    return 0
