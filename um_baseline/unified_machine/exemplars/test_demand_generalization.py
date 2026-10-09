"""U2C Gate B-generalization no-API gate: test_demand_generalization.py

Tests the GENERALIZED demand classifier (demand rules v1) for exemplar
retrieval. Zero API spend, zero network, zero launches. Failure anywhere
blocks the U2C gate verdict.

What it pays (beyond the frozen B0 suite test_exemplars.py, which must keep
passing 35/35 untouched):
  * builder determinism: two separate processes -> byte-identical registry.json;
    committed registry.json matches a fresh build AND is byte-identical to
    U2B's committed registry (stored experience untouched; only the lens changed)
  * demand rules v1: version constant "1" in both copies of the rule; the two
    copies (package + builder) agree on every frozen matrix objective
  * paraphrase matrix (6 genuinely new wordings): all classify
    enumerate_directory_listing and retrieve ex-enum-004-p01 at step 1 --
    including wordings the v0 exact-phrase rule would MISS
  * near-miss matrix (6 file-mentioning non-enumeration demands): all classify
    "other" -> NO_EXEMPLAR (conservatism; the enum exemplar never fires on
    non-enumeration demands)
  * backward compatibility: B0 transfer objective -> EXEMPLAR; B0 negative
    objective -> NO_EXEMPLAR with packet passthrough (same object);
    unified_machine/mission.py byte-identical to U2B (single causal edge untouched)
  * tamper evidence: mutated registry.json -> load_registry raises
  * provenance: registry sha256 recomputes per the documented procedure

Single-exemplar rationale (frozen): the only available observed successful
non-enumeration procedure is the B0 negative-control trajectory, whose
objective IS the backward-compat control. Adding it as a second exemplar
would force either a compat violation or a gerrymandered classifier
distinction. Discrimination is therefore carried by the near-miss matrix.

Run:  python3 test_demand_generalization.py   (from this directory)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
U2B_COMMIT = "15a907e9a11f0aab2a2822f31f62687a6e68ac82"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from unified_machine import exemplars as X  # noqa: E402
import importlib.util  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str = "") -> None:
    CHECKS.append((name, ok, evidence))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {evidence}" if evidence else ""))


def canonical(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def load_builder_module():
    spec = importlib.util.spec_from_file_location("u2c_build_registry", HERE / "build_registry.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BUILDER = load_builder_module()

# ---------------------------------------------------------------------------
# Frozen matrices (verbatim; also frozen in U2C_GATE_BGEN.md)
# ---------------------------------------------------------------------------

PARAPHRASES = [
    # P1: exercises "each on its own line" (v0 rule would MISS: no "one per line")
    "Produce FILES.txt at the repo root with the repo-relative path of every "
    "file under corpus/, each on its own line.",
    # P2: exercises "manifest of" + "each path on its own line" (v0: MISS)
    "Write a complete manifest of all files under data/ to MANIFEST.txt, with "
    "each path on its own line.",
    # P3: exercises "inventory of" + "line-separated" (v0: MISS)
    "Generate an inventory of every file in src/, line-separated, and save it "
    "as INVENTORY.txt.",
    # P4: exercises "all filenames" (v0: MISS -- v0 needed "every file under")
    "List all filenames under corpus/, one per line, into FILES.txt.",
    # P5: exercises "full list of" + "newline-separated" (v0: MISS)
    "Create CATALOG.txt holding the full list of each file under archive/, "
    "newline-separated, as repo-relative paths.",
    # P6: exercises "every filename" + "one file per line" (v0: MISS)
    "Dump every filename in corpus/ to NAMES.txt with one file per line.",
]

NEAR_MISSES = [
    # N1: count, not enumerate
    "Count the files under corpus/ and write the total to COUNT.txt.",
    # N2: find-largest, not enumerate
    "Find the largest file under corpus/ by size and report its repo-relative path.",
    # N3: summarize-each (file-mentioning summary demand), not enumerate
    "Read each file under corpus/ and write a one-sentence summary of each to SUMMARIES.txt.",
    # N4: group-1 phrase present, but tabular form, not line-delimited
    "List all files under corpus/ in a markdown table with their sizes in bytes.",
    # N5: group-1 phrase present, but a predicate check, not a listing
    "Check whether every file under corpus/ ends with a newline; report violators.",
    # N6: aggregation over files, not a listing
    "Print the total number of lines across every file under corpus/.",
]

B0_TRANSFER_OBJECTIVE = (
    Path("/home/hatch/workspace/um-b0-experiments/transfer/control/OBJECTIVE.md")
    .read_text(encoding="utf-8")
)
B0_NEGATIVE_OBJECTIVE = (
    Path("/home/hatch/workspace/um-b0-experiments/negative/control/OBJECTIVE.md")
    .read_text(encoding="utf-8")
)

EXPECTED_INSTANCE_ID = "ex-enum-004-p01"


def packet_for(objective: str, step: int = 1, acceptance=([["true"]]), history=None) -> dict:
    return {
        "mission": {
            "id": "u2c-gate",
            "objective": objective,
            "repo": "/tmp/repo",
            "step": step,
            "acceptance_commands": acceptance,
        },
        "history": [] if history is None else history,
    }


# ---------------------------------------------------------------------------
# 1. Rule version + cross-copy consistency
# ---------------------------------------------------------------------------

def t_rule_version_and_consistency() -> None:
    check("demand_rules_version_is_1",
          X.DEMAND_RULES_VERSION == "1" and BUILDER.DEMAND_RULES_VERSION == "1",
          f"pkg={X.DEMAND_RULES_VERSION} builder={BUILDER.DEMAND_RULES_VERSION}")
    corpus = PARAPHRASES + NEAR_MISSES + [B0_TRANSFER_OBJECTIVE, B0_NEGATIVE_OBJECTIVE]
    mism = [
        o[:60] for o in corpus
        if X.classify_demand(o) != BUILDER.classify_demand(o)
    ]
    check("rule_copies_agree_on_all_matrix_objectives", not mism, f"{len(corpus)} objectives, {len(mism)} mismatches")
    sig_pairs = [
        (X.precondition_signature(o, 1, [], [["true"]]),
         BUILDER.precondition_signature(o, 1, [], [["true"]]))
        for o in corpus
    ]
    check("signature_copies_agree", all(a == b for a, b in sig_pairs))


# ---------------------------------------------------------------------------
# 2. Paraphrase matrix: all fire
# ---------------------------------------------------------------------------

def t_paraphrase_matrix() -> None:
    for i, obj in enumerate(PARAPHRASES, start=1):
        demand = X.classify_demand(obj)
        r, inst = X.retrieve(packet_for(obj))
        ok = (
            demand == "enumerate_directory_listing"
            and r == "EXEMPLAR"
            and inst is not None
            and inst["id"] == EXPECTED_INSTANCE_ID
        )
        check(f"paraphrase_P{i}_retrieves_enum_exemplar", ok, f"demand={demand} retrieval={r}")


def t_paraphrases_beyond_v0() -> None:
    """Every paraphrase must be one the v0 exact-phrase rule would MISS.

    (Proves the matrix actually exercises generalization, not the old rule.)
    """
    def v0_demand(objective_text: str) -> str:
        low = objective_text.lower()
        return (
            "enumerate_directory_listing"
            if ("every file under" in low and "one per line" in low)
            else "other"
        )
    missed = [f"P{i}" for i, o in enumerate(PARAPHRASES, start=1) if v0_demand(o) == "other"]
    check("all_paraphrases_beyond_v0_rule", len(missed) == len(PARAPHRASES),
          f"v0-missed {len(missed)}/{len(PARAPHRASES)}: {missed}")


# ---------------------------------------------------------------------------
# 3. Near-miss matrix: all silent
# ---------------------------------------------------------------------------

def t_near_miss_matrix() -> None:
    for i, obj in enumerate(NEAR_MISSES, start=1):
        demand = X.classify_demand(obj)
        r, inst = X.retrieve(packet_for(obj))
        ok = demand == "other" and r == "NO_EXEMPLAR" and inst is None
        check(f"near_miss_N{i}_no_exemplar", ok, f"demand={demand} retrieval={r}")


# ---------------------------------------------------------------------------
# 4. Backward compatibility
# ---------------------------------------------------------------------------

def t_backward_compat() -> None:
    r, inst = X.retrieve(packet_for(B0_TRANSFER_OBJECTIVE))
    check("b0_transfer_objective_still_retrieves",
          r == "EXEMPLAR" and inst is not None and inst["id"] == EXPECTED_INSTANCE_ID, r)

    p = packet_for(B0_NEGATIVE_OBJECTIVE)
    r, inst = X.retrieve(p)
    check("b0_negative_objective_still_no_exemplar", r == "NO_EXEMPLAR" and inst is None, r)
    out = X.retrieve_into_packet(p)
    check("b0_negative_packet_passthrough_same_object", out is p)

    cp = subprocess.run(
        ["git", "diff", "--quiet", U2B_COMMIT, "--", "unified_machine/mission.py"],
        cwd=str(REPO_ROOT),
    )
    check("mission_py_unchanged_from_u2b", cp.returncode == 0,
          "single causal edge untouched" if cp.returncode == 0 else "mission.py DIFFERS")

    check("registry_sha256_constant_unchanged",
          X.REGISTRY_SHA256 == "5095158b93f243e535e7a11c662a55538b67aff8b09df0c616d76593d8f07564",
          X.REGISTRY_SHA256[:16] + "...")


# ---------------------------------------------------------------------------
# 5. Builder determinism + stored experience untouched
# ---------------------------------------------------------------------------

def t_builder_determinism_and_untouched() -> None:
    reg_path = HERE / "registry.json"
    before = reg_path.read_bytes()
    for i in range(2):
        cp = subprocess.run(
            [sys.executable, str(HERE / "build_registry.py")],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        if cp.returncode != 0:
            check("u2c_builder_determinism", False, f"build {i} failed: {cp.stderr[-300:]}")
            return
    after = reg_path.read_bytes()
    check("u2c_builder_two_processes_byte_identical", before == after,
          f"{len(after)} bytes")
    doc = json.loads(after.decode("utf-8"))
    bare = {k: v for k, v in doc.items() if k != "registry_sha256"}
    recomputed = hashlib.sha256(canonical(bare)).hexdigest()
    check("u2c_registry_sha256_recomputes", recomputed == X.REGISTRY_SHA256,
          recomputed[:16] + "...")
    u2b_bytes = subprocess.run(
        ["git", "show", f"{U2B_COMMIT}:unified_machine/exemplars/registry.json"],
        cwd=str(REPO_ROOT), capture_output=True,
    ).stdout
    check("u2c_registry_bytes_identical_to_u2b", after == u2b_bytes,
          "stored experience untouched" if after == u2b_bytes else "REGISTRY DIFFERS FROM U2B")


# ---------------------------------------------------------------------------
# 6. Tamper evidence
# ---------------------------------------------------------------------------

def t_tamper_evident() -> None:
    raw = (HERE / "registry.json").read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    doc["instances"][0]["cost_vector"]["result_chars"] = 9999
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.write(canonical(doc))
    tmp.close()
    old_path, old_cache = X.REGISTRY_PATH, X._REGISTRY_CACHE
    try:
        X.REGISTRY_PATH = Path(tmp.name)
        X._REGISTRY_CACHE = None
        try:
            X.load_registry()
            check("u2c_tampered_registry_raises", False, "load_registry did not raise")
        except ValueError:
            check("u2c_tampered_registry_raises", True, "ValueError")
    finally:
        X.REGISTRY_PATH, X._REGISTRY_CACHE = old_path, old_cache
        os.unlink(tmp.name)


# ---------------------------------------------------------------------------

def main() -> int:
    t_rule_version_and_consistency()
    t_paraphrase_matrix()
    t_paraphrases_beyond_v0()
    t_near_miss_matrix()
    t_backward_compat()
    t_builder_determinism_and_untouched()
    t_tamper_evident()
    fails = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
    for name, ok, ev in fails:
        print(f"  FAILED: {name} {ev}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
