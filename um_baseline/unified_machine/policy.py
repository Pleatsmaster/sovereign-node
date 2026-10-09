from __future__ import annotations

from pathlib import PurePosixPath
from typing import Iterable, List, Set

from .types import Action


WORK_ACTIONS: Set[str] = {
    "read_file",
    "search",
    "run",
    "write_file",
    "apply_patch",
    "git_diff",
    "status",
    "stop",
}


class PolicyError(ValueError):
    pass


class WorkPolicy:
    def validate(self, action: Action) -> None:
        if action.kind not in WORK_ACTIONS:
            raise PolicyError(f"unsupported action kind: {action.kind}")
        if action.kind in {"read_file", "write_file"}:
            p = action.args.get("path")
            if not isinstance(p, str) or not p:
                raise PolicyError(f"{action.kind} requires path")
        if action.kind == "run":
            argv = action.args.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                raise PolicyError("run requires argv: list[str]")
        if action.kind == "apply_patch":
            if not isinstance(action.args.get("patch"), str):
                raise PolicyError("apply_patch requires patch string")
        if action.kind == "stop":
            status = action.args.get("status", "complete")
            if status not in {"complete", "blocked", "defer"}:
                raise PolicyError("stop.status must be complete|blocked|defer")


IMMUTABLE_SELF_PATHS = {
    "unified_machine/ledger.py",
    "unified_machine/evaluator.py",
    "unified_machine/lineage.py",
    "tests",
    "heldout",
    ".github",
}


class SelfChangePolicy:
    """Frozen evaluator/lineage floor; mutable research machinery above it."""

    def __init__(self, immutable: Iterable[str] = IMMUTABLE_SELF_PATHS):
        self.immutable = tuple(str(PurePosixPath(x)) for x in immutable)

    def validate_touched_paths(self, paths: Iterable[str]) -> None:
        for raw in paths:
            p = str(PurePosixPath(raw))
            if p.startswith("../") or p.startswith("/"):
                raise PolicyError(f"candidate path escapes repo: {raw}")
            for frozen in self.immutable:
                if p == frozen or p.startswith(frozen.rstrip("/") + "/"):
                    raise PolicyError(f"candidate touches frozen path: {p}")
