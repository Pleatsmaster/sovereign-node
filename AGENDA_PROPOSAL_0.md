# AGENDA-PROPOSAL-0

**Status:** AUTHORIZED 2026-10-01 by Stephan — `AUTHORIZE DESIGN + IMPLEMENTATION`.
**Policy version:** `agenda-proposal-0`

## The pressure

The loop can already execute accepted commitments end to end. The next
bottleneck is choosing what deserves doing:

> real world contains unresolved opportunities/problems
> + OPEN obligations = 0
> → **what should I work on?**

This mechanism is the ecological niche for endogenous agenda formation.
It is provoked by *withholding* the habit of converting every observed
issue into instructions — not by manufacturing challenges.

## What this is

When Namariel is idle but real unresolved conditions exist, it may
autonomously originate a **candidate objective**. That is the whole of
the new capability:

- a candidate objective is **not** an obligation;
- it is **not** authorization;
- it is **not** execution.

The human still decides whether a proposal becomes an admitted
commitment, through the existing COMMITMENT-ADMISSION-0 gate unchanged.

The evolutionary ladder this opens (only the second rung is built here):

```
execute human goals → propose own goals → earn trust on proposed goals
→ self-admit narrow goal classes → eventually maintain an agenda
```

Each further rung needs its own authorization, gated by evidence from
the ledger this mechanism keeps.

## The first live behavior we want

Not "Namariel finds something to do." More demanding:

> Namariel observes a real condition and originates a bounded objective
> that a human independently judges worth doing.

`NO_PROPOSAL` is an equally valid outcome.

## Architecture: two stages

**Stage 1 — deterministic condition detection** (`scan_conditions.py`).
No model, no judgment. Reads a declared, bounded sensorium and emits
`CONDITION_DETECTED` records: a falsifiable observation plus evidence.
Conditions are observations, not goals: a failing test justifies a
proposal, but the scanner never emits "fix the test."

**Stage 2 — bounded proposal generation** (agent step, frozen prompt in
`PROPOSAL_PROMPT_0.md`, recorded by `propose_agenda.py`). Runs only
when the gate below holds. The agent may propose work about observed
conditions only; it cannot widen the sensorium, invent hidden
conditions, or ask that its own speculative concerns be treated as
detected facts. Every structural claim the recorder can check, it
checks; the human supplies the judgment.

## Sensorium v0 (frozen set; widen only by explicit authorization)

Every condition must come from a deterministic predicate we can name
and test. v0:

1. `test_suite_status` — each failing life-0 test node id.
2. `evidence_store_integrity` — unparsable lines in `state/*.jsonl`;
   sha256 sidecar mismatches where sidecars exist.
3. `unresolved_or_stale_references` — workspace paths named in
   `records/*.md` that no longer exist; `commit_*`/`need_*` ids named in
   state ledgers or records that resolve to no registry entry.
4. `declared_repo_invariants` — violations of `REPO_INVARIANTS_0.md`
   (file-sha256 / file-exists checks only).

"Repo anomaly" as an open-ended detector is deliberately excluded from
v0.

## The gate (all four conjuncts; evaluated by `propose_agenda.py gate`)

Stage 2 runs only when:

1. the obligation projection is empty (`OPEN executable obligations = 0`);
2. at least one `CONDITION_DETECTED` exists in the latest scan;
3. the condition set differs from the last evaluated condition state
   (`condition_set_hash` ≠ watermark);
4. no proposal already covers the same condition set — any ledger entry
   (`PROPOSED`, `ACCEPTED`, `REJECTED`, `EXPIRED`) bound to that hash
   blocks regeneration.

(3) and (4) together prevent "idle" from becoming "ask the model every
hour for another idea." Re-evaluating an unchanged condition state must
not generate a fresh proposal merely because the previous one was
rejected: uniqueness is condition-bound, via `condition_set_hash`.

## Candidate schema (frozen field list — no other fields accepted)

```
proposal_id
condition_set_hash
source_conditions        # every id must be in the scanned condition set
evidence_refs            # every ref must resolve (file exists / condition observed)
objective                # concrete, ≥ 20 chars
why_now                  # must cite ≥ 1 source_condition id; resolves back to
                         # the observed condition state, never manufactured urgency
expected_consequence
completion_contract      # ≥ 1 predicate, each with id + verifiable end state
required_authority_class # "local-build" only in v0
predicted_cost
known_risks
why_existing_obligations_do_not_cover_it
```

Free-form "importance" scoring is omitted on purpose: it invites the
model to manufacture urgency. `why_now` must resolve back to the
observed condition state.

`proposal_id` is deterministic:
`prop_` + sha256(`condition_set_hash` + canonical `objective`)[:12].

## Lifecycle

```
PROPOSED → ACCEPTED | REJECTED(reason) | EXPIRED
```

- `ACCEPTED`: the human's act, executed via `review_proposal.py decide
  --verdict ACCEPTED`, which builds the acceptance record from the
  proposal and runs it through `admit_commitment.py` unchanged
  (`accepted_by` = `operator`). The proposal becomes a commitment only
  through the existing admission gate.
- `REJECTED`: the human's act, with a **structured reason** (frozen
  enum): `NOT_WORTH_DOING`, `DUPLICATE`, `OBJECTIVE_VAGUE`,
  `NO_MEASURABLE_END_STATE`, `EVIDENCE_INSUFFICIENT`,
  `WRONG_AUTHORITY_CLASS`, `MISDIAGNOSED_CONDITION`, `OTHER`.
  Rejection reasons are first-class evidence: later, "acceptance rate"
  alone is too crude — we want to know *why* self-originated objectives
  die.
- `EXPIRED`: deterministic; a `PROPOSED` entry unreviewed after 7 days
  is marked expired by the scan step.
- `NO_PROPOSAL`: recorded per evaluated condition state when the agent
  judges nothing worth proposing. Advances the watermark like any
  evaluation.

## Trigger

Folded into the existing hourly recovery path
(`commitment-dispatch-recovery`). No new scheduler. The recovery agent,
after dispatch reconciliation: runs the scan, evaluates the gate, and
only on `PROCEED` reads the packet, generates per the frozen prompt,
and records. `NO_CONDITIONS`, unchanged state, already-covered state,
and `NO_PROPOSAL` are all silent. A newly `PROPOSED` entry is reported
once to the operator for independent judgment.

## Frozen prohibitions

- execution authority: **NONE** — proposals never launch workers;
- self-admission: **FORBIDDEN**;
- self-authored commitment: **FORBIDDEN** (only `accepted_by="operator"`
  admits, via the unchanged admission script);
- H / M / K mutation: **FORBIDDEN**;
- sensorium widening by the model: **FORBIDDEN**;
- developmental, lineage, inheritance, or autonomy credit: **NONE**.

## Selection

Proposals live or die by human judgment and then by outcomes. The
ledger (`state/agenda_proposals.jsonl`) keeps every proposal, every
rejection reason, every `NO_PROPOSAL`: the evidence base on which the
trust ladder's later rungs will be judged. Useless, redundant, vague,
or self-serving proposals die here, on the record.

## Write boundary

Stage 1 and the recorder write only: `state/condition_scans.jsonl`,
`state/agenda_proposals.jsonl`, `state/agenda_watermark.json`,
`proposals/packets/`. They never touch commitments, needs, staged
packages, completion contracts, authority classes, H, M, K, or the
OBLIGATION-0 projector. Only the human's explicit `ACCEPTED` decision
admits — through `admit_commitment.py`, which owns those writes.
