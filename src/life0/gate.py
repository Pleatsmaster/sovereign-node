"""LIFE-0 K/CAF gate: policy check before a need may be staged.

What the gate decides: whether a need's proposed work is within what the
organism may do (standing obligations, forbidden targets). What the gate does
NOT decide: whether the mission launches. Launch is an explicit operator act,
always. The standing rule — no mission launches, no API spend, no worker
invocation without Stephan's explicit authorization — is NOT lifted by this
gate and is pinned by tests.

v0 policy is deliberately mechanical and narrow. Refinement needs pressure.
"""
from __future__ import annotations

from typing import Any

from .needs import PRIORITIES

GATE_POLICY_VERSION = "life0-gate-v0"

# Substrate paths and capabilities the organism may never be tasked against.
FORBIDDEN_TARGETS = (
    "namariel-live0-v0.13",      # frozen LIVE-0 floor
    "unified_machine_v0",        # dirty lineage checkout: never touch
    "fact0.db",                  # evidence store is read-only to the pulse
    ".ssh", ".aws", ".gnupg",    # credentials live here; never task against
    "id_ed25519", "id_rsa",
    "credential", "secret", "token", "password",
)

DECISION_STAGE = "STAGE"
DECISION_DENY = "DENY"


def gate_check(need: dict[str, Any]) -> dict[str, Any]:
    """Return {decision, reason, policy_version}. Pure function."""
    if not isinstance(need, dict) or not need.get("delta_id"):
        return _deny("need carries no observed delta reference")
    if need.get("priority") not in PRIORITIES:
        return _deny(f"unknown priority class: {need.get('priority')!r}")
    text = (need.get("possible_need") or "").lower()
    for target in FORBIDDEN_TARGETS:
        if target in text:
            return _deny(f"proposed work references forbidden target: {target}")
    return {
        "decision": DECISION_STAGE,
        "reason": (
            f"need {need['need_id']} references observed delta "
            f"{need['delta_id']}; priority '{need['priority']}' within "
            "standing obligations; no forbidden targets."
        ),
        "policy_version": GATE_POLICY_VERSION,
    }


def _deny(reason: str) -> dict[str, Any]:
    return {"decision": DECISION_DENY, "reason": reason,
            "policy_version": GATE_POLICY_VERSION}
