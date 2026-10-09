"""Cell and relationship registry (W1).

Append-only JSONL registry at <life0>/state/cell_records.jsonl.
New file: zero existing consumers, zero hash bindings, zero writer asserts
(verified read-only 2026-10-09). Every record carries exactly one owner:
{"kind": "cell|relationship|organism", "id": ...}.

Relationship records live here under owner.kind="relationship" (Decision 4:
same ledger family, distinguished by owner kind - not another physical ledger).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional

REGISTRY_FILENAME = "cell_records.jsonl"


def registry_path(life0_dir: str) -> str:
    return os.path.join(life0_dir, "state", REGISTRY_FILENAME)


def _canonical(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _append(life0_dir: str, record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record)
    record["ts"] = time.time()
    record["record_hash"] = hashlib.sha256(
        _canonical(record).encode("utf-8")).hexdigest()
    path = registry_path(life0_dir)
    with open(path, "a", encoding="utf-8") as f:
        f.write(_canonical(record) + "\n")
    return record


def register_cell(life0_dir: str, cell_id: str, procedure_ref: str,
                  workspace_root: str) -> Dict[str, Any]:
    """Write-once cell identity record. Never mutated; status changes append."""
    if not cell_id or not isinstance(cell_id, str):
        raise ValueError("cell_id must be a non-empty string")
    return _append(life0_dir, {
        "type": "cell_registered",
        "owner": {"kind": "cell", "id": cell_id},
        "cell_id": cell_id,
        "procedure_ref": procedure_ref,
        "workspace_root": workspace_root,
        "status": "active",
    })


def record_relationship(life0_dir: str, relationship_id: str, source: str,
                        target: str, trigger: str = "evidence_available",
                        action: str = "permit_reference",
                        revision: int = 0,
                        parent_hash: Optional[str] = None) -> Dict[str, Any]:
    """Fixed relationship record. Belongs to neither endpoint cell."""
    return _append(life0_dir, {
        "type": "relationship_recorded",
        "owner": {"kind": "relationship", "id": relationship_id},
        "relationship_id": relationship_id,
        "source": source,
        "target": target,
        "trigger": trigger,
        "action": action,
        "revision": revision,
        "parent_hash": parent_hash,
        "status": "active",
    })


def read_records(life0_dir: str,
                 type_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    path = registry_path(life0_dir)
    out: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if type_filter and r.get("type") != type_filter:
                continue
            out.append(r)
    return out


def get_cell(life0_dir: str, cell_id: str) -> Optional[Dict[str, Any]]:
    for r in read_records(life0_dir, "cell_registered"):
        if r.get("cell_id") == cell_id:
            return r
    return None


def get_relationship(life0_dir: str,
                     relationship_id: str) -> Optional[Dict[str, Any]]:
    for r in read_records(life0_dir, "relationship_recorded"):
        if r.get("relationship_id") == relationship_id:
            return r
    return None


def set_relationship_status(life0_dir: str, relationship_id: str,
                            status: str) -> Dict[str, Any]:
    """Status change by append-only record. Revocation never mutates."""
    if status not in ("active", "revoked"):
        raise ValueError("status must be active|revoked")
    return _append(life0_dir, {
        "type": "relationship_status",
        "owner": {"kind": "relationship", "id": relationship_id},
        "relationship_id": relationship_id,
        "status": status,
    })


def relationship_is_active(life0_dir: str, relationship_id: str) -> bool:
    """Latest status wins; the original record defaults to active."""
    active = None
    for r in read_records(life0_dir):
        if r.get("relationship_id") != relationship_id:
            continue
        if r.get("type") == "relationship_recorded" and active is None:
            active = (r.get("status") == "active")
        elif r.get("type") == "relationship_status":
            active = (r.get("status") == "active")
    return bool(active)
