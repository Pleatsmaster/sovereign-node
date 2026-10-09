#!/usr/bin/env python3
"""GO-3A readiness check: write the runner-local chain_adapter_config.json and
prove the pinned unified_machine tree is the tree that actually imports.
Writes readiness/baselines.json (identity_pass honest), exits nonzero on
failure. Prints `export` lines for the caller to eval."""
import json
import os
import sys

root = os.getcwd()
um_root = os.path.join(root, "um_baseline")
cfg = {
    "worker_argv": [sys.executable, "worker/llama_um_worker.py"],
    "um_root": um_root,
    "default_bounds": {"max_seconds": 300, "max_steps": 4,
                       "finalization_reserve_seconds": 30},
    "proxy_env": {},
    "acceptance_checkers": {},
}
json.dump(cfg, open("chain_adapter_config.json", "w"), indent=2)
sys.path.insert(0, um_root)
import unified_machine  # noqa: E402
loaded = os.path.realpath(unified_machine.__file__)
expected = os.path.realpath(os.path.join(um_root, "unified_machine"))
ok = loaded.startswith(expected + os.sep)
rec = {"check": "module_identity", "loaded": loaded,
       "pinned_root": expected, "identity_pass": ok, "pass": ok}
os.makedirs("readiness", exist_ok=True)
json.dump(rec, open("readiness/baselines.json", "w"))
print(json.dumps(rec, indent=2))
print(f"export RELATIONAL0_UM_ROOT={um_root}")
print(f"export RELATIONAL0_WORKER_SHIM={root}/worker/llama_um_worker.py")
print(f"export CHAIN_ADAPTER_CONFIG={root}/chain_adapter_config.json")
sys.exit(0 if ok else 1)
