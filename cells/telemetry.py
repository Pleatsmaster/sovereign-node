"""Minimal verification-cost telemetry (W8).

Per exchange attempt: rejection predicate (or "none"), number of predicate
checks, elapsed verification time. No optimization algorithm.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Tuple


def timed_attempt(fn: Callable[[], Tuple[bool, List[str]]],
                  label: str) -> Dict[str, Any]:
    t0 = time.time()
    ok, reasons = fn()
    elapsed = time.time() - t0
    failed = [r for r in reasons if "FAIL" in r]
    return {
        "label": label,
        "permitted": ok,
        "rejection_predicate": (failed[0].split(":")[0] if failed else "none"),
        "checks": len(reasons),
        "elapsed_seconds": round(elapsed, 6),
        "reasons": reasons,
    }
