#!/usr/bin/env python3
"""GO-3A readiness report: aggregate readiness/*.json into an honest verdict.

READY iff every required check has a record AND passed. Missing or failed
checks -> NOT_READY, with the missing/failed names preserved. The process
exit code reflects the verdict so the workflow run shows red on NOT_READY.
Always run (even after earlier failures)."""
import glob
import json
import os
import platform
import sys
import time

checks = {}
for path in sorted(glob.glob("readiness/*.json")):
    name = os.path.basename(path)[:-5]
    try:
        checks[name] = json.load(open(path))
    except Exception as e:  # noqa: BLE001
        checks[name] = {"check": name, "pass": False,
                        "error": f"unreadable: {e}"}
required = ["ram", "model", "contract", "baselines", "tests", "smoke"]
missing = [r for r in required if r not in checks]
failed = [r for r in required if r in checks and not checks[r].get("pass")]
verdict = "READY" if not missing and not failed else "NOT_READY"
timing = {k: int(os.environ.get(f"{k}_seconds", -1))
          for k in ("deps", "provision", "tests", "smoke")}
report = {
    "workflow": "GO-3A readiness (preparation only; no qualification instances)",
    "verdict": verdict,
    "harness_sha": os.environ.get("HARNESS_SHA"),
    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "runner": {"platform": platform.platform(),
               "python": platform.python_version()},
    "required_checks": required,
    "missing_checks": missing,
    "failed_checks": failed,
    "checks": checks,
    "timing_seconds": timing,
    "budget_note": ("Provisioning time recorded above under 'provision'. The "
                    "frozen GO-3 gate caps qualification at 30 min total; this "
                    "report does not reinterpret that budget. If provisioning "
                    "plus three instances exceeds it, the gate definition must "
                    "be explicitly amended."),
    "authorizations": {
        "go3_qualification_instances": "HOLD (separate decision required)",
        "c4": "HOLD",
        "prompt_tuning": "prohibited",
        "external_apis": "prohibited",
        "frozen_interface_change": "prohibited",
    },
}
json.dump(report, open("go3a-readiness-report.json", "w"), indent=2)
print(json.dumps(report, indent=2))
sys.exit(0 if verdict == "READY" else 1)
