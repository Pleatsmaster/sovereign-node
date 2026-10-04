#!/bin/sh
# install.sh — user-space SOVEREIGN node installation. No root required.
#
# Derives all paths from this script's location; makes no machine-specific
# assumptions. Safe to re-run (idempotent).
#
# What it does:
#   1. checks for python3 (the node is stdlib-Python only; no build step)
#   2. verifies the tree against the release MANIFEST.json
#   3. ensures the state directory exists (the pulse also creates it)
#
# What it does NOT do:
#   - no systemd/host integration (see scripts/install_pulse_timer.sh;
#     requires root and is explicitly opt-in)
#   - no network calls, no credentials, no model APIs
#
# Configuration: portable defaults apply (paths relative to the install
# location; see src/life0/config.py). No machine-specific config is shipped.
# Inspect the effective configuration with:
#   python3 "$LIFE0_ROOT/scripts/life0_pulse.py" --write-default-config --config <path>
set -eu

LIFE0_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

echo "SOVEREIGN node install (user-space, no root)"
echo "  life-0 root: $LIFE0_ROOT"

# 1. Python (stdlib only).
command -v python3 >/dev/null 2>&1 || {
    echo "FATAL: python3 not found on PATH (the node needs stdlib-Python only)" >&2
    exit 1
}
echo "  python3: $(command -v python3)"

# 2. Tree integrity against the release manifest.
python3 - "$LIFE0_ROOT" <<'PYEOF'
import hashlib, json, os, sys
root = sys.argv[1]
man = json.load(open(os.path.join(root, "MANIFEST.json"), encoding="utf-8"))
bad, missing = [], []
for rel, want in man["inputs"].items():
    p = os.path.join(root, rel)
    if not os.path.exists(p):
        missing.append(rel)
        continue
    got = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if got != want:
        bad.append(rel)
if bad or missing:
    print(f"FATAL: tree verification failed: bad={bad} missing={missing}", file=sys.stderr)
    sys.exit(1)
print(f"  tree: {len(man['inputs'])}/{len(man['inputs'])} files verify against MANIFEST.json")
PYEOF

# 3. State directory (the pulse creates it on first run; pre-create for clarity).
mkdir -p "$LIFE0_ROOT/state"
echo "  state: $LIFE0_ROOT/state"

echo "OK: node installed (user-space)."
echo "Next steps:"
echo "  run one pulse:  python3 \"$LIFE0_ROOT/scripts/life0_pulse.py\" --once"
echo "  hourly timer (needs root, host integration):"
echo "                  sudo scripts/install_pulse_timer.sh --user <run-user>"
echo "See INSTALL.md for the full install and external-inputs documentation."
