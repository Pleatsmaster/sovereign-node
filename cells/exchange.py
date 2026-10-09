"""Exchange authorization + commitment authorization + publication (W5-W7).

Two independent predicates (v0.3 section 6):
  PermitDisclosure(X,A,B) - authorizes evidence to move (five-invariant
      conjunction over the six v0.3 conditions).
  PermitCommit(B, result) - authorizes a consequence to land, re-checked
      immediately before the consequential write. A permitted disclosure never
      implies a permitted commitment: revocation cannot erase received
      information, but it can and must block subsequent unauthorized acts.

Halt sources (read-only; this module never writes authority state):
  - obligation_directives.jsonl BLOCK/RETIRE for the bound obligation id
    (existing SOVEREIGN revocation discipline, same file/semantics as the
    chain adapter's revoked()).
  - <life0>/state/global_stop sentinel file: operator-created, operator-owned;
    never written by this implementation. Checked read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import evidence
from . import telemetry


def resolve_relationship(life0_dir: str,
                         relationship_id: str) -> Optional[Dict[str, Any]]:
    """Effective relationship record with latest status applied.

    Callers must use this (not the raw recorded dict) so revocation by
    append-only status record is honored.
    """
    from . import registry
    rec = registry.get_relationship(life0_dir, relationship_id)
    if rec is None:
        return None
    rec = dict(rec)
    rec["status"] = ("active" if registry.relationship_is_active(
        life0_dir, relationship_id) else "revoked")
    return rec


def read_halt_state(life0_dir: str, obligation_id: str) -> Dict[str, Any]:
    """Current STOP/revocation state. Read-only."""
    stop_path = os.path.join(life0_dir, "state", "global_stop")
    stopped = os.path.exists(stop_path)
    revoked = False
    directives = os.path.join(life0_dir, "state", "obligation_directives.jsonl")
    if os.path.exists(directives):
        with open(directives, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("need_id") == obligation_id and "at" in e:
                    if (e.get("directive") or "").upper() in ("BLOCK", "RETIRE"):
                        revoked = True
                        break
    return {"stopped": stopped, "revoked": revoked}


def permit_disclosure(relationship: Dict[str, Any],
                      artifact_producer: str,
                      receiver: str,
                      obligation_scope: List[str],
                      artifact_hash: str,
                      halt: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """The six v0.3 conditions as the five-invariant conjunction."""
    reasons: List[str] = []
    ok = True

    def check(name: str, cond: bool):
        nonlocal ok
        reasons.append(f"{name}: {'hold' if cond else 'FAIL'}")
        ok = ok and cond

    check("I_identity: relationship active and admitted",
          relationship.get("status") == "active")
    check("I_history: revision matches recorded decision",
          relationship.get("revision") == 0)
    check("I_evidence: artifact published by source cell",
          artifact_producer == relationship.get("source"))
    check("I_capability: receiver is the relationship target",
          receiver == relationship.get("target"))
    check("I_evidence: artifact within admitted exchange obligation",
          artifact_hash in obligation_scope)
    check("I_temporal: no STOP or revocation intervened",
          not halt.get("stopped") and not halt.get("revoked"))
    return ok, reasons


def permit_commit(cell_id: str,
                  relationship: Dict[str, Any],
                  halt: Dict[str, Any],
                  result_within_authorized_acts: bool) -> Tuple[bool, List[str]]:
    """Re-checked immediately before the consequential write (binding)."""
    reasons: List[str] = []
    ok = True

    def check(name: str, cond: bool):
        nonlocal ok
        reasons.append(f"{name}: {'hold' if cond else 'FAIL'}")
        ok = ok and cond

    check("relationship still active and unrevoked",
          relationship.get("status") == "active")
    check("no STOP intervened since disclosure", not halt.get("stopped"))
    check("no revocation intervened since disclosure", not halt.get("revoked"))
    check("result within cell's authorized acts",
          result_within_authorized_acts)
    _ = cell_id  # attribution carried by the caller, not the predicate
    return ok, reasons


def halt_fingerprint(life0_dir: str, obligation_id: str) -> str:
    """Fingerprint of the halt sources.

    Changes if and only if revocation directives or the STOP sentinel may
    have changed. Used for optimistic concurrency around commitment: the
    existing revocation path is append-only and operator-owned, so no lock
    can be imposed on it without changing the authority model. Instead the
    commit detects a concurrent change and voids itself.
    """
    h = hashlib.sha256()
    dpath = os.path.join(life0_dir, "state", "obligation_directives.jsonl")
    if os.path.exists(dpath):
        with open(dpath, "rb") as f:
            h.update(f.read())
    else:
        h.update(b"no-directives")
    spath = os.path.join(life0_dir, "state", "global_stop")
    h.update(b"stop:1" if os.path.exists(spath) else b"stop:0")
    _ = obligation_id  # directives are per-obligation; file covers all
    return h.hexdigest()


def quarantine_artifact(life0_dir: str, digest: str) -> None:
    """Remove a raced artifact from the trusted shared store (forensics kept)."""
    src = os.path.join(evidence.shared_root(life0_dir), digest)
    qdir = os.path.join(life0_dir, "state", "shared_artifacts_quarantine")
    os.makedirs(qdir, exist_ok=True)
    dst = os.path.join(qdir, digest)
    if os.path.exists(src):
        shutil.move(src, dst)


def commit_result(life0_dir: str, cell_id: str, obligation_id: str,
                  relationship_id: Optional[str], name: str, data: bytes,
                  ledger_append: Callable[[str, Dict[str, Any]], Any],
                  pre_write_hook: Optional[Callable[[], None]] = None
                  ) -> Dict[str, Any]:
    """Commit one consequential result with race detection (GO-2 step 2).

    Sequence: fingerprint -> fresh authorization -> [test hook] ->
    consequential writes -> re-fingerprint. If the halt sources changed
    during the write window, the commit is VOIDED (artifact quarantined,
    void recorded): no unauthorized committed result survives a racing
    revocation. This is detection-and-void, not indivisible atomicity;
    the stronger claim remains withheld.
    """
    fp1 = halt_fingerprint(life0_dir, obligation_id)
    halt = read_halt_state(life0_dir, obligation_id)
    rel = (resolve_relationship(life0_dir, relationship_id)
           if relationship_id else {"status": "active"})
    t = telemetry.timed_attempt(
        lambda: permit_commit(cell_id, rel, halt, True),
        label="permit_commit")
    if not t["permitted"]:
        return {"status": "refused", "telemetry": t, "name": name,
                "reasons": t["reasons"]}
    if pre_write_hook is not None:
        pre_write_hook()
    artifact = publish_artifact(life0_dir, cell_id, name, data)
    ledger_append("cell_commit",
                  {"owner": {"kind": "cell", "id": cell_id}, **artifact})
    fp2 = halt_fingerprint(life0_dir, obligation_id)
    if fp2 != fp1:
        quarantine_artifact(life0_dir, artifact["artifact_hash"])
        ledger_append("cell_commit_voided",
                      {"owner": {"kind": "cell", "id": cell_id},
                       "artifact_hash": artifact["artifact_hash"],
                       "name": name,
                       "reason": "halt sources changed during commit window"})
        return {"status": "voided", "telemetry": t, "name": name,
                "artifact_hash": artifact["artifact_hash"]}
    return {"status": "committed", "telemetry": t, "name": name,
            "artifact": artifact}


def publish_artifact(life0_dir: str, producing_cell: str, name: str,
                     data: bytes) -> Dict[str, Any]:
    """Organism act: publish bytes to the shared store, return the record."""
    digest = evidence.publish(life0_dir, data)
    return {
        "type": "artifact_published",
        "owner": {"kind": "organism", "id": "-"},
        "artifact_hash": digest,
        "name": name,
        "produced_by": producing_cell,
        "ts": time.time(),
    }
