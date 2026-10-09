#!/usr/bin/env python3
"""chain_adapter.py — Stage-1 host integration: join RESEARCH-LOOP-0's
whole-chain request to the existing action runner.

Contract (from the frozen RESEARCH-LOOP-0 controller, candidate
59f070dd — the adapter adapts to the controller, never the reverse):
  chain_adapter.py /absolute/path/REQUEST.json

The controller appends REQUEST.json to the configured worker argv and reads
the terminal report's "status" field:
  report "status" == "PASS"  and projector == SATISFIED -> controller PASS
  report "status" == "FAIL"                             -> controller FAIL
  report "status" == "BLOCKED"                           -> controller BLOCKED
  anything else (or nonzero exit, or missing report)     -> controller BLOCKED/FAIL

The adapter's internal verdicts (SATISFIED / FAILED / BLOCKED / INFRA_ERROR)
are translated EXPLICITLY into the report's "status" field:
  SATISFIED   -> PASS   (exit 0 and projector-confirmed SATISFIED only)
  FAILED      -> FAIL
  BLOCKED     -> BLOCKED
  INFRA_ERROR -> FAIL   (did not pass; detail preserved in verdict/reason)

The adapter:
  1. Validates the controller's REQUEST.json {version, life0, need_id,
     binding_path, binding_hash, workspace, report_path, api_budget_usd,
     runbook} and follows its bound references: binding_path ->
     CHAIN_BINDING.json (hash-verified against binding_hash) -> commitment_id
     -> state/commitments.jsonl (hash-verified against the binding's
     acceptance_record_hash) -> objective. api_budget_usd must be 0.
  2. Runs exactly one bounded chain without a shell: the existing
     MissionRunner's packet construction, policy validation, tool execution,
     ledger and acceptance are reused; the adapter owns the loop so it can
     derive per-call deadlines from the remaining chain budget.
     Working area: <workspace>/work (repo), <workspace>/chain-state (ledger).
  3. Derives each inference call's deadline from (chain_deadline - now) minus
     a finalization reserve. CommandWorker's 300 s is an upper bound, never
     the deadline.
  4. Verifies completion through the authoritative obligation projector
     (<life0>/scripts/project_obligations.py). PASS requires the chain's
     verified completion predicates AND the projector reporting SATISFIED.
  5. Writes the terminal report atomically (temp + os.replace) to the bound
     report_path and exits with the real status.

Host configuration (chain_adapter_config.json next to this file — the adapter
is built around a specific worker on a specific host):
  {"worker_argv": [...], "um_root": "/abs/path",
   "default_bounds": {"max_seconds": N, "max_steps": N,
                      "finalization_reserve_seconds": N}}
worker_argv supports {need_id} and {workspace} substitution. All fields
required; the adapter fails closed without it.

Authority: no new authority. SOVEREIGN_APIS_DISABLED=1 is set and credential
environment variables are stripped before any worker subprocess is spawned.
STOP/is-terminal topology is preserved: a revocation directive for the need
produces zero worker executions.

Exit status:
  0 — the adapter completed supervision, classified the chain, and wrote the
      terminal report. The controller outcome is in report["status"].
  1 — adapter/infrastructure failure (could not classify or report).
  2 — REQUEST.json invalid (nothing was executed).

Step-1 scope: deterministic engineering behavior only. No live inference is
performed by this file itself; the worker argv comes from the host config.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# --- configuration ---------------------------------------------------------

MIN_CALL_SECONDS = 5          # smallest inference call worth issuing
MAX_CALL_SECONDS = 300        # CommandWorker-compatible upper bound
RESERVE_FLOOR_SECONDS = 10    # finalization reserve minimum
PROJECTOR_TIMEOUT = 60
ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
CRED_SUBSTRINGS = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")
ADAPTER_DIR = Path(__file__).resolve().parent


# --- errors ----------------------------------------------------------------

class RequestError(Exception):
    """REQUEST.json (or its bound references) failed validation."""


class WorkerFailed(Exception):
    """The worker subprocess failed (nonzero exit / unparseable output)."""


# --- small helpers ---------------------------------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def _abs(p, name):
    if not isinstance(p, str) or not os.path.isabs(p):
        raise RequestError(f"{name} must be an absolute path, got {p!r}")
    return p


def _ident(v, name):
    if not isinstance(v, str) or not ID_RE.match(v):
        raise RequestError(f"{name} must match {ID_RE.pattern}, got {v!r}")
    return v


# --- host config -----------------------------------------------------------

def config_path():
    """Host config location. CHAIN_ADAPTER_CONFIG overrides for testing;
    under the controller (minimal env) the file next to the adapter wins."""
    env = os.environ.get("CHAIN_ADAPTER_CONFIG")
    if env:
        return Path(env)
    return ADAPTER_DIR / "chain_adapter_config.json"


def load_host_config():
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as exc:
        raise RequestError(f"host config unreadable: {path}: {exc}")
    argv = cfg.get("worker_argv")
    if (not isinstance(argv, list) or not argv
            or not all(isinstance(a, str) and a for a in argv)):
        raise RequestError("host config: worker_argv must be a non-empty "
                           "string list")
    um_root = cfg.get("um_root")
    if not isinstance(um_root, str) or not os.path.isdir(
            os.path.join(um_root, "unified_machine")):
        raise RequestError("host config: um_root must be a directory "
                           "containing the unified_machine package")
    b = cfg.get("default_bounds") or {}
    try:
        max_seconds = float(b["max_seconds"])
        max_steps = int(b["max_steps"])
        reserve = float(b["finalization_reserve_seconds"])
    except (KeyError, TypeError, ValueError):
        raise RequestError("host config: default_bounds needs numeric "
                           "max_seconds, max_steps, finalization_reserve_seconds")
    if max_steps < 1:
        raise RequestError("host config: max_steps must be >= 1")
    if reserve < RESERVE_FLOOR_SECONDS:
        raise RequestError(f"host config: reserve must be >= {RESERVE_FLOOR_SECONDS}s")
    if max_seconds <= reserve + MIN_CALL_SECONDS:
        raise RequestError("host config: max_seconds must exceed reserve + "
                           f"{MIN_CALL_SECONDS}s")
    proxy_env = cfg.get("proxy_env") or {}
    if (not isinstance(proxy_env, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in proxy_env.items())):
        raise RequestError("host config: proxy_env must be a string dict")
    # Integrated acceptance (2026-10-05): optional per-need checker binding.
    # Each entry: {"argv": [...], "timeout_seconds": float}. Placeholders
    # {workspace}, {need_id}, {life0} are substituted at run time.
    acc = cfg.get("acceptance_checkers") or {}
    if not isinstance(acc, dict):
        raise RequestError("host config: acceptance_checkers must be a dict")
    checkers = {}
    for nid, spec in acc.items():
        if not isinstance(spec, dict):
            raise RequestError(
                f"host config: acceptance_checkers[{nid}] must be a dict")
        cargv = spec.get("argv")
        if (not isinstance(cargv, list) or not cargv
                or not all(isinstance(a, str) and a for a in cargv)):
            raise RequestError(
                f"host config: acceptance_checkers[{nid}].argv must be a "
                "non-empty string list")
        try:
            ctimeout = float(spec["timeout_seconds"])
        except (KeyError, TypeError, ValueError):
            raise RequestError(
                f"host config: acceptance_checkers[{nid}] needs numeric "
                "timeout_seconds")
        if ctimeout <= 0:
            raise RequestError(
                f"host config: acceptance_checkers[{nid}].timeout_seconds "
                "must be positive")
        checkers[nid] = {"argv": cargv, "timeout_seconds": ctimeout}
    return {"worker_argv": argv, "um_root": um_root,
            "bounds": {"max_seconds": max_seconds, "max_steps": max_steps,
                       "reserve": reserve},
            "proxy_env": {k: v for k, v in proxy_env.items()
                          if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY",
                                           "ALL_PROXY", "NO_PROXY")},
            "acceptance_checkers": checkers}


# --- request validation ----------------------------------------------------

def load_request(path, host_cfg):
    """Validate the controller's REQUEST.json and follow its bound references
    through the chain binding to the admitted commitment."""
    if not os.path.isabs(path):
        raise RequestError("REQUEST.json path must be absolute")
    try:
        with open(path, "r", encoding="utf-8") as f:
            req = json.load(f)
    except (OSError, ValueError) as exc:
        raise RequestError(f"cannot read REQUEST.json: {exc}")
    if not isinstance(req, dict):
        raise RequestError("REQUEST.json must be a JSON object")
    for k in ("life0", "need_id", "binding_path", "binding_hash",
              "workspace", "report_path", "api_budget_usd"):
        if k not in req:
            raise RequestError(f"REQUEST.json missing required key: {k}")

    need_id = _ident(req["need_id"], "need_id")
    life0 = _abs(req["life0"], "life0")
    binding_path = _abs(req["binding_path"], "binding_path")
    workspace = _abs(req["workspace"], "workspace")
    report_path = _abs(req["report_path"], "report_path")
    if not os.path.isdir(life0):
        raise RequestError(f"life0 is not a directory: {life0}")
    if not os.path.isfile(os.path.join(life0, "scripts",
                                       "project_obligations.py")):
        raise RequestError(f"projector not found under life0: {life0}")
    if not os.path.isfile(binding_path):
        raise RequestError(f"binding_path does not exist: {binding_path}")
    if not os.path.isdir(workspace):
        raise RequestError(f"workspace does not exist: {workspace}")
    if not os.path.isdir(os.path.dirname(report_path)):
        raise RequestError(f"report_path parent does not exist: "
                           f"{os.path.dirname(report_path)}")
    if req.get("api_budget_usd") != 0:
        raise RequestError("api_budget_usd != 0: live API spend is outside "
                           "the adapter's authorized scope")

    # Follow the bound reference: binding file, hash-verified.
    try:
        with open(binding_path, "r", encoding="utf-8") as f:
            binding = json.load(f)
    except ValueError as exc:
        raise RequestError(f"binding is not valid JSON: {exc}")
    if canonical_hash(binding) != req["binding_hash"]:
        raise RequestError("binding_hash mismatch: the chain binding cannot "
                           "be trusted")
    if binding.get("need_id") != need_id:
        raise RequestError("binding need_id does not match request need_id")
    chain_id = binding.get("chain_id")
    if not chain_id:
        raise RequestError("binding has no chain_id")
    _ident(chain_id, "binding.chain_id")
    commitment_id = binding.get("commitment_id")
    if not commitment_id:
        raise RequestError("binding has no commitment_id")

    # Binding -> commitment record, hash-verified against the binding.
    commitment = None
    reg_path = os.path.join(life0, "state", "commitments.jsonl")
    try:
        with open(reg_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                c = (r.get("commitment") or {})
                if (r.get("event") == "COMMITMENT_ADMITTED"
                        and c.get("commitment_id") == commitment_id):
                    commitment = c
                    break
    except OSError as exc:
        raise RequestError(f"cannot read commitment registry: {exc}")
    if commitment is None:
        raise RequestError(f"no COMMITMENT_ADMITTED record for {commitment_id}")
    if canonical_hash(commitment) != binding.get("acceptance_record_hash"):
        raise RequestError("commitment record hash mismatch: the admitted "
                           "charter changed since dispatch")
    objective = (commitment.get("objective") or "").strip()
    if not objective:
        raise RequestError("admitted commitment has no objective")

    def subst(s):
        return s.replace("{need_id}", need_id).replace("{workspace}", workspace)

    worker_argv = [subst(a) for a in host_cfg["worker_argv"]]
    # Per-chain call allocation (2026-10-05): the controller reserves this
    # chain's model-call allowance from the shared ceiling before spawn.
    # None means allocation not configured (adapter falls back to max_steps).
    allowance = req.get("model_call_allowance")
    if allowance is not None:
        if (isinstance(allowance, bool) or not isinstance(allowance, int)
                or allowance < 1):
            raise RequestError("REQUEST.json model_call_allowance must be a "
                               "positive integer or absent")
    # Cell dispatch (RELATIONAL-0 GO-2): an optional cell_id routes the
    # validated request to the cell execution branch. Validated here as a
    # well-formed identifier; registration is checked in the cell branch
    # against the registry (fail-closed).
    cell_id = req.get("cell_id")
    if cell_id is not None and (not isinstance(cell_id, str) or not cell_id):
        raise RequestError("REQUEST.json cell_id must be a non-empty string "
                           "or absent")
    return {
        "need_id": need_id,
        "chain_id": chain_id,
        "life0_dir": life0,
        "objective": objective,
        "commitment_id": commitment_id,
        "authority_class": binding.get("authority_class"),
        "workspace": workspace,
        "repo": os.path.join(workspace, "work"),
        "state_dir": os.path.join(workspace, "chain-state"),
        "report_path": report_path,
        "worker_argv": worker_argv,
        "bounds": host_cfg["bounds"],
        "um_root": host_cfg["um_root"],
        "binding_sha256": canonical_hash(binding),
        "request_sha256": sha256_file(path),
        "model_call_allowance": allowance,
        "cell_id": cell_id,
    }


# --- worker subprocess -----------------------------------------------------

def run_acceptance_checker(host_cfg, req, need_id, env, deadline_seconds):
    """Run the bound acceptance checker for a need.

    Returns None if no checker is bound; otherwise a dict with:
      outcome: "accepted" | "rejected" | "error"
      kind: for "error": "timeout" | "exec_failed" | "no_time"
      exit_code, output (bounded, preserved as evidence)
    The checker runs within deadline_seconds; no shell is used.
    """
    spec = (host_cfg.get("acceptance_checkers") or {}).get(need_id)
    if not spec:
        return None
    argv = [a.replace("{workspace}", req["workspace"])
             .replace("{need_id}", need_id)
             .replace("{life0}", req["life0_dir"])
            for a in spec["argv"]]
    timeout = min(float(spec["timeout_seconds"]), deadline_seconds)
    if timeout <= 0:
        return {"outcome": "error", "kind": "no_time",
                "detail": "insufficient time remaining for acceptance check"}
    try:
        cp = subprocess.run(argv, capture_output=True, text=True,
                            timeout=timeout, env=env, cwd=req["workspace"])
    except subprocess.TimeoutExpired:
        return {"outcome": "error", "kind": "timeout",
                "detail": f"checker timed out after {timeout:.1f}s",
                "argv": argv}
    except OSError as exc:
        return {"outcome": "error", "kind": "exec_failed",
                "detail": f"{type(exc).__name__}: {exc}", "argv": argv}
    output = (cp.stdout + cp.stderr)[-4000:]
    if cp.returncode == 0:
        return {"outcome": "accepted", "exit_code": 0, "output": output,
                "argv": argv}
    return {"outcome": "rejected", "exit_code": cp.returncode,
            "output": output, "argv": argv}


def scrub_env(host_cfg):
    """Copy the environment, disable APIs, strip credential variables.

    The single explicit exception is the host's exterior-egress proxy
    configuration: this VM blocks direct HTTPS egress, so the worker
    subtree receives the proxy variables from the host config's
    "proxy_env" block (operator-supplied, ephemeral). Callers must
    record key names only, never values.
    """
    env = dict(os.environ)
    env["SOVEREIGN_APIS_DISABLED"] = "1"
    for k in list(env):
        ku = k.upper()
        if any(s in ku for s in CRED_SUBSTRINGS):
            del env[k]
    proxy_keys = []
    for k, v in (host_cfg.get("proxy_env") or {}).items():
        if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            env[k] = v
            proxy_keys.append(k)
    return env, proxy_keys


def worker_call(argv, payload, timeout, env):
    """One worker invocation. Same stdin-JSON protocol as CommandWorker.
    No shell. Raises WorkerFailed or subprocess.TimeoutExpired."""
    cp = subprocess.run(
        argv,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    if cp.returncode != 0:
        raise WorkerFailed(f"worker exited {cp.returncode}: {cp.stderr[-2000:]}")
    return cp.stdout


# --- revocation ------------------------------------------------------------

def revoked(life0_dir, need_id):
    """True if an operator BLOCK/RETIRE directive exists for the need.
    This is the same source the obligation projector folds; no new sentinel."""
    path = os.path.join(life0_dir, "state", "obligation_directives.jsonl")
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("need_id") == need_id and "at" in e:
                if (e.get("directive") or "").upper() in ("BLOCK", "RETIRE"):
                    return True
    return False


# --- projector -------------------------------------------------------------

def run_projector(life0_dir, need_id):
    """Run the authoritative obligation projector; return its state for the
    need, or 'UNKNOWN' if the projector cannot be run or parsed."""
    script = os.path.join(life0_dir, "scripts", "project_obligations.py")
    fd, out_path = tempfile.mkstemp(prefix="obligations-", suffix=".json")
    os.close(fd)
    try:
        cp = subprocess.run(
            [sys.executable, script, "--life0", life0_dir, "--out", out_path],
            text=True, capture_output=True, timeout=PROJECTOR_TIMEOUT)
        if cp.returncode != 0:
            return "UNKNOWN", f"projector exit {cp.returncode}: {cp.stderr[-500:]}"
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        obligations = data.get("obligations", data)
        state = obligations.get(need_id, {}).get("state", "UNKNOWN")
        return state, ""
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return "UNKNOWN", f"{type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def staged_ledger_path(life0_dir, need_id):
    """Deterministic path of the need's CHAIN_LEDGER.jsonl (no write)."""
    d = os.path.join(life0_dir, "dispatch", "staged", need_id)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "CHAIN_LEDGER.jsonl")


def record_chain_decision(life0_dir, need_id, record):
    """Append the executor's terminal record to the need's CHAIN_LEDGER.jsonl —
    the authoritative chain record the projector folds."""
    path = staged_ledger_path(life0_dir, need_id)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return path


# --- atomic report ---------------------------------------------------------

def atomic_write_json(path, obj):
    """Write JSON atomically: temp file in the same directory + os.replace.
    Returns the sha256 of the written bytes."""
    d = os.path.dirname(path)
    tmp = os.path.join(d, f".tmp-report-{os.getpid()}")
    data = (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return digest


# --- verdict translation ---------------------------------------------------
# The controller reads report["status"] in {PASS, FAIL, BLOCKED}. The mapping
# below is the explicit translation from the adapter's internal verdicts.

def controller_status(verdict):
    return {"SATISFIED": "PASS", "FAILED": "FAIL", "BLOCKED": "BLOCKED",
            "INFRA_ERROR": "FAIL"}[verdict]


def materialize_inputs(life0_dir, need_id, repo):
    """Copy the staged package's inputs/ (if any) into the chain's working
    tree before the first worker call. Returns {relpath: sha256}."""
    src = os.path.join(life0_dir, "dispatch", "staged", need_id, "inputs")
    if not os.path.isdir(src):
        return {}
    copied = {}
    for root, _dirs, files in os.walk(src):
        for name in files:
            s = os.path.join(root, name)
            rel = os.path.relpath(s, src)
            d = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            with open(s, "rb") as f:
                data = f.read()
            with open(d, "wb") as f:
                f.write(data)
            copied[rel] = hashlib.sha256(data).hexdigest()
    return copied


# --- the adapter -----------------------------------------------------------

def main(argv):
    if len(argv) != 2:
        print("usage: chain_adapter.py /absolute/path/REQUEST.json",
              file=sys.stderr)
        return 2
    # Packet hygiene for bounded runs: no exemplar retrieval. The chain's
    # packet carries the request's objective, staged inputs, this run's
    # history, and tool results — nothing else.
    os.environ["UM_EXEMPLAR_RETRIEVAL"] = "0"
    try:
        host_cfg = load_host_config()
        req = load_request(argv[1], host_cfg)
    except RequestError as exc:
        print(f"request invalid: {exc}", file=sys.stderr)
        return 2

    if host_cfg["um_root"] not in sys.path:
        sys.path.insert(0, host_cfg["um_root"])
    try:
        from unified_machine.mission import MissionRunner
        from unified_machine.types import Action
    except ImportError as exc:
        print(f"cannot import unified_machine from {host_cfg['um_root']}: "
              f"{exc}", file=sys.stderr)
        return 1
    # Fail-closed module identity (RELATIONAL-0 GO-1 binding requirement):
    # the loaded implementation must come from the configured (reviewed)
    # baseline tree. Logging the path is insufficient; a mismatch refuses
    # to execute at all. This changes no authority semantics.
    import unified_machine as _um_identity_check
    _um_loaded = os.path.realpath(
        getattr(_um_identity_check, "__file__", ""))
    _um_expected = os.path.realpath(
        os.path.join(host_cfg["um_root"], "unified_machine"))
    if not (_um_loaded.startswith(_um_expected + os.sep)
            or _um_loaded == _um_expected):
        print(f"fail-closed: unified_machine loaded from {_um_loaded}, "
              f"expected under {_um_expected}; refusing to execute",
              file=sys.stderr)
        return 1

    os.makedirs(req["repo"], exist_ok=True)
    subprocess.run(["git", "init", "-q", req["repo"]], check=False,
                   capture_output=True)
    os.makedirs(req["state_dir"], exist_ok=True)
    residue_dir = os.path.join(os.path.dirname(req["state_dir"]), "residue")
    os.makedirs(residue_dir, exist_ok=True)
    env, proxy_keys = scrub_env(host_cfg)
    t0 = time.time()
    chain_deadline = t0 + req["bounds"]["max_seconds"]
    reserve = req["bounds"]["reserve"]

    # Cell dispatch branch (RELATIONAL-0 GO-2 step 1): a validated request
    # carrying cell_id drives the cell turn instead of the chain loop.
    # Binding/admission validation above is unchanged; the registry is
    # authoritative for the cell's workspace. No chain runner is constructed.
    if req.get("cell_id"):
        from cells.dispatch import run_cell_dispatch, CellDispatchError
        try:
            return run_cell_dispatch(host_cfg, req, env)
        except CellDispatchError as exc:
            print(f"cell dispatch refused: {exc}", file=sys.stderr)
            return 2

    runner = MissionRunner(
        objective=req["objective"],
        repo=req["repo"],
        state_dir=req["state_dir"],
        worker=None,  # the adapter drives worker calls itself (deadlines)
        budget=req["bounds"]["max_steps"],
        acceptance=[],
        mission_id=req["chain_id"],
        worker_label="chain_adapter",
        residue_dir=residue_dir,  # per-chain, empty: no unrelated residues
    )
    history = []
    # Contract defect fix (2026-10-05): the adapter drives the worker loop
    # manually and appends to its local `history`, but `runner._packet()`
    # reads `runner.history` — which stayed empty. The model-facing packet
    # therefore had no action history; the model could not see what it
    # already did or whether writes succeeded. Share the list so the packet
    # carries the actual trajectory.
    runner.history = history
    failed_acts = 0
    timeouts = 0
    worker_calls = 0
    if proxy_keys:
        runner.ledger.append("proxy_env_restored", {
            "keys": sorted(proxy_keys),
            "note": "host exterior-egress proxy; values never logged",
        })
    status = "budget_exhausted"
    stop_reason = ""
    steps = 0
    report_source = "worker"
    acceptance_result = None  # set when a bound checker runs on stop-complete

    def time_left():
        return chain_deadline - time.time()

    def finalize(verdict, chain_decision, reason, _projector=None,
                 _ledger_path=None):
        # _projector/_ledger_path let the caller pre-run the projector and
        # pre-record the chain decision (the SATISFIED path needs the
        # projector's answer BEFORE the report is written).
        nonlocal report_source, worker_calls
        final_packet = {
            "chain": {
                "chain_id": req["chain_id"],
                "need_id": req["need_id"],
                "objective": req["objective"],
                "repo": req["repo"],
                "status": status,
                "stop_reason": stop_reason,
                "steps": steps,
                "failed_acts": failed_acts,
                "timeouts": timeouts,
                "worker_calls": worker_calls,
                "verdict": verdict,
                "chain_decision": chain_decision,
                "reason": reason,
                "acceptance": acceptance_result,
            },
            "history": history,
        }
        # Worker-authored report; adapter-authored fallback on failure.
        # When no worker call was ever issued (e.g. pre-spawn revocation),
        # the worker is not invoked at all.
        if worker_calls == 0:
            report_source = "adapter_zero_calls"
            report = ("No worker calls were issued for this chain "
                      f"(verdict={verdict}, reason={reason}).\n\n"
                      + json.dumps(final_packet, indent=1))
        elif (req["model_call_allowance"] is not None
                and worker_calls >= req["model_call_allowance"]):
            # Per-chain allocation: the report call would exceed the reserved
            # allowance. Terminal reporting remains possible via the adapter
            # fallback (no model call).
            report_source = "adapter_fallback_allowance"
            report = (f"Model-call allowance exhausted "
                      f"({worker_calls}/{req['model_call_allowance']}); "
                      f"report generated without worker "
                      f"(verdict={verdict}, reason={reason}).\n\n"
                      + json.dumps(final_packet, indent=1))
        else:
            rep_timeout = max(5.0, time_left() - 5.0)
            try:
                report = worker_call(
                    req["worker_argv"],
                    {"mode": "report", "packet": final_packet},
                    rep_timeout, env)
                worker_calls += 1
            except Exception as exc:  # noqa: BLE001 - never break finalization
                report_source = "adapter_fallback"
                report = (f"Report generation failed: {type(exc).__name__}: "
                          f"{exc}\n\n" + json.dumps(final_packet, indent=1))

        # Finalization ordering (2026-10-05): for non-SATISFIED verdicts the
        # terminal report must be published BEFORE the chain decision becomes
        # visible to the projector. Otherwise the controller's poll loop
        # observes BLOCKED-without-report and terminates the adapter
        # (AUTHORIZATION_REVOKED) before result.json exists. The SATISFIED
        # path keeps its order (decision → projector → report): the
        # controller exempts SATISFIED/RETIRED from termination, and the
        # projector needs the decision recorded to confirm SATISFIED.
        # The staged-ledger path is deterministic, so the report can
        # reference it before the decision is recorded.
        if verdict == "SATISFIED":
            if _ledger_path is None:
                ledger_path = record_chain_decision(
                    req["life0_dir"], req["need_id"], {
                        "chain_id": req["chain_id"],
                        "need_id": req["need_id"],
                        "evaluated_at": iso_now(),
                        "chain_decision": chain_decision,
                        "depth": steps,
                        "reason": reason,
                    })
            else:
                ledger_path = _ledger_path
            defer_decision_record = None
        else:
            ledger_path = staged_ledger_path(req["life0_dir"], req["need_id"])
            defer_decision_record = {
                "chain_id": req["chain_id"],
                "need_id": req["need_id"],
                "evaluated_at": iso_now(),
                "chain_decision": chain_decision,
                "depth": steps,
                "reason": reason,
            }

        if _projector is None:
            projector_state, projector_note = run_projector(
                req["life0_dir"], req["need_id"])
            if defer_decision_record is not None:
                projector_note = ((projector_note + " ") if projector_note
                                  else "") + ("[pre-decision state; chain "
                                               "decision recorded after report]")
        else:
            projector_state, projector_note = _projector

        cstatus = controller_status(verdict)
        terminal = {
            "adapter": "chain_adapter/2",
            "status": cstatus,  # RESEARCH-LOOP-0 contract: PASS/FAIL/BLOCKED
            "verdict": verdict,  # internal: SATISFIED/FAILED/BLOCKED/INFRA_ERROR
            "chain_id": req["chain_id"],
            "need_id": req["need_id"],
            "request_sha256": req["request_sha256"],
            "binding_sha256": req["binding_sha256"],
            "commitment_id": req["commitment_id"],
            "authority_class": req["authority_class"],
            "objective": req["objective"],
            "chain_decision": chain_decision,
            "reason": reason,
            "projector_state": projector_state,
            "projector_note": projector_note,
            "acceptance": acceptance_result,
            "worker_status": status,
            "stop_reason": stop_reason,
            "steps": steps,
            "failed_acts": failed_acts,
            "timeouts": timeouts,
            "worker_calls": worker_calls,
            "bounds": req["bounds"],
            "elapsed_seconds": round(time.time() - t0, 2),
            "finished_at": iso_now(),
            "worker_argv": req["worker_argv"],
            "report_source": report_source,
            "chain_ledger": ledger_path,
            "staged_inputs": staged_inputs,
            "report": report,
        }
        digest = atomic_write_json(req["report_path"], terminal)
        if defer_decision_record is not None:
            # Publish the chain decision only after the terminal report
            # exists (see ordering note above).
            record_chain_decision(req["life0_dir"], req["need_id"],
                                  defer_decision_record)
        try:
            runner.ledger.append("chain_finished", {
                "chain_id": req["chain_id"],
                "status": cstatus,
                "verdict": verdict,
                "chain_decision": chain_decision,
                "report_path": req["report_path"],
                "report_sha256": digest,
            })
        except Exception:
            pass
        return 0

    runner.ledger.append("chain_started", {
        "chain_id": req["chain_id"],
        "need_id": req["need_id"],
        "objective": req["objective"],
        "bounds": req["bounds"],
        "worker_argv": req["worker_argv"],
    })

    # Materialize the staged package's inputs/ into the working tree before
    # the first worker call. The worker sees fixture files, never the
    # admission records they came from.
    try:
        staged_inputs = materialize_inputs(req["life0_dir"], req["need_id"],
                                           req["repo"])
    except OSError as exc:
        print(f"adapter failed: cannot stage inputs: {exc}", file=sys.stderr)
        return 1
    if staged_inputs:
        runner.ledger.append("inputs_staged", {
            "chain_id": req["chain_id"],
            "files": staged_inputs,
        })

    try:
        # Pre-spawn revocation: zero worker executions when revoked.
        if revoked(req["life0_dir"], req["need_id"]):
            return finalize("BLOCKED", "STOP_REVOKED",
                            "operator BLOCK/RETIRE directive present; "
                            "zero worker executions")

        for step in range(1, req["bounds"]["max_steps"] + 1):
            if revoked(req["life0_dir"], req["need_id"]):
                status = "revoked"
                stop_reason = "operator directive arrived mid-chain"
                return finalize("BLOCKED", "STOP_REVOKED", stop_reason)
            remaining = time_left()
            if remaining <= reserve + MIN_CALL_SECONDS:
                status = "budget_exhausted"
                stop_reason = (f"chain budget exhausted "
                               f"({remaining:.1f}s left <= reserve {reserve}s "
                               f"+ {MIN_CALL_SECONDS}s minimum call)")
                break
            # Per-chain call allocation (2026-10-05): the controller reserves
            # this chain's model-call allowance from the shared ceiling. Every
            # worker invocation counts (act and report). When exhausted, stop
            # calling the worker; local finalization (checker, projector,
            # terminal report) remains possible.
            allowance = req["model_call_allowance"]
            if allowance is not None and worker_calls >= allowance:
                status = "budget_exhausted"
                stop_reason = (f"model-call allowance exhausted "
                               f"({worker_calls}/{allowance} used)")
                history.append({"step": step, "allowance_exhausted":
                                f"{worker_calls}/{allowance}"})
                break
            call_timeout = min(MAX_CALL_SECONDS, remaining - reserve)
            steps = step
            packet = runner._packet(step)
            try:
                raw = worker_call(
                    req["worker_argv"],
                    {"mode": "act", "packet": packet},
                    call_timeout, env)
                worker_calls += 1
                action = Action.from_obj(json.loads(raw))
                runner.policy.validate(action)
            except subprocess.TimeoutExpired:
                timeouts += 1
                failed_acts += 1
                record = {"step": step, "act_timeout": True,
                          "call_timeout": round(call_timeout, 1)}
                history.append(record)
                runner.ledger.append("act_timeout",
                                    {"chain_id": req["chain_id"], **record})
                continue
            except Exception as exc:
                failed_acts += 1
                record = {"step": step,
                          "action_error": f"{type(exc).__name__}: {exc}"}
                history.append(record)
                runner.ledger.append("act_rejected",
                                    {"chain_id": req["chain_id"], **record})
                continue

            result = runner._execute(action)
            if not result.ok:
                failed_acts += 1
            record = {"step": step, "action": action.to_obj(),
                      "result": result.to_obj()}
            history.append(record)
            runner.ledger.append("act_completed",
                                {"chain_id": req["chain_id"], **record})

            if action.kind == "stop":
                claimed = action.args.get("status", "complete")
                stop_reason = action.args.get("reason", "")
                # Evidence gate (U1, same as MissionRunner): a worker-declared
                # 'complete' is not honored without observable evidence and
                # without acceptance configured to verify it.
                if claimed == "complete" and not runner._has_evidence():
                    stop_reason = (stop_reason + " " if stop_reason else "") + (
                        "[evidence-gate] 'complete' claimed with no repo "
                        "changes; stop not honored")
                    history.append({"step": step, "evidence_gate":
                                    "stop-complete rejected: no observable evidence"})
                    continue
                # Integrated acceptance (2026-10-05): when the worker declares
                # stop complete and a checker is bound to this need, the
                # checker must accept the artifact before closure. Runs within
                # the remaining deadline; output preserved as evidence.
                # A correct artifact without worker-declared stop remains
                # insufficient (no automatic completion).
                acceptance = None
                if claimed == "complete":
                    acceptance = run_acceptance_checker(
                        host_cfg, req, req["need_id"], env,
                        time_left() - reserve - 5.0)
                    if acceptance is not None:
                        acceptance_result = {
                            "outcome": acceptance["outcome"],
                            "kind": acceptance.get("kind"),
                            "exit_code": acceptance.get("exit_code"),
                        }
                        runner.ledger.append("acceptance_check", {
                            "chain_id": req["chain_id"],
                            "need_id": req["need_id"],
                            "step": step,
                            "checker_argv": acceptance.get("argv"),
                            "outcome": acceptance["outcome"],
                            "kind": acceptance.get("kind"),
                            "exit_code": acceptance.get("exit_code"),
                            "output": acceptance.get("output", "")[:2000],
                        })
                        history.append({"step": step, "acceptance_check":
                                        acceptance["outcome"]})
                    if acceptance is not None and acceptance["outcome"] != "accepted":
                        if acceptance["outcome"] == "rejected":
                            status = "checker_rejected"
                            stop_reason = (
                                "acceptance checker rejected the artifact "
                                f"(exit {acceptance.get('exit_code')}): "
                                f"{acceptance.get('output', '')[:500]}")
                        else:
                            status = "checker_error"
                            stop_reason = (
                                "acceptance checker execution failed "
                                f"({acceptance.get('kind')}: "
                                f"{acceptance.get('detail', '')[:300]})")
                        break
                status = claimed
                break

        # --- completion predicates -------------------------------------
        # The admitted completion contract's predicates are verified through
        # the authoritative obligation projector below; the chain decision is
        # recorded first so the projector folds it.
        if status == "complete":
            # Tentative SATISFIED: record the chain decision, run the
            # authoritative projector, and only then write the report.
            # SATISFIED requires the projector to report SATISFIED.
            ledger_path = record_chain_decision(
                req["life0_dir"], req["need_id"], {
                    "chain_id": req["chain_id"],
                    "need_id": req["need_id"],
                    "evaluated_at": iso_now(),
                    "chain_decision": "STOP_GOAL_SATISFIED",
                    "depth": steps,
                    "reason": "worker-declared complete; evidence gate passed",
                })
            pstate, pnote = run_projector(req["life0_dir"], req["need_id"])
            if pstate == "SATISFIED":
                return finalize(
                    "SATISFIED", "STOP_GOAL_SATISFIED",
                    "worker-declared complete; evidence gate passed; "
                    "projector reports SATISFIED",
                    _projector=(pstate, pnote), _ledger_path=ledger_path)
            return finalize(
                "INFRA_ERROR", "STOP_GOAL_SATISFIED",
                "completion predicates passed but the projector did not "
                f"report SATISFIED (state={pstate})",
                _projector=(pstate, pnote), _ledger_path=ledger_path)
        if status == "blocked":
            return finalize("BLOCKED", "STOP_WORKER_BLOCKED",
                            f"worker-declared blocked: {stop_reason}")
        if status == "defer":
            return finalize("BLOCKED", "STOP_DEFERRED",
                            f"worker-declared defer: {stop_reason}")
        if status == "revoked":
            return finalize("BLOCKED", "STOP_REVOKED", stop_reason)
        # Integrated acceptance outcomes (2026-10-05): the checker ran on a
        # worker-declared complete. Rejection (artifact wrong) is FAILED;
        # checker execution failure is INFRA_ERROR. Evidence is preserved in
        # the chain ledger (acceptance_check event) and the report.
        if status == "checker_rejected":
            return finalize("FAILED", "STOP_CHECKER_REJECTED", stop_reason)
        if status == "checker_error":
            return finalize("INFRA_ERROR", "STOP_CHECKER_ERROR", stop_reason)
        # budget_exhausted (steps or time)
        if timeouts and not any("action" in r for r in history):
            return finalize("INFRA_ERROR", "INFRA_ERROR",
                            f"zero completed acts; {timeouts} worker call "
                            "timeout(s); chain budget exhausted")
        return finalize("BLOCKED", "STOP_BUDGET",
                        f"chain budget exhausted: {stop_reason}")
    except Exception as exc:  # noqa: BLE001 - adapter must report, not vanish
        try:
            cstatus = "FAIL"
            terminal = {
                "adapter": "chain_adapter/2",
                "status": cstatus,
                "verdict": "INFRA_ERROR",
                "chain_id": req.get("chain_id", "?"),
                "need_id": req.get("need_id", "?"),
                "chain_decision": "INFRA_ERROR",
                "reason": f"adapter exception: {type(exc).__name__}: {exc}",
                "projector_state": "UNKNOWN",
                "steps": steps,
                "failed_acts": failed_acts,
                "elapsed_seconds": round(time.time() - t0, 2),
                "finished_at": iso_now(),
            }
            atomic_write_json(req["report_path"], terminal)
        except Exception:
            pass
        print(f"adapter failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
