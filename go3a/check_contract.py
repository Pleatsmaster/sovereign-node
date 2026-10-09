#!/usr/bin/env python3
"""GO-3A readiness check: verify the frozen worker-contract hash against the
shim on this runner. Writes readiness/contract.json (contract_verified
honest), exits nonzero on drift."""
import hashlib
import importlib.util
import json
import os
import sys

spec = importlib.util.spec_from_file_location("w", "worker/llama_um_worker.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
h = hashlib.sha256(
    (w.CELL_ACTION_CONTRACT + "\n\n" + w.CELL_EXAMPLE).encode()).hexdigest()
expected = os.environ["CONTRACT_SHA256"]
rec = {"check": "worker_contract", "computed_sha256": h,
       "expected_sha256": expected,
       "contract_verified": h == expected, "pass": h == expected}
os.makedirs("readiness", exist_ok=True)
json.dump(rec, open("readiness/contract.json", "w"))
print(json.dumps(rec, indent=2))
sys.exit(0 if rec["pass"] else 1)
