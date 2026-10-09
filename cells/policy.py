"""Restricted cell action policy + fail-closed module identity (W3).

Enforcement model (approved plan): the worker shim's ACTION_CONTRACT text is
advisory. Authority lives in validate(), called on every action between parsing
(Action.from_obj) and execution - the same boundary as the organism runner.
"""
from __future__ import annotations

import os
import sys

# Reviewed baseline: GO-1 decision pins unified_machine_v0. Any other tree
# (e.g. the apparatus copy, which additionally permits list_dir) must fail
# closed, never execute silently. The pin is host-configurable via
# RELATIONAL0_UM_ROOT so the reviewed baseline can be named per deployment;
# the fail-closed check below still refuses anything outside the named root.
PINNED_UM_ROOT = os.environ.get(
    "RELATIONAL0_UM_ROOT", "/home/hatch/workspace/unified_machine_v0")

CELL_ACTIONS = frozenset({
    "read_file",
    "write_file",
    "fetch_evidence",
    "stop",
})

_DIGEST_RE = None


def _digest_re():
    global _DIGEST_RE
    if _DIGEST_RE is None:
        import re
        _DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
    return _DIGEST_RE


def verify_um_root(pinned: str = PINNED_UM_ROOT):
    """Fail-closed module identity (binding GO-1 requirement).

    Inserts the pinned root, imports unified_machine, and verifies the loaded
    module actually came from the pinned tree. Raises RuntimeError (no
    execution) on any mismatch. Logging the path alone is insufficient.
    """
    if pinned not in sys.path:
        sys.path.insert(0, pinned)
    # Force re-resolution: a previously imported unified_machine from another
    # tree must not survive.
    for mod in [m for m in sys.modules if m == "unified_machine"
                or m.startswith("unified_machine.")]:
        del sys.modules[mod]
    import unified_machine  # noqa: E402
    loaded = os.path.realpath(getattr(unified_machine, "__file__", ""))
    expected = os.path.realpath(os.path.join(pinned, "unified_machine"))
    if not loaded.startswith(expected + os.sep) and loaded != expected:
        raise RuntimeError(
            f"fail-closed: unified_machine loaded from {loaded}, "
            f"expected under {expected}; refusing to execute")
    # Import submodules explicitly: the package __init__ does not re-export.
    import unified_machine.ledger  # noqa: E402,F401
    import unified_machine.types  # noqa: E402,F401
    import unified_machine.workspace  # noqa: E402,F401
    return unified_machine


def get_policy():
    """CellPolicy bound to the verified baseline's WorkPolicy."""
    um = verify_um_root()
    from unified_machine.policy import WorkPolicy, PolicyError

    class CellPolicy(WorkPolicy):
        """Restricts - never expands - the organism's action set."""

        def validate(self, action) -> None:
            if action.kind not in CELL_ACTIONS:
                raise PolicyError(
                    f"cell action not permitted: {action.kind}")
            if action.kind in ("read_file", "write_file"):
                # Reuse the baseline's own arg checks (path present).
                super().validate(action)
            elif action.kind == "fetch_evidence":
                h = action.args.get("hash")
                if not isinstance(h, str) or not _digest_re().match(h):
                    raise PolicyError(
                        "fetch_evidence requires hash: 64-hex digest")
            elif action.kind == "stop":
                super().validate(action)

    return CellPolicy(), PolicyError
