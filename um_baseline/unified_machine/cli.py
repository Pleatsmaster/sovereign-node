from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

from .improve import evaluate_candidate, propose_candidate
from .lineage import promote_candidate
from .ledger import Ledger
from .mission import MissionRunner
from .residue import ResidueStore, default_residue_dir
from .types import WorkerSpec
from .workers import build_worker


def _worker_spec(args: argparse.Namespace) -> WorkerSpec:
    if args.worker == "openai":
        model = args.model or os.environ.get("UM_MODEL")
        if not model:
            raise SystemExit("--model or UM_MODEL is required for openai worker")
        return WorkerSpec(kind="openai", model=model)
    cmd = json.loads(args.worker_command_json or "[]")
    if not isinstance(cmd, list) or not cmd:
        raise SystemExit("--worker-command-json must be a JSON list")
    return WorkerSpec(kind="command", command=cmd)


def _add_worker_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--worker", choices=["openai", "command"], default="command")
    p.add_argument("--model")
    p.add_argument("--worker-command-json")


def cmd_mission(args: argparse.Namespace) -> int:
    spec = _worker_spec(args)
    worker = build_worker(spec)
    acceptance = json.loads(args.acceptance_json or "[]")
    if args.worker_label:
        worker_label = args.worker_label
    elif args.worker == "openai" and args.model:
        worker_label = f"openai:{args.model}"
    else:
        worker_label = "command"
    runner = MissionRunner(
        objective=args.objective,
        repo=Path(args.repo),
        state_dir=Path(args.state_dir),
        worker=worker,
        budget=args.budget,
        acceptance=acceptance,
        mission_id=args.mission_id,
        residue_dir=Path(args.residue_dir) if args.residue_dir else None,
        worker_label=worker_label,
    )
    result = runner.run()
    obj = result.to_obj()
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    if args.json_output:
        Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_output).write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    ledger = Ledger(Path(args.state_dir))
    ledger.verify()
    print(json.dumps({"ok": True, "last_hash": ledger.last_hash()}, indent=2))
    return 0


def _residue_store(args: argparse.Namespace) -> ResidueStore:
    return ResidueStore(
        Path(args.residue_dir) if args.residue_dir else default_residue_dir()
    )


def cmd_residue_list(args: argparse.Namespace) -> int:
    store = _residue_store(args)
    state = store.current()
    records = list(state.values())
    if args.status != "all":
        records = [r for r in records if r.get("status") == args.status]
    records.sort(key=lambda r: r.get("ts", 0), reverse=True)
    records = records[: max(0, args.limit)]
    print(json.dumps(records, indent=2, ensure_ascii=False))
    return 0


def cmd_residue_retire(args: argparse.Namespace) -> int:
    store = _residue_store(args)
    try:
        record = store.retire(args.id, args.reason, actor=args.actor)
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"residue-retire failed: {exc}")
    print(json.dumps({"ok": True, "id": record["id"], "status": record["status"]}, indent=2))
    return 0


def cmd_improve_propose(args: argparse.Namespace) -> int:
    spec = _worker_spec(args)
    worker = build_worker(spec)
    self_repo = Path(args.self_repo).resolve()
    cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self_repo, text=True, capture_output=True)
    if cp.returncode != 0:
        raise SystemExit("self-repo must be a git repository")
    parent = args.parent_commit or cp.stdout.strip()
    source_missions = [x for x in (args.source_missions or "").split(",") if x]
    result = propose_candidate(
        worker=worker,
        self_repo=self_repo,
        state_dir=Path(args.state_dir),
        parent_commit=parent,
        source_missions=source_missions,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_improve_eval(args: argparse.Namespace) -> int:
    spec = _worker_spec(args)
    smoke = json.loads(args.smoke_json or '[["python","-m","pytest","-q"]]')
    result = evaluate_candidate(
        self_repo=Path(args.self_repo),
        state_dir=Path(args.state_dir),
        candidate_id=args.candidate_id,
        task_pack=Path(args.task_pack),
        worker_spec=spec,
        smoke_commands=smoke,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0



def cmd_promote(args: argparse.Namespace) -> int:
    smoke = json.loads(args.smoke_json or '[["python","-m","pytest","-q"]]')
    result = promote_candidate(
        self_repo=Path(args.self_repo),
        state_dir=Path(args.state_dir),
        candidate_id=args.candidate_id,
        smoke_commands=smoke,
    )
    Ledger(Path(args.state_dir)).append("candidate_promoted", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_cycle(args: argparse.Namespace) -> int:
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    spec = WorkerSpec.from_obj(cfg["worker"])
    worker = build_worker(spec)
    mission_cfg = cfg["mission"]
    runner = MissionRunner(
        objective=mission_cfg["objective"],
        repo=Path(mission_cfg["repo"]),
        state_dir=Path(cfg["state_dir"]),
        worker=worker,
        budget=int(mission_cfg.get("budget", 12)),
        acceptance=mission_cfg.get("acceptance", []),
    )
    mission_result = runner.run()
    out = {"mission": mission_result.to_obj()}
    improve_cfg = cfg.get("self_improvement")
    if not improve_cfg or not improve_cfg.get("enabled", False):
        out["self_improvement"] = {"status": "DISABLED"}
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    self_repo = Path(improve_cfg["self_repo"]).resolve()
    parent = improve_cfg.get("parent_commit")
    if not parent:
        active_path = Path(cfg["state_dir"]) / "active_parent.json"
        if active_path.exists():
            parent = json.loads(active_path.read_text(encoding="utf-8"))["commit"]
        else:
            cp = subprocess.run(["git","rev-parse","HEAD"], cwd=self_repo, text=True, capture_output=True, check=True)
            parent = cp.stdout.strip()
    proposal = propose_candidate(
        worker=worker,
        self_repo=self_repo,
        state_dir=Path(cfg["state_dir"]),
        parent_commit=parent,
        source_missions=[mission_result.mission_id],
    )
    out["self_improvement"] = {"proposal": proposal}
    if proposal.get("status") != "CANDIDATE":
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    cid = proposal["candidate"]["candidate_id"]
    evaluation = evaluate_candidate(
        self_repo=self_repo,
        state_dir=Path(cfg["state_dir"]),
        candidate_id=cid,
        task_pack=Path(improve_cfg["task_pack"]),
        worker_spec=spec,
        smoke_commands=improve_cfg.get("smoke", [["python","-m","pytest","-q"]]),
    )
    out["self_improvement"]["evaluation"] = evaluation
    if evaluation.get("verdict") == "CHILD_WINS" and improve_cfg.get("auto_promote", False):
        promoted = promote_candidate(
            self_repo=self_repo,
            state_dir=Path(cfg["state_dir"]),
            candidate_id=cid,
            smoke_commands=improve_cfg.get("smoke", [["python","-m","pytest","-q"]]),
        )
        Ledger(Path(cfg["state_dir"])).append("candidate_promoted", promoted)
        out["self_improvement"]["promotion"] = promoted
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="unified-machine")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mission", help="run one autonomous real-work mission")
    m.add_argument("--objective", required=True)
    m.add_argument("--repo", required=True)
    m.add_argument("--state-dir", default=".unified-state")
    m.add_argument("--budget", type=int, default=12)
    m.add_argument("--acceptance-json", default="[]")
    m.add_argument("--mission-id")
    m.add_argument("--json-output")
    m.add_argument("--residue-dir", default=None,
                   help="residue store directory (default: $UM_RESIDUE_DIR or ~/.unified-machine/residue)")
    m.add_argument("--worker-label", default=None,
                   help="label recorded as the residue author (default: openai:<model> or 'command')")
    _add_worker_args(m)
    m.set_defaults(func=cmd_mission)

    v = sub.add_parser("verify", help="verify the persistent hash chain")
    v.add_argument("--state-dir", default=".unified-state")
    v.set_defaults(func=cmd_verify)

    ip = sub.add_parser("improve-propose", help="propose at most one self-change from closed evidence")
    ip.add_argument("--self-repo", required=True)
    ip.add_argument("--state-dir", default=".unified-state")
    ip.add_argument("--parent-commit")
    ip.add_argument("--source-missions", default="")
    _add_worker_args(ip)
    ip.set_defaults(func=cmd_improve_propose)

    ie = sub.add_parser("improve-eval", help="evaluate a frozen candidate on a hidden task pack")
    ie.add_argument("--self-repo", required=True)
    ie.add_argument("--state-dir", default=".unified-state")
    ie.add_argument("--candidate-id", required=True)
    ie.add_argument("--task-pack", required=True)
    ie.add_argument("--smoke-json")
    _add_worker_args(ie)
    ie.set_defaults(func=cmd_improve_eval)

    pr = sub.add_parser("promote", help="materialize an evaluated winner as a lineage branch")
    pr.add_argument("--self-repo", required=True)
    pr.add_argument("--state-dir", default=".unified-state")
    pr.add_argument("--candidate-id", required=True)
    pr.add_argument("--smoke-json")
    pr.set_defaults(func=cmd_promote)

    cyc = sub.add_parser("cycle", help="run one real mission and, if enabled, one gated self-improvement cycle")
    cyc.add_argument("--config", required=True)
    cyc.set_defaults(func=cmd_cycle)

    rl = sub.add_parser("residue-list", help="list current residue records")
    rl.add_argument("--residue-dir", default=None)
    rl.add_argument("--status", choices=["active", "retired", "all"], default="active")
    rl.add_argument("--limit", type=int, default=20)
    rl.set_defaults(func=cmd_residue_list)

    rr = sub.add_parser("residue-retire", help="retire a residue (appended record; reversible by re-admission)")
    rr.add_argument("--id", required=True)
    rr.add_argument("--reason", required=True)
    rr.add_argument("--residue-dir", default=None)
    rr.add_argument("--actor", default="operator")
    rr.set_defaults(func=cmd_residue_retire)

    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
