# COMMITMENT-EXECUTION-0

**Status: AUTHORIZED — IN FORCE (Stephan, 2026-10-01 18:47 EDT).**

## The rule

A valid `COMMITMENT_ADMITTED` event constitutes **standing authorization**
for Namariel to pursue that commitment to its frozen completion condition
**inside its declared authority class**. No second mission-by-mission human
authorization is required.

Not authorization to exceed it. The authority class is a ceiling, not a
suggestion.

## Validity conditions (mechanical)

Standing authorization holds for a need iff all five verify, checked by
`scripts/execution_eligibility.py`:

1. a `COMMITMENT_ADMITTED` record exists for the need's commitment;
2. `accepted_by == "operator"` — exactly;
3. the completion contract validates structurally (non-empty predicates,
   each with id and verifiable condition);
4. the authority class is one of the closed enum
   (`local-read`, `local-build`, `external-propose`);
5. the scope passes the forbidden-target gate (the pulse gate's list);
6. the obligation currently projects OPEN under OBLIGATION-0.

(Six checks; the admission gate already enforced 1–5 at admission time.
Eligibility re-verifies them at execution time because records are
append-only and the world may have changed.)

## The live chain

```
human accepts real commitment
  → deterministic admission (COMMITMENT-ADMISSION-0)
  → OPEN obligation
  → deterministic selection (priority rank, then oldest)
  → AUTO-WORK-0
  → AUTO-CONTINUE-0
  → SATISFIED | BLOCKED | REAL_AUTHORITY_BOUNDARY
```

## Real authority boundaries

The human comes back into the loop **only** at a genuine boundary:

- external consequential action
- H mutation (residue store)
- M mutation
- K mutation
- promotion / inheritance
- financial commitment beyond standing allowance
- private-data release
- new capability grant
- scope expansion
- authority-class expansion
- constitutional change

Everything else inside the accepted commitment runs without asking again.

A boundary encountered mid-execution is a **STOP and escalate** event, not a
judgment call: the chain terminates, the boundary is recorded with mechanical
evidence, and the obligation projects BLOCKED until the human rules.

## Operational topology

```
commitment admitted → immediate eligible execution
hourly pulse        → resume anything legitimately left OPEN
```

The clock is for sensing and resumption, not artificial latency. A newly
admitted eligible commitment triggers the eligibility check immediately;
the hourly pulse remains the recovery mechanism that catches anything still
OPEN after interruption. The pulse reporter no longer reports an
execution-authorized commitment as "awaiting authorization" — it reports the
mechanical eligibility verdict.

## What "unleash" means

Not: Namariel may do anything.

But:

> Namariel may keep moving without us wherever we have already granted the
> right to move.

That is a stronger system than one that asks permission at every transition.

## What this is not

- Not endogenous agenda: no commitment exists without a human acceptance
  event (COMMITMENT-ADMISSION-0, frozen).
- Not a grant of new authority: execution is confined to the declared class;
  crossing a boundary requires the human.
- Not a promotion path: developmental credit for this build is none.

## The next frontier (named, not crossed)

> When does Namariel earn the right to originate what it works on next?

That is the threshold between persistent obligation and an endogenous
agenda. It is recorded here as the next question, not answered.

## First execution

`commit_5df0fff373b41e4a` / `need_f807664088aa0d57918bf0e02e2aafdd`
("Design, implement, and validate COMMITMENT-ADMISSION-0") —
**AUTHORIZED TO EXECUTE** under its recorded authority class (`local-build`)
and frozen completion contract, per the operator's 2026-10-01 18:47 EDT
authorization. Executed and closed 2026-10-01: the committed work was
performed and validated, each completion predicate verified against the
live records with evidence in the package closure.
