"""U2C deterministic exemplar registry / exact-match retrieval (Gate B generalization).

One causal edge: packet -> precondition signature -> (EXEMPLAR | NO_EXEMPLAR).
No LLM, no network, no ledger writes. The retrieved procedure is injected
packet-only as "acquired_procedure" and NEVER executed directly; the worker
still authors every action.

Generalization vs U2B (Gate B0): retrieval is STILL exact-match on the
signature. What generalized is signature *construction* -- specifically the
demand classifier below (DEMAND_RULES_VERSION "1"), a frozen, versioned,
deterministic pure function of (objective_text, step, history,
acceptance_commands) that abstracts over surface phrasing via documented
frozen phrase/feature sets. The substrate owns the labels, the phrase sets,
and the rule; no model chooses anything.

Tamper-evidence: registry.json is verified against REGISTRY_SHA256 (baked in
at build time, mirroring the D0 FROZEN_* precedent in developmental/hook.py).
The stored experience is UNCHANGED from U2B (same instance, same hash); only
the classification lens changed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REGISTRY_PATH = Path(__file__).resolve().parent / "registry.json"
REGISTRY_SHA256 = "5095158b93f243e535e7a11c662a55538b67aff8b09df0c616d76593d8f07564"

# ---------------------------------------------------------------------------
# Frozen demand rules v1 (the generalized classifier).
#
# A demand rule is a conjunction of phrase groups: EVERY group must have at
# least one of its phrases present (case-insensitive substring match on
# whitespace-collapsed text) for the demand label to fire. Demands are tried
# in _DEMAND_RULES order; the first firing rule wins; otherwise "other".
#
# Rationale for the conjunction shape: "enumerate a directory listing" is a
# (demand, form) pair -- a complete file listing (group 1) delivered
# line-delimited (group 2). Either group alone overfires (e.g. "count the
# files" has neither; "list all files ... in a table" has group 1 only;
# "reasons, one per line" has group 2 only -- see known boundary below).
#
# Phrase selection discipline: phrases are substrate-chosen, documented here,
# frozen with the version. "one path per line" is DELIBERATELY excluded:
# the frozen B0 perturbation test replaces "one per line" with
# "several lines each" while "One path per line" remains in the objective,
# and that perturbed objective must stay NO_EXEMPLAR (backward compat).
#
# Known boundary (documented, not fixed in v1): an objective demanding
# line-delimited NON-listing content (e.g. "a complete list of reasons, one
# per line") fires the enumeration demand. v1 keys on surface form, not on
# the listing target being files; tightening that is future work.
# ---------------------------------------------------------------------------

DEMAND_RULES_VERSION = "1"

_ENUMERATE_GROUPS: Tuple[Tuple[str, ...], ...] = (
    (  # group 1: the demand -- a complete file listing
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
    (  # group 2: the form -- line-delimited output
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

_DEMAND_RULES: Tuple[Tuple[str, Tuple[Tuple[str, ...], ...]], ...] = (
    ("enumerate_directory_listing", _ENUMERATE_GROUPS),
)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def classify_demand(objective_text: str) -> str:
    """Frozen deterministic demand classifier (rules v1). Pure function."""
    low = _normalize(objective_text)
    for label, groups in _DEMAND_RULES:
        if all(any(phrase in low for phrase in group) for group in groups):
            return label
    return "other"

_REGISTRY_CACHE: Optional[Dict[str, Any]] = None


def precondition_signature(objective_text, step, history, acceptance_commands):
    """Frozen signature rule: demand via the v1 classifier; phase and
    acceptance_configured semantics UNCHANGED from U2B."""
    demand = classify_demand(objective_text)
    phase = "initial" if (step == 1 and history == []) else "later"
    return {
        "demand": demand,
        "phase": phase,
        "acceptance_configured": bool(acceptance_commands),
    }


def signature_from_packet(packet: Dict[str, Any]) -> Dict[str, Any]:
    """Derive the precondition signature from a MissionRunner packet.

    packet["mission"] carries objective / step / acceptance_commands;
    packet["history"] is the action history (empty at step 1).
    """
    mission = packet["mission"]
    return precondition_signature(
        objective_text=mission["objective"],
        step=mission["step"],
        history=packet["history"],
        acceptance_commands=mission["acceptance_commands"],
    )


def load_registry() -> Dict[str, Any]:
    """Read registry.json, verify against REGISTRY_SHA256, return it.

    REGISTRY_SHA256 is defined over the canonical bytes of the document
    WITHOUT the "registry_sha256" key itself (see build_registry.py). The
    check therefore: (1) recompute that hash from the parsed document with
    the key stripped and compare to the constant; (2) confirm the document's
    embedded "registry_sha256" agrees with the constant. Any mismatch raises:
    the registry is tamper-evident. Within-process caching only; a new
    process always re-reads and re-verifies.
    """
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    raw = REGISTRY_PATH.read_bytes()
    registry = json.loads(raw.decode("utf-8"))
    if registry.get("registry_sha256") != REGISTRY_SHA256:
        raise ValueError(
            "registry document's embedded registry_sha256 does not match REGISTRY_SHA256"
        )
    doc_without_key = {k: v for k, v in registry.items() if k != "registry_sha256"}
    recomputed = hashlib.sha256(
        (
            json.dumps(doc_without_key, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
    ).hexdigest()
    if recomputed != REGISTRY_SHA256:
        raise ValueError(
            f"exemplar registry tampered or rebuilt without updating REGISTRY_SHA256: "
            f"got {recomputed[:16]}..., expected {REGISTRY_SHA256[:16]}..."
        )
    instances = registry.get("instances", [])
    if len(instances) != 1:
        raise ValueError(f"Gate B0 registry must contain exactly one instance, found {len(instances)}")
    _REGISTRY_CACHE = registry
    return registry


def retrieve(packet: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Exact-match lookup: ("EXEMPLAR", instance) or ("NO_EXEMPLAR", None).

    Pure function: no I/O except the verified registry load, no LLM.
    Match = all three signature fields equal the stored instance's observed_state.
    """
    signature = signature_from_packet(packet)
    registry = load_registry()
    instance = registry["instances"][0]
    if signature == instance["observed_state"]:
        return "EXEMPLAR", instance
    return "NO_EXEMPLAR", None


def _acceptance_bool(signature: Dict[str, Any]) -> str:
    return "true" if signature["acceptance_configured"] else "false"


def injection_block(instance: Dict[str, Any]) -> str:
    """Render the exact frozen injection block for a matched instance."""
    sig = instance["observed_state"]
    action_json = json.dumps(
        {"kind": instance["action"]["kind"], "args": instance["action"]["args"]}
    )
    lines = [
        "ACQUIRED PROCEDURE FROM PRIOR EXPERIENCE",
        (
            f"Observed state: demand={sig['demand']}, phase={sig['phase']}, "
            f"acceptance_configured={_acceptance_bool(sig)}"
        ),
        f"Previously successful action: {action_json}",
        f"Action rationale (verbatim, from prior worker): {instance['action']['rationale_verbatim']}",
        (
            f"Observed effect: {instance['observed_effect']} "
            f"(coverage 0.0 -> 1.0 in a single act; "
            f"{instance['cost_vector']['result_chars']} result chars)"
        ),
        f"Provenance: {instance['provenance']['pivot_id']} (registry sha256 {REGISTRY_SHA256})",
    ]
    return "\n".join(lines)


def retrieve_into_packet(packet: Dict[str, Any]) -> Dict[str, Any]:
    """Packet-only injection.

    On NO_EXEMPLAR: returns the packet object UNCHANGED (same object).
    On EXEMPLAR: returns a shallow-copied dict with exactly one added
    top-level key, "acquired_procedure". Everything else identical.
    """
    retrieval, instance = retrieve(packet)
    if retrieval != "EXEMPLAR" or instance is None:
        return packet
    out = dict(packet)
    out["acquired_procedure"] = {
        "retrieval": "EXEMPLAR",
        "exemplar_id": instance["id"],
        "registry_sha256": REGISTRY_SHA256,
        "block": injection_block(instance),
    }
    return out
