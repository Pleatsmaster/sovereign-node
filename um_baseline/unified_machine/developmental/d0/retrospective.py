"""D0-v0 retrospective reconstruction (instrumented, no-API).

Authorized inputs (read-only):
  - frozen acquisition-v1 corpus (examples.jsonl)
  - frozen acquisition-v1 vocabulary
No capsules, no mission launches, no U1 packet integration, no model calls.

Procedure:
  1. hash frozen inputs (before)
  2. full build D0 -> D2 from the corpus evidence
  3. incremental path D0 -> D1 -> D2 via update(); require byte-match
  4. destructive test: delete materialization, rebuild, require byte-match
  5. reorder test: reversed input order -> identical state_hash
  6. emit TRAINING_REQUEST; validate -> REQUEST_VALID; tampered -> REQUEST_INVALID
  7. hash frozen inputs (after); require unchanged
  8. write measurements JSON

Answers only:
  - Can D0 deterministically turn accumulated evidence into persistent,
    reconstructable uncertainty state?
  - Can that state mechanically identify missing discriminating experience?
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from d0 import ledger as L
from d0 import request as R
from d0 import vocabulary as V
from d0 import version_space as VS

CORPUS_DIR = Path.home() / "workspace/um-missions/_learning/corpus/acquisition-v1"
EXAMPLES = CORPUS_DIR / "examples.jsonl"
MANIFEST = CORPUS_DIR / "manifest.json"
VOCAB_PATH = HERE / "vocabularies" / "acquisition_v1.json"
OUT_DIR = HERE / "developmental"
RESULTS = HERE / "retrospective_results.json"


def load_evidence() -> list[dict]:
    items = []
    for line in EXAMPLES.read_text().splitlines():
        if not line.strip():
            continue
        ex = json.loads(line)
        items.append({
            "evidence_id": ex["example_id"],
            "state_before": ex["state_before"],
            "observed_effect": list(ex["observed_effect"]),
            "cost": ex.get("cost"),
            "source_episode": ex.get("source_episode"),
            "provenance": ex.get("provenance"),
        })
    return items


def dir_hashes(d: Path) -> dict:
    return {p.name: L.sha256_file(p) for p in sorted(d.iterdir()) if p.is_file()}


def dir_bytes(d: Path) -> dict:
    return {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()}


def main() -> dict:
    t_all = time.perf_counter()
    before = {str(p): L.sha256_file(p) for p in (EXAMPLES, MANIFEST, VOCAB_PATH)}

    vocab = V.load_vocabulary(VOCAB_PATH)
    vocab_hash = V.vocabulary_hash(vocab)
    evidence = load_evidence()

    measurements: dict = {
        "vocabulary_hash": vocab_hash,
        "vocabulary_id": vocab["vocabulary_id"],
        "k_max": vocab["k_max"],
        "n_predicates": len(vocab["predicates"]),
        "n_evidence": len(evidence),
        "evidence_ids_canonical": sorted(e["evidence_id"] for e in evidence),
        "corpus_hashes_before": before,
    }

    # ---- full build (instrumented) ----
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    tracemalloc.start()
    t0 = time.perf_counter()
    state_full = L.build(evidence, vocab, VOCAB_PATH, [EXAMPLES], OUT_DIR)
    t_build = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    loaded = L.load_materialization(OUT_DIR)
    history = loaded["history"]
    measurements["full_build"] = {
        "wall_seconds": t_build,
        "peak_python_bytes": peak,
        "initial_hypotheses": history[0]["initial_hypothesis_count"],
        "per_step": [
            {
                "transition": r["from"] + "->" + r["to"],
                "experience": r["experience"],
                "n_eliminated": r["n_eliminated_this_step"],
                "n_retained": r["n_retained"],
            }
            for r in history if r["transition"] == "experience_admitted"
        ],
        "n_surviving": state_full["n_surviving"],
        "state_hash": state_full["state_hash"],
        "serialized_bytes": sum(p.stat().st_size for p in OUT_DIR.iterdir() if p.is_file()),
        "file_hashes": dir_hashes(OUT_DIR),
    }

    # ---- disagreement structure ----
    survivors = R._survivor_objects(loaded["survivors"])
    regions = VS.eligible_regions(survivors, evidence, vocab)
    measurements["disagreement"] = {
        "n_eligible_regions": len(regions),
        "n_observed_reach": sum(1 for _ in regions if VS.region_observed_in_history(
            _["region"]["pids"], evidence, vocab)),
        "top5": [
            {
                "rid": e["region"]["rid"],
                "readable": V.region_readable(e["region"]["pids"], vocab),
                "h_fires": e["h_fires"]["hid"],
                "h_quiet": e["h_quiet"]["hid"],
                "observed": VS.region_observed_in_history(e["region"]["pids"], evidence, vocab),
            }
            for e in regions[:5]
        ],
    }

    # ---- incremental path must byte-match full rebuild ----
    tmp = Path(tempfile.mkdtemp(prefix="d0incr-"))
    try:
        t0 = time.perf_counter()
        L.build([], vocab, VOCAB_PATH, [EXAMPLES], tmp)
        L.update(tmp, [evidence[0]], vocab, VOCAB_PATH, [EXAMPLES])
        t1 = time.perf_counter()
        L.update(tmp, [evidence[1]], vocab, VOCAB_PATH, [EXAMPLES])
        t_incr = time.perf_counter() - t0
        incr_bytes = dir_bytes(tmp)
        full_bytes = dir_bytes(OUT_DIR)
        measurements["incremental"] = {
            "wall_seconds": t_incr,
            "byte_match_full_rebuild": incr_bytes == full_bytes,
        }
        assert incr_bytes == full_bytes, "incremental != full rebuild"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- destructive test ----
    doomed_hashes = dir_hashes(OUT_DIR)
    doomed_state = state_full["state_hash"]
    shutil.rmtree(OUT_DIR)
    t0 = time.perf_counter()
    state_rebuilt = L.build(evidence, vocab, VOCAB_PATH, [EXAMPLES], OUT_DIR)
    t_rebuild = time.perf_counter() - t0
    rebuilt_hashes = dir_hashes(OUT_DIR)
    measurements["destructive_rebuild"] = {
        "wall_seconds": t_rebuild,
        "byte_match": rebuilt_hashes == doomed_hashes,
        "state_hash_match": state_rebuilt["state_hash"] == doomed_state,
    }
    assert rebuilt_hashes == doomed_hashes, "destructive rebuild mismatch"
    assert state_rebuilt["state_hash"] == doomed_state

    # ---- reorder test: reversed input order, canonical processing must win ----
    tmp2 = Path(tempfile.mkdtemp(prefix="d0reorder-"))
    try:
        st = L.build(list(reversed(evidence)), vocab, VOCAB_PATH, [EXAMPLES], tmp2)
        measurements["reorder"] = {
            "state_hash_match_canonical": st["state_hash"] == doomed_state,
        }
        assert st["state_hash"] == doomed_state, "input order leaked into state"
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # ---- TRAINING_REQUEST: emit, validate, tamper ----
    loaded = L.load_materialization(OUT_DIR)
    req = R.build_request(loaded, vocab, evidence)
    assert req is not None, "expected a disagreement on the corpus"
    verdict = R.validate_request(req, OUT_DIR, vocab, evidence)
    tampered = json.loads(json.dumps(req))
    tampered["disagreement"]["hypothesis_ids"] = ["C:p01", "NULL"]
    try:
        R.validate_request(tampered, OUT_DIR, vocab, evidence)
        tamper_caught = False
    except R.RequestInvalid:
        tamper_caught = True
    measurements["training_request"] = {
        "emitted": True,
        "request_id": req["request_id"],
        "region": req["disagreement"]["region"],
        "hypothesis_ids": req["disagreement"]["hypothesis_ids"],
        "predictions": req["disagreement"]["predictions"],
        "reachability": req["reachability"],
        "validation": verdict,
        "tamper_rejected": tamper_caught,
    }
    assert verdict == "REQUEST_VALID"
    assert tamper_caught

    after = {str(p): L.sha256_file(p) for p in (EXAMPLES, MANIFEST, VOCAB_PATH)}
    measurements["corpus_hashes_after"] = after
    measurements["inputs_unchanged"] = (before == after)
    assert before == after, "frozen inputs modified"

    measurements["wall_seconds_total"] = time.perf_counter() - t_all
    RESULTS.write_text(L.dump_canonical(measurements) + "\n")
    return measurements


if __name__ == "__main__":
    m = main()
    print(L.dump_canonical({k: m[k] for k in
          ("n_evidence", "vocabulary_hash")}))
    print("state_hash:", m["full_build"]["state_hash"])
    print("surviving:", m["full_build"]["n_surviving"],
          "eligible_regions:", m["disagreement"]["n_eligible_regions"])
    print("request:", m["training_request"]["request_id"][:16],
          m["training_request"]["validation"])
    print("ALL RETROSPECTIVE CHECKS PASSED")
