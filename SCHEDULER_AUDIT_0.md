# LIFE-0 SCHEDULER-AUDIT-0 — frozen record

**Authorized by:** Stephan (operator), 2026-10-01 17:27 EDT, in chat.
**Kind:** apparatus verification. NOT a LIFE episode, NOT organism work.
No developmental, scientific, lineage, or inheritance credit.
**Question:** does the clock invoke deterministic sensing directly, or does an
agent sit in the causal path?
**Verdict:** FAIL as installed → scheduler boundary repaired (this file
records both). After repair, the installed chain is:

```
systemd timer (life0-pulse.timer, hourly, Persistent=true)
  → flock -n single-instance wrapper (scripts/run_pulse_locked.sh)
    → venv python → scripts/life0_pulse.py --once
      → deterministic sensors → NO_ACTION | STAGED | PULSE_INVALID
```

A separate read-only reporter (the pre-existing `life0-pulse` cron slot,
repurposed) routes verdicts to chat and checks timer health. It is downstream
of sensing and never in its path.

---

## Check A — static path audit: FAIL (at the scheduler boundary)

**As-installed actual chain (verified, not inferred):**

```
Hatch runtime scheduler
  → cron agent (LLM; execution.kind=agent, mode=task)
    → agent interprets the job body
      → agent execs the pulse command via its tool
        → life0_pulse.py → src/life0/* (deterministic)
```

The forbidden hidden chain was real: an agent/model sat between the clock
and the pulse. Recorded facts (2026-10-01):

- scheduler type: Hatch runtime cron (agent-executed scheduled task)
- scheduler entry: saved job id `life0-pulse`, owner `goal:namariel-rebuild`,
  execution.kind=agent, mode=task; definition projection at
  `~/workspace/goals/namariel-rebuild/crons/hourly/life0-pulse__interval@1h.md`
  (read-only copy; the saved job is authoritative)
- exact command the agent was told to run:
  `/home/hatch/workspace/namariel-live0-v0.6.1/.venv/bin/python /home/hatch/workspace/namariel-live0/life-0/scripts/life0_pulse.py --once`
- working directory: agent-determined, not fixed by the job
- environment: agent-determined, not fixed by the job
- executable: `.../.venv/bin/python` → `/usr/bin/python3.12` (Python 3.12.3)
- script SHA-256: `dd5b1df03697a9a74ca6665f970159566110a82f9f7c5aa3ad5c43604cc99819`
- config SHA-256 (`config/life0_config.json`):
  `24ac0657c759a1a8c30d524d699df1208b035057912b00b53d22048c96ff8832`
- src SHAs: `__init__` ef8e1e02…, `config` 68c173b1…, `delta` 899fa0c3…,
  `dispatch` af31a09f…, `gate` f125de4b…, `needs` 092cbbde…,
  `pulse` 8afb637a…, `sensors` 5d066693… (full values in the audit shell log)
- stdout/stderr destination: captured by the cron agent into the run record;
  no file log existed
- cadence: every 1h, America/Toronto, anchored 2026-10-01T17:23:44
- other schedulers checked: no user crontab (binary absent), no
  life0/namariel/pulse systemd timers, /etc/cron.d holds only e2scrub_all

**Pulse code itself: CLEAN.** Full-tree grep over `src/life0/*.py` found:
no LLM/API/model imports or calls; no reads of MEMORY.md, goals, or
conversational context; no subagent or agent wrapper; no dynamically generated
commands; no network except the read-only GitHub sensor (api.github.com GETs
only, via the credential surrogate). `gate_check` is a pure function.
The defect was purely the scheduler boundary — the code honored
"clock → sensing" but the clock never invoked it directly.

## Check B — instrumented trigger: PASS (pulse itself)

Ran the exact scheduler command under `strace -f -e trace=%network,process`
(2026-10-01 17:30 EDT, real state dir — a legitimate pulse, NO_ACTION):

- 1 process total; 1 execve (the venv python only); zero subprocesses
- 5 HTTPS CONNECTs, ALL `CONNECT api.github.com:443` (the five read-only
  sensor reads: /user, /repos, /branches, /issues, /actions/runs)
- local unix socket to `/run/hatch/auth/authd.sock` (credential surrogate
  for the GitHub sensor — legitimate sensing surface)
- **zero inference-provider/model traffic** (no openai/anthropic/grok/etc.
  hostnames anywhere in the trace)

## Check C — model-denial test: PASS

Real `run_pulse` code path against an isolated fixture
(`/tmp/sched-audit-0/fixture`, apparatus only), sensor I/O stubbed or
fixture-local, environment scrubbed to `{HOME, LC_CTYPE, PATH}` — model
invocation impossible:

- valid sensors + no delta → **NO_ACTION** ✓
- fixture inbox delta → **STAGED** ✓ (1 need, package created ONLY under the
  fixture dispatch dir; real biography verified untouched)
- repeat, no new delta → **NO_ACTION** ✓ (deterministic need_id dedup)
- github sensor failure → **PULSE_INVALID** ✓ (the code's name for DEGRADED;
  excluded from the NO_ACTION denominator by design)

## Overlap test: FAIL (no single-instance protection)

Static: no lock/flock/fcntl/lockfile anywhere in the pulse path.
Demonstrated with two truly simultaneous fixture pulses (race window forced
with a deterministic sleep between snapshot-read and delta-detect):

- pulse A: **crashed** — `FileExistsError` in `stage_mission` (TOCTOU:
  both passed the `pkg_dir.exists()` check, then both called `mkdir`)
- pulse B: STAGED normally
- `needs.jsonl`: **2 duplicate NEED_CREATED** events (same need_id),
  1 NEED_STATUS
- staged packages: 1 (B won the mkdir race)

So: no duplicate package *content*, but duplicate queue events and a crashed
pulse (exit 1, partial state: observations written, snapshot never updated).
The biography denominator cannot trust overlapping ticks. The TOCTOU in
`stage_mission` is a known latent defect, NOT repaired here (organism code
is out of scope for this audit) — it is neutralized in practice by the
single-instance lock below.

## Repair (scheduler boundary only — 2026-10-01 ~17:35 EDT)

Per the authorization ("record the actual causal path and repair only the
scheduler boundary; do not redesign LIFE-0 around the failure"):

1. **Deterministic timer** (new): `systemd/life0-pulse.service` (oneshot) +
   `systemd/life0-pulse.timer` (`OnCalendar=hourly`, `Persistent=true`).
   Canonical units under `~/workspace/namariel-live0/life-0/systemd/`
   (persistent). Installed to /etc/systemd/system by
   `scripts/install_pulse_timer.sh` (idempotent, root).
2. **Single-instance lock** (new): `scripts/run_pulse_locked.sh` —
   `flock -n` on `state/pulse.lock`; contention logs
   `{"event":"PULSE_SKIPPED","reason":"already_running"}` to
   `state/pulse_cron.log` and exits 0. Pure shell, no cognition.
   Verified live: with the lock held externally, the service logged
   PULSE_SKIPPED, exited 0, recorded no pulse.
3. **Environment fix found during repair:** the first timer-fired pulse
   returned PULSE_INVALID (github+fact0 degraded) because systemd units do
   not inherit the shell's `https_proxy`/HOME. The unit now sets
   `HOME=/home/hatch` and reads `systemd/proxy.env` (snapshotted by the
   installer, mode 600, root-owned — values contain proxy credentials).
   Re-ran: service success, pulse NO_ACTION, all surfaces ok.
4. **Repurposed reporter** (same cron id `life0-pulse`, same schedule/owner/
   delivery): the agent no longer runs the pulse. Its body is now:
   (a) check `systemctl is-active life0-pulse.timer`, reinstall-or-report
   once; (b) read-only: report newly STAGED needs / PULSE_INVALID since the
   watermark, silent on NO_ACTION. Watermark lives in the goal's hidden_files.
   The reporter is downstream of recorded state, never in the sensing path.
5. **Untouched:** pulse.py, sensors.py, needs.py, gate.py, dispatch.py,
   delta.py, config.py, the frozen v0.13 tree, the dirty
   `unified_machine_v0` checkout, FACT-0's database.

**Post-repair installed chain:**

```
systemd timer (hourly, Persistent=true)
  → run_pulse_locked.sh (flock -n; already_running → log + exit 0)
    → venv python 3.12.3 → life0_pulse.py --once
      → sensors (api.github.com GETs only) → delta → need → gate → stage
      → NO_ACTION | STAGED | PULSE_INVALID  →  state/pulse_cron.log
```

First timer tick after repair: 2026-10-01 18:00 EDT. First reporter run
after repair: 2026-10-01 18:23 EDT.

## Known limits (honest)

- `/etc/systemd/system` does not survive VM replacement (only `~` does).
  After a replacement, re-run `scripts/install_pulse_timer.sh` as root.
  The reporter cron checks timer health hourly and raises a one-time alert
  with the exact restore command if the timer is missing.
- The `stage_mission` TOCTOU is latent, not fixed (out of scope); the flock
  makes it unreachable via the scheduler.
- The reporter cron still involves an agent, but strictly downstream of
  already-recorded pulse state: it cannot alter sensing or the denominator.

## Closure

The audit question is answered mechanically: as installed, an agent sat in
the causal path (FAIL); the boundary has been repaired to a deterministic
clock → sensing chain with single-instance protection, verified live.
**The hourly LIFE-0 pulse is now independently verified deterministic
sensing apparatus.** Audit frozen 2026-10-01. Leave the scheduler alone
unless a future audit re-opens it.

## Operator-frozen status (2026-10-01 17:38 EDT, Stephan's words)

SCHEDULER-AUDIT-0
as-installed verdict:
    FAIL
defect A:
    clock -> agent -> pulse
    CAUSAL CONTAMINATION
defect B:
    no single-instance lock
    duplicate NEED_CREATED + staging race reproduced
repair:
    systemd timer
    Persistent=true
    flock single-instance wrapper
    fixed venv/python entrypoint
    explicit environment file
post-repair:
    deterministic sensing path      PASS
    no inference dependency         PASS
    legitimate GitHub sensing only  PASS
    NO_ACTION path                  PASS
    STAGED fixture path             PASS
    INVALID path                    PASS
    overlap exclusion               PASS
pulse code modified:
    NO
frozen organism tree modified:
    NO
developmental credit:
    NONE

Residuals (operator disposition):
- VM-replacement reinstall is an infrastructure continuity limitation, not a
  reason to redesign LIFE-0. Reporter detection within an hour is sufficient.
- The latent staging TOCTOU remains recorded but untouched. The lock makes
  that path unreachable under the authorized scheduler topology. Real
  pressure to move exclusivity into the organism-side staging primitive
  would be another legitimate caller needing concurrent pulse invocation.
  Not before.

Standing boundary (operator):
- The Hatch agent may remain a notification observer ONLY while it cannot
  invoke the pulse, mutate pulse state, create needs, alter verdicts, or
  enter the denominator.
- agent may report life != agent causes life.

Operator directive: stop touching LIFE-0. No new scheduler machinery. No
TOCTOU refactor. No autonomy expansion. Let the repaired clock run; the next
biography entry comes from the world, not from proving the machine is alive.
