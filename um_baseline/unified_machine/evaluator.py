from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from .types import WorkerSpec


def load_task_pack(task_pack: Path) -> List[Dict[str, Any]]:
    """Load a hidden task pack from JSON.

    Accepted shapes:
      [ {...}, {...} ]
      {"tasks": [ {...}, {...} ]}

    Each task:
      {
        "id": "task-1",
        "objective": "...",
        "repo_template": "path or null",   # resolved relative to the pack file
        "budget": 8,
        "acceptance": [["python","-c","print('ok')"]],
        "checker": "path or null"          # resolved relative to the pack file;
                                          # checker.py <repo_dir> <template_dir>
                                          # prints JSON {"pass": bool, "details": str}
      }
    """
    pack_path = Path(task_pack)
    obj = json.loads(pack_path.read_text(encoding="utf-8"))
    tasks = obj["tasks"] if isinstance(obj, dict) and "tasks" in obj else obj
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task pack must contain a non-empty task list")
    base = pack_path.parent
    normalized = []
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            raise ValueError(f"task {i} must be an object")
        tid = str(t.get("id") or f"task-{i}")
        objective = t.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError(f"task {tid} needs a non-empty objective")
        repo_template = t.get("repo_template")
        if repo_template:
            repo_template = str((base / repo_template).resolve())
        checker = t.get("checker")
        if checker:
            checker = str((base / checker).resolve())
            if not Path(checker).exists():
                raise ValueError(f"task {tid} checker not found: {checker}")
        normalized.append(
            {
                "id": tid,
                "objective": objective,
                "repo_template": repo_template,
                "budget": int(t.get("budget", 8)),
                "acceptance": t.get("acceptance", []),
                "checker": checker,
            }
        )
    return normalized


def _worker_cli_args(spec: WorkerSpec) -> List[str]:
    if spec.kind == "openai":
        if not spec.model:
            raise ValueError("openai worker needs a model")
        return ["--worker", "openai", "--model", spec.model]
    if spec.kind == "command":
        if not spec.command:
            raise ValueError("command worker needs a command")
        return ["--worker", "command", "--worker-command-json", json.dumps(spec.command)]
    raise ValueError(f"unknown worker kind: {spec.kind}")


def _fresh_repo(task: Dict[str, Any], dest: Path) -> Path:
    """Create a fresh copy of the task's real-work repository."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    template = task.get("repo_template")
    if template:
        src = Path(template)
        if not src.exists():
            raise FileNotFoundError(f"repo_template not found: {src}")
        shutil.copytree(src, dest, symlinks=True)
    else:
        dest.mkdir(parents=True, exist_ok=True)
        # Make it a git repo so workspace git ops don't fail.
        subprocess.run(["git", "init", "-q"], cwd=dest, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=test@local",
             "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=dest,
            capture_output=True,
        )
    return dest


def _run_checker(
    checker: Path, repo_dir: Path, template_dir: Path | None
) -> tuple[bool, str]:
    """Run a task's objective checker: checker.py <repo_dir> <template_dir>.

    The checker prints JSON {"pass": bool, "details": str} and always exits 0.
    Any deviation (crash, non-zero exit, non-JSON output) counts as not correct.
    """
    try:
        cp = subprocess.run(
            [sys.executable, str(checker), str(repo_dir), str(template_dir or "")],
            text=True,
            capture_output=True,
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"checker error: {type(exc).__name__}: {exc}"
    if cp.returncode != 0:
        return False, f"checker exited {cp.returncode}: {(cp.stderr or cp.stdout)[-500:]}"
    try:
        obj = json.loads(cp.stdout.strip().split("\n")[-1])
    except Exception as exc:  # noqa: BLE001
        return False, f"checker output not JSON: {exc}"
    if not isinstance(obj, dict) or "pass" not in obj:
        return False, "checker output missing 'pass' field"
    return bool(obj["pass"]), str(obj.get("details", ""))


def _run_one_task(
    *,
    code_root: Path,
    task: Dict[str, Any],
    worker_spec: WorkerSpec,
    eval_root: Path,
    arm: str,
) -> Dict[str, Any]:
    task_dir = eval_root / arm / task["id"]
    repo_dir = task_dir / "repo"
    state_dir = task_dir / "state"
    _fresh_repo(task, repo_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "unified_machine", "mission",
        "--objective", task["objective"],
        "--repo", str(repo_dir),
        "--state-dir", str(state_dir),
        "--budget", str(task["budget"]),
        "--acceptance-json", json.dumps(task["acceptance"]),
        *_worker_cli_args(worker_spec),
    ]
    started = time.time()
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(code_root),
            text=True,
            capture_output=True,
            timeout=1800,
        )
        wall = time.time() - started
        if cp.returncode != 0:
            return {
                "id": task["id"],
                "accepted": False,
                "completed": False,
                "correct": False,
                "checker_details": "mission did not run",
                "failed_acts": 10**6,
                "steps": 10**6,
                "wall_seconds": wall,
                "interventions": 0,
                "status": "mission_error",
                "stderr": cp.stderr[-2000:],
            }
        # The mission CLI prints a single pretty-printed JSON object to stdout.
        try:
            obj = json.loads(cp.stdout)
        except json.JSONDecodeError:
            obj = json.loads(cp.stdout.strip().split("\n")[-1])
        status = str(obj.get("status", "unknown"))
        completed = status == "complete"
        # Correctness comes from the objective checker, not the worker's claim.
        # Tasks without a checker keep the legacy behavior (claim stands).
        correct = completed
        checker_details = ""
        if task.get("checker"):
            correct, checker_details = _run_checker(
                Path(task["checker"]),
                repo_dir,
                Path(task["repo_template"]) if task.get("repo_template") else None,
            )
        return {
            "id": task["id"],
            "accepted": completed,
            "completed": completed,
            "correct": correct,
            "checker_details": checker_details,
            "failed_acts": int(obj.get("failed_acts", 0)),
            "steps": int(obj.get("steps", 0)),
            "wall_seconds": float(obj.get("wall_seconds", wall)),
            "interventions": 0,
            "status": status,
        }
    except subprocess.TimeoutExpired:
        return {
            "id": task["id"],
            "accepted": False,
            "completed": False,
            "correct": False,
            "checker_details": "mission timed out",
            "failed_acts": 10**6,
            "steps": 10**6,
            "wall_seconds": time.time() - started,
            "interventions": 0,
            "status": "timeout",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "id": task["id"],
            "accepted": False,
            "completed": False,
            "correct": False,
            "checker_details": f"evaluator error: {exc}",
            "failed_acts": 10**6,
            "steps": 10**6,
            "wall_seconds": time.time() - started,
            "interventions": 0,
            "status": f"error: {type(exc).__name__}: {exc}",
        }


def run_arm(
    *,
    arm: str,
    code_root: Path,
    tasks: List[Dict[str, Any]],
    worker_spec: WorkerSpec,
    eval_root: Path,
) -> Dict[str, Any]:
    """Run every task in the pack with the code at code_root.

    Each task gets a fresh copy of its repository so parent and child
    cannot contaminate each other. Returns an aggregate score dict.
    """
    code_root = Path(code_root).resolve()
    eval_root = Path(eval_root).resolve()
    per_task = []
    for task in tasks:
        per_task.append(
            _run_one_task(
                code_root=code_root,
                task=task,
                worker_spec=worker_spec,
                eval_root=eval_root,
                arm=arm,
            )
        )
    completed = sum(1 for t in per_task if t["completed"])
    false_completions = sum(1 for t in per_task if t["completed"] and not t["correct"])
    return {
        "arm": arm,
        "tasks": per_task,
        "accepted": completed,
        "correct": sum(1 for t in per_task if t["correct"]),
        "false_completions": false_completions,
        "false_completion_rate": (false_completions / completed) if completed else 0.0,
        "total_failed_acts": sum(t["failed_acts"] for t in per_task),
        "total_steps": sum(t["steps"] for t in per_task),
        "total_wall": sum(t["wall_seconds"] for t in per_task),
        "total_interventions": sum(t["interventions"] for t in per_task),
    }


def _arm_summary(arm: Dict[str, Any]) -> Dict[str, Any]:
    return {
        # Diagnostics only: the worker's completion claims are cheap and can
        # be false, so they do not participate in the promotion verdict.
        "accepted": arm["accepted"],
        "false_completions": arm["false_completions"],
        "false_completion_rate": round(arm["false_completion_rate"], 4),
        "correct": arm["correct"],
        "total_failed_acts": arm["total_failed_acts"],
        "total_steps": arm["total_steps"],
        "total_wall": arm["total_wall"],
        "total_interventions": arm["total_interventions"],
    }


def compare(parent: Dict[str, Any], child: Dict[str, Any]) -> Dict[str, Any]:
    """Lexicographic comparison on externally verified research capability:

    correctness > fewer failed acts > lower intervention > lower cost.

    Self-declared completion is recorded as a diagnostic only. Evidence
    showed workers claim completion without doing the work, so optimizing
    the completion flag would reward saying "done" rather than being right.
    """
    verdict = "TIE"
    for p_val, c_val, higher_wins in (
        (parent["correct"], child["correct"], True),
        (parent["total_failed_acts"], child["total_failed_acts"], False),
        (parent["total_interventions"], child["total_interventions"], False),
        (parent["total_wall"], child["total_wall"], False),
    ):
        if c_val == p_val:
            continue
        child_better = (c_val > p_val) if higher_wins else (c_val < p_val)
        verdict = "CHILD_WINS" if child_better else "PARENT_WINS"
        break
    return {
        "verdict": verdict,
        "parent": _arm_summary(parent),
        "child": _arm_summary(child),
        "tasks_parent": parent["tasks"],
        "tasks_child": child["tasks"],
    }
