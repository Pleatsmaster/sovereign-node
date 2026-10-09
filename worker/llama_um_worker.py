#!/usr/bin/env python3
"""JSON-over-stdin cognitive worker backed by a local Meta Llama model.

Expects the llama_server.py daemon on 127.0.0.1:8471.
Stdin:  {"mode": "act"|"report", "packet": {...}}
Stdout: act    -> exactly one JSON action object
        report -> plain-text report
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

SERVER = "http://127.0.0.1:8471/complete"

SYSTEM_ACT = (
    "You are the cognitive worker inside a persistent autonomous research machine. "
    "You choose exactly one next act per response. "
    "Return EXACTLY ONE JSON object and nothing else, no prose, no code fences."
)

ACTION_CONTRACT = r'''
Return exactly one JSON object and nothing else:
{
  "kind": "read_file|search|run|write_file|apply_patch|git_diff|status|stop",
  "args": {...},
  "rationale": "short reason"
}
Tool argument forms:
- read_file: {"path":"...","start_line":1,"end_line":200}
- search: {"query":"...","path":".","limit":80}
- run: {"argv":["python","-c","print('accepted')"]}
- write_file: {"path":"...","content":"..."}
- apply_patch: {"patch":"unified diff"}
- git_diff: {}
- status: {}
- stop: {"status":"complete|blocked|defer","reason":"..."}
Do not claim completion until the objective's acceptance evidence has actually been obtained.
Prefer ordinary work. Do not propose persistent changes to the research machine during an ordinary mission.
'''.strip()


ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": [
                "read_file", "search", "run", "write_file",
                "apply_patch", "git_diff", "status", "stop",
            ],
        },
        "args": {"type": "object"},
        "rationale": {"type": "string"},
    },
    "required": ["kind", "args", "rationale"],
    "additionalProperties": False,
}

EXAMPLE = (
    'Example of a correct response (JSON only):\n'
    '{"kind": "read_file", "args": {"path": "README.md", "start_line": 1, "end_line": 50}, '
    '"rationale": "start by reading the README to understand the repo"}'
)

# Cell contract (RELATIONAL-0 GO-2 step 3): the exact tool interface a cell
# worker actually has. Selected automatically when the packet carries a
# "cell" block. This corrects the advertised/permitted mismatch; the
# enforcement (CellPolicy) is unchanged. Frozen at
# life-0/cells/CELL_CONTRACT_FROZEN.md - do not edit without re-freezing.
CELL_ACTION_CONTRACT = r'''
Return exactly one JSON object and nothing else:
{
  "kind": "read_file|write_file|fetch_evidence|stop",
  "args": {...},
  "rationale": "short reason"
}
Tool argument forms:
- read_file: {"path":"...","start_line":1,"end_line":200}  (paths are confined to your cell workspace)
- write_file: {"path":"...","content":"..."}  (writes stay inside your cell workspace)
- fetch_evidence: {"hash":"<64-hex artifact digest>"}  (only artifacts listed in shared_evidence are authorized; any other hash is refused)
- stop: {"status":"complete|blocked|defer","reason":"..."}
You have exactly these four tools. There is no run, search, apply_patch, git_diff, or status; proposing them is rejected.
'''.strip()

CELL_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["read_file", "write_file", "fetch_evidence", "stop"],
        },
        "args": {"type": "object"},
        "rationale": {"type": "string"},
    },
    "required": ["kind", "args", "rationale"],
    "additionalProperties": False,
}

CELL_EXAMPLE = (
    'Example of a correct response (JSON only):\n'
    '{"kind": "fetch_evidence", "args": {"hash": "<a 64-hex digest from shared_evidence>"}, '
    '"rationale": "retrieve the authorized evidence artifact"}'
)


def complete(system: str, user: str, max_tokens: int, schema: dict | None = None) -> str:
    payload: dict = {"system": system, "user": user, "max_tokens": max_tokens}
    if schema is not None:
        payload["schema"] = schema
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        SERVER, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=280) as resp:
        return json.loads(resp.read())["text"].strip()


def unfence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.startswith("json"):
            t = t[4:].lstrip()
    # tolerate prose around a single JSON object
    start, end = t.find("{"), t.rfind("}")
    if 0 <= start < end:
        return t[start : end + 1]
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-tokens", type=int, default=800)
    args = ap.parse_args()

    msg = json.loads(sys.stdin.read())
    mode = msg.get("mode")
    packet = msg.get("packet", {})

    if mode == "report":
        user = (
            "Write a concise research/work report from the packet below. Distinguish observed evidence from inference. "
            "Do not invent success. Include final status, evidence, changes made, unresolved blockers, and reusable lessons.\n\n"
            + json.dumps(packet, ensure_ascii=False, indent=2)
        )
        print(complete("You write concise, honest research reports.", user, args.max_tokens * 2))
        return 0

    user = (
        ACTION_CONTRACT
        + "\n\n"
        + EXAMPLE
        + "\n\nCURRENT PACKET:\n"
        + json.dumps(packet, ensure_ascii=False, indent=2)
    )
    # Cell mode: the packet carries a "cell" block, so the worker is a cell
    # worker. Advertise exactly the four tools the cell contract permits.
    if isinstance(packet.get("cell"), dict):
        user = (
            CELL_ACTION_CONTRACT
            + "\n\n"
            + CELL_EXAMPLE
            + "\n\nCURRENT PACKET:\n"
            + json.dumps(packet, ensure_ascii=False, indent=2)
        )
        schema = CELL_ACTION_SCHEMA
    else:
        schema = ACTION_SCHEMA
    text = unfence(complete(SYSTEM_ACT, user, args.max_tokens, schema=schema))
    action = json.loads(text)
    if not isinstance(action, dict) or "kind" not in action:
        raise ValueError("worker did not return a valid action object")
    print(json.dumps(action))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "kind": "stop",
            "args": {"status": "blocked", "reason": f"worker error: {type(exc).__name__}: {exc}"[:500]},
            "rationale": "worker failed to produce an action",
        }))
        raise SystemExit(0)
