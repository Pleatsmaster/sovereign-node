# LIFE-0 v0: Event-Driven Pulse — Design

**Status:** authorized 2026-10-01 by Stephan. Operator-built apparatus (like
FACT-0 before it). No lineage credit, no scientific credit beyond apparatus.
**Scope (boxed):** LIFE-0 v0 = pulse + delta detector + need queue + mission
dispatch. Everything after consequence already largely exists and is NOT built
here.

## The loop

```
world delta -> need -> authorized work -> consequence -> history
    -> pressure -> candidate change -> falsification -> inheritance
```

The loop manufactures **exposure to reality, not activity**. The critical
first term is *world delta*, not "Namariel invents something to do."

## The three surfaces (exactly these, no more)

1. **GitHub um-lineage mirror** (`{login}/unified_machine`), read-only via
   `api.github.com` REST GETs only (surrogate credential pattern from
   `~/workspace/skills/github/`). Sensors: default-branch HEAD sha, open
   issues (pulls excluded from need formation), latest CI workflow-run
   conclusions per branch. If the repo has no Actions, the sensor records
   `ci.available: false` — surface unavailable, never an error. The local
   dirty checkout at `~/workspace/unified_machine_v0` is NEVER touched.
2. **FACT-0 evidence store** (`~/workspace/namariel-live0/fact-0/data/fact0.db`),
   via the fact0 query interface (read-only): newly-STALE evidence (CURRENT at
   last pulse, STALE now), new supersessions, new admissions. The pulse's own
   queries are logged by FACT-0's passive use-provenance as
   `caller="life0-pulse"` — observability working as designed.
3. **Operator inbox** (`life-0/inbox/`). A new file is a delta is a need
   ("unfinished user work"). Files are placed here ONLY when the operator
   explicitly routes genuine work to Namariel — never by the pulse, never by a
   worker, never manufactured. Processed files move to `inbox/done/`.

Do NOT add news, social feeds, research crawlers, or autonomous browsing.
Three surfaces are enough until the organism shows it can metabolize them.

## Pulse design (non-negotiable)

- One runner (`pulse.run_pulse`): sense -> snapshot -> `D_t = E_t - E_{t-1}`
  (mechanical) -> OBSERVATION records
  `{observation_id, source, timestamp, content_hash, previous_state,
  current_state, delta_kind, delta_id}`.
- State persists locally: `state/sensors.json` (last snapshot),
  `state/observations.jsonl`, `state/needs.jsonl` (event log + fold),
  `state/pulse_log.jsonl`.
- **Three terminal pulse states (never two).**
  - `NO_ACTION` — all required surfaces observed successfully; no legitimate
    need detected. A valid life event, never skipped.
  - `STAGED` — valid deltas processed through need formation, gating, and
    staging (per-need outcomes in the queue; counts in the pulse record).
  - `PULSE_INVALID` / DEGRADED — one or more required surfaces could not be
    observed reliably (outage, malformed store, permission failure, sensor
    exception). The failing surfaces are recorded on the pulse. Deltas from
    healthy surfaces are still processed (each surface's deltas are
    independent — a GitHub outage must not swallow an operator inbox file),
    but the pulse is NEVER a NO_ACTION.
- **NO_ACTION means valid observation, never failed observation.** A sensor
  failure must not silently count as "nothing happened": PULSE_INVALID pulses
  are EXCLUDED from the NO_ACTION denominator in any base-rate computation
  (`pulse.pulse_stats()` implements this query — it is the one the future
  earned-autonomy analysis must use). Corrupting the denominator corrupts the
  biography from day one.
- **Cognition is called by difference, not by clock.** The pulse makes ZERO
  frontier-model calls and spends nothing. Deterministic sensors only; a pulse
  runs in seconds. (Pinned structurally by tests: no model-SDK imports
  anywhere in `life0`.)
- A sensor that fails records `sensor_error` in its snapshot section. A blind
  sensor is not evidence of change: it produces no deltas and never crashes
  the pulse.
- The first pulse establishes the baseline and emits no deltas. Exception:
  CI newly observable with a failing default-branch run fires `ci_new_failure`
  on first observation — silence about a red branch is the sensor's worst
  failure mode.

## Need formation

Need record: `{need_id, delta_id, source, delta_kind, possible_need,
evidence{delta_id, previous_state, current_state}, priority, obligation,
status, created_at}`.

- `need_id` is substrate-minted and deterministic (`need_` + sha256 of the
  delta core): a persistent delta never spawns duplicate needs across pulses.
- **A need MUST reference an observed delta. No evidence -> no task. No
  exceptions.** Enforced by `form_need()`, which raises on missing/malformed
  deltas.
- v0 formation is deterministic templating from the delta kind. Worker
  interpretation of a need happens later, inside the explicitly authorized
  mission — not in the pulse.
- Priorities: blocking failure > integrity/safety problem > unfinished user
  work > recurrent operational friction > useful opportunity > informational
  housekeeping. The queue folds newest-status-wins and reads highest-priority
  first.
- Need lifecycle: NEW -> STAGED_AWAITING_AUTHORIZATION -> (operator act) ->
  AUTHORIZED_LAUNCHED | DECLINED; RESOLVED when the delta disappears. A need
  already handled is never re-staged by later pulses.
- **Earned-autonomy record (fields for future measurement; the analysis is NOT
  built).** Every NEED_STATUS event carries `operator_decision`
  (pending | approved | rejected), `decided_by` (pulse | operator), and the
  reason + timestamp. Staging records pending/decided_by=pulse; a later
  operator act appends approved/rejected with reason + time; a gate decline
  is rejected/decided_by=pulse (the gate encodes operator policy, but no
  human decided that instance). The staged `MISSION_PACKAGE.json` carries the
  same `operator_decision` slot plus a `consequence` slot (filled from the
  consequence record once known). The future path — repeated approval ->
  candidate standing authority -> falsification -> bounded autonomous
  dispatch — must be readable from these records.

## K/CAF gate + dispatch: the authorization boundary

**Standing rule (NOT lifted): no mission launches, no API spend, no worker
invocation without Stephan's explicit authorization.**

- The gate (`gate.py`, policy `life0-gate-v0`, deliberately mechanical)
  checks: the need references an observed delta; the priority class is known;
  the proposed work touches no forbidden target (frozen v0.13 tree, the dirty
  lineage checkout, the evidence store for writing, credential paths).
  Decision: STAGE or DENY with a recorded reason.
- The gate decides what may be *staged*. It does not authorize launch.
- **Dispatch stages; it never launches.** The staged package
  (`dispatch/staged/<need_id>/`: `MISSION_PACKAGE.json`, `OBJECTIVE.md`,
  `STAGE_RECORD.json`) contains NO worker command (`worker_command: null`),
  NO executable plan, and `launch_authorized: false`. Supplying the worker
  command, spending budget, and invoking the worker is an explicit operator
  act performed outside this package. The stager module imports nothing that
  can execute a subprocess and performs no network I/O — pinned by tests.

## Standing obligations (NOT self-authored goals)

Environmental constraints supplied by the operator, preserved verbatim in
code (`needs.STANDING_OBLIGATIONS`) and docs:

1. Preserve the integrity of the LIVE substrate.
2. Complete explicitly assigned work.
3. Maintain factual awareness required by current work.
4. Notice recurring operational pressure.
5. Propose improvements when evidence justifies them.

`standing obligations != self-authored goals`. If Namariel later develops
endogenous agenda formation (G_t), it will coexist with or partly replace
this bootstrap — that is a later pressure, not this build.

## Consequence handoff (defined, not built)

When a staged mission is later authorized and run, its consequence record
MUST contain: `mission_id, need_id, delta_id, worker_id, worker_model,
authorized_by, authorized_at, started_at, finished_at, tests_changed,
artifact_produced, failure_removed, cost_incurred, new_failure_exposed,
operator_accepted, external_result_changed, notes` — appended to
`state/consequences.jsonl` (schema `life0.consequence` v1, see
`pulse.consequence_schema()`).

Consequence recording, pressure extraction, candidate formation,
falsification, and promotion are NOT part of LIFE-0 v0. The schema is the
handoff contract only. Most episodes should produce NO_PERSISTENT_CHANGE —
that is healthy.

## Scheduling

Cron-ready entry point: `scripts/life0_pulse.py --once`. Intended schedule:
hourly. The cron is NOT created by this build — Mata creates it after review.
The pulse is idempotent and cheap; overlapping runs are harmless (second run
sees no new deltas -> NO_ACTION).

## What is NOT built

No consequence scoring, no history/pressure/candidate machinery, no
promotion loop (all "already largely exist" per the authorization — the
consequence schema is the seam). No self-authored goals or endogenous agenda.
No additional surfaces. No auto-launch (bounded auto-launch needs a separate
explicit directive). No worker invocation, no spend, no model calls in the
pulse. No modifications to frozen v0.13 or to FACT-0 behavior. No cron
installation.

## Module map

- `src/life0/sensors.py` — three deterministic read-only sensors
  (GitHub fetcher injectable for tests). The FACT-0 sensor pins
  `namariel_live` to the frozen v0.13 tree (read-only import; the frozen tree
  is never written) because FACT-0's query interface needs v0.13's auth
  module while the acceptance venv carries an older editable install.
- `src/life0/delta.py` — mechanical `detect_deltas`, observation records,
  deterministic ids.
- `src/life0/needs.py` — need formation (evidence-enforced), priorities,
  standing obligations, JSONL queue + fold.
- `src/life0/gate.py` — K/CAF policy check (v0 mechanical).
- `src/life0/dispatch.py` — mission package stager (never launches).
- `src/life0/pulse.py` — the runner; `consequence_schema()` handoff contract.
- `scripts/life0_pulse.py` — cron-ready entry point.
