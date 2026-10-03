"""LIFE-0 pulse runner: sense -> snapshot -> delta -> need -> gate -> stage.

One pulse:
  1. Sense the three surfaces (deterministic, read-only, seconds).
  2. Load the previous snapshot; compute D_t = E_t - E_{t-1} mechanically.
  3. Emit OBSERVATION records, one per delta.
  4. Terminal state is one of three:
       NO_ACTION     — all surfaces observed; no legitimate need detected.
       STAGED        — valid deltas processed into needs/gating/staging.
       PULSE_INVALID — a required surface could not be observed reliably.
                       Never counted as NO_ACTION; excluded from the
                       NO_ACTION denominator.
  5. On STAGED: form needs (dedupe by deterministic need_id), gate each,
     stage the approved ones, move processed inbox files to inbox/done/.

The pulse makes zero frontier-model calls, spends nothing, launches nothing.
Cognition is called by difference, not by clock.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from . import config as config_mod
from .delta import canonical, detect_deltas, make_observation, utcnow_iso
from .dispatch import STATUS_STAGED, stage_mission
from .gate import DECISION_DENY, DECISION_STAGE, gate_check
from .needs import enqueue_need, form_need, load_queue, set_status


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(canonical(obj) + "\n")


def run_pulse(cfg: dict[str, Any] | None = None,
              github_fetch: Callable | None = None) -> dict[str, Any]:
    """Run one pulse. `github_fetch` is injectable for tests (no network).

    Terminal states (three, not two):
    - NO_ACTION: all required surfaces observed successfully; no legitimate
      need detected.
    - STAGED: valid deltas were processed through need formation, gating, and
      staging (per-need outcomes in the queue; counts in the result).
    - PULSE_INVALID: one or more required surfaces could not be observed
      reliably. Deltas from healthy surfaces are still processed (each
      surface's deltas are independent), but the pulse is NEVER counted as
      NO_ACTION and is EXCLUDED from the NO_ACTION denominator in any
      base-rate computation — a sensor failure must never silently count as
      "nothing happened".
    """
    from . import sensors

    cfg = cfg or config_mod.load()
    state_dir = Path(cfg["state_dir"])
    pulse_at = utcnow_iso()
    pulse_id = "pulse_" + pulse_at.replace(":", "").replace("-", "").replace(".", "")

    # 1. Sense.
    fetch = github_fetch or sensors.github_http_get
    gh = sensors.sense_github(cfg.get("github_repo"),
                              cfg.get("github_repo_fallback_name", "unified_machine"),
                              http_get=fetch)
    fact0 = sensors.sense_fact0(cfg["fact0_db"])
    inbox = sensors.sense_inbox(cfg["inbox_dir"])
    snapshot = {"pulse_id": pulse_id, "at": pulse_at,
                "github": gh, "fact0": fact0, "inbox": inbox}
    surface_status = {s: ("error" if snapshot[s].get("sensor_error") else "ok")
                      for s in ("github", "fact0", "inbox")}
    degraded = [s for s, st in surface_status.items() if st == "error"]

    # 2. Delta against previous snapshot. A blind surface yields no deltas
    # (a failed sensor is not evidence of change) — handled in detect_deltas.
    snap_path = state_dir / "sensors.json"
    prev: dict[str, Any] = {}
    if snap_path.exists():
        prev = json.loads(snap_path.read_text(encoding="utf-8"))
    deltas = detect_deltas(prev, snapshot)

    # 3. Observations, one per delta.
    obs_path = state_dir / "observations.jsonl"
    observations = [make_observation(d) for d in deltas]
    for ob in observations:
        _append_jsonl(obs_path, {"event": "OBSERVATION", **ob})

    result: dict[str, Any] = {
        "pulse_id": pulse_id, "at": pulse_at,
        "surfaces": surface_status,
        "deltas": len(deltas),
    }
    if degraded:
        result["degraded_surfaces"] = degraded
        result["note"] = ("one or more required surfaces could not be observed "
                          "reliably; this pulse is excluded from the NO_ACTION "
                          "denominator")

    # 4. No delta and all surfaces healthy -> NO_ACTION: a valid life event.
    #    NO_ACTION requires successful observation; it is never the answer to
    #    a failed sensor.
    if not deltas and not degraded:
        result["result"] = "NO_ACTION"
        _append_jsonl(state_dir / "pulse_log.jsonl",
                      {"event": "PULSE", **result,
                       "note": "all surfaces observed; no legitimate need detected"})
        _write_json(snap_path, snapshot)
        return result

    # 5. Needs -> gate -> stage (deltas from healthy surfaces only; the
    #    detector already yields none for blind surfaces).
    needs_created, needs_staged, needs_denied = 0, 0, 0
    inbox_done: list[str] = []
    for d in deltas:
        need = form_need(d)  # raises without an observed delta: by design
        is_new = enqueue_need(state_dir, need)
        if is_new:
            needs_created += 1
        status = load_queue(state_dir)[need["need_id"]]["status"]
        if status != "NEW":
            continue  # already handled by an earlier pulse: no duplicate work
        gate_record = gate_check(need)
        if gate_record["decision"] == DECISION_STAGE:
            stage_mission(need, gate_record, cfg["dispatch_dir"])
            set_status(state_dir, need["need_id"], STATUS_STAGED,
                       operator_decision="pending", decided_by="pulse",
                       note="staged; launch requires explicit operator act")
            needs_staged += 1
            if d["source"] == "inbox":
                inbox_done.append(d["current_state"]["name"])
        elif gate_record["decision"] == DECISION_DENY:
            set_status(state_dir, need["need_id"], "DECLINED",
                       operator_decision="rejected", decided_by="pulse",
                       note=gate_record["reason"])
            needs_denied += 1

    # Inbox files whose needs were staged move to done/ (documented convention).
    # The pulse never creates inbox files; it only retires processed ones.
    inbox_dir = Path(cfg["inbox_dir"])
    for name in inbox_done:
        src = inbox_dir / name
        if src.exists():
            src.replace(inbox_dir / "done" / src.name)

    result["result"] = "PULSE_INVALID" if degraded else "STAGED"
    result["needs_created"] = needs_created
    result["needs_staged"] = needs_staged
    result["needs_denied"] = needs_denied
    if degraded:
        result["note"] = (result.get("note", "") +
                          f"; processed {len(deltas)} deltas from healthy surfaces")
    _append_jsonl(state_dir / "pulse_log.jsonl", {"event": "PULSE", **result})
    _write_json(snap_path, snapshot)
    return result


def pulse_stats(state_dir: str | Path) -> dict[str, Any]:
    """Base-rate statistics over the pulse log.

    The NO_ACTION denominator EXCLUDES PULSE_INVALID pulses: a sensor failure
    must never silently count as "nothing happened". This is the query future
    earned-autonomy analysis must use.
    """
    total = invalid = no_action = staged = 0
    p = Path(state_dir) / "pulse_log.jsonl"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                if ev.get("event") != "PULSE":
                    continue
                total += 1
                r = ev.get("result")
                if r == "PULSE_INVALID":
                    invalid += 1
                elif r == "NO_ACTION":
                    no_action += 1
                elif r == "STAGED":
                    staged += 1
    valid = total - invalid
    return {
        "total_pulses": total,
        "invalid_pulses": invalid,
        "valid_pulses": valid,
        "no_action_pulses": no_action,
        "staged_pulses": staged,
        # The denominator that matters: valid pulses only.
        "no_action_rate": (no_action / valid) if valid else None,
        "note": "PULSE_INVALID excluded from the NO_ACTION denominator",
    }


def consequence_schema() -> dict[str, Any]:
    """Handoff contract (defined, not built): when a staged mission is later
    authorized and run, its consequence record MUST contain these fields and
    be appended to state/consequences.jsonl. The pulse does not score
    consequences; it only defines the schema so later machinery can."""
    return {
        "schema": "life0.consequence",
        "schema_version": 1,
        "required_fields": [
            "mission_id", "need_id", "delta_id",
            "worker_id", "worker_model",
            "authorized_by", "authorized_at",
            "started_at", "finished_at",
            "tests_changed", "artifact_produced", "failure_removed",
            "cost_incurred", "new_failure_exposed",
            "operator_accepted", "external_result_changed",
            "notes",
        ],
        "sink": "state/consequences.jsonl",
        "note": ("Consequence recording, pressure extraction, candidate "
                 "formation, falsification, and promotion are NOT part of "
                 "LIFE-0 v0. This schema is the handoff contract only."),
    }
