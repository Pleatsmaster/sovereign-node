# RESEARCH-LOOP-0 validation — 2026-10-04

Base release: `efdfd62bd828a025d0861383d7dc8be4f37c07fd`.
Environment: Python 3.12, pytest 9.1.1, Linux; isolated temporary instance
directories. No live operational ledger was available or modified.

## Focused result: PASS

```
python -m pytest tests/test_research_loop.py tests/test_commitment_dispatch.py \
  tests/test_commitment_execution.py tests/test_resource_queue.py \
  tests/test_obligation_projection.py -q

68 passed in 6.46s
```

23 new controller tests plus 45 existing dependency tests.

The sequential integration test executed four isolated workers in one call:

```
PASS → refresh → FAIL → refresh → BLOCKED → refresh → PASS → empty frontier
```

The watch integration test started a real controller subprocess against an
empty isolated instance, waited for its durable WAITING_FRONTIER record,
admitted a fixture through the existing admission function, and observed one
worker execution without restarting the controller. It then yielded at its
one-launch lifetime budget. These are apparatus tests, not research results.

Restart/replay added zero duplicate worker executions. An unresolved launch
intent yielded EXECUTION_UNCERTAIN. Operator reconciliation allowed a different
authorized task to run without retrying the uncertain task or refunding its
budget. STOP inserted after intent but before spawn produced zero worker
executions. HOLD while running terminated the process and prevented selection
of the next task. Worker-only PASS without authoritative satisfaction was
recorded BLOCKED.

## Broader release checks: not fully green

The full suite cannot collect `tests/test_pulse.py`: the documented external
`fact0` package is absent in this environment. `namariel_live` is also an
external import in that test module. No replacement stubs were added to make
the release appear self-contained.

With that module excluded, three existing tests failed. All three also failed
in an independent, unmodified worktree at the base release:

```
python -m pytest -q --ignore=tests/test_pulse.py
# unmodified base: 3 failed, 134 passed in 11.61s
```

Final candidate regression: **2 failed, 158 passed in 15.80s** (160 tests,
pulse module excluded). The first hash-determinism test passed on this run;
its earlier failure remains evidence of timing sensitivity, not a correction.

| Existing test | Observed cause |
| --- | --- |
| `test_condition_set_hash_deterministic` | Scanner includes volatile pytest elapsed time in evidence that contributes to the condition hash. |
| `test_rejected_proposal_blocks_regeneration` | Same volatile condition hash changes across repeated scans; failure can vary with test timing. |
| `test_happy_path_two_step_chain` | Fixture's GOOD_SOURCE assumes `~/workspace/namariel-live0/life-0/AUTO_WORK_0.md`, absent here; d4 remains unsatisfied. |

These failures are preserved as inherited evidence. This change does not
repair the frozen release, modify an acceptance criterion or fabricate a
missing host path. The focused controller result does not certify the whole
release in this environment.

## Provenance and capability boundary

All **64 original MANIFEST.json input files** still match their recorded
SHA-256 values. Existing tracked release files, installer and manifest are
unchanged. Only the controller, controller tests and two implementation /
validation documents are added on a candidate branch.

Implemented and apparatus-verified: durable outer continuation across eligible
admitted commitments, watch wakeup, cumulative bounds, worker identity binding,
revocation checks and conservative crash handling.

Not demonstrated: live-node activation, an autonomous scientific discovery
cycle, paid/model worker operation, OS-level worker confinement, or live
resource-dispatch E2E. Activation requires the actual node state and its trusted
chain-executor adapter. B1 remains PENDING REAL PRECONDITION; 031C and 031D were
not re-enqueued or run.
