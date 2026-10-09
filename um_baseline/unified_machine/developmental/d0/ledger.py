"""D0 developmental ledger: deterministic materialized view D_t = f(H_t, C_t, V).

Materialization (under <root>/developmental/):
  state.json         -- configuration, counts, hashes; state_hash identifies D_t
  version_space.jsonl-- one record per SURVIVING hypothesis, canonical order
  history.jsonl      -- transition records incl. initial construction

Every record carries source identity (vocabulary hash, evidence source
hashes, d0 code hash) sufficient to prove it came from f(H,C,V).

Anti-tamper: load_materialization() recomputes all hashes and refuses to
build on modified state. There is no manually editable developmental truth.

Incremental update is an optimization only: update() from D_i plus new
evidence must byte-match a full rebuild() from EMPTY over all evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import vocabulary as V
from . import version_space as VS

D0_VERSION = "0"


def dump_canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def d0_code_hash() -> str:
    """Content hash of the d0 organ code itself (part of f)."""
    d0_dir = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(d0_dir.glob("*.py")):
        h.update(p.name.encode("utf-8"))
        h.update(b"\x00")
        h.update(p.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()


def source_identity(vocab: dict, vocab_hash: str, evidence_source_hashes: list[str]) -> dict:
    return {
        "vocabulary_id": vocab["vocabulary_id"],
        "vocabulary_version": vocab["version"],
        "vocabulary_hash": vocab_hash,
        "evidence_source_hashes": sorted(evidence_source_hashes),
        "d0_code_hash": d0_code_hash(),
        "d0_version": D0_VERSION,
    }


def _history_record_initial(vocab, vocab_hash, evidence_source_hashes, n_initial) -> dict:
    # Pure function of (vocabulary, code): f(EMPTY, EMPTY, V). It must NOT
    # embed the evidence list -- otherwise a partial build's initial record
    # could never byte-match a full rebuild's, and incremental update could
    # not be a pure optimization. The evidence application order is the
    # admission-record sequence (record_seq order) plus state.json evidence_ids.
    return {
        "record_seq": 0,
        "transition": "initial_construction",
        "from": "EMPTY",
        "to": "D0",
        "steps": [
            "vocabulary_enumeration",
            "initial_version_space",
            "evidence_application_order",
        ],
        "predicate_enumeration_order": [p["id"] for p in vocab["predicates"]],
        "conjunction_enumeration_order": "TRUE first; then by (size, lexicographic predicate-id tuple); NULL last",
        "pruning_canonicalization": "sorted predicate-id tuples; no other pruning in v0",
        "k_max": vocab["k_max"],
        "consistency_rule": vocab["consistency_rule"]["name"],
        "consistency_rule_version": vocab["consistency_rule"]["version"],
        "initial_hypothesis_count": n_initial,
        "source_identity": source_identity(vocab, vocab_hash, evidence_source_hashes),
    }


def _history_record_admission(seq, from_tag, to_tag, evidence, eliminated, n_retained,
                              vocab, vocab_hash, evidence_source_hashes) -> dict:
    return {
        "record_seq": seq,
        "transition": "experience_admitted",
        "from": from_tag,
        "to": to_tag,
        "experience": evidence["evidence_id"],
        "observed_effect": list(evidence.get("observed_effect") or []),
        "cost": evidence.get("cost"),
        "eliminated": [{"hid": e["hid"], "eliminated_by": e["eliminated_by"]} for e in eliminated],
        "n_eliminated_this_step": len(eliminated),
        "n_retained": n_retained,
        "source_identity": source_identity(vocab, vocab_hash, evidence_source_hashes),
    }


def _version_space_lines(survivors: list[dict], vocab: dict) -> str:
    lines = []
    for h in survivors:
        lines.append(dump_canonical({
            "hid": h["hid"],
            "predicates": list(h["pids"]) if h["pids"] is not None else None,
            "readable": V.hypothesis_readable(h, vocab),
        }))
    return "\n".join(lines) + "\n"


def _write_materialization(out_dir: Path, state: dict, vs_text: str, history: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    vs_hash = sha256_text(vs_text)
    history_text = "\n".join(dump_canonical(r) for r in history) + "\n"
    history_hash = sha256_text(history_text)
    state = dict(state)
    state["version_space_hash"] = vs_hash
    state["history_hash"] = history_hash
    state["state_hash"] = sha256_text(dump_canonical({k: v for k, v in state.items() if k != "state_hash"}))
    (out_dir / "version_space.jsonl").write_text(vs_text)
    (out_dir / "history.jsonl").write_text(history_text)
    (out_dir / "state.json").write_text(dump_canonical(state) + "\n")


def build(
    evidence_list: list[dict],
    vocab: dict,
    vocab_path: str | Path,
    evidence_source_paths: list[str | Path],
    out_dir: str | Path,
) -> dict:
    """Full rebuild from EMPTY. Returns the state dict (with state_hash)."""
    out_dir = Path(out_dir)
    vocab_hash = V.vocabulary_hash(vocab)
    src_hashes = [sha256_file(Path(p)) for p in evidence_source_paths]
    evidence = VS.canonical_evidence_order(evidence_list)
    evidence_ids = [e["evidence_id"] for e in evidence]

    hyps = VS.initial_space(vocab)
    history = [_history_record_initial(vocab, vocab_hash, src_hashes, len(hyps))]

    survivors = hyps
    seq = 1
    for i, ev in enumerate(evidence):
        survivors, eliminated = VS.apply_evidence(survivors, [ev], vocab)
        history.append(_history_record_admission(
            seq, f"D{i}", f"D{i+1}", ev, eliminated, len(survivors),
            vocab, vocab_hash, src_hashes,
        ))
        seq += 1

    state = {
        "d0_version": D0_VERSION,
        "family": vocab["vocabulary_id"],
        "k_max": vocab["k_max"],
        "vocabulary_id": vocab["vocabulary_id"],
        "vocabulary_version": vocab["version"],
        "vocabulary_hash": vocab_hash,
        "vocabulary_path": str(vocab_path),
        "evidence_source_files": [
            {"path": str(p), "sha256": sha256_file(Path(p))} for p in evidence_source_paths
        ],
        "evidence_ids": evidence_ids,
        "n_evidence": len(evidence),
        "n_initial_hypotheses": len(hyps),
        "n_surviving": len(survivors),
        "n_eliminated": len(hyps) - len(survivors),
        "developmental_tag": f"D{len(evidence)}",
    }
    _write_materialization(out_dir, state, _version_space_lines(survivors, vocab), history)
    return load_materialization(out_dir)["state"]


def load_materialization(out_dir: str | Path) -> dict:
    """Load and verify. Raises on any tamper or inconsistency."""
    out_dir = Path(out_dir)
    state_text = (out_dir / "state.json").read_text()
    vs_text = (out_dir / "version_space.jsonl").read_text()
    history_text = (out_dir / "history.jsonl").read_text()
    state = json.loads(state_text)

    expect_vs = sha256_text(vs_text)
    if state.get("version_space_hash") != expect_vs:
        raise ValueError("TAMPER: version_space.jsonl hash mismatch")
    expect_hist = sha256_text(history_text)
    if state.get("history_hash") != expect_hist:
        raise ValueError("TAMPER: history.jsonl hash mismatch")
    expect_state = sha256_text(dump_canonical({k: v for k, v in state.items() if k != "state_hash"}))
    if state.get("state_hash") != expect_state:
        raise ValueError("TAMPER: state.json state_hash mismatch")

    history = [json.loads(line) for line in history_text.splitlines() if line.strip()]
    survivors = [json.loads(line) for line in vs_text.splitlines() if line.strip()]
    seqs = [r["record_seq"] for r in history]
    if seqs != list(range(len(history))):
        raise ValueError("history record_seq not contiguous from 0")
    if len(survivors) != state.get("n_surviving"):
        raise ValueError("survivor count mismatch vs state.json")
    return {"state": state, "survivors": survivors, "history": history}


def update(
    out_dir: str | Path,
    new_evidence_list: list[dict],
    vocab: dict,
    vocab_path: str | Path,
    evidence_source_paths: list[str | Path],
) -> dict:
    """Incremental update: load verified D_i, admit new evidence -> D_j.

    New evidence ids must be unseen. Result must byte-match build() over the
    full evidence set (enforced by tests, not trusted).
    """
    out_dir = Path(out_dir)
    loaded = load_materialization(out_dir)  # verifies; raises on tamper
    state, history = loaded["state"], loaded["history"]
    vocab_hash = V.vocabulary_hash(vocab)
    if state.get("vocabulary_hash") != vocab_hash:
        raise ValueError("vocabulary changed under an existing materialization")

    known_ids = set(state.get("evidence_ids", []))
    new_ids = [e["evidence_id"] for e in new_evidence_list]
    if len(set(new_ids)) != len(new_ids) or (known_ids & set(new_ids)):
        raise ValueError("new evidence must carry unseen, unique evidence_ids")

    # reconstruct survivor hypothesis objects from the materialized records
    survivors = []
    for rec in loaded["survivors"]:
        pids = rec["predicates"]
        survivors.append({"hid": rec["hid"], "pids": list(pids) if pids is not None else None})

    src_hashes = [sha256_file(Path(p)) for p in evidence_source_paths]
    evidence = VS.canonical_evidence_order(new_evidence_list)
    seq = len(history)
    step_base = len(known_ids)
    for i, ev in enumerate(evidence):
        survivors, eliminated = VS.apply_evidence(survivors, [ev], vocab)
        history.append(_history_record_admission(
            seq, f"D{step_base + i}", f"D{step_base + i + 1}", ev, eliminated,
            len(survivors), vocab, vocab_hash, src_hashes,
        ))
        seq += 1

    all_ids = sorted(known_ids | set(new_ids))
    new_state = dict(state)
    new_state["evidence_source_files"] = [
        {"path": str(p), "sha256": sha256_file(Path(p))} for p in evidence_source_paths
    ]
    new_state["evidence_ids"] = all_ids
    new_state["n_evidence"] = len(all_ids)
    new_state["n_surviving"] = len(survivors)
    new_state["n_eliminated"] = state["n_initial_hypotheses"] - len(survivors)
    new_state["developmental_tag"] = f"D{len(all_ids)}"
    _write_materialization(out_dir, new_state, _version_space_lines(survivors, vocab), history)
    return load_materialization(out_dir)["state"]
