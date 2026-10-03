"""LIFE-0 dispatch stager: stage mission packages, never launch them.

AUTHORIZATION BOUNDARY (read this before touching this file):

The pulse NEVER auto-launches, NEVER invokes a worker, NEVER spends. The
staged mission package deliberately contains NO worker command, NO executable
plan, and an explicit launch_authorized: false marker. Turning a staged
package into a running mission — filling in the worker command, spending
budget, invoking the worker — is an explicit operator act, performed outside
this package, under the standing rule that no mission launches without
Stephan's explicit authorization.

This module imports nothing that can execute a subprocess and performs no
network I/O. Tests pin that boundary structurally.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .delta import canonical, utcnow_iso
from .needs import STANDING_OBLIGATIONS

PACKAGE_SCHEMA = "life0.mission_package"
PACKAGE_VERSION = 1

STATUS_STAGED = "STAGED_AWAITING_AUTHORIZATION"


def stage_mission(need: dict[str, Any], gate_record: dict[str, Any],
                  dispatch_dir: str | Path) -> Path:
    """Write the staged mission package for a gated need. Returns the package
    directory. Raises if the gate did not approve staging."""
    if gate_record.get("decision") != "STAGE":
        raise ValueError("cannot stage a need the gate did not approve")
    if not need.get("delta_id"):
        raise ValueError("cannot stage a need without an observed delta")

    need_id = need["need_id"]
    pkg_dir = Path(dispatch_dir) / need_id
    if pkg_dir.exists():
        # Already staged: staging is idempotent, never duplicated.
        return pkg_dir
    pkg_dir.mkdir(parents=True)

    mission_id = f"life0-{need_id}"
    objective = _objective_text(need, mission_id)

    package = {
        "schema": PACKAGE_SCHEMA,
        "schema_version": PACKAGE_VERSION,
        # NOTE: no worker_command, no executable plan. The worker command is
        # supplied by the operator at explicit authorization time, never here.
        "worker_command": None,
        "launch_authorized": False,
        "status": STATUS_STAGED,
        "mission_id": mission_id,
        "need_id": need_id,
        "delta_id": need["delta_id"],
        "priority": need["priority"],
        "obligation": need["obligation"],
        "objective_path": "OBJECTIVE.md",
        "budget_suggested": 16,
        "acceptance": [],
        "staged_at": utcnow_iso(),
        "gate": gate_record,
        # --- earned-autonomy record (fields for future measurement; NOT an
        # analysis). The future path — repeated approval -> candidate standing
        # authority -> falsification -> bounded autonomous dispatch — must be
        # readable from these records. The operator (or an operator-actuated
        # tool) updates operator_decision when the decision is made; the
        # consequence is filled from the consequence record once known.
        "operator_decision": {
            "decision": "pending",      # pending | approved | rejected
            "decided_by": "pulse",      # pulse | operator
            "reason": "staged; launch requires explicit operator act",
            "at": None,                # set when the operator decides
        },
        "consequence": None,           # filled from state/consequences.jsonl once known
    }
    (pkg_dir / "MISSION_PACKAGE.json").write_text(
        json.dumps(package, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (pkg_dir / "OBJECTIVE.md").write_text(objective, encoding="utf-8")
    (pkg_dir / "STAGE_RECORD.json").write_text(
        json.dumps({
            "need_id": need_id,
            "mission_id": mission_id,
            "gate": gate_record,
            "staged_at": package["staged_at"],
            "note": ("Staged by the LIFE-0 pulse. Launch requires an explicit "
                     "operator act; this package cannot launch itself."),
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return pkg_dir


def _objective_text(need: dict[str, Any], mission_id: str) -> str:
    obligations = "\n".join(STANDING_OBLIGATIONS)
    return f"""# {mission_id} — STAGED, NOT AUTHORIZED

**Status: STAGED_AWAITING_AUTHORIZATION. This mission has not been launched.
Launching it — supplying a worker command, spending budget, invoking a
worker — requires an explicit operator act. Nothing in this package can
execute itself.**

## Need

{need['possible_need']}

- Priority: {need['priority']}
- Triggering delta: {need['delta_id']} (source: {need['source']}, kind: {need['delta_kind']})
- Evidence: previous_state and current_state recorded in the need's delta;
  see the pulse observation log.

## Standing obligations (operator-supplied constraints, not self-authored goals)

{obligations}

## Boundary

The worker, when eventually invoked by the operator, operates under the
frozen LIVE-0 substrate rules: replaceable worker, organism identity
independent of the worker (Proof 2), no authority granted by tasking.
"""
