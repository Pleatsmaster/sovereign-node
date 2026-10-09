#!/usr/bin/env python3
"""GO-3A readiness check: start qwen_server.py on the provisioned model and
prove the /complete worker protocol answers. Writes readiness/smoke.json
(pass honest), exits nonzero on failure. Leaves the server running."""
import json
import os
import subprocess
import sys
import time
import urllib.request

MODEL = os.path.abspath("qwen3b.gguf")
env = dict(os.environ, WORKER_TEMPERATURE="0.0", WORKER_THREADS="4")
log = open("qwen_server.log", "w")
subprocess.Popen([sys.executable, "worker/qwen_server.py", MODEL, "--ctx",
                  "4096"], env=env, stdout=log, stderr=subprocess.STDOUT)


def complete(payload):
    req = urllib.request.Request(
        "http://127.0.0.1:8471/complete",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


ok, detail = False, ""
deadline = time.time() + 600
while time.time() < deadline:
    try:
        complete({"user": "reply with {}", "max_tokens": 8})
        ok = True
        break
    except Exception as e:  # noqa: BLE001
        detail = f"{type(e).__name__}: {e}"
        time.sleep(10)
resp = ""
if ok:
    try:
        r = complete({"user": "Say OK.", "max_tokens": 8})
        resp = json.dumps(r)[:200]
        ok = "text" in r
    except Exception as e:  # noqa: BLE001
        detail = f"{type(e).__name__}: {e}"
        ok = False
rec = {"check": "worker_backend", "server_up": ok, "response": resp,
       "detail": detail, "pass": ok}
os.makedirs("readiness", exist_ok=True)
json.dump(rec, open("readiness/smoke.json", "w"))
print(json.dumps(rec, indent=2))
sys.exit(0 if ok else 1)
