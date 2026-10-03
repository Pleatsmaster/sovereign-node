"""OBLIGATION-0 projection tests.

Deterministic, stdlib only, no API. Covers the fold transitions and the
2026-10-01 incident: a historical STAGED event must not project OPEN when a
later authoritative terminal record closes the lineage.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from project_obligations import Projector  # noqa: E402


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def make_life0():
    root = tempfile.mkdtemp(prefix="life0-obligation-test-")
    os.makedirs(os.path.join(root, "state"))
    os.makedirs(os.path.join(root, "dispatch", "staged"))
    os.makedirs(os.path.join(root, "records"))
    return root


def add_package(root, need_id, package):
    pdir = os.path.join(root, "dispatch", "staged", need_id)
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, "MISSION_PACKAGE.json"), "w", encoding="utf-8") as f:
        json.dump(package, f)


def add_ledger(root, need_id, decisions):
    pdir = os.path.join(root, "dispatch", "staged", need_id)
    os.makedirs(pdir, exist_ok=True)
    rows = [
        {"chain_decision": d, "evaluated_at": "2026-10-01T22:%02d:00+00:00" % (10 + i),
         "depth": i}
        for i, d in enumerate(decisions)
    ]
    write_jsonl(os.path.join(pdir, "CHAIN_LEDGER.jsonl"), rows)
    return os.path.join(pdir, "CHAIN_LEDGER.jsonl")


def add_verdict(root, name, ledger_path):
    with open(ledger_path, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()
    path = os.path.join(root, "records", name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# frozen verdict\nledger sha256 %s\n" % h)
    return path


class TestTransitions(unittest.TestCase):
    def test_open_lifecycle(self):
        t = Projector.transition
        self.assertEqual(t(None, "need_created"), "OPEN")
        self.assertEqual(t("OPEN", "staged"), "OPEN")
        self.assertEqual(t("OPEN", "authorized"), "OPEN")
        self.assertEqual(t("OPEN", "dispatched"), "OPEN")
        self.assertEqual(t("OPEN", "continued"), "OPEN")
        self.assertEqual(t("OPEN", "executed"), "OPEN")

    def test_blocked(self):
        t = Projector.transition
        self.assertEqual(t("OPEN", "chain_blocked"), "BLOCKED")
        self.assertEqual(t(None, "blocked"), "BLOCKED")

    def test_terminal(self):
        t = Projector.transition
        for etype in ("chain_satisfied", "closed_satisfied", "frozen_verdict"):
            self.assertEqual(t("OPEN", etype), "SATISFIED")
            self.assertEqual(t("BLOCKED", etype), "SATISFIED")
        self.assertEqual(t("OPEN", "retired"), "RETIRED")

    def test_unknown_type_no_transition(self):
        self.assertEqual(Projector.transition("OPEN", "mystery"), "OPEN")
        self.assertIsNone(Projector.transition(None, "mystery"))


class TestIncidentRegression(unittest.TestCase):
    """The 2026-10-01 shape: STAGED at t0, terminal records at t1.
    Projection must be SATISFIED, never OPEN."""

    def test_staged_then_closed_is_satisfied(self):
        root = make_life0()
        nid = "need_test001"
        write_jsonl(os.path.join(root, "state", "needs.jsonl"), [
            {"event": "NEED_CREATED", "at": "2026-10-01T20:42:53+00:00", "need_id": nid},
            {"event": "NEED_STATUS", "at": "2026-10-01T20:42:54+00:00", "need_id": nid,
             "status": "STAGED_AWAITING_AUTHORIZATION", "decided_by": "pulse"},
        ])
        add_package(root, nid, {
            "need_id": nid,
            "status": "CLOSED",
            "staged_at": "2026-10-01T20:42:53+00:00",
            "authorized_at": "2026-10-01T20:45:22+00:00",
            "operator_decision": {"decision": "AUTHORIZE", "decided_by": "operator"},
            "closure": {"closed_by": "operator", "closed_at": "2026-10-01T21:24:47+00:00"},
        })
        result = Projector(root).project()
        self.assertEqual(result["obligations"][nid]["state"], "SATISFIED")
        self.assertEqual(result["open_obligations"], [])
        self.assertEqual(result["pending_authorization"], [])

    def test_late_staging_cannot_reopen_terminal(self):
        """A STAGED record arriving after the terminal event is an anomaly,
        not a live obligation. This is the reporter's 2026-10-01 mistake."""
        root = make_life0()
        nid = "need_test002"
        write_jsonl(os.path.join(root, "state", "needs.jsonl"), [
            {"event": "NEED_CREATED", "at": "2026-10-01T20:42:53+00:00", "need_id": nid},
        ])
        add_package(root, nid, {
            "need_id": nid,
            "status": "CLOSED",
            "staged_at": "2026-10-01T20:42:53+00:00",
            "closure": {"closed_by": "operator", "closed_at": "2026-10-01T21:24:47+00:00"},
        })
        # A stale/duplicate staging observation recorded AFTER closure.
        write_jsonl(os.path.join(root, "state", "needs.jsonl"), [
            {"event": "NEED_CREATED", "at": "2026-10-01T20:42:53+00:00", "need_id": nid},
            {"event": "NEED_STATUS", "at": "2026-10-01T22:30:00+00:00", "need_id": nid,
             "status": "STAGED_AWAITING_AUTHORIZATION", "decided_by": "pulse"},
        ])
        result = Projector(root).project()
        ob = result["obligations"][nid]
        self.assertEqual(ob["state"], "SATISFIED")
        self.assertEqual(len(ob["anomalies"]), 1)
        self.assertIn("after terminal", ob["anomalies"][0]["note"])

    def test_chain_satisfied_is_terminal(self):
        root = make_life0()
        nid = "need_test003"
        write_jsonl(os.path.join(root, "state", "needs.jsonl"), [
            {"event": "NEED_CREATED", "at": "2026-10-01T22:00:11+00:00", "need_id": nid},
        ])
        add_package(root, nid, {"need_id": nid, "status": "STAGED_AWAITING_AUTHORIZATION",
                                "staged_at": "2026-10-01T22:00:11+00:00"})
        ledger = add_ledger(root, nid, ["CONTINUE_ELIGIBLE", "STOP_NO_PROGRESS",
                                        "STOP_GOAL_SATISFIED"])
        add_verdict(root, "X_RUN_VERDICT_FROZEN.md", ledger)
        # A mission consequence recorded after the chain terminal (normal
        # record-keeping latency) confirms; it must not become an anomaly.
        write_jsonl(os.path.join(root, "state", "consequences.jsonl"), [
            {"mission_id": "life0-%s" % nid, "kind": "internal_analysis",
             "at": "2026-10-01T22:12:47+00:00"},
        ])
        result = Projector(root).project()
        ob = result["obligations"][nid]
        self.assertEqual(ob["state"], "SATISFIED")
        # The frozen verdict arrives after the chain terminal: confirmation, not anomaly.
        self.assertEqual(len(ob["terminal_confirmations"]), 2)
        types = sorted(c["type"] for c in ob["terminal_confirmations"])
        self.assertEqual(types, ["executed", "frozen_verdict"])
        self.assertEqual(ob["anomalies"], [])
        det = ob["determining_event"]
        self.assertEqual(det["type"], "chain_satisfied")

    def test_failed_chain_is_blocked(self):
        root = make_life0()
        nid = "need_test004"
        write_jsonl(os.path.join(root, "state", "needs.jsonl"), [
            {"event": "NEED_CREATED", "at": "2026-10-01T22:00:11+00:00", "need_id": nid},
        ])
        add_package(root, nid, {"need_id": nid, "status": "STAGED_AWAITING_AUTHORIZATION",
                                "staged_at": "2026-10-01T22:00:11+00:00"})
        add_ledger(root, nid, ["CONTINUE_ELIGIBLE", "STOP_NO_PROGRESS"])
        result = Projector(root).project()
        ob = result["obligations"][nid]
        self.assertEqual(ob["state"], "BLOCKED")
        self.assertEqual(result["by_state"]["BLOCKED"], [nid])

    def test_staged_only_is_open_and_pending(self):
        root = make_life0()
        nid = "need_test005"
        write_jsonl(os.path.join(root, "state", "needs.jsonl"), [
            {"event": "NEED_CREATED", "at": "2026-10-01T22:00:11+00:00", "need_id": nid},
            {"event": "NEED_STATUS", "at": "2026-10-01T22:00:12+00:00", "need_id": nid,
             "status": "STAGED_AWAITING_AUTHORIZATION", "decided_by": "pulse"},
        ])
        result = Projector(root).project()
        ob = result["obligations"][nid]
        self.assertEqual(ob["state"], "OPEN")
        self.assertEqual(result["pending_authorization"], [nid])

    def test_retired_is_absorbing(self):
        root = make_life0()
        nid = "need_test006"
        write_jsonl(os.path.join(root, "state", "needs.jsonl"), [
            {"event": "NEED_CREATED", "at": "2026-10-01T20:00:00+00:00", "need_id": nid},
        ])
        write_jsonl(os.path.join(root, "state", "obligation_directives.jsonl"), [
            {"at": "2026-10-01T21:00:00+00:00", "need_id": nid, "directive": "RETIRE"},
        ])
        result = Projector(root).project()
        self.assertEqual(result["obligations"][nid]["state"], "RETIRED")

    def test_unbound_verdict_reported(self):
        root = make_life0()
        path = os.path.join(root, "records", "ORPHAN_RUN_VERDICT_FROZEN.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("ledger sha256 %s\n" % ("0" * 64))
        result = Projector(root).project()
        self.assertEqual(len(result["unbound_verdicts"]), 1)


if __name__ == "__main__":
    unittest.main()
