"""D0 vocabulary: frozen predicate set, hypothesis enumeration, evaluation.

Deterministic, model-free. The vocabulary JSON is the only authorized
hypothesis family in D0-v0 (acquisition-v1). Predicate ops are restricted
to the frozen schema vocabulary {==,!=,>,<,>=,<=,in}; anything else is a
hard error (this closes the 'eq'/'gte' dialect-drift seam: the vocabulary
is substrate-enumerated, never model-emitted).
"""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

ALLOWED_OPS = frozenset({"==", "!=", ">", "<", ">=", "<=", "in"})


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_vocabulary(path: str | Path) -> dict:
    """Load and validate the frozen vocabulary. Fails loudly on any drift."""
    vocab = json.loads(Path(path).read_text())
    preds = vocab["predicates"]
    ids = [p["id"] for p in preds]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate predicate ids in vocabulary")
    if ids != sorted(ids):
        raise ValueError("predicate ids must be in lexicographic enumeration order")
    by_id = {}
    for p in preds:
        if p["op"] not in ALLOWED_OPS:
            raise ValueError(f"predicate {p['id']}: op {p['op']!r} not in frozen vocabulary")
        if not isinstance(p["field"], str) or not p["field"]:
            raise ValueError(f"predicate {p['id']}: bad field")
        by_id[p["id"]] = p
    groups = vocab.get("exclusive_groups", [])
    for g in groups:
        if len(set(g)) != len(g):
            raise ValueError(f"exclusive group {g}: duplicate ids")
        for pid in g:
            if pid not in by_id:
                raise ValueError(f"exclusive group references unknown predicate {pid}")
    k_max = vocab["k_max"]
    if not isinstance(k_max, int) or k_max < 1:
        raise ValueError("k_max must be a positive int")
    vocab["_by_id"] = by_id
    # exclusive pairs as a set of frozensets for O(1) lookup
    pairs = set()
    for g in groups:
        for a, b in itertools.combinations(sorted(g), 2):
            pairs.add(frozenset((a, b)))
    vocab["_exclusive_pairs"] = pairs
    return vocab


def vocabulary_hash(vocab: dict) -> str:
    """Hash of the vocabulary as frozen on disk (excludes runtime caches)."""
    frozen = {k: v for k, v in vocab.items() if not k.startswith("_")}
    return sha256_bytes(canonical_json(frozen))


def eval_predicate(pred: dict, state: dict) -> bool:
    """Evaluate one predicate against a concrete observable state."""
    field = pred["field"]
    if field not in state:
        raise KeyError(f"state missing observable field {field!r}")
    actual = state[field]
    op = pred["op"]
    want = pred["value"]
    if op == "==":
        return actual == want
    if op == "!=":
        return actual != want
    if op == ">":
        return actual > want
    if op == "<":
        return actual < want
    if op == ">=":
        return actual >= want
    if op == "<=":
        return actual <= want
    if op == "in":
        return actual in want
    raise ValueError(f"unreachable op {op!r}")  # load_vocabulary gates this


# --------------------------------------------------------------------------
# hypotheses
# --------------------------------------------------------------------------

def enumerate_hypotheses(vocab: dict) -> list[dict]:
    """All hypotheses in canonical order: [TRUE] + by (size, lex ids) + [NULL].

    Hypothesis dict: {"hid": str, "pids": list[str] | None}.
    NULL has pids None (never fires); TRUE has pids [] (fires everywhere).
    """
    ids = [p["id"] for p in vocab["predicates"]]
    k_max = vocab["k_max"]
    hyps = [{"hid": "TRUE", "pids": []}]
    for size in range(1, k_max + 1):
        for combo in itertools.combinations(ids, size):
            pids = list(combo)  # combinations() already yields lex order
            hyps.append({"hid": "C:" + "|".join(pids), "pids": pids})
    hyps.append({"hid": "NULL", "pids": None})
    return hyps


def fires(hyp: dict, state: dict, vocab: dict) -> bool:
    """Does the trigger hypothesis fire at a concrete state?"""
    pids = hyp["pids"]
    if pids is None:  # NULL
        return False
    by_id = vocab["_by_id"]
    return all(eval_predicate(by_id[pid], state) for pid in pids)


def region_satisfied(region_pids: list[str], state: dict, vocab: dict) -> bool:
    """Does a concrete state satisfy a region (conjunction)?"""
    by_id = vocab["_by_id"]
    return all(eval_predicate(by_id[pid], state) for pid in region_pids)


def prediction_in_region(hyp: dict, region_pids: list[str], vocab: dict) -> str:
    """Three-valued prediction of a hypothesis inside a region.

    FIRES:        C_H ⊆ C_R (fires in every state of the region)
    QUIET:        NULL, or some hypothesis predicate is exclusive with some
                  region predicate (fires in no state of the region)
    UNDETERMINED: otherwise (the region does not decide the hypothesis)
    """
    pids = hyp["pids"]
    if pids is None:
        return "QUIET"
    rset = set(region_pids)
    if set(pids) <= rset:
        return "FIRES"
    pairs = vocab["_exclusive_pairs"]
    for p in pids:
        for q in region_pids:
            if p != q and frozenset((p, q)) in pairs:
                return "QUIET"
    return "UNDETERMINED"


def enumerate_regions(vocab: dict) -> list[dict]:
    """All regions in canonical order (same language as hypotheses)."""
    ids = [p["id"] for p in vocab["predicates"]]
    k_max = vocab["k_max"]
    regions = [{"rid": "R:TRUE", "pids": []}]
    for size in range(1, k_max + 1):
        for combo in itertools.combinations(ids, size):
            pids = list(combo)
            regions.append({"rid": "R:" + "|".join(pids), "pids": pids})
    return regions


def region_readable(region_pids: list[str], vocab: dict) -> str:
    by_id = vocab["_by_id"]
    if not region_pids:
        return "TRUE (whole space)"
    return " AND ".join(by_id[pid]["readable"] for pid in region_pids)


def hypothesis_readable(hyp: dict, vocab: dict) -> str:
    pids = hyp["pids"]
    if pids is None:
        return "NULL: never intervene"
    if not pids:
        return "TRUE: intervene in every state"
    by_id = vocab["_by_id"]
    return "IF " + " AND ".join(by_id[pid]["readable"] for pid in pids) + " THEN INTERVENE"
