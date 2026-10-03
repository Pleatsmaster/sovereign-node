#!/usr/bin/env python3
"""LIFE-0 pulse entry point — cron-ready.

Runs one event-driven pulse: sense three surfaces, detect world deltas,
form needs, gate, stage mission packages. Makes zero model calls, spends
nothing, launches nothing.

Intended schedule: hourly via cron (Mata creates the cron after review).
Example crontab (NOT installed by this script):

    0 * * * * /home/hatch/workspace/namariel-live0-v0.6.1/.venv/bin/python \\
        /home/hatch/workspace/namariel-live0/life-0/scripts/life0_pulse.py --once \\
        >> /home/hatch/workspace/namariel-live0/life-0/state/pulse_cron.log 2>&1

Exit codes: 0 = pulse complete (including NO_ACTION); 1 = unexpected failure;
2 = configuration error.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIFE0_ROOT / "src"))

# Resolve namariel_live against the frozen v0.13 tree (read-only) before any
# import: FACT-0's query interface needs v0.13's auth module, while the
# acceptance venv carries an older editable namariel_live. The frozen tree is
# never written by the pulse.
sys.path.insert(0, str(Path.home() / "workspace/namariel-live0-v0.13/src"))

from life0 import config as config_mod  # noqa: E402
from life0.pulse import run_pulse  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="LIFE-0 event-driven pulse (v0)")
    ap.add_argument("--once", action="store_true",
                    help="run exactly one pulse and exit")
    ap.add_argument("--config", default=None, help="path to life0_config.json")
    ap.add_argument("--write-default-config", action="store_true",
                    help="write the default config file and exit")
    args = ap.parse_args()

    if args.write_default_config:
        p = config_mod.write_default(args.config)
        print(f"wrote {p}")
        return 0

    try:
        cfg = config_mod.load(args.config)
    except Exception as e:
        print(f"CONFIG_ERROR: {e}", file=sys.stderr)
        return 2

    try:
        result = run_pulse(cfg)
    except Exception:
        traceback.print_exc()
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
