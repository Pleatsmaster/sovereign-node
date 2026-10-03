# AGENDA-OPPORTUNITY-0

**Status:** AUTHORIZED 2026-10-01 by Stephan — `AUTHORIZE DESIGN + IMPLEMENTATION`.
**Policy version:** `agenda-opportunity-0`

## The missing half

AGENDA-PROPOSAL-0 built the pressure half of the charter's environment:

```
Ω = pressures + opportunities
```

A healthy Namariel — no failing tests, intact ledgers, no dangling
references, invariants holding — could sit idle forever, because the
only thing that could wake it was pathology. That is a maintenance
agenda, not a research agenda. Biological and ecological systems do not
develop only because something is broken: new niches, resources,
affordances, and reachable states matter too.

This mechanism adds the second eye. The pressure scanner is frozen and
untouched. A second deterministic observation type joins it:

```
OPPORTUNITY_DETECTED
```

A pressure says: something is wrong.
An opportunity says: something changed or exists that could legitimately
advance an already accepted higher-level purpose, even though nothing is
broken.

The loop becomes:

```
world/history
→ { PRESSURE_DETECTED, OPPORTUNITY_DETECTED }
→ candidate objective
→ human selection
→ commitment
→ autonomous execution
```

## What this is not

- Not a task list. The horizon declares questions, not tasks; an OPEN
  question never authorizes work.
- Not brainstorming. Every opportunity comes from a deterministic
  predicate over records. No model hallucinates "interesting ideas."
- Not a widening of the pressure sensorium. `scan_conditions.py` is
  frozen byte-identical; its four predicates, its schema, and its
  expiry behavior are unchanged.
- Not a capability registry. No declared capability surface exists yet;
  that predicate family is explicitly deferred, not silently improvised.

## The horizon (operator-frozen, model-immutable)

`RESEARCH_HORIZON_0.md` declares the currently open Namariel research
questions: RH0 (CLOSED_BASELINE — causal persistence, established),
RH1–RH2/RH4–RH5 (OPEN), RH3 (OPEN_PARTIAL). Only the operator may add,
remove, close, reopen, or redefine questions. The model may not create
new horizon ids, widen the horizon, or reinterpret CLOSED as OPEN.

Two operator-maintained ledgers anchor the horizon to records
(scanner-read, never scanner-written):

- `state/horizon_bindings.jsonl` — frozen bindings of a mission/chain
  to ≥1 RH* question, recorded by the operator when a mission is run
  against the horizon. No binding is ever invented retroactively to
  populate the scanner.
- `state/evidence_links.jsonl` — records that a specific result
  artifact has been evaluated against a horizon question. Absence of a
  link means bearing is unresolved, not positive (horizon rule 4).

## Sensorium v0: three deterministic opportunity predicates

Every opportunity must come from a named, deterministic predicate we
can test. v0:

1. `unlinked_result_artifact` — a consequence record produced a result
   artifact whose originating mission/chain carries a frozen binding to
   ≥1 RH* question, and no evidence link records that artifact's
   bearing on any bound question. The opportunity means *research-bound
   result exists ∧ its bearing remains unlinked* — never *some file
   exists ⇒ maybe research*. Artifacts whose missions carry no frozen
   binding do not fire, however suggestive they look.
2. `testable_residue_pending` — a residue in the residue store
   transitioned to mechanically testable since the previous scan:
   status `active`, and at least one mission-ledger event exists with a
   timestamp after the residue's recording (weak, mechanical form of
   "a later pivot exists"; whether any pivot *qualifies* is the test
   harness's judgment, stated verbatim in the observation). Fires on
   the witnessed transition only — a residue already testable at the
   first scan is baseline, not news. Its natural horizon scope is
   RH1 and/or RH3, per the residue's existing scope — not whatever
   Stage 2 would prefer.
3. `resource_became_available` — a sensor availability flag flipped
   false→true since the previous scan (witnessed transition; a newly
   appearing sensor at true is not a transition). Raw affordance,
   objectively observable. Any proposal built on it must still bind to
   ≥1 OPEN horizon id; a newly available resource with no defensible
   relationship to RH1–RH5 normally yields NO_PROPOSAL.

Opportunity records use the same observation-only schema as pressures
(no goal/action/priority fields; every string from a fixed template).
Each record's evidence carries `horizon_scope`: the frozen allowed set
of horizon ids any proposal citing that opportunity may bind to
(artifact: the mission's bound questions; residue: RH1/RH3; resource:
all OPEN/OPEN_PARTIAL ids).

## The gate (unchanged conjuncts, wider condition set)

Stage 2 runs only when, over the *combined* pressure+opportunity
condition set:

1. the obligation projection is empty;
2. at least one condition of either kind exists;
3. the combined `condition_set_hash` differs from the watermark;
4. no ledger entry in any state covers that hash.

One mechanism, two eyes: the existing watermark
(`state/agenda_watermark.json`), uniqueness semantics, proposal ledger
(`state/agenda_proposals.jsonl`), review path (`review_proposal.py`),
and hourly run are shared. `PROPOSAL_PROMPT_0.md` is superseded as the
live prompt by `OPPORTUNITY_PROMPT_0.md` and retained frozen as the
pressure-only historical record.

## Candidate schema: one delta

The frozen field list gains exactly one field: `horizon_ids`
(list of `RH*` ids, may be empty). Validation is mechanical:

- every id must exist in `RESEARCH_HORIZON_0.md`;
- CLOSED_BASELINE ids are refused (`HORIZON_ID_CLOSED`);
- unknown ids are refused (`HORIZON_ID_UNKNOWN`);
- if any `source_conditions` entry is an opportunity, `horizon_ids`
  must be non-empty (`HORIZON_BINDING_REQUIRED`);
- every id must lie within the cited opportunities' `horizon_scope`
  (`HORIZON_SCOPE_VIOLATION`) — this is what keeps Stage 2 from
  binding an opportunity to whichever question it prefers.

Pressure-only proposals are unaffected: they carry no `horizon_ids`
and validate exactly as under AGENDA-PROPOSAL-0.

## Lifecycle, trigger, prohibitions

Unchanged: `PROPOSED → ACCEPTED | REJECTED(reason) | EXPIRED`,
`NO_PROPOSAL` valid for every observed opportunity, 7-day deterministic
expiry, human acceptance through the unchanged admission gate
(`accepted_by="operator"`), folded into the existing hourly
`commitment-dispatch-recovery` run (both scanners, then the gate).

Frozen prohibitions, carried over and extended:

- execution authority: **NONE**; self-admission: **FORBIDDEN**;
  self-authored commitment: **FORBIDDEN**; H/M/K mutation: **FORBIDDEN**;
- sensorium widening by the model: **FORBIDDEN** (both eyes);
- horizon modification: **OPERATOR ONLY**; horizon reinterpretation by
  the mechanism: **FORBIDDEN**;
- developmental, lineage, inheritance, or autonomy credit: **NONE**.

## Write boundary

The opportunity scanner writes only `state/opportunity_scans.jsonl`.
The gate/recorder write only `state/agenda_proposals.jsonl`,
`state/agenda_watermark.json`, `proposals/packets/`. Nothing touches
commitments, needs, staged packages, completion contracts, authority
classes, H, M, K, the OBLIGATION-0 projector, the residue store, or
mission ledgers. `horizon_bindings.jsonl` and `evidence_links.jsonl`
are operator-written; the scanner reads them.

## Deferred, explicitly

- Capability-registry predicates (no declared surface exists yet).
- Protocol-declared decision points (no protocol declares them yet).
- A machine-readable residue lifecycle registry (statuses currently
  live in the research record; the scanner tracks became-testable
  transitions and Stage 2 checks prior testing before proposing).

## The conceptual transition

```
Ω = { something became wrong } ∪ { something became newly testable/reachable }
      ─────── pressure ───────     ─────────── opportunity ───────────
```

After this, the operator maintains the horizon and the world. Namariel
gets its first chance to notice that something has become possible,
relevant, or unresolved — and to say, on the record, whether anything
deserves becoming the next experiment.
