"""U2A developmental hook: passive post-terminal integration of the D0 organ.

U2A^cand = U1 + D0. Exactly one causal integration point exists:

    mission terminal event -> D0 recomputation/update

The hook is called AFTER the mission has fully terminated (ledger verified,
MissionResult constructed). It is causally downstream: there is no path back
into the worker packet, action selection, acceptance, or artifact of any
mission -- past, current, or future.

The organ may:
  * read authorized completed-mission evidence;
  * update its deterministic materialized state;
  * record developmental provenance;
  * emit a mechanically valid TRAINING_REQUEST.

It may not:
  * modify worker context;
  * retrieve exemplars into work;
  * launch a capsule;
  * spend API money;
  * change acceptance;
  * mutate U1;
  * admit residues.

Pure-function invariant (preserved by the live integration):

    D_t = f(H_t, C_t, V)

The materialized directory is NEVER authoritative. It is a deterministic view
of the authoritative evidence log (H_t). If the materialized directory
disappears, materialize_from_log() rebuilds it byte-identically:

    rm -rf D -> f(H, C, V) -> D

TRAINING_REQUEST is data, never a command. Emitted requests live at
developmental/requests/<request_id>.json as:

    {"request": {...}, "status": "proposed", "launch_authorized": false, ...}

There is literally no executor reachable from D0. The only transition to
execution is an explicit operator act.

    machine chooses what evidence it needs; human still authorizes spend.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .d0 import ledger as L
from .d0 import request as R
from .d0 import vocabulary as V


# ---------------------------------------------------------------------------
# Frozen identities (D0-v0). The hook refuses to build on anything else.
# ---------------------------------------------------------------------------

#: sha256 of the frozen acquisition-v1 vocabulary content.
FROZEN_VOCAB_HASH = "2bbbb5a7eef58d4806eb3992c8bc06aaf1605a14b9cc895c635339bb12e0d79a"
#: sha256 of the frozen acquisition-v1 corpus examples file.
FROZEN_EXAMPLES_HASH = "fdfba6b261ed3a2e20fc8dc2f0ed8e9e8b5ffa558eeaf2ca89540f11b2d63f66"
#: Canonical frozen location of the corpus examples (C_t provenance).
FROZEN_EXAMPLES = Path(
    "/home/hatch/workspace/um-missions/_learning/corpus/acquisition-v1/examples.jsonl"
)

D0_VERSION = "0"


def _here() -> Path:
    return Path(__file__).resolve().parent


def candidate_vocab_path() -> Path:
    """The vocabulary copy owned by this candidate (U2A owns D0)."""
    return _here() / "vocabularies" / "acquisition_v1.json"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def resolve_developmental_root(explicit: Optional[str | Path] = None) -> Path:
    """Where the developmental state lives.

    Learned developmental state is organism-level (accumulates across
    missions), not per-mission: it must survive checkouts and worktrees, so
    it lives outside the lineage repo -- same rationale as the residue store.
    Precedence: explicit argument > $UM_DEVELOPMENTAL_DIR > ~/.unified-machine/developmental.
    """
    if explicit:
        return Path(explicit)
    env = os.environ.get("UM_DEVELOPMENTAL_DIR")
    if env:
        return Path(env)
    return Path.home() / ".unified-machine" / "developmental"


def _log_path(root: Path) -> Path:
    return root / "history" / "evidence_log.jsonl"


def _materialized_dir(root: Path) -> Path:
    return root / "materialized"


def _requests_dir(root: Path) -> Path:
    return root / "requests"


# ---------------------------------------------------------------------------
# Vocabulary (V) -- owned copy, verified by content hash
# ---------------------------------------------------------------------------

def load_vocab() -> dict:
    """Load the candidate-owned vocabulary; refuse unless it is D0-v0's V."""
    path = candidate_vocab_path()
    vocab = V.load_vocabulary(path)
    if V.vocabulary_hash(vocab) != FROZEN_VOCAB_HASH:
        raise ValueError(
            f"vocabulary content drift: {path} does not match frozen D0-v0 "
            f"(expected {FROZEN_VOCAB_HASH[:16]}...)"
        )
    return vocab


# ---------------------------------------------------------------------------
# Evidence validation
# ---------------------------------------------------------------------------

def validate_evidence_item(item: Any) -> Optional[dict]:
    """Check an acquisition-v1 evidence item; return normalized or None.

    Malformed items are never admitted (they would corrupt the version
    space). Returning None is a rejection, not an error.
    """
    if not isinstance(item, dict):
        return None
    eid = item.get("evidence_id")
    state_before = item.get("state_before")
    observed_effect = item.get("observed_effect")
    if not isinstance(eid, str) or not eid:
        return None
    if not isinstance(state_before, dict):
        return None
    if not isinstance(observed_effect, list) or not all(
        isinstance(e, str) for e in observed_effect
    ):
        return None
    cost = item.get("cost")
    if cost is not None and not isinstance(cost, (int, float, dict)):
        return None
    out = {
        "evidence_id": eid,
        "state_before": state_before,
        "observed_effect": list(observed_effect),
    }
    if cost is not None:
        out["cost"] = cost
    for opt in ("source_episode", "provenance"):
        if item.get(opt) is not None:
            out[opt] = item[opt]
    return out


# ---------------------------------------------------------------------------
# Authoritative history log (H_t) -- append-only, the ONLY writer is the hook
# ---------------------------------------------------------------------------

def read_log(root: Path) -> List[dict]:
    """Read the authoritative evidence log in admission order."""
    lp = _log_path(root)
    if not lp.exists():
        return []
    entries = []
    for line in lp.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))
    entries.sort(key=lambda e: e["seq"])
    return entries


def _write_log(root: Path, entries: List[dict]) -> None:
    hp = _log_path(root)
    hp.parent.mkdir(parents=True, exist_ok=True)
    tmp = hp.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n",
        encoding="utf-8",
    )
    tmp.replace(hp)


def _log_evidence(entries: List[dict]) -> List[dict]:
    """Flatten log entries to the ordered evidence list (provenance stripped).

    The worker identity recorded per entry is provenance metadata; it is NOT
    an input to f. Worker independence is structural: nothing downstream of
    this function sees it.
    """
    return [e["evidence"] for e in entries]


# ---------------------------------------------------------------------------
# Terminal events
# ---------------------------------------------------------------------------

def _worker_provenance(worker: Any) -> dict:
    """Record which worker ran the mission. Provenance only; never an input."""
    model = getattr(worker, "model", None)
    if model is not None:
        return {"kind": "openai", "model": str(model)}
    command = getattr(worker, "command", None)
    if command is not None:
        return {"kind": "command", "command": [str(c) for c in command]}
    return {"kind": type(worker).__name__}


def harvest_developmental_evidence(runner: Any, result: Any) -> List[dict]:
    """v0 harvest: only explicitly-carried acquisition-v1 evidence is authorized.

    An ordinary mission does not produce acquisition-v1 observations on its
    own; inferring them from an arbitrary trace would fabricate developmental
    evidence. v0 therefore harvests nothing here. Evidence enters D0 only
    through explicit, well-formed terminal-event payloads (integration gate,
    future explicit producers). An empty harvest is the valid
    NO_RELEVANT_DEVELOPMENTAL_EVIDENCE outcome: D0 correctly stays unchanged.
    """
    return []


def build_terminal_event(runner: Any, result: Any) -> dict:
    """Construct the terminal event for a finished mission (real path)."""
    evidence = harvest_developmental_evidence(runner, result)
    return {
        "event_type": "mission_terminal",
        "mission_id": str(getattr(runner, "mission_id", "?")),
        "terminal_status": str(getattr(result, "status", "?")),
        "finished_at": time.time(),
        "worker": _worker_provenance(getattr(runner, "worker", None)),
        "developmental_evidence": evidence,
        "mission_ref": {
            "state_dir": str(getattr(runner, "state_dir", "")),
            "repo": str(getattr(runner, "repo", "")),
        },
    }


# ---------------------------------------------------------------------------
# Materialization: D_t = f(H_t, C_t, V)
# ---------------------------------------------------------------------------

def _check_frozen_examples() -> None:
    if not FROZEN_EXAMPLES.exists():
        raise ValueError(f"frozen corpus examples missing: {FROZEN_EXAMPLES}")
    digest = hashlib.sha256(FROZEN_EXAMPLES.read_bytes()).hexdigest()
    if digest != FROZEN_EXAMPLES_HASH:
        raise ValueError(
            f"frozen corpus examples changed under the hook "
            f"(got {digest[:16]}..., want {FROZEN_EXAMPLES_HASH[:16]}...)"
        )


def _evidence_sources(root: Path, entries: List[dict]) -> List[Path]:
    """Provenance for the evidence bytes.

    v0: every authorized evidence item originates from the frozen corpus, so
    the corpus file is the honest source citation (and it reproduces the
    retrospective's provenance exactly). If a non-corpus-origin entry ever
    appears, the authoritative log itself joins the sources.
    """
    sources: List[Path] = []
    if any(e.get("origin") != "live_mission" for e in entries) or not entries:
        _check_frozen_examples()
        sources.append(FROZEN_EXAMPLES)
    if any(e.get("origin") == "live_mission" for e in entries):
        sources.append(_log_path(root))
    return sources


def materialize_from_log(root: Path) -> dict:
    """Recompute D_t from the authoritative log: D_t = f(H_t, C_t, V).

    This is THE pure recompute. The hook calls it after every admission; the
    rm -rf recovery path calls it directly. Same function, same bytes --
    the materialization can never silently diverge from the log.

    It also regenerates the COMPLETE request history (one file per prefix
    state with a disagreement), so requests/ is fully derived: after
    rm -rf, recovery is total. Nothing under the developmental root except
    the log itself and hook_errors.jsonl holds irreplaceable data.

    Returns {"materialization": ..., "request_id": ...} for the current state.
    """
    root = Path(root)
    vocab = load_vocab()
    entries = read_log(root)
    evidence = _log_evidence(entries)
    sources = _evidence_sources(root, entries)
    out = _materialized_dir(root)
    L.build(evidence, vocab, candidate_vocab_path(), sources, out)
    materialization = L.load_materialization(out)  # tamper-verified load
    request_id = _regenerate_request_history(root, vocab, entries,
                                            materialization, evidence)
    return {"materialization": materialization, "request_id": request_id}


# ---------------------------------------------------------------------------
# TRAINING_REQUEST as data (never a command)
# ---------------------------------------------------------------------------

def _write_request_envelope(rdir: Path, req: dict, developmental_tag: str) -> None:
    """Persist one request envelope. Fully deterministic: no wall-clock time,
    so regeneration is byte-identical."""
    envelope = {
        "request": req,
        "status": "proposed",
        "launch_authorized": False,
        "developmental_tag": developmental_tag,
        "note": (
            "TRAINING_REQUEST is data, never a command. No executor is "
            "reachable from D0. Execution requires an explicit operator act: "
            "machine chooses what evidence it needs; human still authorizes spend."
        ),
    }
    path = rdir / f"{req['request_id']}.json"
    # Deterministic filename: re-emission is idempotent.
    path.write_text(json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")


def _request_for_prefix(root: Path, vocab: dict, prefix_entries: list) -> tuple:
    """Derive the request for one history prefix (if any disagreement).

    Builds the prefix state in a disposable temp dir -- the real
    materialization is never disturbed. Returns (request, developmental_tag)
    or (None, tag).
    """
    evidence = _log_evidence(prefix_entries)
    sources = _evidence_sources(root, prefix_entries)
    tag = f"D{len(evidence)}"
    tmp = Path(tempfile.mkdtemp(prefix="u2a-req-"))
    try:
        L.build(evidence, vocab, candidate_vocab_path(), sources, tmp)
        mat = L.load_materialization(tmp)
        req = R.build_request(mat, vocab, evidence)
        if req is None:
            return None, tag
        # Reconstructively validate before persisting: never emit a request
        # that does not mechanically follow from the referenced state.
        R.validate_request(req, tmp, vocab, evidence)
        return req, tag
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _regenerate_request_history(root: Path, vocab: dict, entries: list,
                                materialization: dict, evidence: list) -> Optional[str]:
    """Regenerate every request file from the log prefixes.

    requests/ is fully derived: one deterministic file per prefix state with
    a disagreement. Stale files are cleared first, so recovery after rm -rf
    (or any corruption) reproduces the directory byte-identically.
    Returns the current state's request_id (or None).
    """
    rdir = _requests_dir(root)
    rdir.mkdir(parents=True, exist_ok=True)
    for p in rdir.glob("*.json"):
        p.unlink()
    # Historical prefixes in disposable dirs...
    for i in range(len(entries)):
        req, tag = _request_for_prefix(root, vocab, entries[:i])
        if req is not None:
            _write_request_envelope(rdir, req, tag)
    # ...current state from the real (tamper-verified) materialization.
    req = R.build_request(materialization, vocab, evidence)
    if req is None:
        return None
    R.validate_request(req, _materialized_dir(root), vocab, evidence)
    _write_request_envelope(rdir, req, materialization["state"]["developmental_tag"])
    return req["request_id"]


# ---------------------------------------------------------------------------
# The hook: mission terminal event -> D0 recomputation/update
# ---------------------------------------------------------------------------

def _log_hook_error(root: Path, event: dict, exc: BaseException) -> None:
    try:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        ep = root / "hook_errors.jsonl"
        record = {
            "at": time.time(),
            "mission_id": (event or {}).get("mission_id"),
            "error": f"{type(exc).__name__}: {exc}",
        }
        with ep.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:
        pass


def on_mission_terminal(event: dict, developmental_root: Optional[str | Path] = None) -> dict:
    """Process one mission terminal event. NEVER raises into the caller.

    Steps: validate event -> validate + dedupe evidence -> append to the
    authoritative log -> recompute/materialize D_t -> maybe emit a request.
    Returns a summary dict (with ok/error). All failures are contained and
    recorded to hook_errors.jsonl; the mission that triggered the hook is
    never affected.
    """
    root = Path(developmental_root) if developmental_root else resolve_developmental_root()
    try:
        return _process_terminal_event(event, root)
    except Exception as exc:  # noqa: BLE001 -- containment is the contract
        _log_hook_error(root, event if isinstance(event, dict) else {}, exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _process_terminal_event(event: dict, root: Path) -> dict:
    if not isinstance(event, dict) or event.get("event_type") != "mission_terminal":
        raise ValueError("event must be a mission_terminal dict")
    mission_id = event.get("mission_id")
    if not mission_id:
        raise ValueError("terminal event missing mission_id")
    raw_evidence = event.get("developmental_evidence", [])
    if not isinstance(raw_evidence, list):
        raise ValueError("developmental_evidence must be a list")

    validated = []
    rejected = 0
    for item in raw_evidence:
        norm = validate_evidence_item(item)
        if norm is None:
            rejected += 1
        else:
            validated.append(norm)

    entries = read_log(root)
    known_ids = {e["evidence"]["evidence_id"] for e in entries}
    new_entries = []
    for ev in validated:
        if ev["evidence_id"] in known_ids:
            continue  # idempotent: re-fed events do not double-admit
        known_ids.add(ev["evidence_id"])
        new_entries.append(
            {
                "seq": len(entries) + len(new_entries),
                "admitted_at": time.time(),
                "mission_id": str(mission_id),
                "terminal_status": str(event.get("terminal_status", "?")),
                "worker": event.get("worker", {}),
                "origin": event.get("origin", "live_mission"),
                "evidence": ev,
            }
        )
    if new_entries:
        _write_log(root, entries + new_entries)

    built = materialize_from_log(root)
    state = built["materialization"]["state"]
    return {
        "ok": True,
        "mission_id": str(mission_id),
        "n_evidence_new": len(new_entries),
        "n_evidence_rejected": rejected,
        "n_evidence_total": state["n_evidence"],
        "developmental_tag": state["developmental_tag"],
        "state_hash": state["state_hash"],
        "n_surviving": state["n_surviving"],
        "request_id": built["request_id"],
    }


# ---------------------------------------------------------------------------
# Failure-contained entry point for MissionRunner.run()
# ---------------------------------------------------------------------------

def on_mission_finished(runner: Any, result: Any) -> dict:
    """Build the terminal event and process it. Never raises.

    Called once per mission, after the mission has fully terminated. Any
    failure (including import failure) is contained: the mission's result
    stands regardless.
    """
    try:
        event = build_terminal_event(runner, result)
        return on_mission_terminal(event)
    except Exception as exc:  # noqa: BLE001
        try:
            _log_hook_error(resolve_developmental_root(), {}, exc)
        except Exception:
            pass
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
