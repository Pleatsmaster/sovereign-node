#!/bin/sh
# run_pulse_locked.sh — deterministic single-instance LIFE-0 pulse invocation.
#
# No cognition anywhere in this script: acquire an exclusive lock, or record
# already_running and exit 0. The lock is held for the whole pulse via fd 9.
#
# Portable: all paths derive from this script's location; the interpreter is
# python3 from PATH (the node is stdlib-Python only — no venv, no hardcoded
# machine paths).
set -u

LIFE0_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
LOCK="$LIFE0_ROOT/state/pulse.lock"
LOG="$LIFE0_ROOT/state/pulse_cron.log"
SCRIPT="$LIFE0_ROOT/scripts/life0_pulse.py"

command -v python3 >/dev/null 2>&1 || {
    echo "FATAL: python3 not found on PATH" >&2
    exit 1
}
mkdir -p "$LIFE0_ROOT/state"

# Open (creating) the lock file, then try a non-blocking exclusive lock.
exec 9>"$LOCK"
if ! flock -n 9; then
    printf '{"event":"PULSE_SKIPPED","reason":"already_running","at":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"
    exit 0
fi

# Lock held for the duration of the pulse (fd 9 survives exec).
exec python3 "$SCRIPT" --once
