#!/usr/bin/env python3
"""AGENDA-OPPORTUNITY-0 Stage 1 (second eye): deterministic opportunity detection.

Reads the declared, bounded opportunity sensorium (v0) and emits
OPPORTUNITY_DETECTED records — falsifiable observations with evidence.
Never goals, never instructions, never imperatives: the record schema is
the same observation-only six-key shape as pressures, and every
opportunity string comes from a fixed template.

Sensorium v0 (each a named, deterministic predicate):
  unlinked_result_artifact  — a consequence record produced a result
                              artifact whose originating mission/chain
                              carries a frozen operator binding to >=1 RH*
                              question, and no evidence link records that
                              artifact's bearing on any bound question
  testable_residue_pending  — a residue transitioned to mechanically
                              testable since the previous scan: status
                              active, and >=1 mission-ledger event exists
                              with ts after the residue's recording (weak
                              mechanical form of "a later pivot exists";
                              whether any pivot qualifies is the test
                              harness's judgment, stated in the record)
  resource_became_available — a sensor availability flag flipped
                              false->true since the previous scan

Transition-based predicates (residue, resource) fire on witnessed change
only: the first scan establishes the baseline silently. A lost scan
history fails silent (no transition can be witnessed), never floods.

Writes (only): state/opportunity_scans.jsonl. Read-only toward every
other store, including the residue store and mission ledgers.

Exit codes: 0 = scanned (even with source errors — they are recorded, not
invented around); 2 = scanner failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "scripts"))

import agenda_common as ac  # noqa: E402

# Same observation-only key set as pressures: no action/fix/goal/priority.
OPPORTUNITY_KEYS = ("condition_id", "source", "observed_at",
                    "condition", "evidence", "scan_id")


def _opportunity(source: str, text: str, evidence: dict,
                 observed_at: str, scan_id: str) -> dict:
    opp = {
        "condition_id": ac.condition_id_for(source, text),
        "source": source,
        "observed_at": observed_at,
        "condition": text,
        "evidence": evidence,
        "scan_id": scan_id,
    }
    assert set(opp.keys()) == set(OPPORTUNITY_KEYS), "observation schema drift"
    assert source in ac.OPPORTUNITY_SOURCES
    return opp


# --------------------------------------------------------------------------
# Predicate 1: unlinked_result_artifact
# --------------------------------------------------------------------------

def scan_unlinked_artifacts(life0: Path, horizon: dict,
                            observed_at: str, scan_id: str
                            ) -> tuple[list[dict], list[str]]:
    """Research-bound result whose bearing on the horizon is unlinked.

    Requires the originating mission/chain to already carry a frozen
    operator binding to >=1 RH* question. Bindings are never invented
    here; without one, however suggestive an artifact looks, it fires
    nothing.
    """
    errors: list[str] = []
    conditions: list[dict] = []

    bindings: dict[str, set[str]] = {}
    for b in ac.read_horizon_bindings(life0):
        mid = b.get("mission_id") or b.get("chain_id")
        ids = b.get("horizon_ids") or []
        if not mid or not isinstance(ids, list):
            continue
        valid = {h for h in ids if h in horizon}
        if valid:
            bindings.setdefault(mid, set()).update(valid)

    links: set[tuple[str, str]] = set()
    for ln in ac.read_evidence_links(life0):
        a, h = ln.get("artifact"), ln.get("horizon_id")
        if isinstance(a, str) and isinstance(h, str):
            links.add((a, h))

    for r in ac.read_jsonl(life0 / "state" / "consequences.jsonl"):
        mission_id = r.get("mission_id")
        artifacts = r.get("artifact_produced") or []
        if not mission_id or not isinstance(artifacts, list):
            continue
        bound = bindings.get(mission_id)
        if not bound:
            continue  # no frozen binding: not a research-bound result
        for artifact in artifacts:
            if not isinstance(artifact, str) or not artifact.strip():
                continue
            unlinked = sorted(h for h in bound if (artifact, h) not in links)
            if not unlinked:
                continue
            text = (f"unlinked result artifact: {artifact} "
                    f"(mission {mission_id}); bearing unresolved on: "
                    f"{', '.join(unlinked)}")
            conditions.append(_opportunity(
                "unlinked_result_artifact", text,
                {"artifact": artifact, "mission_id": mission_id,
                 "bound_horizon": sorted(bound),
                 "unlinked_horizon": unlinked,
                 "horizon_scope": sorted(bound)},
                observed_at, scan_id))
    return conditions, errors


# --------------------------------------------------------------------------
# Predicate 2: testable_residue_pending
# --------------------------------------------------------------------------

def _residue_store() -> Path:
    override = os.environ.get("UM_RESIDUE_DIR")
    if override:
        return Path(override) / "residue.jsonl"
    return Path.home() / ".unified-machine" / "residue" / "residue.jsonl"


def _ledger_max_ts(ledger: Path) -> tuple[float | None, str | None]:
    """(max event ts, error). Read-only open; fail-closed per ledger."""
    try:
        con = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
        try:
            row = con.execute("select max(ts) from events").fetchone()
            return (float(row[0]) if row and row[0] is not None else None,
                    None)
        finally:
            con.close()
    except Exception as e:
        return None, f"{ledger.parent.name}: {e}"


def _later_event_counts(ledger: Path, after_ts: float) -> tuple[int, str | None]:
    try:
        con = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
        try:
            row = con.execute(
                "select count(*) from events where ts > ?",
                (after_ts,)).fetchone()
            return int(row[0]), None
        finally:
            con.close()
    except Exception as e:
        return 0, f"{ledger.parent.name}: {e}"


def scan_testable_residues(life0: Path, um_missions: Path,
                           prev_testable: set[str],
                           observed_at: str, scan_id: str
                           ) -> tuple[list[dict], list[str], set[str]]:
    """Residues that became mechanically testable since the previous scan.

    TESTABLE_v0(r) := r.status == "active" AND some mission ledger holds
    an event with ts after r's recording. This is the weak mechanical form
    of "a later pivot exists"; whether any pivot qualifies is the test
    harness's judgment, said verbatim in the record. Only witnessed
    transitions fire.
    """
    errors: list[str] = []
    conditions: list[dict] = []
    current_testable: set[str] = set()

    ledgers: list[tuple[str, Path]] = []
    if um_missions.is_dir():
        # Recursive: missions keep .state-<id>/ledger.sqlite3 both at the
        # root and nested under per-mission directories. pathlib's "**"
        # matches zero or more segments, so the top-level ledgers are
        # included as well. Dedup by path; disambiguate duplicate mission
        # names with the parent path relative to the ledger root.
        paths = sorted(um_missions.glob("**/.state-*/ledger.sqlite3"),
                       key=lambda p: p.as_posix())
        seen: set[str] = set()
        uniq: list[Path] = []
        for p in paths:
            key = p.as_posix()
            if key not in seen:
                seen.add(key)
                uniq.append(p)
        names: dict[str, int] = {}
        for p in uniq:
            nm = p.parent.name
            nm = nm[len(".state-"):] if nm.startswith(".state-") else nm
            names[nm] = names.get(nm, 0) + 1
        for p in uniq:
            nm = p.parent.name
            nm = nm[len(".state-"):] if nm.startswith(".state-") else nm
            if names[nm] > 1:
                nm = p.parent.relative_to(um_missions).as_posix()
            ledgers.append((nm, p))
    else:
        errors.append(f"mission ledger root missing: {um_missions}")

    ledger_max: dict[str, float] = {}
    ledger_path: dict[str, Path] = {}
    for mission, ledger in ledgers:
        max_ts, err = _ledger_max_ts(ledger)
        if err:
            errors.append(err)
            continue
        if max_ts is not None:
            ledger_max[mission] = max_ts
            ledger_path[mission] = ledger

    store = _residue_store()
    residues = ac.read_jsonl(store)
    if not residues and not store.exists():
        errors.append(f"residue store missing: {store}")

    for r in residues:
        rid = r.get("id")
        if not isinstance(rid, str) or r.get("status") != "active":
            continue
        try:
            rts = float(r.get("ts"))
        except (TypeError, ValueError):
            errors.append(f"residue {rid}: unparsable ts")
            continue
        later: dict[str, int] = {}
        for mission, max_ts in ledger_max.items():
            if max_ts > rts:
                n, err = _later_event_counts(ledger_path[mission], rts)
                if err:
                    errors.append(err)
                    continue
                if n > 0:
                    later[mission] = n
        if not later:
            continue
        current_testable.add(rid)
        if rid in prev_testable:
            continue  # already testable last scan: not a transition
        missions = sorted(later)
        text = (f"residue {rid} became testable: mission ledgers with "
                f"later events: {', '.join(missions)}")
        conditions.append(_opportunity(
            "testable_residue_pending", text,
            {"residue_id": rid, "recorded_ts": rts,
             "later_missions": {m: later[m] for m in missions},
             "residue_scope": r.get("scope"),
             "horizon_scope": ["RH1", "RH3"],
             "note": ("weak mechanical form: later mission events exist; "
                      "whether any pivot qualifies is the test harness's "
                      "judgment")},
            observed_at, scan_id))
    return conditions, errors, current_testable


# --------------------------------------------------------------------------
# Predicate 3: resource_became_available
# --------------------------------------------------------------------------

def _availability_map(node, path: tuple[str, ...],
                       out: dict[str, bool]) -> None:
    if isinstance(node, dict):
        avail = node.get("available")
        if isinstance(avail, bool) and path:
            out[".".join(path)] = avail
        for k, v in node.items():
            if isinstance(k, str):
                _availability_map(v, path + (k,), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _availability_map(v, path + (str(i),), out)


def scan_resource_availability(life0: Path, open_ids: list[str],
                               prev_map: dict[str, bool],
                               observed_at: str, scan_id: str
                               ) -> tuple[list[dict], list[str], dict[str, bool]]:
    """Sensor availability flags that flipped false->true since last scan.

    Only witnessed transitions fire: a sensor never before seen at false
    is not a transition, however available it is now.
    """
    errors: list[str] = []
    conditions: list[dict] = []
    sensors_path = life0 / "state" / "sensors.json"
    try:
        sensors = json.loads(sensors_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [], [f"sensors.json unreadable: {e}"], dict(prev_map)
    current: dict[str, bool] = {}
    _availability_map(sensors, (), current)
    for resource in sorted(current):
        if prev_map.get(resource) is False and current[resource] is True:
            conditions.append(_opportunity(
                "resource_became_available",
                f"resource became available: {resource}",
                {"resource": resource,
                 "previous_available": False,
                 "observed_available": True,
                 "horizon_scope": sorted(open_ids)},
                observed_at, scan_id))
    return conditions, errors, current


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def scan(life0: Path, um_missions: Path) -> dict:
    observed_at = ac.utcnow_iso()
    scan_id = "oscan_" + ac.sha_hex(observed_at)[:12]
    conditions: list[dict] = []
    source_errors: list[str] = []

    horizon = ac.load_horizon(life0)
    if not horizon:
        source_errors.append(
            "RESEARCH_HORIZON_0.md missing or unparsable: horizon-dependent "
            "predicates skipped (fail-closed)")
        open_ids: list[str] = []
    else:
        open_ids = [qid for qid in sorted(horizon)
                    if horizon[qid]["status"] in ac.HORIZON_OPEN_STATUSES]

    prev = ac.latest_opportunity_scan(life0)
    prev_testable = set((prev or {}).get("testable_residues") or [])
    prev_map = dict((prev or {}).get("availability_map") or {})
    testable_now: set[str] = set(prev_testable)
    availability_now: dict[str, bool] = dict(prev_map)

    if horizon:
        c1, e1 = scan_unlinked_artifacts(life0, horizon, observed_at, scan_id)
        conditions.extend(c1)
        source_errors.extend(e1)

        c2, e2, testable_now = scan_testable_residues(
            life0, um_missions, prev_testable, observed_at, scan_id)
        conditions.extend(c2)
        source_errors.extend(e2)

        c3, e3, availability_now = scan_resource_availability(
            life0, open_ids, prev_map, observed_at, scan_id)
        conditions.extend(c3)
        source_errors.extend(e3)

    dedup: dict[str, dict] = {}
    for c in conditions:
        dedup[c["condition_id"]] = c
    conditions = [dedup[k] for k in sorted(dedup)]

    csh = ac.condition_set_hash(conditions)
    ac.append_jsonl(
        life0 / "state" / "opportunity_scans.jsonl",
        {"event": "SCAN_RECORDED", "scan_id": scan_id,
         "scanned_at": observed_at, "conditions": conditions,
         "condition_set_hash": csh,
         "testable_residues": sorted(testable_now),
         "availability_map": availability_now,
         "source_errors": source_errors,
         "policy_version": ac.POLICY_VERSION})

    return {"scan_id": scan_id,
            "opportunity_count": len(conditions),
            "opportunity_set_hash": csh,
            "testable_residue_count": len(testable_now),
            "source_errors": source_errors,
            "policy_version": ac.POLICY_VERSION}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="AGENDA-OPPORTUNITY-0 Stage 1: deterministic opportunity scan.")
    ap.add_argument("--life0", default=str(LIFE0_ROOT))
    ap.add_argument("--um-missions",
                    default=str(Path.home() / "workspace" / "um-missions"),
                    help="mission ledger root (default: ~/workspace/um-missions)")
    args = ap.parse_args(argv)
    try:
        result = scan(Path(args.life0), Path(args.um_missions))
    except Exception as e:
        print(json.dumps({"scanned": False, "error": str(e)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
