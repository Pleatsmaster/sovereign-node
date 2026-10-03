# CHAIN RUNBOOK 0 — executing an AUTO-CONTINUE-0 chain

Status: procedural operator apparatus, v1, 2026-10-01. NOT a constitutional
record. It documents how the executing agent must run a chain; it grants
no authority. Amendable by the operator. The authority remains
AUTO-WORK-0 and AUTO-CONTINUE-0; the gates remain the classifier scripts,
the frozen completion contract, and — since v1 — the snapshot barrier
and the dispatcher below.

v1 supersedes the v0 "STOP is terminal by instruction" rule with
executable topology: the agent cannot dispatch a continuation without a
dispatcher-issued token, and the dispatcher cannot issue one after a
STOP. Agent constraints are properties of the topology, not instructions
to the agent.

## The chain lifecycle (mechanical)

Snapshots and tokens live OUTSIDE the mission package, under
`life-0/dispatch/snapshots/<need_id>/`, so the d5 package-integrity
predicate never observes transient worker state.

1. AUTO-WORK-0 ELIGIBLE → write the dispatch record (unchanged).
2. Agent creates `<snapshots>/<need_id>/.staging-s00/` and spawns the
   s00 worker with the brief: write ONLY to the staging dir.
3. Agent runs `publish_step_snapshot.py --staging ... --snapshots-dir ...
   --step-id <id> --depth 0 --expect <artifacts>`.
   The script validates, hashes, and ATOMICALLY renames staging to
   `snapshots/s00/`, writing `STEP_COMPLETED.json {snapshot_hash, ...}`.
   No STEP_COMPLETED.json → the step did not complete, mechanically.
4. Agent runs `classify_auto_continue_0.py evaluate --snapshot
   <snapshots>/s00 ...`. The evaluator reads ONLY the snapshot; without
   a verifiable snapshot it refuses (exit 2) and writes nothing to the
   ledger. There is no code path that evaluates a half-written
   workspace.
   - Any STOP_* → the chain ends. Go to step 8.
   - CONTINUE_ELIGIBLE → continue.
5. Agent writes the mechanical proposal `CHAIN_PROPOSAL_sNN.json`
   (exactly the ten fields) into the package and runs `classify
   --parent-snapshot <snapshots>/sNN-1 --write-verdict <pkg>`.
   The verdict is bound to the parent's snapshot_hash.
   INELIGIBLE → stop, report the failed criteria.
6. Agent runs `authorize_dispatch.py --contract ... --proposal ...
   --ledger ... --classification <verdict> --snapshots-dir ...
   --write-auth <snapshots>/<need_id>/`.
   The dispatcher verifies: ELIGIBLE verdict bound to this proposal and
   to the exact parent snapshot; the parent's LATEST ledger decision is
   CONTINUE_ELIGIBLE (any STOP_* → refusal `STOP_IS_TERMINAL`, exit 3,
   no token — no override path exists); no existing token or recorded
   child for the parent (fan-out forbidden). On success it writes
   `DISPATCH_AUTH_sNN.json`, bound by hash to the exact proposal,
   verdict, and parent snapshot bytes.
   Refusal → the chain ends. Go to step 8.
7. ONLY with the token on disk, the agent spawns the continuation
   worker. Staging for sNN is seeded by copying the parent snapshot's
   content files (excluding STEP_COMPLETED.json); the worker amends in
   place. Then publish → evaluate with `--snapshot` AND
   `--dispatch-auth` AND `--snapshots-dir`. The evaluator re-verifies
   every token binding; a step without a valid token is recorded
   `STOP_UNAUTHORIZED_DISPATCH` (visible in the ledger) and the chain
   terminates. Repeat from step 5 while CONTINUE_ELIGIBLE, depth ≤ 5.
8. Append the consequence record; report to the operator. After publish,
   the agent may sync the snapshot's content files into `<pkg>/work/`
   for human readability — the evaluator never reads `work/`.

## Commitment-originated chains (COMMITMENT-DISPATCH-0)

For a need dispatched under COMMITMENT-DISPATCH-0, the chain starts
differently at the entry only:

1. The dispatcher has already verified execution eligibility, the
   supported authority class, and the binding hashes, and has run the
   AUTO-WORK-0 classifier (commitment-charter path: CC1/CC2/CC3/D3c) to
   ELIGIBLE. That verification IS the AUTO-WORK-0 entry condition for
   commitment chains — the delta-charter text scan cannot apply to
   commitments (they carry an acceptance record, not an operator delta
   file), and the same substantive constraints are enforced through the
   admission + execution machinery. Do not re-run the entry gate.
2. Read `dispatch/chains/<chain_id>/CHAIN_BINDING.json`. Materialize the
   chain contract deterministically:
   `dispatch_commitment.py materialize-contract --binding <path> --out
   <chain_dir>/CHAIN_CONTRACT.json`.
   done_predicates come verbatim from the frozen completion contract;
   bounds, vocabularies, gap policy, and retry policy are the frozen
   AUTO-CONTINUE-0 constants. Verify the contract loads
   (`load_contract` hash check) before proceeding.
3. Spawn the s00 worker with the brief: write ONLY to
   `<snapshots>/<need_id>/.staging-s00/`. The brief is the commitment's
   accepted objective, verbatim — the agent adds nothing to it.
4. Continue from step 3 of the standard lifecycle above (publish →
   evaluate → propose → authorize → ...).

The worker's tool envelope is the recorded authority class, unchanged.
If the work cannot proceed inside that class, the chain records the
blocker and stops; the dispatcher and the agent do not widen it.

## What the agent must never do

- Evaluate without `--snapshot`. Spawn a continuation worker without a
  `DISPATCH_AUTH_sNN.json` token. These are not guidelines; the scripts
  refuse, and the ledger records violations as STOPs.
- Edit `CHAIN_LEDGER.jsonl`, any snapshot, any token, or any frozen
  record. Corrections are new ledger entries, never edits.
- Reinterpret a STOP. A suspect STOP is reported with mechanical
  evidence (paths, timestamps, hashes); the chain still ends.

## Incident reference

First live chain, LIFE-PRESSURE-002, 2026-10-01: the executing agent
evaluated s00 before its writes were visible (premature entry + false
`--worker-status failed` entry), then overrode the resulting
STOP_STEP_FAILED / STOP_NO_PROGRESS on private judgment that the
baseline was false. The judgment was factually correct — terminal 8/8
was independently re-verified — but the override violated the
constitution. Frozen verdict:
`life-0/records/LIFE-PRESSURE-002_RUN_VERDICT_FROZEN.md`
(task VALID, capability DEMONSTRATED, constitutional compliance FAIL).
The v1 topology above is the repair: the visibility race is closed by
the publication barrier, and the override is closed by the dispatcher —
regression-tested in `tests/test_chain_hardening.py` (7 tests,
including the exact incident scenario). No mechanism change to the
contract, the predicates, or the constitutional records was needed.
