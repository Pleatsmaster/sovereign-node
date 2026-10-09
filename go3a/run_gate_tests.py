#!/usr/bin/env python3
"""GO-3A readiness check: reproduce the frozen GO-2 execution path by running
the scripted relational0 test suite. Writes readiness/tests.json (pass honest
from pytest's return code), exits nonzero on failure."""
import json
import os
import re
import subprocess
import sys

os.makedirs("readiness", exist_ok=True)
with open("readiness/pytest.log", "w") as log:
    p = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/test_relational0_c0.py",
         "tests/test_relational0_c1.py",
         "tests/test_relational0_go2.py", "-q"],
        stdout=log, stderr=subprocess.STDOUT)
log_text = open("readiness/pytest.log").read()
m = re.search(r"(\d+) passed", log_text)
rec = {"check": "execution_path", "pytest_rc": p.returncode,
       "tests_passed": int(m.group(1)) if m else 0,
       "pass": p.returncode == 0}
json.dump(rec, open("readiness/tests.json", "w"))
print(json.dumps(rec, indent=2))
sys.exit(0 if rec["pass"] else 1)
