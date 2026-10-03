#!/usr/bin/env python3
"""COMMITMENT-EXECUTION-0: deterministic obligation selection.

Among all needs, selects the single obligation eligible for immediate
execution: projecting OPEN under OBLIGATION-0 AND passing the
COMMITMENT-EXECUTION-0 eligibility check. Selection order is mechanical:
priority rank (life0.needs.PRIORITIES), then earliest created_at.

Read-only: makes zero model calls, spends nothing, launches nothing,
writes nothing. Selection reports; execution remains an act.

Exit codes: 0 = selected (or none eligible — see verdict); 2 = failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "src"))
sys.path.insert(0, str(LIFE0_ROOT / "scripts"))

from life0.needs import PRIORITIES  # noqa: E402
from project_obligations import Projector  # noqa: E402
import execution_eligibility as elig  # noqa: E402

POLICY_VERSION = "commitment-execution-0"


def select(life0: Path) -> dict:
    proj = Projector(str(life0)).project()
    rank = {p: i for i, p in enumerate(PRIORITIES)}

    candidates = []
    for need_id in proj["open_obligations"]:
        verdict = elig.check(life0, need_id)
        if not verdict.get("eligible"):
            continue
        need = elig.find_need(life0, need_id) or {}
        candidates.append({
            "need_id": need_id,
            "commitment_id": verdict["commitment_id"],
            "authority_class": verdict["authority_class"],
            "priority": need.get("priority", ""),
            "created_at": need.get("created_at", ""),
        })
    candidates.sort(key=lambda c: (rank.get(c["priority"], 99), c["created_at"]))

    if not candidates:
        return {"selected": None, "verdict": "NO_ELIGIBLE_OBLIGATION",
                "policy_version": POLICY_VERSION}
    return {"selected": candidates[0],
            "verdict": "SELECTED",
            "considered": len(candidates),
            "policy_version": POLICY_VERSION}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Deterministically select the next eligible obligation.")
    ap.add_argument("--life0", default=str(LIFE0_ROOT))
    args = ap.parse_args(argv)
    try:
        result = select(Path(args.life0))
    except Exception as e:
        print(json.dumps({"selected": None, "verdict": "SELECTION_FAILED",
                          "error": str(e)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
