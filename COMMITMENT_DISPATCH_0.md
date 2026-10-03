# COMMITMENT-DISPATCH-0

Status: AUTHORIZED — design + implementation (Stephan, 2026-10-01 ~19:00 EDT).
Purpose: close the last human-shaped hole in the causal chain between an
already-authorized commitment and the already-existing AUTO-WORK-0 /
AUTO-CONTINUE-0 machinery.

## The standing rule

For an OPEN commitment already proven EXECUTION_AUTHORIZED under
COMMITMENT-EXECUTION-0, the dispatcher automatically instantiates and
launches the existing AUTO-WORK-0 / AUTO-CONTINUE-0 chain. No new
authority is created. No second human act is required.

```
COMMITMENT_ADMITTED
  → obligation projects OPEN
  → COMMITMENT-EXECUTION-0 says EXECUTION_AUTHORIZED
  → COMMITMENT-DISPATCH-0
  → AUTO-WORK-0
  → AUTO-CONTINUE-0
  → SATISFIED | BLOCKED | REAL_BOUNDARY
```

## What the dispatcher is

A mechanical bridge. It verifies, binds, and records. It does not reason,
reinterpret the commitment, choose a new objective, rank commitments, fan
out, or become another policy layer.

The dispatcher receives exactly one selected commitment (from the existing
deterministic selector, unchanged) and drives its dispatch state forward
exactly one step.

## Binding (re-verified before launch; any mismatch → REFUSE)

- commitment_id
- need_id
- acceptance_record_hash (sha256 of the canonical registry commitment)
- completion_contract_hash
- authority_class — passed through unchanged; never translated into a
  broader worker/tool capability set
- eligibility_artifact_hash (sha256 of the canonical EXECUTION-0 verdict)
- current_obligation_projection (must be OPEN at bind and at launch)
- chain_bounds hash (frozen AUTO-CONTINUE-0 bounds: max_depth 5, fan_out 1,
  same need, immutable contract and budget, contract-gated retry)
- dispatch_nonce (fresh per intent; prevents replay)

## Exactly-once dispatch

Durable states per idempotency key (`commitment-dispatch:<commitment_id>:<need_id>`),
append-only `state/commitment_dispatch.jsonl`:

```
(none) → DISPATCH_INTENT → DISPATCHED → terminal (via OBLIGATION-0 projection)
```

- DISPATCH_REFUSED is supersedable by a fresh intent (new nonce).
- DISPATCHED and DISPATCH_BLOCKED are terminal for the key.
- A crash between "eligible" and "worker running" reconciles (completes the
  open intent); it never relaunches. Restart means reconcile, not relaunch.

## No authority translation, no autonomous widening

The dispatcher launches only authority classes already demonstrated by the
existing AUTO-WORK-0 / AUTO-CONTINUE-0 path. The supported set is exactly
`{"local-build"}`. Anything else refuses. This implementation must not be
used as an excuse to generalize undeclared classes.

If the AUTO-WORK-0 entry gate (the existing classifier, extended
mechanically to commitment packages — same fail-closed substance) cannot
execute within the class, the result is DISPATCH_BLOCKED: terminal for the
key, reported to the human. The dispatcher does not reinterpret the
refusal into an authorization.

## Trigger paths (same dispatcher)

1. **Immediate:** upon admission of an eligible commitment, the executor
   runs `dispatch_commitment.py dispatch --need <need_id>`; on DISPATCHED
   it executes the chain per CHAIN_RUNBOOK_0.md. Admission → execution
   with no artificial latency.
2. **Recovery:** the hourly cron runs `dispatch_commitment.py reconcile`,
   which selects the one deterministic eligible obligation (existing
   selector, unchanged) and drives it: binds what was left behind,
   completes interrupted intents, reports. The hourly clock is recovery
   infrastructure, not the normal latency source.

## What the dispatcher never touches

It writes only to `state/commitment_dispatch.jsonl` and to
`dispatch/chains/<chain_id>/CHAIN_BINDING.json`. It never mutates the
completion contract, the authority class, H, M, or K.

## Chain-contract materialization

The executor derives the AUTO-CONTINUE-0 chain contract deterministically
from the CHAIN_BINDING.json: done_predicates verbatim from the frozen
completion contract; chain bounds, gap/operation vocabularies, gap policy,
retry policy, and bounds from the frozen AUTO-CONTINUE-0 constants;
frozen_hash computed the way the classifier verifies it. No judgment.

## The idle condition

Once this is live, the chain is continuous:

```
human accepts work → Namariel remembers it → Namariel starts it
  → Namariel continues it
  → Namariel stops only when done, blocked, or at a real boundary
```

If Namariel is idle, it means something precise: there is genuinely no
admitted executable obligation — not that another missing transition is
waiting for a button press.

## Boundaries (unchanged)

- The endogenous-agenda frontier is named but NOT authorized. The
  dispatcher only fires on human-accepted commitments.
- OBLIGATION-0's projector is untouched; terminal state still dominates.
- The real authority boundaries (COMMITMENT_EXECUTION_0.md) still require
  the human; the chain machinery enforces them.
