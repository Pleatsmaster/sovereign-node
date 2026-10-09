#!/usr/bin/env python3
"""GO-3A readiness check: download Qwen2.5-3B (revision-pinned URL) and verify
SHA-256. Writes readiness/model.json (hash_verified honest), exits nonzero on
mismatch."""
import hashlib
import json
import os
import subprocess
import sys

url = os.environ["MODEL_URL"]
expected = os.environ["MODEL_SHA256"]
dest = "qwen3b.gguf"
subprocess.run(["curl", "-sSL", "-o", dest, url], check=True)
h = hashlib.sha256()
with open(dest, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
actual = h.hexdigest()
rec = {"check": "model_integrity", "url": url,
       "expected_sha256": expected, "actual_sha256": actual,
       "bytes": os.path.getsize(dest),
       "hash_verified": actual == expected, "pass": actual == expected}
os.makedirs("readiness", exist_ok=True)
json.dump(rec, open("readiness/model.json", "w"))
print(json.dumps(rec, indent=2))
sys.exit(0 if rec["pass"] else 1)
