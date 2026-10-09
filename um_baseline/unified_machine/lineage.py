from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .policy import SelfChangePolicy


DIFF_PATH_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def patch_paths(patch: str) -> List[str]:
    paths = DIFF_PATH_RE.findall(patch)
    # deleted files may have /dev/null on +++; capture old path too.
    for old in re.findall(r"^--- a/(.+)$", patch, re.MULTILINE):
        if old not in paths:
            paths.append(old)
    return sorted(set(p for p in paths if p != "/dev/null"))


@dataclass
class Candidate:
    candidate_id: str
    parent_commit: str
    patch_sha256: str
    touched_paths: List[str]
    source_missions: List[str]
    intent: str
    created_at: float
    status: str = "candidate"

    def to_obj(self) -> Dict:
        return asdict(self)


class LineageStore:
    def __init__(self, state_dir: Path):
        self.root = Path(state_dir) / "lineage"
        self.root.mkdir(parents=True, exist_ok=True)

    def create_candidate(
        self,
        *,
        parent_commit: str,
        patch: str,
        source_missions: List[str],
        intent: str,
        policy: Optional[SelfChangePolicy] = None,
    ) -> Tuple[Candidate, Path]:
        touched = patch_paths(patch)
        if not touched:
            raise ValueError("candidate patch touches no repository paths")
        (policy or SelfChangePolicy()).validate_touched_paths(touched)
        digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        cid = digest[:16]
        candidate = Candidate(
            candidate_id=cid,
            parent_commit=parent_commit,
            patch_sha256=digest,
            touched_paths=touched,
            source_missions=source_missions,
            intent=intent,
            created_at=time.time(),
        )
        cdir = self.root / cid
        cdir.mkdir(parents=True, exist_ok=False)
        patch_path = cdir / "candidate.patch"
        patch_path.write_text(patch, encoding="utf-8")
        (cdir / "manifest.json").write_text(json.dumps(candidate.to_obj(), indent=2, sort_keys=True), encoding="utf-8")
        return candidate, patch_path

    def record_evaluation(self, candidate_id: str, evaluation: Dict) -> None:
        cdir = self.root / candidate_id
        if not cdir.exists():
            raise FileNotFoundError(candidate_id)
        (cdir / "evaluation.json").write_text(json.dumps(evaluation, indent=2, sort_keys=True), encoding="utf-8")

    def mark_status(self, candidate_id: str, status: str) -> None:
        mpath = self.root / candidate_id / "manifest.json"
        obj = json.loads(mpath.read_text(encoding="utf-8"))
        obj["status"] = status
        mpath.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def create_child_worktree(self_repo: Path, parent_commit: str, patch_path: Path, destination: Path) -> Path:
    self_repo = Path(self_repo).resolve()
    destination = Path(destination).resolve()
    cp = subprocess.run(
        ["git", "worktree", "add", "--detach", str(destination), parent_commit],
        cwd=self_repo,
        text=True,
        capture_output=True,
    )
    if cp.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {cp.stderr}")
    patch = patch_path.read_text(encoding="utf-8")
    cp = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=destination,
        input=patch,
        text=True,
        capture_output=True,
    )
    if cp.returncode != 0:
        subprocess.run(["git", "worktree", "remove", "--force", str(destination)], cwd=self_repo)
        raise RuntimeError(f"candidate patch failed to apply: {cp.stderr}")
    return destination


def promote_candidate(
    *,
    self_repo: Path,
    state_dir: Path,
    candidate_id: str,
    smoke_commands: List[List[str]],
    branch_prefix: str = "um-lineage",
) -> Dict:
    """Materialize a previously evaluated winner as a real git descendant.

    Promotion never rewrites main. It creates/updates a lineage branch pointing
    at the child commit and records that commit as the active parent in state.
    """
    self_repo = Path(self_repo).resolve()
    store = LineageStore(state_dir)
    cdir = store.root / candidate_id
    manifest_path = cdir / "manifest.json"
    eval_path = cdir / "evaluation.json"
    if not manifest_path.exists() or not eval_path.exists():
        raise FileNotFoundError("candidate must have manifest and evaluation")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evaluation = json.loads(eval_path.read_text(encoding="utf-8"))
    if evaluation.get("verdict") != "CHILD_WINS":
        raise RuntimeError("only a CHILD_WINS candidate can be promoted")
    patch_path = cdir / "candidate.patch"
    with tempfile.TemporaryDirectory(prefix="um-promote-") as td:
        child = Path(td) / "child"
        create_child_worktree(self_repo, manifest["parent_commit"], patch_path, child)
        for argv in smoke_commands:
            cp = subprocess.run(argv, cwd=child, text=True, capture_output=True)
            if cp.returncode != 0:
                raise RuntimeError(
                    f"promotion smoke failed: {argv}\n{cp.stdout[-4000:]}\n{cp.stderr[-4000:]}"
                )
        subprocess.run(["git", "add", "-A"], cwd=child, check=True)
        cp = subprocess.run(
            [
                "git",
                "-c",
                "user.name=Unified Machine",
                "-c",
                "user.email=unified-machine@local",
                "commit",
                "-m",
                f"UM promote {candidate_id}: {manifest.get('intent','candidate')}",
            ],
            cwd=child,
            text=True,
            capture_output=True,
        )
        if cp.returncode != 0:
            raise RuntimeError(f"candidate commit failed: {cp.stderr}")
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=child, text=True, capture_output=True, check=True
        ).stdout.strip()
        branch = f"{branch_prefix}/{candidate_id}"
        subprocess.run(["git", "branch", "-f", branch, commit], cwd=self_repo, check=True)
    active = {
        "candidate_id": candidate_id,
        "commit": commit,
        "branch": branch,
        "parent_commit": manifest["parent_commit"],
        "promoted_at": time.time(),
    }
    (Path(state_dir) / "active_parent.json").write_text(
        json.dumps(active, indent=2, sort_keys=True), encoding="utf-8"
    )
    store.mark_status(candidate_id, "promoted")
    return active
