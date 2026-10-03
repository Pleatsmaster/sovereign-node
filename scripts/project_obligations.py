#!/usr/bin/env python3
"""OBLIGATION-0: deterministic obligation projection.

Reads the full event history of every need/mission lineage under life-0 and
projects exactly one current obligation state per need:

    OPEN | BLOCKED | SATISFIED | RETIRED

Invariant 1 (OBLIGATION-0): terminal state dominates historical openness.
A prior STAGED / AUTHORIZED / DISPATCHED event cannot make a need OPEN when a
later authoritative terminal record closes it. Terminal states are absorbing:
an event recorded after a terminal event for the same need is preserved (as a
terminal confirmation or an anomaly) and cannot change the projected state.

No agent judgment is involved. This is a deterministic fold over the ledgers,
exactly like the repaired sensing clock.

Usage:
    project_obligations.py --life0 <life-0 dir> --out <obligations.json>
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

STATES = ("OPEN", "BLOCKED", "SATISFIED", "RETIRED")
TERMINAL = {"SATISFIED", "RETIRED"}
TERMINAL_EVENT_TYPES = {"chain_satisfied", "closed_satisfied", "frozen_verdict", "retired"}

CHAIN_BLOCKED_DECISIONS = {
    "STOP_STEP_FAILED",
    "STOP_NO_PROGRESS",
    "STOP_UNAUTHORIZED_DISPATCH",
    "STOP_DEPTH_EXHAUSTED",
    "STOP_BUDGET",
    "STOP_BOUNDARY",
}

VERDICT_HASH_RE = re.compile(r"sha256\s+([0-9a-f]{64})")


def parse_at(s):
    """Parse an ISO-8601 timestamp to an aware datetime for chronological sort.

    Raw string sort is wrong across mixed UTC offsets
    ('17:24:47-04:00' < '20:42:53+00:00' lexicographically but later in time).
    """
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def mtime_iso(path):
    return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).isoformat()


def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Projector:
    def __init__(self, life0):
        self.life0 = life0
        self.events = []          # list of dicts: at, seq, need_id, type, source, detail
        self._seq = 0
        self.unbound_verdicts = []

    def emit(self, need_id, at, etype, source, detail=None):
        self._seq += 1
        self.events.append({
            "at": at,
            "seq": self._seq,
            "need_id": need_id,
            "type": etype,
            "source": source,
            "detail": detail or {},
        })

    # ---- source readers -------------------------------------------------

    def read_needs(self):
        for e in load_jsonl(os.path.join(self.life0, "state", "needs.jsonl")):
            ev = e.get("event")
            nid = e.get("need_id") or (e.get("need") or {}).get("need_id")
            if not nid:
                continue
            if ev == "NEED_CREATED":
                self.emit(nid, e["at"], "need_created", "state/needs.jsonl")
            elif ev == "NEED_STATUS" and e.get("status") == "STAGED_AWAITING_AUTHORIZATION":
                self.emit(nid, e["at"], "staged", "state/needs.jsonl",
                           {"decided_by": e.get("decided_by")})

    def read_packages(self):
        staged = os.path.join(self.life0, "dispatch", "staged")
        if not os.path.isdir(staged):
            return
        for need_id in sorted(os.listdir(staged)):
            pdir = os.path.join(staged, need_id)
            if not os.path.isdir(pdir):
                continue
            mp = os.path.join(pdir, "MISSION_PACKAGE.json")
            if os.path.exists(mp):
                with open(mp, "r", encoding="utf-8") as f:
                    p = json.load(f)
                if p.get("staged_at"):
                    self.emit(need_id, p["staged_at"], "staged",
                               "dispatch/staged/<need>/MISSION_PACKAGE.json")
                od = p.get("operator_decision") or {}
                if p.get("authorized_at") or od.get("decision") == "AUTHORIZE" or p.get("launch_authorized"):
                    self.emit(need_id, p.get("authorized_at") or mtime_iso(mp),
                               "authorized", "dispatch/staged/<need>/MISSION_PACKAGE.json",
                               {"decided_by": od.get("decided_by")})
                closure = p.get("closure")
                if p.get("status") == "CLOSED" and closure:
                    self.emit(need_id, closure.get("closed_at") or mtime_iso(mp),
                               "closed_satisfied",
                               "dispatch/staged/<need>/MISSION_PACKAGE.json",
                               {"closed_by": closure.get("closed_by")})
            disp = os.path.join(pdir, "AUTO_WORK_0_DISPATCH.json")
            if os.path.exists(disp):
                self.emit(need_id, mtime_iso(disp), "dispatched",
                           "dispatch/staged/<need>/AUTO_WORK_0_DISPATCH.json")
            ledger = os.path.join(pdir, "CHAIN_LEDGER.jsonl")
            if os.path.exists(ledger):
                for le in load_jsonl(ledger):
                    d = le.get("chain_decision", "")
                    if d == "CONTINUE_ELIGIBLE":
                        etype = "continued"
                    elif d == "STOP_GOAL_SATISFIED":
                        etype = "chain_satisfied"
                    elif d.startswith("STOP_"):
                        etype = "chain_blocked"
                    else:
                        continue
                    self.emit(need_id, le.get("evaluated_at") or mtime_iso(ledger),
                               etype, "dispatch/staged/<need>/CHAIN_LEDGER.jsonl",
                               {"chain_decision": d, "depth": le.get("depth")})

    def read_consequences(self):
        for e in load_jsonl(os.path.join(self.life0, "state", "consequences.jsonl")):
            nid = e.get("need_id")
            if not nid:
                # mission_id takes the form life0-<need_id>
                m = re.match(r"^life0-(need_\w+)$", e.get("mission_id") or "")
                if m:
                    nid = m.group(1)
            if not nid:
                continue
            at = e.get("recorded_at") or e.get("finished_at") or e.get("at")
            if not at:
                continue
            self.emit(nid, at, "executed", "state/consequences.jsonl",
                       {"mission_id": e.get("mission_id"),
                        "outcome": e.get("kind") or "mission_consequence"})

    def read_verdicts(self):
        # Bind frozen verdicts to lineages by the ledger SHA-256 they record.
        ledger_hashes = {}
        staged = os.path.join(self.life0, "dispatch", "staged")
        if os.path.isdir(staged):
            for need_id in sorted(os.listdir(staged)):
                ledger = os.path.join(staged, need_id, "CHAIN_LEDGER.jsonl")
                if os.path.exists(ledger):
                    ledger_hashes[sha256_file(ledger)] = need_id
        records = os.path.join(self.life0, "records")
        if not os.path.isdir(records):
            return
        for name in sorted(os.listdir(records)):
            if not name.endswith("_RUN_VERDICT_FROZEN.md"):
                continue
            path = os.path.join(records, name)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            m = VERDICT_HASH_RE.search(text)
            if not m:
                self.unbound_verdicts.append({"file": name, "reason": "no ledger sha256 in verdict"})
                continue
            need_id = ledger_hashes.get(m.group(1))
            if not need_id:
                self.unbound_verdicts.append({"file": name, "reason": "ledger hash matches no staged lineage"})
                continue
            self.emit(need_id, mtime_iso(path), "frozen_verdict",
                       "records/%s" % name, {"ledger_sha256": m.group(1)[:16] + "..."})

    def read_directives(self):
        # Optional operator directives. Absent file = no directives.
        for e in load_jsonl(os.path.join(self.life0, "state", "obligation_directives.jsonl")):
            nid = e.get("need_id")
            directive = (e.get("directive") or "").upper()
            if not nid or "at" not in e:
                continue
            if directive == "RETIRE":
                self.emit(nid, e["at"], "retired", "state/obligation_directives.jsonl")
            elif directive == "BLOCK":
                self.emit(nid, e["at"], "blocked", "state/obligation_directives.jsonl")

    # ---- fold ------------------------------------------------------------

    @staticmethod
    def transition(state, etype):
        if etype in ("need_created", "staged", "authorized", "dispatched", "continued"):
            return "OPEN"
        if etype == "executed":
            return "OPEN" if state is None else state
        if etype in ("chain_blocked", "blocked"):
            return "BLOCKED"
        if etype in ("chain_satisfied", "closed_satisfied", "frozen_verdict"):
            return "SATISFIED"
        if etype == "retired":
            return "RETIRED"
        return state  # unknown type: no transition (caller records anomaly)

    KNOWN_TYPES = {
        "need_created", "staged", "authorized", "dispatched", "continued",
        "executed", "chain_blocked", "blocked",
        "chain_satisfied", "closed_satisfied", "frozen_verdict", "retired",
    }

    AUTHORIZING_TYPES = {"authorized", "dispatched", "continued", "executed",
                         "chain_blocked", "chain_satisfied"}

    def project(self):
        self.read_needs()
        self.read_packages()
        self.read_consequences()
        self.read_verdicts()
        self.read_directives()

        by_need = {}
        for ev in self.events:
            by_need.setdefault(ev["need_id"], []).append(ev)

        obligations = {}
        for need_id in sorted(by_need):
            # Chronological fold. Sort by parsed instant, NOT by raw string:
            # mixed UTC offsets do not order lexicographically.
            evs = sorted(by_need[need_id], key=lambda e: (parse_at(e["at"]), e["seq"]))
            state = None
            determining = None
            confirmations = []
            anomalies = []
            consumed = []
            for ev in evs:
                if ev["type"] not in self.KNOWN_TYPES:
                    anomalies.append(dict(ev, note="unknown event type; no transition"))
                    continue
                if state in TERMINAL:
                    # After SATISFIED, events consistent with completion
                    # (terminal records, late execution records) confirm;
                    # events claiming new openness are anomalies.
                    # After RETIRED, every later event is an anomaly.
                    if state == "SATISFIED" and (
                        ev["type"] in TERMINAL_EVENT_TYPES or ev["type"] == "executed"
                    ):
                        confirmations.append(ev)
                    else:
                        anomalies.append(dict(ev, note="event after terminal state; state unchanged"))
                    continue
                new_state = self.transition(state, ev["type"])
                consumed.append(ev)
                if new_state != state:
                    state = new_state
                    determining = ev
            obligations[need_id] = {
                "state": state or "OPEN",
                "events_consumed": len(consumed),
                "determining_event": (
                    {"at": determining["at"], "type": determining["type"],
                     "source": determining["source"], "detail": determining["detail"]}
                    if determining else None
                ),
                "terminal_confirmations": [
                    {"at": e["at"], "type": e["type"], "source": e["source"]} for e in confirmations
                ],
                "anomalies": [
                    {"at": e["at"], "type": e["type"], "source": e["source"],
                     "note": e.get("note", "")} for e in anomalies
                ],
            }

        by_state = {s: [] for s in STATES}
        for need_id, o in obligations.items():
            by_state[o["state"]].append(need_id)

        open_ids = by_state["OPEN"]
        pending_auth = []
        for need_id in open_ids:
            evs = [e for e in self.events if e["need_id"] == need_id]
            if not any(e["type"] in self.AUTHORIZING_TYPES for e in evs):
                pending_auth.append(need_id)

        return {
            "projected_at": iso_now(),
            "invariant": "OBLIGATION-0 #1: terminal state dominates historical openness",
            "obligations": obligations,
            "by_state": by_state,
            "open_obligations": open_ids,
            "pending_authorization": pending_auth,
            "unbound_verdicts": self.unbound_verdicts,
        }


def main(argv=None):
    ap = argparse.ArgumentParser(description="OBLIGATION-0 deterministic obligation projection")
    ap.add_argument("--life0", required=True, help="life-0 directory")
    ap.add_argument("--out", required=True, help="output obligations.json path (atomic write)")
    args = ap.parse_args(argv)

    result = Projector(args.life0).project()

    tmp = args.out + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=False)
        f.write("\n")
    os.rename(tmp, args.out)

    summary = {s: len(v) for s, v in result["by_state"].items()}
    print("projected %d lineage(s): %s" % (len(result["obligations"]), json.dumps(summary)))
    print("open: %s" % result["open_obligations"])
    print("pending_authorization: %s" % result["pending_authorization"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
