#!/usr/bin/env python3
"""Qwen-backed inference server exposing the frozen worker protocol.

Identical /complete protocol to llama_server.py:
  POST /complete {"system": str, "user": str, "max_tokens": int, "schema"?: {...}}
  -> {"text": str}

The worker shim (llama_um_worker.py) is unchanged: it speaks HTTP to
127.0.0.1:8471 regardless of which model serves. The frozen cell contract
(CELL_CONTRACT_FROZEN.md) covers the advertised tools, not the transport.

Model: Qwen2.5-3B-Instruct Q4_K_M, official Qwen GGUF repo, revision-pinned.
SHA-256 verified by the caller before starting this server.

Usage: python3 qwen_server.py /path/to/model.gguf [--ctx 4096]
Env:   WORKER_TEMPERATURE (default 0.0 — the GO-3 gate fixes temperature 0.0)
       WORKER_THREADS (default 4)
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from llama_cpp import Llama


def main() -> None:
    model_path = sys.argv[1]
    n_ctx = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--ctx" else 4096
    temperature = float(os.environ.get("WORKER_TEMPERATURE", "0.0"))
    n_threads = int(os.environ.get("WORKER_THREADS", "4"))
    llm = Llama(model_path=model_path, n_ctx=n_ctx, n_threads=n_threads,
                verbose=False)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/complete":
                self.send_error(404)
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                messages = []
                if body.get("system"):
                    messages.append({"role": "system", "content": body["system"]})
                messages.append({"role": "user", "content": body["user"]})
                out = llm.create_chat_completion(
                    messages=messages,
                    max_tokens=int(body.get("max_tokens", 800)),
                    temperature=temperature,
                    response_format=(
                        {"type": "json_object", "schema": body["schema"]}
                        if body.get("schema")
                        else {"type": "json_object"}
                    ),
                )
                text = out["choices"][0]["message"]["content"] or ""
                data = json.dumps({"text": text}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:  # noqa: BLE001
                err = json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)

        def log_message(self, *args):  # noqa: D102
            pass

    print(f"qwen server up: {model_path} ctx={n_ctx} temp={temperature} "
          f"threads={n_threads}", flush=True)
    HTTPServer(("127.0.0.1", 8471), Handler).serve_forever()


if __name__ == "__main__":
    main()
