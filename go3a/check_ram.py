#!/usr/bin/env python3
"""GO-3A readiness check: RAM admission. Writes readiness/ram.json (pass
field honest), exits nonzero on failure."""
import json
import os
import sys

mem = {}
with open("/proc/meminfo") as f:
    for line in f:
        k, v = line.split(":", 1)
        mem[k.strip()] = int(v.split()[0])  # KiB
avail = mem["MemAvailable"] / 1024 / 1024
req = float(os.environ["RAM_REQUIRED_GIB"])
rec = {"check": "ram_admission", "avail_gib": round(avail, 2),
       "required_gib": req, "pass": avail >= req}
os.makedirs("readiness", exist_ok=True)
json.dump(rec, open("readiness/ram.json", "w"))
print(json.dumps(rec, indent=2))
sys.exit(0 if rec["pass"] else 1)
