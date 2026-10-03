#!/usr/bin/env python3
"""Step snapshot publication barrier.

A worker NEVER writes directly to the evaluated location. The lifecycle is:

  worker writes <staging>/            (temp dir; invisible to the evaluator)
  -> publish_step_snapshot validates the expected artifacts, hashes the
     tree, and ATOMICALLY renames <staging>/ to <snapshots>/sNN/
  -> writes <snapshots>/sNN/STEP_COMPLETED.json {step_id, snapshot_hash, ...}
  -> the evaluator reads ONLY the published snapshot, never the live
     workspace.

There is no code path by which the evaluator observes a half-written
workspace: without STEP_COMPLETED.json, evaluation refuses (exit 2) and
writes nothing to the ledger. This eliminates the 2026-10-01 failure in
which the post-step evaluator ran against worker files still landing on
disk, recorded a false failed evaluation, and polluted the append-only
chain ledger.

Staging and snapshots live OUTSIDE the mission package
(e.g. life-0/dispatch/snapshots/<need_id>/) so the d5 package-integrity
predicate never observes transient worker state.

Deterministic: stdlib only. No network, no subprocess, no model calls.
Fail-closed: anything unverifiable -> no publish, exit 2.

Exit codes: 0 = published (details on stdout);
            2 = unusable input (staging unreadable/incomplete, target exists).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

STEP_COMPLETED = "STEP_COMPLETED.json"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def tree_manifest(root: Path) -> list[dict]:
    """Deterministic manifest of every file under root (posix relpaths)."""
    items = []
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.name != STEP_COMPLETED:
            items.append({
                "path": f.relative_to(root).as_posix(),
                "sha256": sha256_file(f),
            })
    return items


def snapshot_hash(manifest: list[dict]) -> str:
    return hashlib.sha256(canonical(manifest)).hexdigest()


def verify_snapshot(snap_dir: Path):
    """Returns (record, error). Recomputes the tree hash."""
    rec_path = snap_dir / STEP_COMPLETED
    try:
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
    except Exception:
        return None, "STEP_COMPLETED.json unreadable"
    if not isinstance(rec, dict) or "snapshot_hash" not in rec:
        return None, "STEP_COMPLETED.json malformed"
    if snapshot_hash(tree_manifest(snap_dir)) != rec["snapshot_hash"]:
        return None, "snapshot tree hash does not verify: snapshot tampered or incomplete"
    return rec, None


def cmd_publish(args) -> int:
    staging = Path(args.staging)
    snaps = Path(args.snapshots_dir)
    target = snaps / f"s{args.depth:02d}"

    if not staging.is_dir():
        print(json.dumps({"published": False,
                           "reason": "staging dir missing"}), file=sys.stderr)
        return 2
    problems = []
    for rel in args.expect or []:
        p = staging / rel
        if not p.is_file():
            problems.append(f"expected artifact absent: {rel}")
        elif p.suffix == ".json":
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                problems.append(f"expected artifact not valid JSON: {rel}")
    if problems:
        print(json.dumps({"published": False, "reason": problems[0],
                           "problems": problems}), file=sys.stderr)
        return 2
    if target.exists():
        print(json.dumps({"published": False,
                           "reason": f"snapshot target exists: {target} "
                                     "(no overwrite; snapshots are immutable)"}),
              file=sys.stderr)
        return 2

    manifest = tree_manifest(staging)
    if not manifest:
        print(json.dumps({"published": False,
                           "reason": "staging dir is empty"}), file=sys.stderr)
        return 2
    shash = snapshot_hash(manifest)

    snaps.mkdir(parents=True, exist_ok=True)
    os.rename(staging, target)  # atomic: evaluator never sees a partial tree

    record = {
        "step_id": args.step_id,
        "depth": args.depth,
        "snapshot_hash": shash,
        "manifest": manifest,
        "expected": list(args.expect or []),
        "worker_status": args.worker_status,
        "published_at": utcnow_iso(),
    }
    (target / STEP_COMPLETED).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Self-check: the published snapshot must verify immediately.
    _, err = verify_snapshot(target)
    if err:
        print(json.dumps({"published": False, "reason": err}), file=sys.stderr)
        return 2

    print(json.dumps({"published": True, "snapshot": str(target),
                      "snapshot_hash": shash, "step_id": args.step_id,
                      "files": len(manifest)}, indent=2, sort_keys=True))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="publish a worker step snapshot")
    ap.add_argument("--staging", required=True,
                    help="temp dir the worker wrote to")
    ap.add_argument("--snapshots-dir", required=True,
                    help="snapshots root, e.g. life-0/dispatch/snapshots/<need_id>")
    ap.add_argument("--step-id", required=True)
    ap.add_argument("--depth", type=int, required=True)
    ap.add_argument("--expect", action="append", default=None,
                    help="relative artifact path that must exist "
                         "(repeatable)")
    ap.add_argument("--worker-status", default="completed",
                    choices=("completed", "failed"))
    return cmd_publish(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
