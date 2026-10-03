# RESOURCE-QUEUE-0 — Resource-Gated Queued Execution

Status: AUTHORIZED — design + implementation (Stephan, 2026-10-02 ~01:23 EDT).
Purpose: close the transition boundary between an authorized job and its
execution when only a measurable resource condition blocks it.

## The standing rule

For a job already authorized by the operator (GO), with a frozen packet,
blocked only by a resource condition, the queue holds it durably and the
hourly scheduler checks the condition mechanically. When the resource
becomes available, the job dispatches automatically with no second human
authorization.

```
AUTHORIZED -> WAITING_RESOURCE -> READY -> DISPATCH_INTENT -> DISPATCHED
  -> TERMINAL
```

No chat intervention between those states.

## Why this is earned, not apparatus creep

The same failure occurred twice with the same structure: a valid causal
branch existed, but continuity stopped at a transition boundary — first
behind NO_ACTION, then behind WAITING_RESOURCE. This extends the existing
COMMITMENT-DISPATCH-0 pattern (durable intent, exactly-once launch,
locking, reconciliation, deterministic eligibility, terminal-state
dominance) to resource-blocked jobs. It is continuity plumbing for
already-authorized work. No developmental credit.

## Invariants

- GO persists across temporary resource unavailability. A temporary
  external boundary suspends execution; it does not erase authorization
  and does not require another human contact event.
- resource_available + valid queued authorization => automatic execution.
- Probes are mechanical and deterministic: stdlib only, no network, no
  subprocess, no model calls. Unknown is never coerced into available or
  unavailable; the job keeps waiting.
- HOLD/STOP: revoke(job_id) is terminal and is checked before every
  transition out of WAITING_RESOURCE.

## Binding (re-verified before dispatch; any mismatch → REFUSE, fail closed)

- job_id, authorization record (operator GO), frozen packet hashes
  (label_sha256 recomputed at enqueue and at dispatch), required resource,
  next-job relation. A changed packet between enqueue and dispatch refuses;
  it is never repaired.

## Exactly-once (local scope, stated precisely)

This queue guarantees exactly-once *state progression and dispatch intent*
locally: durable states per idempotency key (`resource-job:<job_id>`),
append-only `state/resource_queue.jsonl`. A crash between intent and
dispatch reconciles (completes the open intent); it never relaunches.

It does NOT by itself guarantee exactly-once *remote execution*. The
end-to-end invariant is:

  exactly-once execution
    = durable dispatch intent
    + stable idempotency key (`resource-job:<job_id>`, shipped in the handoff)
    + downstream deduplication (the execution endpoint accepts, persists,
      and deduplicates on the same key)

Without the third term, a crash after remote acceptance but before local
acknowledgement can still execute twice. The handoff carries the stable
key; the execution side must honor it.

## The missing signal (standing known condition)

No deterministic grokbot-availability signal exists on the analysis box
(inspected 2026-10-02): no CLI, no config, no API, no shared filesystem
and no network path to Grok Bot's box; usage state arrives only via
operator relay. The probe raises BLOCKED_RESOURCE_SIGNAL_MISSING with
the observed interfaces and the required condition. The tick records this
once per job, keeps the job in WAITING_RESOURCE, and stays silent. No
availability heuristic is invented.

## What the queue never touches

It writes only to `state/resource_queue.jsonl`,
`state/resource_queue_revocations.jsonl`, and
`dispatch/resource_jobs/<job_id>/`. It never mutates the frozen packet,
the authorization record, H, M, or K. Enqueueing and revoking are
operator acts; the scheduler only ticks.

## RESOURCE_SIGNAL_0 (frozen interface; producer not yet built)

The one narrow interface remaining:

  RESOURCE_SIGNAL_0: E_execution -> Q_dispatcher

Nothing upstream (031C, dispatcher, queue semantics) is modified until
this exists.

Minimal contract — the execution side produces:

```json
{
  "schema": "resource-signal-0",
  "resource": "grokbot-weekly-usage",
  "state": "OPEN",
  "observed_at": "2026-10-02T05:30:00Z",
  "valid_until": "2026-10-02T06:30:00Z",
  "window_id": "2026-W40",
  "source": "grokbot-local-probe",
  "sequence": 184
}
```

Allowed states: OPEN | CLOSED | UNKNOWN only.

Consumer rule:

  dispatch allowed
    <=> state = OPEN AND fresh AND authentic
        AND resource_id matches AND NOT HOLD AND NOT STOP

Everything else -> remain WAITING_RESOURCE.

Three requirements:

1. The execution machine owns truth. The dispatcher never infers
   availability from elapsed time, past reset dates, failed requests,
   chat messages, or historical usage patterns.
2. Transport and observation stay separate. Chain:
   Grok environment -> measures -> resource_signal.json -> transports ->
   Namariel machine -> verifies -> resource_queue tick.
   Transport may eventually be GitHub, a tiny authenticated endpoint,
   shared storage, or manual file relay. The queue does not care which.
3. Freshness is mandatory. An old OPEN is more dangerous than no signal.
   valid_until / strict TTL turns stale availability into UNKNOWN, never
   leaves it OPEN.

When the real Grok-side availability mechanism is known, implement the
smallest possible producer/transport adapter around it — never
contaminate the queue with provider-specific logic.

## First live job

assay-031C (r_9c1e9b9d18cc × um-op-031:p01, frozen L0), required resource
grokbot, reason grokbot_weekly_usage_exhausted. On terminal 031C, assay-031D
is released as eligible per its frozen preregistered ordering — eligible
for operator authorization, never auto-enqueued, never altered.
