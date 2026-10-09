from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from .evaluator import compare, load_task_pack, run_arm
from .ledger import Ledger
from .lineage import LineageStore, create_child_worktree
from .policy import SelfChangePolicy
from .types import WorkerSpec
from .workers import Worker


IMPROVEMENT_CONTRACT = """
You are proposing at most one executable change to the research machine itself (the organism around the fixed base model).
The objective is not to make a diagnostic score move. The change must address a recurring deficiency visible in closed mission evidence and plausibly improve unseen future work.
Eligible mutation surface: research policy/prompt, planning, tool-use policy, stopping behavior, artifact handling, context construction, memory/retrieval, harness logic that affects research behavior.
Frozen and untouchable: the evaluator, lineage machinery, ledger, tests, the hidden evaluation task pack, and the objective checkers. Base model weights are fixed for this generation.
If the evidence does not identify a reusable deficiency, return NO_CHANGE.
If proposing a candidate, first write exactly one line starting with the marker INTENT: stating the observed recurring deficiency D and your change in the form "D -> Δ" (one sentence). Then provide the unified git diff and nothing else.
""".strip()


def _recent_missions(ledger: Ledger, limit: int = 12) -> List[Dict[str, Any]]:
    return ledger.search_memory("", limit=limit)


def propose_candidate(
    *,
    worker: Worker,
    self_repo: Path,
    state_dir: Path,
    parent_commit: str,
    source_missions: List[str],
) -> Dict[str, Any]:
    ledger = Ledger(state_dir)
    evidence = _recent_missions(ledger)
    packet = {
        "mode": "self_improvement_proposal",
        "contract": IMPROVEMENT_CONTRACT,
        "parent_commit": parent_commit,
        "closed_evidence": evidence,
        "repo": str(Path(self_repo).resolve()),
        "frozen_paths": list(SelfChangePolicy().immutable),
        "instruction": "Inspect source if needed through a separate ordinary mission first. For this call, report NO_CHANGE or provide INTENT + unified diff.",
    }
    # report() is used deliberately: this is not an action in an ordinary task.
    text = worker.report(packet).strip()
    if text.upper().startswith("NO_CHANGE"):
        ledger.append("improvement_no_change", {"parent_commit": parent_commit, "text": text})
        return {"status": "NO_CHANGE", "text": text}
    marker = "INTENT:"
    if marker not in text:
        raise ValueError("candidate response missing INTENT marker")
    intent, rest = text.split(marker, 1)[1].split("\n", 1)
    patch_start = rest.find("diff --git ")
    if patch_start < 0:
        raise ValueError("candidate response contains no unified diff")
    patch = rest[patch_start:]
    store = LineageStore(state_dir)
    candidate, patch_path = store.create_candidate(
        parent_commit=parent_commit,
        patch=patch,
        source_missions=source_missions,
        intent=intent.strip(),
    )
    ledger.append("candidate_created", candidate.to_obj())
    return {"status": "CANDIDATE", "candidate": candidate.to_obj(), "patch_path": str(patch_path)}


def evaluate_candidate(
    *,
    self_repo: Path,
    state_dir: Path,
    candidate_id: str,
    task_pack: Path,
    worker_spec: WorkerSpec,
    smoke_commands: List[List[str]],
) -> Dict[str, Any]:
    self_repo = Path(self_repo).resolve()
    store = LineageStore(state_dir)
    cdir = store.root / candidate_id
    manifest = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
    patch_path = cdir / "candidate.patch"
    parent_commit = manifest["parent_commit"]
    tasks = load_task_pack(task_pack)

    with tempfile.TemporaryDirectory(prefix="um-child-") as td, tempfile.TemporaryDirectory(prefix="um-eval-") as ed:
        child = Path(td) / "child"
        create_child_worktree(self_repo, parent_commit, patch_path, child)
        smoke = []
        smoke_ok = True
        for argv in smoke_commands:
            cp = subprocess.run(argv, cwd=child, text=True, capture_output=True)
            row = {"argv": argv, "returncode": cp.returncode, "stdout": cp.stdout[-8000:], "stderr": cp.stderr[-8000:]}
            smoke.append(row)
            smoke_ok = smoke_ok and cp.returncode == 0
        if not smoke_ok:
            result = {"verdict": "SMOKE_FAIL", "smoke": smoke}
            store.record_evaluation(candidate_id, result)
            store.mark_status(candidate_id, "rejected_smoke")
            return result

        eval_root = Path(ed)
        parent = run_arm(
            arm="parent",
            code_root=self_repo,
            tasks=tasks,
            worker_spec=worker_spec,
            eval_root=eval_root,
        )
        child_score = run_arm(
            arm="child",
            code_root=child,
            tasks=tasks,
            worker_spec=worker_spec,
            eval_root=eval_root,
        )
        result = compare(parent, child_score)
        result["smoke"] = smoke
        store.record_evaluation(candidate_id, result)
        store.mark_status(candidate_id, "promotable" if result["verdict"] == "CHILD_WINS" else "rejected_eval")
        Ledger(state_dir).append("candidate_evaluated", {"candidate_id": candidate_id, **result})
        return result
