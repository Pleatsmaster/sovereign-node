"""K/CAF gate + dispatch stager: the authorization boundary, pinned.

The pulse NEVER auto-launches, NEVER invokes a worker, NEVER spends.
The staged package contains no worker command and cannot execute itself.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from life0 import dispatch as dispatch_mod
from life0.dispatch import STATUS_STAGED, stage_mission
from life0.gate import DECISION_DENY, DECISION_STAGE, gate_check
from life0.needs import form_need


def _need(kind="new_file"):
    d = {
        "delta_id": "delta_" + "b" * 32,
        "source": "inbox", "kind": kind, "key": "w.md",
        "previous_state": None,
        "current_state": {"name": "w.md", "sha256": "q" * 64, "bytes": 2},
        "observed_at": "2026-10-01T00:00:00+00:00",
    }
    return form_need(d)


def _gate_ok():
    return {"decision": DECISION_STAGE, "reason": "test", "policy_version": "t"}


def test_gate_allows_clean_need():
    rec = gate_check(_need())
    assert rec["decision"] == DECISION_STAGE


def test_gate_denies_without_delta():
    rec = gate_check({"possible_need": "do something", "priority": "useful opportunity"})
    assert rec["decision"] == DECISION_DENY


def test_gate_denies_forbidden_targets():
    for target in ("namariel-live0-v0.13", "unified_machine_v0", "fact0.db",
                   ".ssh/config", "credential"):
        need = _need()
        need["possible_need"] = f"Please inspect {target} and fix it"
        rec = gate_check(need)
        assert rec["decision"] == DECISION_DENY, target


def test_stage_refuses_denied_gate(tmp_path):
    with pytest.raises(ValueError, match="did not approve"):
        stage_mission(_need(), {"decision": DECISION_DENY, "reason": "x"}, tmp_path)


def test_staged_package_cannot_launch(tmp_path):
    pkg_dir = stage_mission(_need(), _gate_ok(), tmp_path)
    package = json.loads((pkg_dir / "MISSION_PACKAGE.json").read_text())
    # No worker invocation exists in the staged package.
    assert package["worker_command"] is None
    assert "command" not in package
    assert package["launch_authorized"] is False
    assert package["status"] == STATUS_STAGED == "STAGED_AWAITING_AUTHORIZATION"
    # Nothing in the package directory is executable or invokes execution.
    for p in pkg_dir.iterdir():
        text = p.read_text()
        assert "subprocess" not in text
        assert "os.system" not in text
        assert "Popen" not in text
    objective = (pkg_dir / "OBJECTIVE.md").read_text()
    assert "STAGED_AWAITING_AUTHORIZATION" in objective
    assert "explicit operator act" in objective


def test_stage_is_idempotent(tmp_path):
    d1 = stage_mission(_need(), _gate_ok(), tmp_path)
    d2 = stage_mission(_need(), _gate_ok(), tmp_path)
    assert d1 == d2
    assert len(list(tmp_path.iterdir())) == 1


def test_dispatch_module_cannot_execute():
    """Structural pin: the stager module has no execution primitives at all."""
    src = Path(dispatch_mod.__file__).read_text()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert "subprocess" not in imported
    assert "os" not in imported
    assert "socket" not in imported
    assert "urllib" not in imported
    assert "shutil" not in imported


def test_no_frontier_model_imports_anywhere():
    """The pulse makes zero model calls: pin structurally across life0."""
    life0_src = Path(dispatch_mod.__file__).parent
    banned = ("openai", "anthropic", "google.generativeai", "boto3", "requests")
    for py in life0_src.glob("*.py"):
        text = py.read_text()
        for b in banned:
            assert b not in text, f"{py.name} references {b}"
