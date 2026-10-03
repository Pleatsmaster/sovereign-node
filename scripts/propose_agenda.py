#!/usr/bin/env python3
"""AGENDA-OPPORTUNITY-0 Stage 2: gate, proposal packet, and validated recording.

The model step itself happens outside this script (in the recovery agent,
following OPPORTUNITY_PROMPT_0.md). This script owns everything mechanical
around it:

  gate               — evaluate the four conjuncts over the combined
                       pressure+opportunity condition set; PROCEED or a block
  prepare            — write the proposal packet for the agent (gate must hold)
  record             — validate a drafted proposal and append PROPOSAL_RECORDED
  record-no-proposal — record a NO_PROPOSAL evaluation for a condition state

One mechanism, two eyes: the pressure scanner (scan_conditions.py) is
frozen; the opportunity scanner (scan_opportunities.py) feeds the same
gate, watermark, ledger, and review path. Validation is fail-closed:
unknown fields, unwidenable authority classes, unobserved source
conditions, unresolvable evidence, manufactured urgency (why_now citing
nothing observed), vague objectives, and unbound or out-of-scope horizon
ids are all refused with a named reason. Nothing here proposes, ranks,
or launches.

Exit codes: 0 = ok; 2 = refused / gate blocked / failure. Reasons are JSON
on stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "scripts"))
sys.path.insert(0, str(LIFE0_ROOT / "src"))

import agenda_common as ac  # noqa: E402
from project_obligations import Projector  # noqa: E402

PACKET_VERSION = "agenda-opportunity-packet-0"


# --------------------------------------------------------------------------
# Gate
# --------------------------------------------------------------------------

def gate(life0: Path) -> dict:
    """Evaluate the four conjuncts. Returns {proceed, reason, ...}."""
    base = {"policy_version": ac.POLICY_VERSION}

    # 1. Obligation projection must be empty: idle means genuinely nothing.
    try:
        proj = Projector(str(life0)).project()
    except Exception as e:
        return {**base, "proceed": False, "reason": "PROJECTION_FAILED",
                "error": str(e)}
    open_obs = proj.get("open_obligations", [])
    if open_obs:
        return {**base, "proceed": False, "reason": "OPEN_OBLIGATIONS_EXIST",
                "open_obligations": open_obs}

    # 2. At least one detected condition, of either kind. One mechanism,
    #    two eyes: pressures and opportunities share the gate.
    scan = ac.combined_scan(life0)
    if scan is None or not scan.get("conditions"):
        return {**base, "proceed": False, "reason": "NO_CONDITIONS"}
    csh = scan["condition_set_hash"]

    # 3. The condition set must differ from the last evaluated state.
    wm = ac.read_watermark(life0)
    if wm.get("last_evaluated_condition_set_hash") == csh:
        return {**base, "proceed": False,
                "reason": "CONDITION_STATE_UNCHANGED",
                "condition_set_hash": csh,
                "last_outcome": wm.get("outcome")}

    # 4. No proposal already covers this condition set — in any state.
    #    Uniqueness is condition-bound: a rejected (or expired, or live)
    #    proposal for an unchanged condition state never regenerates.
    for pid, entry in ac.load_proposal_ledger(life0).items():
        p = entry.get("proposal") or {}
        if p.get("condition_set_hash") == csh and entry["state"] in (
                "PROPOSED", "ACCEPTED", "REJECTED", "EXPIRED"):
            return {**base, "proceed": False, "reason": "ALREADY_COVERED",
                    "condition_set_hash": csh,
                    "covering_proposal_id": pid,
                    "covering_state": entry["state"]}

    return {**base, "proceed": True, "reason": "PROCEED",
            "condition_set_hash": csh,
            "condition_count": len(scan["conditions"]),
            "pressure_count": len(scan["pressures"]),
            "opportunity_count": len(scan["opportunities"])}


# --------------------------------------------------------------------------
# Packet
# --------------------------------------------------------------------------

def prepare(life0: Path) -> dict:
    g = gate(life0)
    if not g["proceed"]:
        return {"prepared": False, "gate": g}
    scan = ac.combined_scan(life0)
    assert scan is not None
    packet = {
        "packet_version": PACKET_VERSION,
        "generated_at": ac.utcnow_iso(),
        "condition_set_hash": scan["condition_set_hash"],
        "pressure_scan_id": scan["pressure_scan_id"],
        "opportunity_scan_id": scan["opportunity_scan_id"],
        "observed_pressures": scan["pressures"],
        "observed_opportunities": scan["opportunities"],
        # Flat list retained for readers that expect the v0 shape.
        "conditions": scan["conditions"],
        "open_horizon_questions": ac.open_horizon_questions(life0),
        "current_obligations": {
            "open_obligations": [],
            "note": ("projection empty at packet time; the drafter must "
                     "still write why_existing_obligations_do_not_cover_it "
                     "honestly against this state."),
        },
        "obligation_summary": {
            "open_obligations": [],
            "note": ("projection empty at packet time; the drafter must "
                     "still write why_existing_obligations_do_not_cover_it "
                     "honestly against this state."),
        },
        "proposal_prompt": "OPPORTUNITY_PROMPT_0.md",
        "proposal_prompt_sha256": ac.prompt_sha256(life0),
        "candidate_schema": list(ac.PROPOSAL_SCHEMA_FIELDS),
        "constraints": {
            "required_authority_class": "local-build",
            "self_admission": "FORBIDDEN",
            "execution_authority": "NONE",
            "horizon_modification": "OPERATOR_ONLY",
        },
    }
    pkt_dir = life0 / "proposals" / "packets"
    pkt_dir.mkdir(parents=True, exist_ok=True)
    pkt_path = pkt_dir / f"{scan['condition_set_hash']}.json"
    pkt_path.write_text(ac.canonical(packet) + "\n", encoding="utf-8")
    return {"prepared": True, "packet_path": str(pkt_path),
            "condition_set_hash": scan["condition_set_hash"],
            "condition_count": len(scan["conditions"])}


# --------------------------------------------------------------------------
# Record: strict validation of a drafted proposal
# --------------------------------------------------------------------------

def _refuse(reason: str, **detail) -> dict:
    return {"recorded": False, "reason": reason,
            "policy_version": ac.POLICY_VERSION, **detail}


def _evidence_ref_ok(ref: dict, condition_ids: set[str], life0: Path) -> bool:
    if not isinstance(ref, dict):
        return False
    kind = ref.get("kind")
    if kind == "condition":
        return ref.get("condition_id") in condition_ids
    if kind == "file":
        p = ref.get("path", "")
        if not isinstance(p, str) or not p:
            return False
        target = Path(p) if Path(p).is_absolute() else life0 / p
        return target.exists()
    return False


def validate_proposal(life0: Path, proposal: dict) -> tuple[dict | None, dict | None]:
    """Return (normalized_proposal, None) or (None, refusal)."""
    if not isinstance(proposal, dict):
        return None, _refuse("PROPOSAL_NOT_AN_OBJECT")

    # Frozen schema: exactly these fields. This is what keeps free-form
    # "importance" scoring — or any other invented channel — out.
    keys = set(proposal.keys())
    expected = set(ac.PROPOSAL_SCHEMA_FIELDS)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        return None, _refuse("SCHEMA_MISMATCH",
                             missing=missing, extra=extra)

    scan = ac.combined_scan(life0)
    if scan is None or not scan.get("conditions"):
        return None, _refuse("NO_CONDITION_STATE")
    condition_ids = {c["condition_id"] for c in scan["conditions"]}

    # The proposal binds to the condition state it was drafted against.
    if proposal["condition_set_hash"] != scan["condition_set_hash"]:
        return None, _refuse("STALE_CONDITION_SET",
                             proposal_hash=proposal["condition_set_hash"],
                             current_hash=scan["condition_set_hash"])

    # Constraint 2 (constitutional): the model cannot widen the sensorium.
    # Every source condition must be an observed one.
    src = proposal["source_conditions"]
    if (not isinstance(src, list) or not src
            or not all(isinstance(s, str) for s in src)):
        return None, _refuse("SOURCE_CONDITIONS_INVALID")
    unobserved = [s for s in src if s not in condition_ids]
    if unobserved:
        return None, _refuse("UNOBSERVED_CONDITION",
                             unobserved=unobserved)

    # Authority: v0 supports exactly the demonstrated class.
    if proposal["required_authority_class"] not in ac.PROPOSAL_AUTHORITY_CLASSES:
        return None, _refuse("AUTHORITY_CLASS_UNSUPPORTED",
                             got=proposal["required_authority_class"],
                             supported=list(ac.PROPOSAL_AUTHORITY_CLASSES))

    # AGENDA-OPPORTUNITY-0: horizon binding. Pressure-only proposals carry
    # no horizon_ids. Any proposal citing an opportunity must bind >=1
    # OPEN/OPEN_PARTIAL horizon id, and every bound id must lie within the
    # cited opportunities' horizon_scope — the mechanism enforces that
    # Stage 2 binds the opportunity to its declared bearing, not to
    # whichever question it prefers.
    horizon_ids = proposal["horizon_ids"]
    if (not isinstance(horizon_ids, list)
            or not all(isinstance(h, str) for h in horizon_ids)):
        return None, _refuse("HORIZON_IDS_INVALID")
    opp_ids = {c["condition_id"] for c in scan["conditions"]
               if c["source"] in ac.OPPORTUNITY_SOURCES}
    opp_cited = [s for s in src if s in opp_ids]
    horizon = ac.load_horizon(life0)
    if opp_cited and not horizon:
        return None, _refuse("HORIZON_UNAVAILABLE")
    if horizon:
        for h in horizon_ids:
            if h not in horizon:
                return None, _refuse("HORIZON_ID_UNKNOWN", horizon_id=h)
            if horizon[h]["status"] not in ac.HORIZON_OPEN_STATUSES:
                return None, _refuse("HORIZON_ID_CLOSED", horizon_id=h,
                                     status=horizon[h]["status"])
    if opp_cited:
        if not horizon_ids:
            return None, _refuse("HORIZON_BINDING_REQUIRED")
        allowed: set[str] = set()
        for c in scan["conditions"]:
            if c["condition_id"] in opp_cited:
                allowed.update(
                    (c.get("evidence") or {}).get("horizon_scope") or [])
        outside = [h for h in horizon_ids if h not in allowed]
        if outside:
            return None, _refuse("HORIZON_SCOPE_VIOLATION",
                                 outside_scope=outside,
                                 allowed_scope=sorted(allowed))

    # Evidence must resolve.
    refs = proposal["evidence_refs"]
    if not isinstance(refs, list) or not refs:
        return None, _refuse("EVIDENCE_REFS_EMPTY")
    bad_refs = [r for r in refs
                if not _evidence_ref_ok(r, condition_ids, life0)]
    if bad_refs:
        return None, _refuse("EVIDENCE_UNRESOLVABLE", bad_refs=bad_refs)

    # Concrete objective, mirroring the admission bar.
    objective = proposal["objective"]
    if not isinstance(objective, str) or len(objective.strip()) < ac.OBJECTIVE_MIN_CHARS:
        return None, _refuse("OBJECTIVE_VAGUE",
                             min_chars=ac.OBJECTIVE_MIN_CHARS)

    # why_now must resolve back to the observed condition state: it must
    # cite at least one source condition id verbatim. Manufactured urgency
    # about unobserved things fails here.
    why_now = proposal["why_now"]
    if (not isinstance(why_now, str) or not why_now.strip()
            or not any(s in why_now for s in src)):
        return None, _refuse("WHY_NOW_UNGROUNDED",
                             note=("why_now must cite at least one "
                                   "source_conditions id verbatim"))

    contract = proposal["completion_contract"]
    preds = contract.get("predicates") if isinstance(contract, dict) else None
    if not isinstance(preds, list) or not preds:
        return None, _refuse("COMPLETION_CONTRACT_EMPTY")
    for p in preds:
        if (not isinstance(p, dict) or not p.get("id")
                or not p.get("verifiable")):
            return None, _refuse("PREDICATE_UNVERIFIABLE")

    for field in ("expected_consequence",
                  "why_existing_obligations_do_not_cover_it"):
        v = proposal[field]
        if not isinstance(v, str) or not v.strip():
            return None, _refuse("FIELD_EMPTY", field=field)
    risks = proposal["known_risks"]
    if not isinstance(risks, list) or not risks or not all(
            isinstance(r, str) and r.strip() for r in risks):
        return None, _refuse("KNOWN_RISKS_EMPTY")
    cost = proposal["predicted_cost"]
    if not isinstance(cost, str) or not cost.strip():
        return None, _refuse("FIELD_EMPTY", field="predicted_cost")

    # Instrumentation (not developmental machinery): the drafter must name
    # the model identity that produced the draft, so future comparative
    # lineage can distinguish history-driven generator change from a
    # changed worker or prompt.
    gm = proposal["generator_model"]
    if not isinstance(gm, str) or not gm.strip():
        return None, _refuse("GENERATOR_MODEL_MISSING")

    # Deterministic id. If the drafter supplied one it must match;
    # otherwise the recorder assigns it.
    expected_id = ac.proposal_id_for(scan["condition_set_hash"],
                                     objective.strip())
    supplied = proposal.get("proposal_id")
    if supplied not in (None, "", expected_id):
        return None, _refuse("PROPOSAL_ID_MISMATCH", expected=expected_id)
    normalized = dict(proposal)
    normalized["proposal_id"] = expected_id
    normalized["objective"] = objective.strip()
    return normalized, None


def record(life0: Path, proposal: dict) -> dict:
    normalized, refusal = validate_proposal(life0, proposal)
    if refusal is not None:
        return refusal
    assert normalized is not None
    pid = normalized["proposal_id"]

    # Idempotency first: the same proposal recorded twice is one proposal,
    # regardless of gate state.
    if pid in ac.load_proposal_ledger(life0):
        return {"recorded": True, "duplicate": True, "proposal_id": pid,
                "policy_version": ac.POLICY_VERSION}

    # The gate is enforced here, not just in the cron instructions: Stage 2
    # runs only when the gate holds.
    g = gate(life0)
    if not g["proceed"]:
        return _refuse("GATE_BLOCKED", gate=g)

    now = ac.utcnow_iso()
    stored = dict(normalized)
    stored["state"] = "PROPOSED"
    stored["proposed_at"] = now
    stored["proposed_by"] = ac.POLICY_VERSION
    # Instrumentation: bind to the prompt bytes actually on disk at record
    # time. Recomputed here, never trusted from the draft or packet.
    stored["generator_prompt_sha256"] = ac.prompt_sha256(life0)
    ac.append_jsonl(
        life0 / "state" / "agenda_proposals.jsonl",
        {"event": "PROPOSAL_RECORDED", "at": now, "proposal": stored,
         "policy_version": ac.POLICY_VERSION})
    ac.write_watermark(life0, {
        "last_evaluated_condition_set_hash": normalized["condition_set_hash"],
        "evaluated_at": now,
        "outcome": "PROPOSED",
        "proposal_id": pid,
    })
    return {"recorded": True, "proposal_id": pid,
            "policy_version": ac.POLICY_VERSION}


def record_no_proposal(life0: Path, condition_set_hash: str,
                       note: str) -> dict:
    scan = ac.combined_scan(life0)
    if scan is None or scan["condition_set_hash"] != condition_set_hash:
        return _refuse("STALE_CONDITION_SET",
                       current_hash=(scan["condition_set_hash"]
                                     if scan else None))
    # Same gate discipline as record: only evaluate when the gate holds,
    # except the idempotent re-record of an already-evaluated state.
    g = gate(life0)
    if not g["proceed"] and g["reason"] != "CONDITION_STATE_UNCHANGED":
        return _refuse("GATE_BLOCKED", gate=g)
    now = ac.utcnow_iso()
    ac.append_jsonl(
        life0 / "state" / "agenda_proposals.jsonl",
        {"event": "PROPOSAL_NO_PROPOSAL", "at": now,
         "condition_set_hash": condition_set_hash,
         "note": note or "",
         "policy_version": ac.POLICY_VERSION})
    ac.write_watermark(life0, {
        "last_evaluated_condition_set_hash": condition_set_hash,
        "evaluated_at": now,
        "outcome": "NO_PROPOSAL",
    })
    return {"recorded": True, "outcome": "NO_PROPOSAL",
            "policy_version": ac.POLICY_VERSION}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="AGENDA-OPPORTUNITY-0 Stage 2 mechanics.")
    ap.add_argument("--life0", default=str(LIFE0_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gate", help="evaluate the four conjuncts")
    sub.add_parser("prepare", help="write the proposal packet (gate must hold)")

    p_rec = sub.add_parser("record", help="validate and record a drafted proposal")
    p_rec.add_argument("--proposal", required=True,
                       help="path to the drafted proposal JSON")

    p_np = sub.add_parser("record-no-proposal",
                          help="record a NO_PROPOSAL evaluation")
    p_np.add_argument("--condition-set-hash", required=True)
    p_np.add_argument("--note", default="")

    args = ap.parse_args(argv)
    life0 = Path(args.life0)
    try:
        if args.cmd == "gate":
            result = gate(life0)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.cmd == "prepare":
            result = prepare(life0)
            print(json.dumps(result, sort_keys=True))
            return 0 if result.get("prepared") else 2
        if args.cmd == "record":
            proposal = json.loads(
                Path(args.proposal).read_text(encoding="utf-8"))
            result = record(life0, proposal)
            print(json.dumps(result, sort_keys=True))
            return 0 if result.get("recorded") else 2
        if args.cmd == "record-no-proposal":
            result = record_no_proposal(life0, args.condition_set_hash,
                                        args.note)
            print(json.dumps(result, sort_keys=True))
            return 0 if result.get("recorded") else 2
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
