# OBLIGATION-0

**Status:** SPECIFIED 2026-10-01 (Stephan).
**Pressure:** the 18:23 pulse reporter inferred "awaiting explicit authorization"
from a historical STAGED event (EPISODE-001, staged 16:42 EDT) while ignoring
later terminal events (authorized → executed → accepted → closed 17:24 EDT).
The system could remember events but could not reliably compute what remains
unfinished now.

## Invariant 1

> **Terminal state dominates historical openness.**

## Projection

For each need/mission lineage, the projector consumes its whole relevant event
sequence and returns exactly one current obligation state:

- `OPEN` — created, staged, authorized, dispatched, or executed; no terminal
  record; not blocked.
- `BLOCKED` — the execution chain terminated without goal satisfaction and
  without a permitted retry (or an explicit block directive); needs an operator.
- `SATISFIED` — an authoritative terminal record closes the lineage: mission
  package CLOSED with a closure record, chain ledger latest decision
  `STOP_GOAL_SATISFIED`, or a frozen run verdict bound to the lineage.
- `RETIRED` — explicit operator directive retires the obligation.

Rules:

1. The projection is a deterministic fold over the ledgers in time order.
   No agent decides whether work "seems unfinished."
2. `SATISFIED` and `RETIRED` are absorbing. A prior `STAGED`, `AUTHORIZED`,
   or `DISPATCHED` event cannot make a need `OPEN` if a later authoritative
   terminal record closes it.
3. Any event recorded after a terminal event for the same need is preserved
   (as a terminal confirmation or an anomaly) and cannot change the projected
   state.
4. The projection is derived state: recomputable from the ledgers at any time,
   never hand-edited.

## Authoritative event sources (in precedence of record, not of vote)

- `state/needs.jsonl` — `NEED_CREATED`, `NEED_STATUS`
- `dispatch/staged/<need_id>/MISSION_PACKAGE.json` — staged / authorized /
  closed with closure
- `dispatch/staged/<need_id>/CHAIN_LEDGER.jsonl` — continuation and terminal
  chain decisions
- `state/consequences.jsonl` — execution evidence
- `records/*_RUN_VERDICT_FROZEN.md` — frozen terminal verdicts, bound to a
  lineage by the ledger SHA-256 they record
- `state/obligation_directives.jsonl` (optional) — operator `RETIRE` / `BLOCK`
  directives

Sensing observations (pulse log) are not obligation state. A `STAGED` pulse
result for a need that projects `SATISFIED` is a historical observation, not
a pending obligation.

## What it is not

OBLIGATION-0 does not create, prioritize, or schedule work. It answers one
question — *what remains unfinished now* — from the records, so that no
downstream consumer can mistake a remembered staging for a live obligation.

## Reference shapes (2026-10-01, frozen)

- EPISODE-001: `STAGED → AUTHORIZED → EXECUTED → ACCEPTED → CLOSED`
  = `SATISFIED`
- PRESSURE-002: `STAGED → AUTO-WORK → continuation → frozen terminal verdict`
  = `SATISFIED`

`current open obligations = ∅`, `pending authorization = ∅`.
