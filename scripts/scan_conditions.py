#!/usr/bin/env python3
"""AGENDA-PROPOSAL-0 Stage 1: deterministic condition detection.

Reads the declared, bounded sensorium (v0) and emits CONDITION_DETECTED
records — falsifiable observations with evidence. Never goals, never
instructions, never imperatives: the record schema has no field that can
carry one, and every condition string comes from a fixed template.

Sensorium v0 (each a named, deterministic predicate):
  test_suite_status            — each failing life-0 test node id
  evidence_store_integrity     — unparsable lines in state/*.jsonl;
                                 sha256 sidecar mismatches where sidecars exist
  unresolved_or_stale_references — workspace paths named in records/*.md that
                                 no longer exist; commit_*/need_* ids named in
                                 records or state ledgers that resolve nowhere
  declared_repo_invariants     — violations of REPO_INVARIANTS_0.md

Also deterministically expires PROPOSED proposals older than 7 days.

Writes (only): state/condition_scans.jsonl, state/agenda_proposals.jsonl
(expiry events), and nothing else. Read-only toward every other store.

Exit codes: 0 = scanned (even with source errors — they are recorded, not
invented around); 2 = scanner failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "scripts"))

import agenda_common as ac  # noqa: E402

# Condition records are observations. This is the entire allowed key set —
# there is deliberately no "action", "fix", "goal", or "recommendation".
CONDITION_KEYS = ("condition_id", "source", "observed_at",
                  "condition", "evidence", "scan_id")

PATH_RE = re.compile(
    r"(~/[^\s\)'\"`,;]+|/home/hatch/workspace/[^\s\)'\"`,;]+"
    r"|sandbox://workspace/[^\s\)'\"`,;]+)")
ID_RE = re.compile(r"\b(commit_[0-9a-f]{16}|need_[0-9a-f]{32})\b")
INVARIANTS_FENCE_RE = re.compile(r"```invariants\n(.*?)\n```", re.DOTALL)

TRAILING_PUNCT = ".,;:!?)]}'\""


def _strip_trailing(ref: str) -> str:
    return ref.rstrip(TRAILING_PUNCT)


def _resolve_ref(ref: str) -> Path | None:
    ref = _strip_trailing(ref)
    if ref.startswith("sandbox://workspace/"):
        return Path.home() / "workspace" / ref[len("sandbox://workspace/"):]
    if ref.startswith("~/"):
        return Path.home() / ref[2:]
    if ref.startswith("/home/hatch/workspace/"):
        return Path(ref)
    return None


def _condition(source: str, text: str, evidence: dict,
               observed_at: str, scan_id: str) -> dict:
    cond = {
        "condition_id": ac.condition_id_for(source, text),
        "source": source,
        "observed_at": observed_at,
        "condition": text,
        "evidence": evidence,
        "scan_id": scan_id,
    }
    assert set(cond.keys()) == set(CONDITION_KEYS), "observation schema drift"
    return cond


# --------------------------------------------------------------------------
# Source 1: test_suite_status
# --------------------------------------------------------------------------

def scan_test_suite(life0: Path, tests_dir: Path,
                    observed_at: str, scan_id: str) -> tuple[list[dict], list[str]]:
    """Each failing test node id becomes one condition.

    A failing test is an observation ("test X is failing"), never an
    instruction. What — if anything — should be done about it is Stage 2's
    question, and only if the gate holds.
    """
    errors: list[str] = []
    if not tests_dir.is_dir():
        errors.append(f"tests dir missing: {tests_dir}")
        return [], errors
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir),
             "-q", "--tb=no", "-rf"],
            capture_output=True, text=True, timeout=600, cwd=str(life0))
    except Exception as e:  # fail-closed: record the error, invent nothing
        errors.append(f"pytest runner failed: {e}")
        return [], errors

    failed: list[str] = []
    summary = ""
    for line in r.stdout.splitlines():
        if line.startswith("FAILED "):
            node = line[len("FAILED "):].split(" - ")[0].strip()
            if node:
                failed.append(node)
        elif "passed" in line or "failed" in line or "error" in line:
            summary = line.strip()
    conditions = [
        _condition(
            "test_suite_status",
            f"life-0 test failing: {node}",
            {"node_id": node, "pytest_exit_code": r.returncode,
             "summary": summary},
            observed_at, scan_id)
        for node in sorted(set(failed))
    ]
    return conditions, errors


# --------------------------------------------------------------------------
# Source 2: evidence_store_integrity
# --------------------------------------------------------------------------

def _verify_sidecar(path: Path) -> str | None:
    """Return an error string on mismatch, None when OK or no sidecar."""
    sidecar = path.with_suffix(path.suffix + ".sha256")
    alt = path.parent / (path.name + ".sha256")
    sc = sidecar if sidecar.exists() else (alt if alt.exists() else None)
    if sc is None:
        return None
    expected = sc.read_text(encoding="utf-8").strip().split()[0]
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    if h.hexdigest() != expected:
        return (f"sha256 mismatch for {path.name}: recorded {expected[:12]}…, "
                f"observed {h.hexdigest()[:12]}…")
    return None


def scan_evidence_integrity(life0: Path, observed_at: str,
                            scan_id: str) -> tuple[list[dict], list[str]]:
    conditions: list[dict] = []
    state_dir = life0 / "state"
    for jf in sorted(state_dir.glob("*.jsonl")):
        bad_lines: list[int] = []
        try:
            for i, line in enumerate(
                    jf.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    bad_lines.append(i)
        except OSError as e:
            bad_lines = [-1]
            conditions.append(_condition(
                "evidence_store_integrity",
                f"state ledger unreadable: {jf.name}",
                {"file": jf.name, "error": str(e)}, observed_at, scan_id))
            continue
        if bad_lines:
            conditions.append(_condition(
                "evidence_store_integrity",
                f"state ledger has unparsable lines: {jf.name}",
                {"file": jf.name, "bad_lines": bad_lines},
                observed_at, scan_id))
        mismatch = _verify_sidecar(jf)
        if mismatch:
            conditions.append(_condition(
                "evidence_store_integrity", mismatch,
                {"file": jf.name}, observed_at, scan_id))
    for rec in sorted((life0 / "records").glob("*")):
        if rec.is_file():
            mismatch = _verify_sidecar(rec)
            if mismatch:
                conditions.append(_condition(
                    "evidence_store_integrity", mismatch,
                    {"file": f"records/{rec.name}"}, observed_at, scan_id))
    return conditions, []


# --------------------------------------------------------------------------
# Source 3: unresolved_or_stale_references
# --------------------------------------------------------------------------

def _registry_ids(life0: Path) -> tuple[set[str], set[str]]:
    commits, needs = set(), set()
    for r in ac.read_jsonl(life0 / "state" / "commitments.jsonl"):
        c = r.get("commitment") or {}
        if c.get("commitment_id"):
            commits.add(c["commitment_id"])
    for r in ac.read_jsonl(life0 / "state" / "needs.jsonl"):
        n = r.get("need") or {}
        if n.get("need_id"):
            needs.add(n["need_id"])
        # NEED_STATUS events carry need_id at top level too
        if r.get("need_id"):
            needs.add(r["need_id"])
    return commits, needs


def scan_references(life0: Path, observed_at: str,
                    scan_id: str) -> tuple[list[dict], list[str]]:
    conditions: list[dict] = []
    commits, needs = _registry_ids(life0)

    records_dir = life0 / "records"
    md_files = sorted(records_dir.glob("*.md")) if records_dir.is_dir() else []
    for md in md_files:
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in PATH_RE.finditer(line):
                ref = _strip_trailing(m.group(0))
                target = _resolve_ref(ref)
                if target is None:
                    continue
                if not target.exists():
                    conditions.append(_condition(
                        "unresolved_or_stale_references",
                        f"records reference missing path: {ref}",
                        {"record": f"records/{md.name}", "line": i,
                         "ref": ref, "resolved": str(target)},
                        observed_at, scan_id))

    # Dangling commit_/need_ ids across records and state ledgers.
    corpus: list[Path] = list(md_files)
    state_dir = life0 / "state"
    if state_dir.is_dir():
        corpus += sorted(state_dir.glob("*.jsonl"))
    seen_missing: set[str] = set()
    for path in corpus:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in ID_RE.finditer(line):
                ref_id = m.group(0)
                ok = (ref_id in commits) if ref_id.startswith("commit_") \
                    else (ref_id in needs)
                if not ok and ref_id not in seen_missing:
                    seen_missing.add(ref_id)
                    rel = str(path.relative_to(life0))
                    conditions.append(_condition(
                        "unresolved_or_stale_references",
                        f"dangling reference with no registry entry: {ref_id}",
                        {"file": rel, "line": i, "ref_id": ref_id},
                        observed_at, scan_id))
    return conditions, []


# --------------------------------------------------------------------------
# Source 4: declared_repo_invariants
# --------------------------------------------------------------------------

def scan_invariants(life0: Path, invariants_path: Path,
                    observed_at: str,
                    scan_id: str) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    conditions: list[dict] = []
    if not invariants_path.exists():
        errors.append(f"invariants file missing: {invariants_path}")
        return [], errors
    try:
        text = invariants_path.read_text(encoding="utf-8")
    except OSError as e:
        errors.append(f"invariants file unreadable: {e}")
        return [], errors
    m = INVARIANTS_FENCE_RE.search(text)
    if not m:
        errors.append("no ```invariants fenced JSON block found")
        return [], errors
    try:
        invariants = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        errors.append(f"invariants block is not valid JSON: {e}")
        return [], errors

    for inv in invariants:
        inv_id = inv.get("id", "?")
        check = inv.get("check")
        rel = inv.get("path", "")
        target = life0 / rel
        violated = False
        detail: dict = {"invariant_id": inv_id, "check": check, "path": rel}
        if check == "file-sha256":
            expected = inv.get("expected_sha256", "")
            if not target.is_file():
                violated = True
                detail["observed"] = "missing"
            else:
                h = hashlib.sha256(target.read_bytes()).hexdigest()
                detail["observed_sha256"] = h
                if h != expected:
                    violated = True
                    detail["expected_sha256"] = expected
        elif check == "file-exists":
            if not target.is_file():
                violated = True
                detail["observed"] = "missing"
        else:
            errors.append(f"unknown check {check!r} in {inv_id}")
            continue
        if violated:
            conditions.append(_condition(
                "declared_repo_invariants",
                f"declared invariant violated: {inv_id} "
                f"({inv.get('description', 'no description')})",
                detail, observed_at, scan_id))
    return conditions, errors


# --------------------------------------------------------------------------
# Expiry + driver
# --------------------------------------------------------------------------

def expire_stale_proposals(life0: Path, now: datetime) -> list[str]:
    """Mark PROPOSED entries older than EXPIRY_DAYS as EXPIRED. Deterministic."""
    expired: list[str] = []
    cutoff = now - timedelta(days=ac.EXPIRY_DAYS)
    for pid, entry in ac.load_proposal_ledger(life0).items():
        if entry["state"] != "PROPOSED":
            continue
        try:
            proposed_at = datetime.fromisoformat(entry["proposal"]["proposed_at"])
        except (ValueError, TypeError, KeyError):
            continue
        if proposed_at.tzinfo is None:
            proposed_at = proposed_at.replace(tzinfo=timezone.utc)
        if proposed_at < cutoff:
            ac.append_jsonl(
                life0 / "state" / "agenda_proposals.jsonl",
                {"event": "PROPOSAL_EXPIRED", "at": ac.utcnow_iso(),
                 "proposal_id": pid,
                 "policy_version": ac.POLICY_VERSION})
            expired.append(pid)
    return expired


def scan(life0: Path, tests_dir: Path, invariants_path: Path) -> dict:
    observed_at = ac.utcnow_iso()
    conditions: list[dict] = []
    source_errors: list[str] = []

    # scan_id must be stable within this scan; the hash excludes it anyway.
    scan_id = "scan_" + ac.sha_hex(observed_at)[:12]

    for fn in (lambda: scan_test_suite(life0, tests_dir, observed_at, scan_id),
               lambda: scan_evidence_integrity(life0, observed_at, scan_id),
               lambda: scan_references(life0, observed_at, scan_id),
               lambda: scan_invariants(life0, invariants_path,
                                       observed_at, scan_id)):
        conds, errs = fn()
        conditions.extend(conds)
        source_errors.extend(errs)

    # Deterministic order: condition_id sort. Duplicate observations from
    # overlapping predicates collapse to one (same id by construction).
    dedup: dict[str, dict] = {}
    for c in conditions:
        dedup[c["condition_id"]] = c
    conditions = [dedup[k] for k in sorted(dedup)]

    csh = ac.condition_set_hash(conditions)
    ac.append_jsonl(
        life0 / "state" / "condition_scans.jsonl",
        {"event": "SCAN_RECORDED", "scan_id": scan_id,
         "scanned_at": observed_at, "conditions": conditions,
         "condition_set_hash": csh, "source_errors": source_errors,
         "policy_version": ac.POLICY_VERSION})

    expired = expire_stale_proposals(
        life0, datetime.now(timezone.utc))

    return {"scan_id": scan_id,
            "condition_count": len(conditions),
            "condition_set_hash": csh,
            "source_errors": source_errors,
            "expired_proposals": expired,
            "policy_version": ac.POLICY_VERSION}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="AGENDA-PROPOSAL-0 Stage 1: deterministic condition scan.")
    ap.add_argument("--life0", default=str(LIFE0_ROOT))
    ap.add_argument("--tests-dir", default=None,
                    help="pytest target dir (default: <life0>/tests)")
    ap.add_argument("--invariants", default=None,
                    help="invariants file (default: <life0>/REPO_INVARIANTS_0.md)")
    args = ap.parse_args(argv)

    life0 = Path(args.life0)
    tests_dir = Path(args.tests_dir) if args.tests_dir else life0 / "tests"
    invariants = (Path(args.invariants) if args.invariants
                  else life0 / "REPO_INVARIANTS_0.md")
    try:
        result = scan(life0, tests_dir, invariants)
    except Exception as e:
        print(json.dumps({"scanned": False, "error": str(e)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
