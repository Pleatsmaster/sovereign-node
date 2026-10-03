# COMMITMENT-ADMISSION-0

**Status: AUTHORIZED — DESIGN + IMPLEMENTATION (Stephan, 2026-10-01 18:40 EDT).**
Self-authored goals: **forbidden.** Authority expansion: **none.** Developmental credit: **none.**

## Purpose

OBLIGATION-0 remembers work already admitted as a LIFE need. It cannot see
accepted Namariel program commitments that never entered the LIFE lineage —
they live only in chat until somebody remembers them. COMMITMENT-ADMISSION-0
closes that gap.

Its purpose is not to let Namariel invent work. It is to make a
human-accepted commitment **durable** instead of leaving it trapped in chat.

This is not endogenous agenda. Humans still originate the commitments.
Namariel simply stops losing them at the conversation boundary.

## The frozen rule

A commitment becomes an obligation **only after an explicit human acceptance
event** identifying:

1. a concrete objective,
2. a completion condition (verifiable predicates),
3. an authority class,
4. a scope.

**No model may originate that acceptance event.** The admission machinery
transcribes and validates a human act; it never performs one.

## Minimal record

```
commitment_id            commit_<sha256(objective|accepted_by|accepted_at)[:16]>  (deterministic)
origin                   where the human acceptance happened (chat reference, verbatim enough to find)
accepted_by              "operator" — exactly; anything else is rejected
accepted_at              ISO-8601 timestamp of the human's acceptance
objective                concrete objective (≥ 20 chars; "improve Namariel" fails this)
completion_contract      { predicates: [ { id, verifiable } ] } — at least one predicate
authority_class          one of the closed enum below; recorded, never granted
scope                    bounds of the work (≤ 500 chars)
status                   OPEN (at admission)
parent_commitment_id     null, or an existing commitment_id
```

## Authority classes (closed enum)

- `local-read` — read and derive within the workspace. No writes, no external acts.
- `local-build` — write code/artifacts and run tests within the workspace. No
  external actuation, no external API mutation.
- `external-propose` — may prepare external actions but never execute them;
  execution requires a separate explicit operator authorization.

Admission is a **recording act, not a granting act**. The machinery has no code
path that upgrades, widens, or invents an authority class.

## Three hard exclusions

1. **No vague goals.** An objective without a completion contract of verifiable
   predicates cannot be admitted. "Improve Namariel" fails structurally.
2. **No agent-generated objective can self-admit.** `accepted_by` must be
   `"operator"`; any other value — including any agent or model identity — is
   rejected. The acceptance event must reference the human's own message.
3. **No admission may itself widen authority, H, M, or K.** The admitted
   record's authority class equals the human-stated class. Admission cannot
   task against forbidden targets (credentials, the frozen v0.13 tree, the
   dirty lineage checkout — the same list the pulse gate enforces).

## The admission gate (mechanical)

`scripts/admit_commitment.py` validates the acceptance record and, on success:

1. appends `COMMITMENT_ADMITTED` to `state/commitments.jsonl` (append-only);
2. appends `NEED_CREATED` to `state/needs.jsonl` with `source: "commitment"`,
   `origin: "commitment:<commitment_id>"`, priority `"accepted program commitment"`;
3. stages the mission package under `dispatch/staged/<need_id>/` with status
   `STAGED_AWAITING_AUTHORIZATION`, `worker_command: null`,
   `launch_authorized: false` — the same inert shape the pulse produces.

Admission is idempotent: the same acceptance re-submitted is refused as
"already admitted", never duplicated, never a second need.

The pulse never stages commitment-needs (it stages delta-needs only, by
design). Staging at admission is the commitment's entry into the obligation
flow — deterministic, zero model calls, zero launches.

## The flow

```
human accepts real work
  → commitment admitted
  → OPEN obligation (need, staged)
  → pulse sees it (reporter, via OBLIGATION-0)
  → AUTO-WORK / AUTO-CONTINUE (only on explicit authorization)
  → SATISFIED or BLOCKED
```

## Status derivation

The registry records **admissions**; it does not track status transitions. A
commitment's live status is derived from its need's OBLIGATION-0 projection:
terminal states are absorbing, exactly as for delta-needs. No projector
changes were needed or made.

## What this is not

- Not endogenous agenda: no commitment exists without a human acceptance event.
- Not a priority override: commitments enter the same staging and
  authorization flow as any need.
- Not a promotion path: developmental credit for this build is none, by the
  authorizing decision.

## First commitment

`commit_5df0fff373b41e4a` — "Design, implement, and validate
COMMITMENT-ADMISSION-0" — accepted by the operator 2026-10-01 18:40 EDT,
admitted through this very mechanism (`need_f807664088aa0d57918bf0e02e2aafdd`,
`life0-need_f807664088aa0d57918bf0e02e2aafdd`). The mechanism's own
validation includes its self-admission: all five completion predicates
verified 2026-10-01 (mechanism validates, registry append-only, need staged
inert, obligation projects OPEN, self-admitted through admit_commitment.py).
