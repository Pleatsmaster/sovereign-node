"""TRAINING_REQUEST: machine-emitted, mechanically verifiable experience request.

A request is VALID only if the deterministic validator can reconstruct the
disagreement from the referenced developmental state. Otherwise:

    REQUEST_INVALID

No model-written rationale is used or needed. The request never launches
itself; launching remains an explicit operator act.
"""

from __future__ import annotations

from . import ledger as L
from . import vocabulary as V
from . import version_space as VS


class RequestInvalid(Exception):
    pass


def _proof_hash(region_rid: str, hypothesis_ids: list[str], predictions: dict,
                 version_space_hash: str) -> str:
    return L.sha256_text(L.dump_canonical({
        "region_rid": region_rid,
        "hypothesis_ids": list(hypothesis_ids),
        "predictions": dict(predictions),
        "version_space_hash": version_space_hash,
    }))


def _survivor_objects(records: list[dict]) -> list[dict]:
    hyps = []
    for rec in records:
        pids = rec["predicates"]
        hyps.append({"hid": rec["hid"], "pids": list(pids) if pids is not None else None})
    return hyps


def build_request(
    materialization: dict,
    vocab: dict,
    evidence_list: list[dict],
) -> dict | None:
    """Emit a TRAINING_REQUEST from developmental state. None if no disagreement."""
    state = materialization["state"]
    survivors = _survivor_objects(materialization["survivors"])
    dis = VS.find_disagreement(survivors, evidence_list, vocab)
    if dis is None:
        return None
    region = dis["region"]
    vocab_hash = V.vocabulary_hash(vocab)
    proof = _proof_hash(region["rid"], dis["hypothesis_ids"], dis["predictions"],
                        state["version_space_hash"])
    request = {
        "family": vocab["vocabulary_id"],
        "developmental_state_hash": state["state_hash"],
        "developmental_tag": state["developmental_tag"],
        "disagreement": {
            "hypothesis_ids": list(dis["hypothesis_ids"]),
            "region": {
                "rid": region["rid"],
                "predicates": list(region["pids"]),
                "readable": dis["region_readable"],
            },
            "predictions": dict(dis["predictions"]),
        },
        "reachability": {
            "ordinary_work": {"status": dis["ordinary_work"]},
            "capsule": {
                "status": "not_constructible",
                "reason": "v0 retrospective performs no capsule construction",
                "spec": None,
            },
        },
        "derivation": {
            "vocabulary_hash": vocab_hash,
            "version_space_hash": state["version_space_hash"],
            "disagreement_proof_hash": proof,
        },
    }
    request["request_id"] = L.sha256_text(L.dump_canonical(request))
    return request


def validate_request(
    request: dict,
    materialization_dir,
    vocab: dict,
    evidence_list: list[dict],
) -> str:
    """Reconstruct the disagreement from the referenced state. Raise if impossible."""
    loaded = L.load_materialization(materialization_dir)  # tamper-checked
    state = loaded["state"]

    def bad(reason: str) -> RequestInvalid:
        return RequestInvalid(f"REQUEST_INVALID: {reason}")

    if request.get("developmental_state_hash") != state.get("state_hash"):
        raise bad("developmental_state_hash does not match materialized state")
    if request.get("family") != vocab["vocabulary_id"]:
        raise bad("family mismatch")
    deriv = request.get("derivation", {})
    if deriv.get("vocabulary_hash") != V.vocabulary_hash(vocab):
        raise bad("vocabulary_hash mismatch")
    if deriv.get("version_space_hash") != state.get("version_space_hash"):
        raise bad("version_space_hash mismatch")

    survivors = _survivor_objects(loaded["survivors"])
    dis = VS.find_disagreement(survivors, evidence_list, vocab)
    if dis is None:
        raise bad("no disagreement reconstructible from referenced state")
    req_dis = request.get("disagreement", {})
    if req_dis.get("region", {}).get("rid") != dis["region"]["rid"]:
        raise bad("region does not match reconstructed disagreement")
    if list(req_dis.get("hypothesis_ids", [])) != list(dis["hypothesis_ids"]):
        raise bad("hypothesis_ids do not match reconstructed disagreement")
    if dict(req_dis.get("predictions", {})) != dict(dis["predictions"]):
        raise bad("predictions do not match reconstructed disagreement")

    expect_proof = _proof_hash(dis["region"]["rid"], dis["hypothesis_ids"],
                               dis["predictions"], state["version_space_hash"])
    if deriv.get("disagreement_proof_hash") != expect_proof:
        raise bad("disagreement_proof_hash mismatch")

    reach = request.get("reachability", {})
    ow = reach.get("ordinary_work", {}).get("status")
    if ow not in ("observed", "unknown"):
        raise bad(f"ordinary_work status {ow!r} not permitted in v0")
    expect_ow = ("observed"
                 if VS.region_observed_in_history(dis["region"]["pids"], evidence_list, vocab)
                 else "unknown")
    if ow != expect_ow:
        raise bad("ordinary_work reachability does not match evidence")
    cap = reach.get("capsule", {})
    if cap.get("status") != "not_constructible":
        raise bad("v0 performs no capsule construction; status must be not_constructible")

    body = {k: v for k, v in request.items() if k != "request_id"}
    if request.get("request_id") != L.sha256_text(L.dump_canonical(body)):
        raise bad("request_id mismatch")

    # hypothesis ids must be surviving hypotheses of the referenced state
    known = {r["hid"] for r in loaded["survivors"]}
    for hid in req_dis.get("hypothesis_ids", []):
        if hid not in known:
            raise bad(f"hypothesis {hid} not in referenced version space")
    return "REQUEST_VALID"
