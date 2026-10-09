"""U2B Gate B0 no-API gate: test_exemplars.py

Deterministic, mechanical checks for the U2B^cand exemplar registry +
exact-match retrieval (unified_machine/exemplars/). Zero API spend, zero
network, zero launches. Failure anywhere blocks the Gate B0 verdict.

What it pays:
  * builder determinism: two separate processes -> byte-identical registry.json;
    committed registry.json matches a fresh build
  * exact-match fires on a step-1 packet whose objective contains both frozen
    phrases with acceptance configured
  * perturbations do NOT match (missing one phrase; demand other; step=2;
    non-empty history; no acceptance)
  * NO_EXEMPLAR and UM_EXEMPLAR_RETRIEVAL=0 produce a packet byte-identical
    to U2A's _packet output (same object / canonical JSON equality)
  * on match, exactly one key ("acquired_procedure") is added; everything else
    identical; injection block is byte-exact vs the frozen spec text
  * tampered registry.json -> load_registry raises
  * provenance hashes verify against the real frozen input files
  * stub MissionRunner run with a mock worker: retrieval ON vs OFF produce
    identical ledger event-kind sequences and identical repos; they differ only
    by retrieval_log.jsonl and (on match) the packet key

Run:  python3 test_exemplars.py   (from this directory, or anywhere;
      the test resolves the candidate repo root from its own path)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]  # unified_machine/exemplars -> repo root
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from unified_machine import exemplars as X  # noqa: E402
from unified_machine.mission import MissionRunner  # noqa: E402
from unified_machine.types import Action  # noqa: E402
from unified_machine.workers import Worker  # noqa: E402


# ---------------------------------------------------------------------------
# Frozen reference values (Gate B0 spec)
# ---------------------------------------------------------------------------

EXPECTED_REGISTRY_SHA256 = X.REGISTRY_SHA256
EXPECTED_INSTANCE_ID = "ex-enum-004-p01"
EXPECTED_PIVOT_ID = "tr-enumeration-affordance-004:p01"
EXPECTED_EFFECT = "ACQUIRE_COMPLETE_SOURCE_SET"
EXPECTED_ARGV = ["bash", "-lc", "find sources -type f | sort"]
EXPECTED_RATIONALE = "Enumerate every file under sources/ to build a complete manifest."
EXPECTED_COST = {"result_chars": 4686, "acts_consumed": 1}
EXPECTED_SIGNATURE = {
    "demand": "enumerate_directory_listing",
    "phase": "initial",
    "acceptance_configured": True,
}

PIVOT_FILE = Path(
    "/home/hatch/workspace/um-missions/_learning/runs"
    "/.state-tr-enumeration-affordance-004-fx1/pivots.jsonl"
)
OBJECTIVE_FILE = Path(
    "/home/hatch/workspace/um-missions/_learning/curriculum/episodes"
    "/tr-enumeration-affordance-004/control/OBJECTIVE.md"
)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str = "") -> None:
    CHECKS.append((name, ok, evidence))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {evidence}" if evidence else ""))


def canonical(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def frozen_objective() -> str:
    return OBJECTIVE_FILE.read_text(encoding="utf-8")


def matching_packet() -> dict:
    return {
        "mission": {
            "id": "test-mid",
            "objective": frozen_objective(),
            "repo": "/tmp/repo",
            "step": 1,
            "acceptance_commands": [["true"]],
        },
        "history": [],
    }


# ---------------------------------------------------------------------------
# 1. Builder determinism (two processes -> byte-identical; committed matches fresh)
# ---------------------------------------------------------------------------

def t_builder_determinism() -> None:
    reg_path = HERE / "registry.json"
    before = reg_path.read_bytes()
    for i in range(2):
        cp = subprocess.run(
            [sys.executable, str(HERE / "build_registry.py")],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        if cp.returncode != 0:
            check("builder_determinism", False, f"build {i} failed: {cp.stderr[-300:]}")
            return
    after = reg_path.read_bytes()
    check("builder_determinism_two_processes_byte_identical", before == after,
          f"{len(before)} bytes, sha {hashlib.sha256(after).hexdigest()[:16]}...")
    doc = json.loads(after.decode("utf-8"))
    check("builder_registy_sha256_field_matches_constant",
          doc.get("registry_sha256") == EXPECTED_REGISTRY_SHA256,
          f"embedded {str(doc.get('registry_sha256'))[:16]}...")
    # Recompute per the documented procedure (canonical bytes of doc WITHOUT the key).
    bare = {k: v for k, v in doc.items() if k != "registry_sha256"}
    recomputed = hashlib.sha256(canonical(bare)).hexdigest()
    check("builder_sha256_procedure_recomputes", recomputed == EXPECTED_REGISTRY_SHA256,
          recomputed[:16] + "...")


# ---------------------------------------------------------------------------
# 2. Exact match fires; 3. perturbations do not
# ---------------------------------------------------------------------------

def t_exact_match() -> None:
    r, inst = X.retrieve(matching_packet())
    check("exact_match_fires", r == "EXEMPLAR" and inst is not None and inst["id"] == EXPECTED_INSTANCE_ID,
          f"{r}/{inst['id'] if inst else None}")
    check("exact_match_instance_effect", inst["observed_effect"] == EXPECTED_EFFECT)
    check("exact_match_instance_action_verbatim",
          inst["action"]["kind"] == "run"
          and inst["action"]["args"] == {"argv": EXPECTED_ARGV}
          and inst["action"]["rationale_verbatim"] == EXPECTED_RATIONALE)
    check("exact_match_instance_cost", inst["cost_vector"] == EXPECTED_COST,
          str(inst["cost_vector"]))
    check("exact_match_instance_signature", inst["observed_state"] == EXPECTED_SIGNATURE)


def t_perturbations() -> None:
    base = matching_packet()

    def retrieve_with(**kw):
        p = {
            "mission": {**base["mission"]},
            "history": list(base["history"]),
        }
        for k, v in kw.items():
            if k in ("objective", "step", "acceptance_commands"):
                p["mission"][k] = v
            elif k == "history":
                p["history"] = v
            else:
                raise AssertionError(k)
        return X.retrieve(p)

    obj = base["mission"]["objective"]
    no_line = obj.replace("one per line", "several lines each")  # drop one frozen phrase
    assert "one per line" not in no_line.lower()
    r, _ = retrieve_with(objective=no_line)
    check("perturb_missing_phrase_no_match", r == "NO_EXEMPLAR", r)

    other_obj = "Write MANIFEST.txt listing files."  # neither frozen phrase
    r, _ = retrieve_with(objective=other_obj)
    check("perturb_demand_other_no_match", r == "NO_EXEMPLAR", r)

    r, _ = retrieve_with(step=2)
    check("perturb_step2_no_match", r == "NO_EXEMPLAR", r)

    r, _ = retrieve_with(history=[{"step": 1, "note": "x"}])
    check("perturb_nonempty_history_no_match", r == "NO_EXEMPLAR", r)

    r, _ = retrieve_with(acceptance_commands=[])
    check("perturb_no_acceptance_no_match", r == "NO_EXEMPLAR", r)


# ---------------------------------------------------------------------------
# 4. NO_EXEMPLAR passthrough = same object; canonical JSON byte-identical
# ---------------------------------------------------------------------------

def t_noexemplar_passthrough() -> None:
    p = matching_packet()
    p["mission"]["step"] = 2  # forces NO_EXEMPLAR
    out = X.retrieve_into_packet(p)
    check("noexemplar_returns_same_object", out is p)
    check("noexemplar_no_acquired_key", "acquired_procedure" not in out)


# ---------------------------------------------------------------------------
# 5. On match: exactly one key added, everything else identical; block byte-exact
# ---------------------------------------------------------------------------

EXPECTED_BLOCK = (
    "ACQUIRED PROCEDURE FROM PRIOR EXPERIENCE\n"
    "Observed state: demand=enumerate_directory_listing, phase=initial, acceptance_configured=true\n"
    'Previously successful action: {"kind": "run", "args": {"argv": ["bash", "-lc", "find sources -type f | sort"]}}\n'
    "Action rationale (verbatim, from prior worker): Enumerate every file under sources/ to build a complete manifest.\n"
    "Observed effect: ACQUIRE_COMPLETE_SOURCE_SET (coverage 0.0 -> 1.0 in a single act; 4686 result chars)\n"
    f"Provenance: tr-enumeration-affordance-004:p01 (registry sha256 {EXPECTED_REGISTRY_SHA256})"
)


def t_match_injection() -> None:
    p = matching_packet()
    keys_before = set(p.keys())
    out = X.retrieve_into_packet(p)
    check("match_returns_new_object", out is not p)
    added = set(out.keys()) - keys_before
    removed = keys_before - set(out.keys())
    check("match_exactly_one_key_added", added == {"acquired_procedure"} and not removed,
          f"added={sorted(added)} removed={sorted(removed)}")
    check("match_other_keys_identical",
          all(out[k] == p[k] for k in keys_before),
          "all pre-existing top-level values == originals")
    ap = out["acquired_procedure"]
    check("match_acquired_procedure_shape",
          set(ap.keys()) == {"retrieval", "exemplar_id", "registry_sha256", "block"}
          and ap["retrieval"] == "EXEMPLAR"
          and ap["exemplar_id"] == EXPECTED_INSTANCE_ID
          and ap["registry_sha256"] == EXPECTED_REGISTRY_SHA256)
    check("match_injection_block_byte_exact", ap["block"] == EXPECTED_BLOCK,
          f"{len(ap['block'])} chars" if ap["block"] != EXPECTED_BLOCK else f"{len(ap['block'])} chars")


# ---------------------------------------------------------------------------
# 6. Tampered registry -> load raises
# ---------------------------------------------------------------------------

def t_tamper_evident() -> None:
    raw = (HERE / "registry.json").read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    doc["instances"][0]["cost_vector"]["result_chars"] = 9999  # content tamper
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.write(canonical(doc))
    tmp.close()
    old_path, old_cache = X.REGISTRY_PATH, X._REGISTRY_CACHE
    try:
        X.REGISTRY_PATH = Path(tmp.name)
        X._REGISTRY_CACHE = None
        try:
            X.load_registry()
            check("tampered_registry_raises", False, "load_registry did not raise")
        except ValueError as e:
            check("tampered_registry_raises", True, f"{type(e).__name__}")
    finally:
        X.REGISTRY_PATH, X._REGISTRY_CACHE = old_path, old_cache
        os.unlink(tmp.name)


# ---------------------------------------------------------------------------
# 7. Provenance hashes verify against the real frozen files
# ---------------------------------------------------------------------------

def t_provenance_against_frozen() -> None:
    reg = X.load_registry()
    built = reg["built_from"]
    inst = reg["instances"][0]
    pivot_bytes = PIVOT_FILE.read_bytes()
    check("provenance_pivot_file_hash",
          built["pivot_file_sha256"] == hashlib.sha256(pivot_bytes).hexdigest()
          and inst["provenance"]["pivot_file_sha256"] == hashlib.sha256(pivot_bytes).hexdigest())
    rec = None
    for line in pivot_bytes.decode("utf-8").splitlines():
        r = json.loads(line)
        if r.get("pivot_id") == EXPECTED_PIVOT_ID:
            rec = r
            break
    rec_sha = hashlib.sha256(canonical(rec)).hexdigest()
    check("provenance_pivot_record_hash",
          inst["provenance"]["pivot_record_sha256"] == rec_sha, rec_sha[:16] + "...")
    check("provenance_pivot_effect_assertion",
          rec["effect"]["functional_effects"] == [EXPECTED_EFFECT])
    obj_sha = hashlib.sha256(OBJECTIVE_FILE.read_bytes()).hexdigest()
    check("provenance_objective_file_hash", built["objective_file_sha256"] == obj_sha,
          obj_sha[:16] + "...")
    check("provenance_paths_absolute",
          Path(built["pivot_file"]).is_absolute() and Path(built["objective_file"]).is_absolute())


# ---------------------------------------------------------------------------
# 8. Stub MissionRunner run: ON vs OFF ledger/repo identical; only log + key differ
# ---------------------------------------------------------------------------

class MockWorker(Worker):
    def __init__(self):
        self.packets = []

    def next_action(self, packet):
        self.packets.append(packet)
        return Action.from_obj({"kind": "stop", "args": {"status": "complete", "reason": "mock done"}})

    def report(self, packet):
        return "mock report"


def make_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)


def ledger_kinds(state_dir: Path) -> list[str]:
    import sqlite3
    conn = sqlite3.connect(state_dir / "ledger.sqlite3")
    try:
        return [r[0] for r in conn.execute("SELECT kind FROM events ORDER BY rowid")]
    finally:
        conn.close()


def run_stub(repo: Path, flag: str, mission_id: str):
    state_dir = Path(tempfile.mkdtemp(prefix="u2b_state_"))
    old = {k: os.environ.get(k) for k in ("UM_EXEMPLAR_RETRIEVAL", "UM_DEVELOPMENTAL_HOOK")}
    os.environ["UM_EXEMPLAR_RETRIEVAL"] = flag
    os.environ["UM_DEVELOPMENTAL_HOOK"] = "0"  # keep the real ~/.unified-machine/developmental untouched
    try:
        worker = MockWorker()
        runner = MissionRunner(
            objective=frozen_objective(),
            repo=repo,
            state_dir=state_dir,
            worker=worker,
            budget=2,
            acceptance=[["true"]],
            mission_id=mission_id,
            memory_query="zzzz_no_match_zzzz",
        )
        head_before = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        result = runner.run()
        head_after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        status_after = subprocess.run(
            ["git", "status", "--short"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        log_path = state_dir / "retrieval_log.jsonl"
        log_lines = (
            [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
            if log_path.exists() else []
        )
        return {
            "result": result, "worker": worker, "kinds": ledger_kinds(state_dir),
            "head_before": head_before, "head_after": head_after, "status": status_after,
            "log_lines": log_lines, "state_dir": state_dir,
        }
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def t_runner_on_vs_off() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="u2b_repo_"))
    repo = tmp / "repo"
    make_git_repo(repo)
    try:
        on = run_stub(repo, "1", "u2b-stub")
        off = run_stub(repo, "0", "u2b-stub")
        check("runner_on_off_same_ledger_kinds", on["kinds"] == off["kinds"],
              str(on["kinds"]))
        check("runner_repo_untouched_on", on["head_before"] == on["head_after"] and on["status"] == "")
        check("runner_repo_untouched_off", off["head_before"] == off["head_after"] and off["status"] == "")
        check("runner_on_log_written",
              len(on["log_lines"]) == 1
              and on["log_lines"][0]["retrieval"] == "EXEMPLAR"
              and on["log_lines"][0]["exemplar_id"] == EXPECTED_INSTANCE_ID
              and on["log_lines"][0]["step"] == 1
              and on["log_lines"][0]["mission_id"] == "u2b-stub"
              and on["log_lines"][0]["signature"] == EXPECTED_SIGNATURE,
              str(on["log_lines"]))
        check("runner_off_no_log", off["log_lines"] == [])
        pkt_on, pkt_off = on["worker"].packets[0], off["worker"].packets[0]
        check("runner_on_packet_has_key", pkt_on.get("acquired_procedure", {}).get("exemplar_id") == EXPECTED_INSTANCE_ID)
        check("runner_off_packet_no_key", "acquired_procedure" not in pkt_off)
        bare_on = {k: v for k, v in pkt_on.items() if k != "acquired_procedure"}
        check("runner_packets_differ_only_by_key", bare_on == pkt_off)
        check("runner_off_packet_matches_u2a_dash_packet",
              canonical(pkt_off) == canonical(_fresh_packet(repo, "u2b-stub")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fresh_packet(repo: Path, mission_id: str) -> dict:
    """A _packet(1) straight from MissionRunner: the U2A reference output."""
    old = os.environ.get("UM_EXEMPLAR_RETRIEVAL")
    state_dir = Path(tempfile.mkdtemp(prefix="u2b_ref_"))
    try:
        worker = MockWorker()
        runner = MissionRunner(
            objective=frozen_objective(), repo=repo, state_dir=state_dir, worker=worker,
            budget=2, acceptance=[["true"]], mission_id=mission_id,
            memory_query="zzzz_no_match_zzzz",
        )
        return runner._packet(1)
    finally:
        if old is None:
            os.environ.pop("UM_EXEMPLAR_RETRIEVAL", None)
        else:
            os.environ["UM_EXEMPLAR_RETRIEVAL"] = old
        shutil.rmtree(state_dir, ignore_errors=True)


# ---------------------------------------------------------------------------

def main() -> int:
    t_builder_determinism()
    t_exact_match()
    t_perturbations()
    t_noexemplar_passthrough()
    t_match_injection()
    t_tamper_evident()
    t_provenance_against_frozen()
    t_runner_on_vs_off()
    fails = [c for c in CHECKS if not c[1]]
    print(f"\n{CHECKS.__len__() - len(fails)}/{len(CHECKS)} checks passed")
    for name, ok, ev in fails:
        print(f"  FAILED: {name} {ev}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
