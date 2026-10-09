"""Build the frozen U2B exemplar registry (Gate B0, no API).

Reads ONLY two frozen inputs:
  1. Pivot file: um-missions/_learning/runs/.state-tr-enumeration-affordance-004-fx1/pivots.jsonl
     (the single record with pivot_id == "tr-enumeration-affordance-004:p01")
  2. Frozen objective: um-missions/_learning/curriculum/episodes/
     tr-enumeration-affordance-004/control/OBJECTIVE.md

Emits: unified_machine/exemplars/registry.json

Deterministic: running this script twice (separate processes) yields
byte-identical output, verified by test_exemplars.py. The script prints the
registry sha256 on stdout (line starting with "REGISTRY_SHA256="); that value
is baked into exemplars/__init__.py's REGISTRY_SHA256 constant after review.

registry_sha256 procedure (documented so a third party can recompute):
  1. Build the document WITHOUT the "registry_sha256" key.
  2. Encode canonical: json.dumps(sort_keys=True, separators=(",",":"),
     ensure_ascii=False) + trailing "\n".
  3. sha256 of those bytes -> hex digest D.
  4. Insert "registry_sha256": D into the document (key placement is
     irrelevant: re-encoding always uses sort_keys=True, so it round-trips).
  5. Write the canonical encoding of the final document (WITH the key) +
     trailing newline to registry.json.

Run: python3 unified_machine/exemplars/build_registry.py   (from repo root)
     or: python3 build_registry.py                          (from this directory)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

PIVOT_FILE = Path(
    "/home/hatch/workspace/um-missions/_learning/runs"
    "/.state-tr-enumeration-affordance-004-fx1/pivots.jsonl"
)
OBJECTIVE_FILE = Path(
    "/home/hatch/workspace/um-missions/_learning/curriculum/episodes"
    "/tr-enumeration-affordance-004/control/OBJECTIVE.md"
)
WANT_PIVOT_ID = "tr-enumeration-affordance-004:p01"
EXPECTED_SIGNATURE = {
    "demand": "enumerate_directory_listing",
    "phase": "initial",
    "acceptance_configured": True,
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj) -> bytes:
    """Canonical JSON bytes: sorted keys, tight separators, utf-8, LF."""
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


# Frozen demand rules v1 -- MUST stay identical to unified_machine/exemplars/__init__.py.
# (Duplicated verbatim per the U2B builder pattern; test_demand_generalization.py
# asserts the two copies agree on every frozen matrix objective.)
DEMAND_RULES_VERSION = "1"

_ENUMERATE_GROUPS = (
    (
        "every file under",
        "all files under",
        "each file under",
        "every file in",
        "all files in",
        "list all files",
        "list of all files",
        "complete list of",
        "full list of",
        "every filename",
        "all filenames",
        "file manifest",
        "manifest of",
        "file inventory",
        "inventory of",
    ),
    (
        "one per line",
        "one file per line",
        "each on its own line",
        "each file on its own line",
        "each path on its own line",
        "one line per",
        "line-separated",
        "newline-separated",
        "separated by newlines",
    ),
)

_DEMAND_RULES = (("enumerate_directory_listing", _ENUMERATE_GROUPS),)


def classify_demand(objective_text: str) -> str:
    low = _normalize(objective_text)
    for label, groups in _DEMAND_RULES:
        if all(any(phrase in low for phrase in group) for group in groups):
            return label
    return "other"


def precondition_signature(objective_text, step, history, acceptance_commands):
    """Frozen signature rule (demand rules v1; phase/acceptance UNCHANGED from U2B)."""
    demand = classify_demand(objective_text)
    phase = "initial" if (step == 1 and history == []) else "later"
    return {
        "demand": demand,
        "phase": phase,
        "acceptance_configured": bool(acceptance_commands),
    }


def find_pivot(pivot_file: Path) -> dict:
    records = []
    with pivot_file.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    hits = [r for r in records if r.get("pivot_id") == WANT_PIVOT_ID]
    if len(hits) != 1:
        raise ValueError(f"expected exactly 1 record with pivot_id {WANT_PIVOT_ID!r}, found {len(hits)}")
    return hits[0]


def build_registry() -> tuple[dict, str]:
    if not PIVOT_FILE.exists():
        raise ValueError(f"pivot file missing: {PIVOT_FILE}")
    if not OBJECTIVE_FILE.exists():
        raise ValueError(f"objective file missing: {OBJECTIVE_FILE}")
    pivot_bytes = PIVOT_FILE.read_bytes()

    pivot = find_pivot(PIVOT_FILE)
    effects = pivot["effect"]["functional_effects"]
    if effects != ["ACQUIRE_COMPLETE_SOURCE_SET"]:
        raise ValueError(f"pivot {WANT_PIVOT_ID} functional_effects != ['ACQUIRE_COMPLETE_SOURCE_SET']: {effects}")

    objective_text = OBJECTIVE_FILE.read_text(encoding="utf-8")
    signature = precondition_signature(
        objective_text,
        step=1,
        history=[],
        acceptance_commands=[["acceptance-check"]],  # non-empty: acceptance is configured
    )
    if signature != EXPECTED_SIGNATURE:
        raise ValueError(
            f"derived precondition signature != expected:\n  got {signature}\n  want {EXPECTED_SIGNATURE}"
        )

    pivot_file_sha = sha256_bytes(pivot_bytes)
    pivot_record_sha = sha256_bytes(canonical(pivot))
    objective_sha = sha256_bytes(objective_text.encode("utf-8"))

    raw = pivot["action"]["raw"]
    instance = {
        "id": "ex-enum-004-p01",
        "observed_state": signature,
        "action": {
            "kind": raw["kind"],
            "args": raw["args"],
            "rationale_verbatim": raw["rationale"],
        },
        "observed_effect": "ACQUIRE_COMPLETE_SOURCE_SET",
        "cost_vector": {
            "result_chars": pivot["effect"]["cost"]["result_chars"],
            "acts_consumed": pivot["effect"]["cost"]["acts_consumed"],
        },
        "provenance": {
            "pivot_id": WANT_PIVOT_ID,
            "pivot_file": str(PIVOT_FILE),
            "pivot_file_sha256": pivot_file_sha,
            "pivot_record_sha256": pivot_record_sha,
        },
    }
    # Verbatim cross-checks against the frozen record (mechanical, no interpretation).
    assert instance["action"]["kind"] == "run", "kind must be the pivot's verbatim kind"
    assert instance["action"]["args"] == {"argv": ["bash", "-lc", "find sources -type f | sort"]}
    assert instance["action"]["rationale_verbatim"] == (
        "Enumerate every file under sources/ to build a complete manifest."
    )
    assert instance["cost_vector"] == {"result_chars": 4686, "acts_consumed": 1}

    doc = {
        "registry_version": "0",
        "built_from": {
            "pivot_file": str(PIVOT_FILE),
            "pivot_file_sha256": pivot_file_sha,
            "pivot_record_sha256": pivot_record_sha,
            "objective_file": str(OBJECTIVE_FILE),
            "objective_file_sha256": objective_sha,
        },
        "instances": [instance],
    }
    registry_sha = sha256_bytes(canonical(doc))  # WITHOUT the registry_sha256 key itself
    doc["registry_sha256"] = registry_sha
    return doc, registry_sha


def main() -> None:
    doc, registry_sha = build_registry()
    out = HERE / "registry.json"
    out.write_bytes(canonical(doc))
    print(f"REGISTRY_SHA256={registry_sha}")
    print(f"WROTE {out}")


if __name__ == "__main__":
    main()
