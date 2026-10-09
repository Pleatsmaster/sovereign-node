"""U2A no-API integration gate: test_u2a_integration.py

Tests the ACTUAL integrated candidate (U1 + D0 post-terminal hook).
Zero API spend, zero network, zero launches. Failure anywhere blocks
promotion consideration -- this gate is Gate A for the live adapter.

What it pays:
  * prefix replay:   D_t^{live-hook} = D_t^{retrospective} per history prefix,
                     and D^{incremental} = D^{live} = D^{reconstructed} byte-for-byte
  * worker independence: f(H,C,V|gpt-5) = f(H,C,V|other) byte-for-byte
  * ordinary-work noninterference: worker/action/acceptance path <-/-- D0
  * request-as-data: TRAINING_REQUEST is data (proposed, launch_authorized=false),
                     no executor reachable from D0
  * pure-function recovery: rm -rf D -> f(H,C,V) -> D byte-identical
  * D0-v0 unchanged: the integrated organ is the frozen organ

Run:  python3 test_u2a_integration.py   (from this directory, or anywhere;
      the test resolves the candidate repo root from its own path)
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Repo resolution (the candidate under test)
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]  # unified_machine/developmental -> repo root
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from unified_machine.developmental import hook as H  # noqa: E402
from unified_machine.developmental.d0 import ledger as L  # noqa: E402
from unified_machine.developmental.d0 import request as R  # noqa: E402
from unified_machine.developmental.d0 import vocabulary as V  # noqa: E402


# ---------------------------------------------------------------------------
# Frozen reference values (D0-v0 freeze + retrospective freeze)
# ---------------------------------------------------------------------------

FROZEN_D0_CODE_HASH = "db07762babcd428ed21ad0820740681fa3e65ebc32d535e2f823d006fbeb54d7"
FROZEN_VS_HASH_D2 = "9b05c71a205adf4347137639de4288cc9143155708773af4bab60e6543cbc5f1"
FROZEN_HISTORY_HASH_D2 = "3c4772835bc04f58d49212717fb4e524a0892360b7eb9cecc03c811f2f7197b7"
FROZEN_STATE_HASH_D2 = "3b63c83d6e29df5bfb689d76a0f3ac9aef0a191f1dbe16d2ebfc9428fd2900ba"
FROZEN_N_SURVIVING_D2 = 591
FROZEN_REQUEST_REGION = "R:p07"
FROZEN_REQUEST_FIRES = "C:p07"
FROZEN_REQUEST_QUIET = "C:p01|p08"

FROZEN_DEV = Path("/home/hatch/workspace/um-developmental")
FROZEN_D0_SRC = FROZEN_DEV / "d0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _dir_hashes(d: Path) -> dict:
    return {
        str(p.relative_to(d)): _sha256_file(p)
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


def _corpus_evidence() -> list:
    """Frozen corpus examples mapped to the d0 evidence shape (as the
    retrospective's load_evidence did)."""
    items = []
    for line in H.FROZEN_EXAMPLES.read_text(encoding="utf-8").splitlines():
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


def _terminal_event(mission_id: str, evidence: list, worker_model: str = "gpt-5",
                    origin: str = "frozen_corpus") -> dict:
    return {
        "event_type": "mission_terminal",
        "mission_id": mission_id,
        "terminal_status": "complete",
        "finished_at": 0.0,
        "worker": {"kind": "openai", "model": worker_model},
        "developmental_evidence": evidence,
        "origin": origin,
        "mission_ref": {"state_dir": "/tmp/none", "repo": "/tmp/none"},
    }


def _materialized_hashes(root: Path) -> dict:
    return _dir_hashes(H._materialized_dir(root))


def _tmp_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="u2a-gate-"))


# ---------------------------------------------------------------------------
# 1. D0-v0 unchanged in the candidate
# ---------------------------------------------------------------------------

def test_d0_unchanged():
    for p in sorted((HERE / "d0").glob("*.py")):
        src = FROZEN_D0_SRC / p.name
        assert src.exists(), f"frozen source missing: {src}"
        assert p.read_bytes() == src.read_bytes(), f"d0/{p.name} differs from frozen"
    assert L.d0_code_hash() == FROZEN_D0_CODE_HASH, "d0_code_hash drifted"
    vocab = V.load_vocabulary(HERE / "vocabularies" / "acquisition_v1.json")
    assert V.vocabulary_hash(vocab) == H.FROZEN_VOCAB_HASH
    # frozen corpus still frozen (the hook's C_t)
    assert _sha256_file(H.FROZEN_EXAMPLES) == H.FROZEN_EXAMPLES_HASH


# ---------------------------------------------------------------------------
# 2. Prefix replay: D^{incremental} = D^{live} = D^{reconstructed}, and the
#    live hook reproduces the retrospective per prefix.
# ---------------------------------------------------------------------------

def _build_direct(evidence: list, out: Path):
    vocab = H.load_vocab()
    L.build(evidence, vocab, H.candidate_vocab_path(), [H.FROZEN_EXAMPLES], out)


def test_prefix_replay_triple_equality():
    ev = _corpus_evidence()
    assert [e["evidence_id"] for e in ev] == ["acqv1-ex-001", "acqv1-ex-002"]
    vocab = H.load_vocab()

    live_root = _tmp_root()
    tmpdirs = [live_root]
    try:
        # ---- prefix H_0 -> D_0 (no events yet) ----
        s0 = H.on_mission_terminal(_terminal_event("gate-m0", []), live_root)
        assert s0["ok"], s0
        assert s0["developmental_tag"] == "D0" and s0["n_surviving"] == 782

        recon0 = _tmp_root(); tmpdirs.append(recon0)
        _build_direct([], recon0 / "m")
        assert _dir_hashes(H._materialized_dir(live_root)) == _dir_hashes(recon0 / "m"), \
            "D0: live != reconstructed"

        # ---- prefix H_1 -> D_1 ----
        s1 = H.on_mission_terminal(_terminal_event("gate-m1", [ev[0]]), live_root)
        assert s1["ok"], s1
        assert s1["developmental_tag"] == "D1" and s1["n_surviving"] == 782
        assert s1["n_evidence_new"] == 1 and s1["n_evidence_total"] == 1

        recon1 = _tmp_root(); tmpdirs.append(recon1)
        _build_direct([ev[0]], recon1 / "m")
        assert _dir_hashes(H._materialized_dir(live_root)) == _dir_hashes(recon1 / "m"), \
            "D1: live != reconstructed"

        inc1 = _tmp_root(); tmpdirs.append(inc1)
        L.build([], vocab, H.candidate_vocab_path(), [H.FROZEN_EXAMPLES], inc1 / "m")
        L.update(inc1 / "m", [ev[0]], vocab, H.candidate_vocab_path(), [H.FROZEN_EXAMPLES])
        assert _dir_hashes(inc1 / "m") == _dir_hashes(H._materialized_dir(live_root)), \
            "D1: incremental != live"

        # ---- prefix H_2 -> D_2 ----
        s2 = H.on_mission_terminal(_terminal_event("gate-m2", [ev[1]]), live_root)
        assert s2["ok"], s2
        assert s2["developmental_tag"] == "D2" and s2["n_surviving"] == FROZEN_N_SURVIVING_D2
        assert s2["n_evidence_total"] == 2

        recon2 = _tmp_root(); tmpdirs.append(recon2)
        _build_direct([ev[0], ev[1]], recon2 / "m")
        live_hashes = _dir_hashes(H._materialized_dir(live_root))
        assert live_hashes == _dir_hashes(recon2 / "m"), "D2: live != reconstructed"

        inc2 = _tmp_root(); tmpdirs.append(inc2)
        L.build([], vocab, H.candidate_vocab_path(), [H.FROZEN_EXAMPLES], inc2 / "m")
        L.update(inc2 / "m", [ev[0]], vocab, H.candidate_vocab_path(), [H.FROZEN_EXAMPLES])
        L.update(inc2 / "m", [ev[1]], vocab, H.candidate_vocab_path(), [H.FROZEN_EXAMPLES])
        assert _dir_hashes(inc2 / "m") == live_hashes, "D2: incremental != live"
    finally:
        for d in tmpdirs:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. Retrospective correspondence at D2
# ---------------------------------------------------------------------------

def test_retrospective_correspondence():
    ev = _corpus_evidence()
    root = _tmp_root()
    try:
        H.on_mission_terminal(_terminal_event("gate-m0", []), root)
        H.on_mission_terminal(_terminal_event("gate-m1", [ev[0]]), root)
        s2 = H.on_mission_terminal(_terminal_event("gate-m2", [ev[1]]), root)
        assert s2["ok"], s2
        mdir = H._materialized_dir(root)
        hashes = _dir_hashes(mdir)
        # The version space -- the actual developmental content -- is byte-identical
        # to the frozen retrospective's.
        assert hashes["version_space.jsonl"] == FROZEN_VS_HASH_D2, \
            "live version space != retrospective version space"
        # So is the elimination history (source_identity is path-independent).
        assert hashes["history.jsonl"] == FROZEN_HISTORY_HASH_D2, \
            "live history != retrospective history"
        # state.json: every field matches except the ownership provenance
        # (vocabulary_path: the candidate owns its copy) and the state_hash
        # covering it. The content hashes it covers do match.
        live_state = json.loads((mdir / "state.json").read_text())
        frozen_state = json.loads((FROZEN_DEV / "developmental" / "state.json").read_text())
        assert live_state["version_space_hash"] == frozen_state["version_space_hash"]
        assert live_state["history_hash"] == frozen_state["history_hash"]
        for k, v in frozen_state.items():
            if k in ("vocabulary_path", "state_hash"):
                continue
            assert live_state[k] == v, f"state field {k} differs from retrospective"
        assert live_state["vocabulary_path"] == str(H.candidate_vocab_path())
        assert live_state["n_surviving"] == FROZEN_N_SURVIVING_D2
        assert live_state["developmental_tag"] == "D2"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. TRAINING_REQUEST is data, never a command
# ---------------------------------------------------------------------------

def test_request_as_data():
    ev = _corpus_evidence()
    root = _tmp_root()
    try:
        H.on_mission_terminal(_terminal_event("gate-m0", []), root)
        H.on_mission_terminal(_terminal_event("gate-m1", [ev[0]]), root)
        s2 = H.on_mission_terminal(_terminal_event("gate-m2", [ev[1]]), root)
        rid = s2["request_id"]
        assert rid, "no request emitted at D2"
        rpath = H._requests_dir(root) / f"{rid}.json"
        assert rpath.exists(), "request file missing"
        envelope = json.loads(rpath.read_text())
        # Data, never a command:
        assert envelope["status"] == "proposed"
        assert envelope["launch_authorized"] is False
        assert "no executor" in envelope["note"].lower() or "never a command" in envelope["note"]
        req = envelope["request"]
        assert req["request_id"] == rid
        # Mechanically valid: reconstructive validation passes against the live state.
        vocab = H.load_vocab()
        R.validate_request(req, H._materialized_dir(root), vocab,
                           [e["evidence"] for e in H.read_log(root)])
        # Same scientific content as the retrospective's request.
        dis = req["disagreement"]
        assert dis["region"]["rid"] == FROZEN_REQUEST_REGION
        assert list(dis["hypothesis_ids"]) == [FROZEN_REQUEST_FIRES, FROZEN_REQUEST_QUIET]
        assert dis["predictions"] == {FROZEN_REQUEST_FIRES: "INTERVENE",
                                      FROZEN_REQUEST_QUIET: "QUIET"}
        # Tampering with the envelope's authorization flag cannot forge execution:
        # the inner request is untouched and still validates; the flag is data.
        envelope["launch_authorized"] = True
        R.validate_request(envelope["request"], H._materialized_dir(root), vocab,
                           [e["evidence"] for e in H.read_log(root)])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. Worker independence: f(H,C,V|w1) = f(H,C,V|w2) byte-for-byte
# ---------------------------------------------------------------------------

def test_worker_independence():
    ev = _corpus_evidence()
    roots = []
    try:
        hashes = []
        for model in ("gpt-5", "gpt-5-nano", "llama-3.2-1b-instruct"):
            root = _tmp_root()
            roots.append(root)
            H.on_mission_terminal(
                _terminal_event("gate-m0", [], worker_model=model), root)
            H.on_mission_terminal(
                _terminal_event("gate-m1", [ev[0]], worker_model=model), root)
            s = H.on_mission_terminal(
                _terminal_event("gate-m2", [ev[1]], worker_model=model), root)
            assert s["ok"], s
            # worker identity IS recorded in the log (provenance)...
            log_models = {e["worker"]["model"] for e in H.read_log(root)}
            assert log_models == {model}
            # ...but the materialized developmental state is byte-identical.
            hashes.append(_dir_hashes(H._materialized_dir(root)))
        assert hashes[0] == hashes[1] == hashes[2], \
            "developmental state depends on worker identity"
    finally:
        for d in roots:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# 6. Ordinary-work noninterference (static): worker/action/acceptance path
#    has no edge from D0.
# ---------------------------------------------------------------------------

def test_noninterference_static():
    mission_src = (REPO_ROOT / "unified_machine" / "mission.py").read_text()
    tree = ast.parse(mission_src)
    # Collect (function, start, end) for every function/method.
    funcs = []

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):  # noqa: N802
            funcs.append((node.name, node.lineno, node.end_lineno))
            self.generic_visit(node)

    V().visit(tree)
    verify_line = next(
        i + 1 for i, l in enumerate(mission_src.splitlines())
        if "self.ledger.verify()" in l
    )
    run = next(f for f in funcs if f[0] == "run")
    for i, line in enumerate(mission_src.splitlines(), start=1):
        if "developmental" not in line:
            continue
        owner = next((f[0] for f in funcs if f[1] <= i <= f[2]), None)
        assert owner == "run", f"'developmental' referenced in {owner}, line {i}"
        assert i > verify_line, f"developmental hook not after ledger.verify() (line {i})"
    # The organ never imports the organism (one-way dependency).
    for p in sorted((HERE / "d0").glob("*.py")):
        assert "unified_machine" not in p.read_text(), f"d0/{p.name} imports organism"
    # No executor reachable from the hook: allowlist the hook's imports.
    # (The string "openai" appears only as a worker-kind DATA label in
    # _worker_provenance; what matters is that no executor is imported.)
    hook_src = (HERE / "hook.py").read_text()
    htree = ast.parse(hook_src)
    allowed_top = {"hashlib", "json", "os", "time", "pathlib", "typing",
                   "shutil", "tempfile"}
    for node in ast.walk(htree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                assert top in allowed_top, f"hook.py imports forbidden module: {a.name}"
        elif isinstance(node, ast.ImportFrom):
            if (node.level or 0) > 0 or node.module == "__future__":
                continue
            top = (node.module or "").split(".")[0]
            assert top in allowed_top, \
                f"hook.py has forbidden absolute import: {ast.dump(node)[:80]}"
    for name in ("Popen", "system", "spawnl", "spawnv", "execv", "execl"):
        assert f"os.{name}" not in hook_src, f"hook.py references os.{name}"
    # The hook never touches mission.py's worker-facing methods by name.
    for name in ("_packet", "_execute", "_run_acceptance", "next_action"):
        assert name not in hook_src, f"hook.py references worker path: {name}"


# ---------------------------------------------------------------------------
# 7. The hook never touches the mission (dynamic)
# ---------------------------------------------------------------------------

def test_hook_never_touches_mission():
    mission_dir = _tmp_root()
    (mission_dir / "ledger.sqlite3").write_bytes(b"sentinel")
    (mission_dir / "notes.txt").write_text("sentinel")
    before = _dir_hashes(mission_dir)
    root = _tmp_root()
    try:
        s = H.on_mission_terminal(_terminal_event("gate-m9", []), root)
        assert s["ok"], s
        assert _dir_hashes(mission_dir) == before, "hook modified the mission dir"
        # Garbage in: contained, never raises.
        bad = H.on_mission_terminal({"nonsense": 1}, root)
        assert bad["ok"] is False and "error" in bad
        bad2 = H.on_mission_terminal(
            {"event_type": "mission_terminal", "mission_id": "x",
             "developmental_evidence": [{"bogus": True}]}, root)
        assert bad2["ok"] is True and bad2["n_evidence_rejected"] == 1
        assert bad2["n_evidence_new"] == 0
    finally:
        shutil.rmtree(mission_dir, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 8. Pure-function recovery: rm -rf D -> f(H,C,V) -> D byte-identical
# ---------------------------------------------------------------------------

def test_rmrf_recovery():
    ev = _corpus_evidence()
    root = _tmp_root()
    try:
        H.on_mission_terminal(_terminal_event("gate-m0", []), root)
        H.on_mission_terminal(_terminal_event("gate-m1", [ev[0]]), root)
        H.on_mission_terminal(_terminal_event("gate-m2", [ev[1]]), root)
        before_mat = _dir_hashes(H._materialized_dir(root))
        before_req = _dir_hashes(H._requests_dir(root))
        # The corruption event:
        shutil.rmtree(H._materialized_dir(root))
        shutil.rmtree(H._requests_dir(root))
        # Recovery is the same pure function over the authoritative log:
        built = H.materialize_from_log(root)
        assert _dir_hashes(H._materialized_dir(root)) == before_mat, \
            "materialization not recovered byte-identically"
        assert _dir_hashes(H._requests_dir(root)) == before_req, \
            "requests not recovered byte-identically"
        assert built["materialization"]["state"]["developmental_tag"] == "D2"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 9/10. Idempotent re-feed; empty evidence is a valid no-op
# ---------------------------------------------------------------------------

def test_idempotent_refeed():
    ev = _corpus_evidence()
    root = _tmp_root()
    try:
        e1 = _terminal_event("gate-m1", [ev[0]])
        s1 = H.on_mission_terminal(e1, root)
        assert s1["n_evidence_new"] == 1
        s1b = H.on_mission_terminal(e1, root)  # same event again
        assert s1b["ok"] and s1b["n_evidence_new"] == 0
        assert s1b["n_evidence_total"] == 1
        assert s1b["state_hash"] == s1["state_hash"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_evidence_no_change():
    ev = _corpus_evidence()
    root = _tmp_root()
    try:
        H.on_mission_terminal(_terminal_event("gate-m1", [ev[0]]), root)
        H.on_mission_terminal(_terminal_event("gate-m2", [ev[1]]), root)
        before = _dir_hashes(H._materialized_dir(root))
        # NO_RELEVANT_DEVELOPMENTAL_EVIDENCE: a real ordinary mission's event.
        s = H.on_mission_terminal(
            _terminal_event("um-op-999", [], worker_model="gpt-5",
                            origin="live_mission"), root)
        assert s["ok"] and s["n_evidence_new"] == 0
        assert s["developmental_tag"] == "D2"
        # ...but the terminal event itself was still processed: the materialization
        # was (re)computed and the log is untouched (no evidence => no new history).
        assert len(H.read_log(root)) == 2
        assert _dir_hashes(H._materialized_dir(root)) == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 11. build_terminal_event: the real path carries provenance, no evidence in v0
# ---------------------------------------------------------------------------

def test_build_terminal_event():
    from unified_machine.types import MissionResult

    class FakeWorker:
        model = "gpt-5"

    class FakeRunner:
        mission_id = "um-op-123"
        state_dir = Path("/tmp/s")
        repo = Path("/tmp/r")
        worker = FakeWorker()

    result = MissionResult(
        mission_id="um-op-123", status="complete", objective="o", repo="/tmp/r",
        repo_commit=None, steps=3, failed_acts=0, wall_seconds=1.0,
        report="r", final_event_hash="h",
    )
    event = H.build_terminal_event(FakeRunner(), result)
    assert event["event_type"] == "mission_terminal"
    assert event["mission_id"] == "um-op-123"
    assert event["terminal_status"] == "complete"
    assert event["worker"] == {"kind": "openai", "model": "gpt-5"}
    assert event["developmental_evidence"] == []  # v0: nothing authorized
    # The hook accepts it and D stays at D0.
    root = _tmp_root()
    try:
        s = H.on_mission_terminal(event, root)
        assert s["ok"] and s["developmental_tag"] == "D0"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 12/13. Live mission run through MissionRunner (stub worker, zero API):
#         hook fires post-terminal; UM_DEVELOPMENTAL_HOOK=0 disables it.
# ---------------------------------------------------------------------------

class _StubWorker:
    def next_action(self, packet):
        from unified_machine.types import Action
        return Action(kind="stop", args={"status": "blocked", "reason": "stub"})

    def report(self, packet):
        return "stub report"


def _run_stub_mission(dev_root: Path, hook_enabled: bool):
    from unified_machine.mission import MissionRunner
    repo = Path(tempfile.mkdtemp(prefix="u2a-repo-"))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    state_dir = Path(tempfile.mkdtemp(prefix="u2a-state-"))
    old_hook = os.environ.get("UM_DEVELOPMENTAL_HOOK")
    old_dir = os.environ.get("UM_DEVELOPMENTAL_DIR")
    os.environ["UM_DEVELOPMENTAL_HOOK"] = "1" if hook_enabled else "0"
    os.environ["UM_DEVELOPMENTAL_DIR"] = str(dev_root)
    try:
        runner = MissionRunner(
            objective="stub mission for the U2A gate",
            repo=repo,
            state_dir=state_dir,
            worker=_StubWorker(),
            budget=4,
            mission_id="um-stub-001",
        )
        result = runner.run()
        return result, state_dir, repo
    finally:
        if old_hook is None:
            os.environ.pop("UM_DEVELOPMENTAL_HOOK", None)
        else:
            os.environ["UM_DEVELOPMENTAL_HOOK"] = old_hook
        if old_dir is None:
            os.environ.pop("UM_DEVELOPMENTAL_DIR", None)
        else:
            os.environ["UM_DEVELOPMENTAL_DIR"] = old_dir


def test_mission_run_fires_hook():
    dev_root = _tmp_root()
    try:
        result, state_dir, repo = _run_stub_mission(dev_root, hook_enabled=True)
        try:
            assert result.status == "blocked"  # ordinary work proceeded normally
            # The hook ran post-terminal on a fresh root: the materialization exists
            # and is D0 (no evidence harvested in v0 -> correctly unchanged).
            state = json.loads((H._materialized_dir(dev_root) / "state.json").read_text())
            assert state["developmental_tag"] == "D0" and state["n_surviving"] == 782
            assert state["n_evidence"] == 0
            # The mission ledger itself is intact and verified.
            from unified_machine.ledger import Ledger
            Ledger(state_dir).verify()
        finally:
            shutil.rmtree(state_dir, ignore_errors=True)
            shutil.rmtree(repo, ignore_errors=True)
    finally:
        shutil.rmtree(dev_root, ignore_errors=True)


def test_mission_run_hook_disabled():
    dev_root = _tmp_root()
    try:
        result, state_dir, repo = _run_stub_mission(dev_root, hook_enabled=False)
        try:
            assert result.status == "blocked"
            assert not dev_root.exists() or not any(dev_root.iterdir()), \
                "hook ran despite UM_DEVELOPMENTAL_HOOK=0"
        finally:
            shutil.rmtree(state_dir, ignore_errors=True)
            shutil.rmtree(repo, ignore_errors=True)
    finally:
        shutil.rmtree(dev_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Runner (no pytest dependency -- same pattern as test_d0_v0.py)
# ---------------------------------------------------------------------------

TESTS = [
    test_d0_unchanged,
    test_prefix_replay_triple_equality,
    test_retrospective_correspondence,
    test_request_as_data,
    test_worker_independence,
    test_noninterference_static,
    test_hook_never_touches_mission,
    test_rmrf_recovery,
    test_idempotent_refeed,
    test_no_evidence_no_change,
    test_build_terminal_event,
    test_mission_run_fires_hook,
    test_mission_run_hook_disabled,
]


def main() -> int:
    passed = 0
    failed = []
    for fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"  PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed.append((fn.__name__, repr(exc)))
            print(f"  FAIL {fn.__name__}: {exc!r}")
    print(f"{passed}/{len(TESTS)} integration tests passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
