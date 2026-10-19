# RESEARCH-LOOP-0

Implementation candidate, 2026-10-04. Extends release
`efdfd62` without replacing its pulse, classifiers, dispatchers or installer.
The DEMO_2 release remains frozen. This change runs separately from the pulse.

## Behavior

```
project existing mission and obligations
→ exclude terminal, blocked, revoked and already-owned work
→ select by existing priority / creation time
→ reverify and bind through COMMITMENT-DISPATCH-0
→ run one bounded chain through the installed worker adapter
→ record PASS / FAIL / BLOCKED with lineage
→ refresh and select again
```

Every local outcome returns to selection. A local failure does not prevent a
different authorized task from running. A failed attempt does not acquire
permission to retry itself. AUTO-CONTINUE-0 still governs continuation *within*
each chain; this controller governs selection *between* chains.

The persistent mission comes from existing standing obligations and the
operator-frozen research horizon. Only admitted, eligible `local-build`
commitments enter the execution frontier. Existing proposal gates may prepare
an agenda packet when idle; preparing a packet does not authorize a new task.
The existing horizon, acceptance and contract semantics are unchanged.

## Activation on the actual node

The released repository contains **no live instance state and no worker
executor**. Those are documented external inputs. This candidate does not
invent either. The tests use isolated fixture workers; they are not research
missions, B1 evidence, or a claim of live activation.

1. Make this candidate checkout available alongside the frozen node. Do not
   copy files over its release or modify `install.sh` / `MANIFEST.json`.
2. Bind the node's existing trusted chain executor to the adapter protocol
   below. It must follow `CHAIN_RUNBOOK_0.md`, including snapshot publication,
   classifiers, continuation tokens, immutable bounds and completion checks.
3. Initialize once with that command and a cumulative budget, then run.

Example, **replace both paths and the executor command with actual host paths**:

```sh
python3 /path/to/candidate/scripts/research_loop.py --life0 /path/to/live-node init \
  --worker-argv '["python3", "/path/to/existing-chain-adapter.py"]' \
  --max-launches 4 --max-seconds 1200 --timeout-seconds 300

python3 /path/to/candidate/scripts/research_loop.py --life0 /path/to/live-node run --watch
```

`run` drains ready work and yields at an empty frontier. `run --watch` stays
resident while idle, watches for newly admitted work, and resumes automatically.
It needs an operator/service to start the process; it is not a background
service created by running these tests. Existing pulse and scan scheduling
continue supplying observations. The controller rereads local durable state;
it does not launch a second pulse or make model calls itself.

## Worker adapter protocol

The controller executes the frozen argv **without a shell**, appending one
absolute `REQUEST.json` path. That request contains:

- `life0`, `need_id`, `binding_path`, `binding_hash`;
- `workspace`, `report_path`, and the existing chain runbook path;
- `api_budget_usd: 0`.

The adapter runs exactly one existing bounded chain. It reads the bound
commitment and contract; it does not choose another task, widen authority or
bypass the existing continuation classifiers. It writes its terminal report
atomically at `report_path` and exits:

```json
{"status": "PASS", "reason": "frozen completion predicates verified"}
```

`FAIL` and `BLOCKED` are also valid report statuses. A PASS report is accepted
only with a successful process exit **and** `SATISFIED` from the existing
authoritative obligation projector. A PASS string alone cannot close a task.
Closure/evaluation remain the existing executor's responsibility. Arbitrary
predicates cannot be inferred or solved by this mechanical controller.

The runner is a trusted installed adapter, not a security sandbox. Worker and
runtime file hashes are bound at initialization and checked before launch.
Credential environment variables are not passed through, and
`SOVEREIGN_APIS_DISABLED=1` is set. Network/API restrictions and filesystem
confinement still require the existing executor/runtime to enforce them;
an environment flag is not a network firewall. This version allocates no paid
API budget or external actuation authority.

## Durable state and stopping

The controller writes only its config, controls, append-only hash-linked
execution ledger, worker requests/logs/reports and existing agenda packets.
The original dispatcher writes its original binding and dispatch records.
The worker writes only within the scope of its already-authorized chain.

- `state/research_loop_config.json`: frozen worker/runtime identity and budget;
- `state/research_loop.jsonl`: initialization, frontier changes, worker intents,
  local results and global yields;
- `state/research_loop_controls.jsonl`: operator HOLD/STOP;
- `dispatch/research_loop/<need_id>/`: request and process evidence.

Launches and timeout reservations are cumulative across process restarts.
The full per-launch timeout is charged before invocation; unused time is not
refunded. `max_seconds` caps these reservations, not billing or idle watch time.
Timeout detection polls at 0.1 seconds and termination permits up to two seconds
of cleanup before killing the process group. The command cannot reinitialize
an existing budget. The existing per-chain depth and completion bounds still
apply independently.

Global yields: operator veto; integrity/authority boundary; budget exhaustion;
uncertain execution after a crash; empty executable frontier in drain mode.
An empty frontier in watch mode is a durable wait, not the end of the service.
Unchanged idle state does not generate repeated ledger entries or packets.

One controller holds the instance lock for its lifetime. Other dispatchers and
workers must obey existing operating rules; this lock does not coordinate
unrelated processes on other hosts. HOLD/STOP is checked before dispatch,
before spawn, and during execution; it cannot undo completed external effects.

## Revocation and crash recovery

Global stop, or add `--need <need_id>` for a local veto:

```sh
python3 /path/to/candidate/scripts/research_loop.py --life0 /path/to/live-node \
  control STOP --reason "operator stop"
```

HOLD and STOP remain dominant for this loop lifetime. The controller does not
remove a veto or reinterpret it as permission to continue.

Crash after a worker intent but before a recorded local result yields
`EXECUTION_UNCERTAIN`. The worker might have run: there is no blind replay.
First verify that the old worker has stopped and inspect its evidence. Then:

```sh
python3 /path/to/candidate/scripts/research_loop.py --life0 /path/to/live-node \
  reconcile --need <need_id> --worker-stopped --reason "actual reconciliation evidence"
```

Reconciliation records PASS only if the existing projector reports SATISFIED;
otherwise it records BLOCKED. It never reruns the uncertain job or refunds its
reservation. Other authorized work can subsequently proceed. `--worker-stopped`
is an explicit operator assertion, not automatic crash diagnosis.

## Resource seam and known state

B1 remains **PENDING REAL PRECONDITION**. 031C and 031D are terminal according
to the supplied operational record; neither is seeded or re-enqueued here.
The controller reads resource queue state but never edits, ticks or directly
executes a resource job. The existing resource scheduler owns that path.

Link a resource-backed commitment with a queue record's `need_id` (or matching
`job_id`) before selection; linked obligations are excluded from direct worker
execution. `required_resource` on a need or commitment also excludes it.
Resource jobs without obligation identities stay in the separate queue. This
version does not supply the missing resource-signal/execution adapter, and
does not claim B1 closure. Queue dispatch keeps first right of execution.

## Verification

Run the outer-loop tests and its existing integration dependencies:

```sh
python3 -m pytest tests/test_research_loop.py tests/test_commitment_dispatch.py \
  tests/test_commitment_execution.py tests/test_resource_queue.py \
  tests/test_obligation_projection.py -q
```

Acceptance covers sequential PASS/FAIL/BLOCKED continuation, watch wakeup,
restart/replay, uncertain-execution reconciliation, dispatch starvation,
authority exclusion, budget persistence, global/local veto, revocation at the
spawn boundary and during execution, timeout, false PASS, worker/config/ledger/
horizon tampering, instance locking, and resource-path exclusion.

The full shipped suite additionally needs the documented external `fact0` and
`namariel_live` packages and original evidence paths. See
`RESEARCH_LOOP_0_VALIDATION.md` for results and inherited failures. No release
repair is included in this change.
