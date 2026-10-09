# Frozen cell worker interface (GO-2 step 3)

SHA-256 of the advertised contract text: `dce28a328c306e34f5b0f28dfe9e6be2bd70e4aef93da8ff49227ff788d728d3`

## Contract

```
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

Example of a correct response (JSON only):
{"kind": "fetch_evidence", "args": {"hash": "<a 64-hex digest from shared_evidence>"}, "rationale": "retrieve the authorized evidence artifact"}
```

## Output schema

```json
{
  "type": "object",
  "properties": {
    "kind": {
      "type": "string",
      "enum": [
        "read_file",
        "write_file",
        "fetch_evidence",
        "stop"
      ]
    },
    "args": {
      "type": "object"
    },
    "rationale": {
      "type": "string"
    }
  },
  "required": [
    "kind",
    "args",
    "rationale"
  ],
  "additionalProperties": false
}
```
