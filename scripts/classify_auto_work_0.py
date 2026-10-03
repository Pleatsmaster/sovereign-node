#!/usr/bin/env python3
"""AUTO-WORK-0 mechanical classifier.

Determines whether a staged LIFE-0 mission package satisfies the
AUTO-WORK-0 standing authority class (bounded internal analysis).

Deterministic: stdlib only. No network, no subprocess, no model calls.
Read-only, except --write which adds the verdict JSON beside the package
(additive; never mutates existing records).

The pulse stages only a reference to the operator delta ("Complete
explicitly assigned operator work from inbox file '<name>' (sha256
<sha256>)"). The charter checks therefore run against the operator's
delta file itself, resolved via observations.jsonl and verified by sha256
— the classifier confirms the staged package faithfully carries the
operator's charter, then checks that charter against the criteria.

Fail-closed: any criterion that cannot be verified -> INELIGIBLE.

Exit codes: 0 = classification completed (verdict in the JSON output);
            2 = unusable input (package unreadable, delta unresolvable).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from life0.gate import FORBIDDEN_TARGETS  # noqa: E402  (single source: pulse gate list)

AUTHORITY = "AUTO-WORK-0"
EXPECTED_SCHEMA = "life0.mission_package"
EXPECTED_STATUS = "STAGED_AWAITING_AUTHORIZATION"

PROHIBITION_MARKERS = (
    "must not", "must never", "never", "forbidden", "prohibited",
    "forbids", "without authorization",
)

# Imperative external-action patterns. Checked only against
# non-prohibitive sentences, so that a prohibition containing "spend"
# is not misread as a spend directive.
DENYLIST = (
    "deploy", "publish", "push to", "curl ", "wget ", "ssh ",
    "create account", "sign up", "enter payment", "billing", "spend",
    "payment", "api key", "private key", "upload private", "export secret",
    "send private", "grant capabilit", "enable billing",
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", text) if s.strip()]


def is_prohibitive(sentence: str) -> bool:
    low = sentence.lower()
    return any(m in low for m in PROHIBITION_MARKERS)


def prohibitive_with(text: str, stems: tuple[str, ...]) -> str | None:
    """First prohibitive sentence containing any of the stems."""
    for s in split_sentences(text):
        low = s.lower()
        if is_prohibitive(s) and any(st in low for st in stems):
            return s
    return None


def explicit_prohibition(text: str, pattern: str) -> str | None:
    """Direct regex for a prohibition phrasing (e.g. 'modifies no H, M, K').

    Complements the generic prohibitive-sentence check: charters phrase
    the same ban many ways, and a fail-closed classifier must recognize
    the ban rather than the exact wording — while still failing closed
    when no recognizable phrasing is present.
    """
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return m.group(0).strip()[:160] if m else None


def denylist_hits(text: str) -> list[str]:
    """DENYLIST patterns in non-prohibitive sentences only."""
    hits: list[str] = []
    for s in split_sentences(text):
        if is_prohibitive(s):
            continue
        low = s.lower()
        for pat in DENYLIST:
            if pat in low and pat not in hits:
                hits.append(pat)
    return hits


def local_deliverables(text: str) -> list[str]:
    """Filenames with .json/.md suffix that are not part of URLs."""
    clean = re.sub(r"https?://\S+", "", text)
    return re.findall(r"[A-Za-z0-9_.-]+\.(?:json|md)\b", clean)


def structural_criteria(pkg: dict) -> list[dict]:
    out: list[dict] = []

    def rec(cid: str, name: str, passed: bool, detail: str) -> None:
        out.append({"id": cid, "name": name, "passed": passed,
                    "detail": detail})

    rec("S1", "package schema",
        pkg.get("schema") == EXPECTED_SCHEMA,
        f"schema={pkg.get('schema')!r}")
    rec("S2", "freshly staged, awaiting authorization",
        pkg.get("status") == EXPECTED_STATUS,
        f"status={pkg.get('status')!r}")
    rec("S3", "launch not authorized",
        pkg.get("launch_authorized") is False,
        f"launch_authorized={pkg.get('launch_authorized')!r}")
    od = pkg.get("operator_decision") or {}
    # decided_by "admission": commitment packages staged by
    # COMMITMENT-ADMISSION-0 (COMMITMENT-DISPATCH-0 bridge). Same substance
    # as the pulse case: decision pending, launch not pre-authorized.
    rec("S4", "no operator decision smuggled in",
        od.get("decision") == "pending"
        and od.get("decided_by") in ("pulse", "admission"),
        f"operator_decision={od.get('decision')!r} by {od.get('decided_by')!r}")
    rec("S5", "no pre-baked worker command",
        pkg.get("worker_command") is None,
        f"worker_command={pkg.get('worker_command')!r}")
    return out


def resolve_delta(pkg: dict, life0_root: Path) -> tuple[dict | None, str]:
    """Resolve the operator delta file for the package.

    Returns (info, error). info carries delta_id, path, sha256-verified flag.
    """
    delta_id = pkg.get("delta_id")
    if not delta_id:
        return None, "package has no delta_id"
    obs = None
    obs_path = life0_root / "state" / "observations.jsonl"
    try:
        with obs_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("delta_id") == delta_id:
                    obs = o
                    break
    except FileNotFoundError:
        return None, f"observations file missing: {obs_path}"
    if obs is None:
        return None, f"no observation for delta_id {delta_id}"
    cur = obs.get("current_state") or {}
    name, sha = cur.get("name"), cur.get("sha256")
    if not name or not sha:
        return None, "observation current_state lacks name/sha256"
    # The pulse moves processed inbox files to done/.
    candidates = [life0_root / "inbox" / "done" / name,
                  life0_root / "inbox" / name]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return None, f"delta file {name} not found in inbox or inbox/done"
    actual = sha256_file(path)
    if actual != sha:
        return None, (f"delta sha256 mismatch for {name}: "
                      f"recorded {sha[:16]}… vs file {actual[:16]}…")
    return {"delta_id": delta_id, "name": name, "path": str(path),
            "sha256": sha, "sha256_verified": True}, ""


def charter_criteria(delta_text: str, objective_text: str,
                     delta_name: str) -> list[dict]:
    out: list[dict] = []

    def rec(cid: str, name: str, passed: bool, detail: str) -> None:
        out.append({"id": cid, "name": name, "passed": passed,
                    "detail": detail})

    low = delta_text.lower()
    review_lang = any(w in low for w in (
        "inspect", "review", "analyz", "existing records",
        "authoritative records", "history under review"))
    deliverables = local_deliverables(delta_text)
    rec("C1", "declared internal analysis over existing records, "
              "local deliverables",
        bool(review_lang and deliverables),
        f"review_language={review_lang} deliverables={deliverables[:4]}")

    s = (prohibitive_with(delta_text, ("promot", "install", "activat",
                                          "execut"))
         or explicit_prohibition(
             delta_text,
             r"must\s+not\s+.{0,120}(promot|install|activat|execut)"))
    rec("C2", "promotion/install/activation/execution prohibited",
        s is not None, f"evidence={s[:120] + '…' if s else None}")

    s = (prohibitive_with(delta_text, ("modif",))
         or explicit_prohibition(
             delta_text,
             r"modif\w*\s+no\b.{0,60}h,\s*m,\s*k"))
    km = s is not None and ("h, m, k" in s.lower() or "h/m/k" in s.lower()
                            or "modif" in s.lower())
    rec("C3", "K/H/M modification prohibited",
        km, f"evidence={s[:120] + '…' if s and km else None}")

    s = (prohibitive_with(delta_text, ("extern", "execut", "actuat", "deploy"))
         or explicit_prohibition(
             delta_text,
             r"must\s+not\s+.{0,120}(execut|extern|actuat|deploy)"))
    hits = denylist_hits(delta_text)
    rec("C4", "no external actuation",
        s is not None and not hits,
        f"prohibition={bool(s)} denylist_hits={hits}")

    pub = any(w in low for w in ("publish", "upload", "deploy", "release"))
    rec("C5", "outputs confined to mission workspace",
        bool(deliverables) and not pub and not hits,
        f"deliverables_local={bool(deliverables)} publish_verbs={pub}")

    rec("C6", "no spend/accounts/secrets-export directives",
        not hits, f"denylist_hits={hits}")

    rec("D3", "staged objective faithfully references the operator delta",
        delta_name in (objective_text or ""),
        f"delta_name_in_objective={delta_name in (objective_text or '')}")
    return out


def _canon_sha256(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def commitment_charter_criteria(pkg: dict, life0_root: Path,
                                pkg_dir: Path) -> list[dict]:
    """Charter checks for commitment-origin packages (no operator delta file).

    The commitment's charter is its acceptance record: objective, scope,
    origin, and completion contract. Same substance as the delta path:
    the staged package must faithfully carry the accepted charter
    (hash-verified against the registry), and the charter must not direct
    external actuation or touch forbidden targets. Admission already
    established well-formedness; these checks re-verify it at the
    AUTO-WORK-0 entry gate. Fail-closed.
    """
    out: list[dict] = []

    def rec(cid: str, name: str, passed: bool, detail: str) -> None:
        out.append({"id": cid, "name": name, "passed": passed,
                    "detail": detail})

    commitment_id = pkg.get("commitment_id")
    reg: dict | None = None
    reg_path = life0_root / "state" / "commitments.jsonl"
    try:
        with reg_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("event") == "COMMITMENT_ADMITTED":
                    c = r.get("commitment") or {}
                    if c.get("commitment_id") == commitment_id:
                        reg = c
                        break
    except FileNotFoundError:
        reg = None

    embedded = pkg.get("commitment") or {}
    faithful = (reg is not None and bool(embedded)
                and _canon_sha256(reg) == _canon_sha256(embedded))
    rec("CC1", "commitment charter resolved from registry and "
               "hash-verified against the staged package",
        faithful,
        f"registry_found={reg is not None} hash_match={faithful}")

    text = ""
    if reg is not None:
        preds = (reg.get("completion_contract") or {}).get("predicates") or []
        text = "\n".join([
            reg.get("objective") or "",
            reg.get("scope") or "",
            reg.get("origin") or "",
        ] + [str(p.get("id")) + " " + str(p.get("verifiable"))
             for p in preds if isinstance(p, dict)])
    hits = denylist_hits(text)
    rec("CC2", "no denylisted directives in the acceptance text",
        not hits, f"denylist_hits={hits}")

    low = text.lower()
    bad_targets = [t for t in FORBIDDEN_TARGETS if t.lower() in low]
    rec("CC3", "forbidden-target gate passes on the acceptance text",
        not bad_targets, f"targets={bad_targets}")

    try:
        objective_text = (pkg_dir / "OBJECTIVE.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        objective_text = ""
    rec("D3c", "staged objective faithfully references the commitment",
        bool(commitment_id) and commitment_id in objective_text,
        f"commitment_id_in_objective={bool(commitment_id) and commitment_id in objective_text}")
    return out


def classify(pkg_dir: Path) -> dict:
    life0_root = pkg_dir.parents[2]  # dispatch/staged/<need> -> life-0
    result: dict = {
        "classifier": "classify_auto_work_0.py",
        "authority": AUTHORITY,
        "package_dir": str(pkg_dir),
        "classified_at": utcnow_iso(),
    }
    try:
        pkg = json.loads((pkg_dir / "MISSION_PACKAGE.json")
                         .read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        result["error"] = f"unreadable MISSION_PACKAGE.json: {e}"
        result["verdict"] = "UNUSABLE"
        return result
    result["mission_id"] = pkg.get("mission_id")
    result["need_id"] = pkg.get("need_id")
    result["delta_id"] = pkg.get("delta_id")

    criteria = structural_criteria(pkg)
    if pkg.get("commitment_id") and not pkg.get("delta_id"):
        # Commitment-origin package: the charter is the acceptance record,
        # not an operator delta file.
        result["commitment_id"] = pkg.get("commitment_id")
        criteria += commitment_charter_criteria(pkg, life0_root, pkg_dir)
        result["criteria"] = criteria
        failed = [c["id"] for c in criteria if not c["passed"]]
        result["verdict"] = "ELIGIBLE" if not failed else "INELIGIBLE"
        result["failed_criteria"] = failed
        return result
    delta_info, err = resolve_delta(pkg, life0_root)
    if delta_info is None:
        criteria.append({"id": "D1/D2", "name": "delta resolvable and "
                         "sha256-verified", "passed": False, "detail": err})
        result["criteria"] = criteria
        result["verdict"] = "INELIGIBLE"
        return result
    result["delta_file"] = delta_info["path"]
    result["delta_sha256_verified"] = True
    objective_text = ""
    try:
        objective_text = (pkg_dir / "OBJECTIVE.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    delta_text = Path(delta_info["path"]).read_text(encoding="utf-8")
    criteria += charter_criteria(delta_text, objective_text,
                                 delta_info["name"])
    result["criteria"] = criteria
    failed = [c["id"] for c in criteria if not c["passed"]]
    result["verdict"] = "ELIGIBLE" if not failed else "INELIGIBLE"
    result["failed_criteria"] = failed
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: classify_auto_work_0.py <staged_package_dir> [--write]",
              file=sys.stderr)
        return 2
    pkg_dir = Path(argv[1])
    if not pkg_dir.is_dir():
        print(f"not a directory: {pkg_dir}", file=sys.stderr)
        return 2
    result = classify(pkg_dir)
    if result.get("verdict") == "UNUSABLE":
        print(json.dumps(result, indent=2))
        return 2
    if "--write" in argv:
        (pkg_dir / "AUTO_WORK_0_CLASSIFICATION.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
