#!/bin/sh
# run_pulse_locked.sh — deterministic single-instance LIFE-0 pulse invocation.
#
# No cognition anywhere in this script: acquire an exclusive lock, or record
# already_running and exit 0. The lock is held for the whole pulse via fd 9.
#
# Part of SCHEDULER-AUDIT-0 (scheduler-boundary repair). Apparatus, not organism.
set -u

LIFE0_ROOT="/home/hatch/workspace/namariel-live0/life-0"
LOCK="$LIFE0_ROOT/state/pulse.lock"
LOG="$LIFE0_ROOT/state/pulse_cron.log"
PY="/home/hatch/workspace/namariel-live0-v0.6.1/.venv/bin/python"
SCRIPT="$LIFE0_ROOT/scripts/life0_pulse.py"

# Open (creating) the lock file, then try a non-blocking exclusive lock.
exec 9>"$LOCK"
if ! flock -n 9; then
    printf '{"event":"PULSE_SKIPPED","reason":"already_running","at":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"
    exit 0
fi

# Lock held for the duration of the pulse (fd 9 survives exec).
exec "$PY" "$SCRIPT" --once
