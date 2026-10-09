"""Effective-history view + cell packet construction (W2).

H_i^effective = View(L, i, R, E): built before each worker invocation from
owned records plus authorized projections. Never contains another cell's
private events. Organism-global residue is suppressed (Decision 9).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import registry


def effective_history(life0_dir: str, cell_id: str,
                      run_events: Optional[List[Dict[str, Any]]] = None,
                      exchange_records: Optional[List[Dict[str, Any]]] = None,
                      ) -> List[Dict[str, Any]]:
    """Assemble the cell's effective history.

    - Owned records: registry events with owner.kind == "cell", id == cell_id.
    - Run events: per-run ledger events owned by this cell (passed in).
    - Authorized projections: permitted exchange records where this cell is the
      receiver - reference (artifact hash) only, never the other cell's events.
    """
    hist: List[Dict[str, Any]] = []
    for r in registry.read_records(life0_dir):
        if r.get("owner", {}).get("kind") == "cell" \
                and r.get("owner", {}).get("id") == cell_id:
            hist.append(r)
    for e in run_events or []:
        if e.get("owner", {}).get("kind") == "cell" \
                and e.get("owner", {}).get("id") == cell_id:
            hist.append(e)
    for x in exchange_records or []:
        if x.get("decision") == "permitted" \
                and x.get("received_by") == cell_id:
            # Authorized projection: the reference only.
            hist.append({
                "type": "exchange_reference",
                "owner": {"kind": "relationship",
                          "id": x.get("relationship_id")},
                "exchange_id": x.get("exchange_id"),
                "relationship_id": x.get("relationship_id"),
                "artifact_hash": x.get("artifact_hash"),
            })
    return hist


def build_packet(cell_id: str, procedure_ref: str, workspace_root: str,
                 step: int, budget: int,
                 history: List[Dict[str, Any]],
                 shared_evidence: Optional[List[str]] = None,
                 objective: str = "") -> Dict[str, Any]:
    """The single point where a cell worker's decision context is assembled.

    Explicitly absent: other cells' events, undisclosed relationship internals,
    organism-global residue, unrestricted execution capability.
    """
    return {
        "cell": {
            "cell_id": cell_id,
            "procedure_ref": procedure_ref,
            "objective": objective,
            "step": step,
            "budget": budget,
            "remaining": budget - step + 1,
        },
        "history": history[-8:],
        "shared_evidence": list(shared_evidence or []),
        "workspace_rules": {
            "root": workspace_root,
            "tools": ["read_file", "write_file", "fetch_evidence", "stop"],
            "note": ("read_file/write_file are confined to the cell workspace; "
                     "fetch_evidence returns only authorized artifacts; "
                     "no run/search/apply_patch/git_diff/status."),
        },
    }
