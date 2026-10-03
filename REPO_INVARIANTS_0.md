# REPO_INVARIANTS_0 — declared repository invariants (sensorium v0)

These are the `declared_repo_invariants` the AGENDA-PROPOSAL-0 Stage 1
scanner checks. Each entry is a deterministic predicate: a file's sha256
must match, or a file must exist. A violation emits a
`CONDITION_DETECTED` observation — never an instruction.

This list changes only by explicit operator edit. The scanner reads the
fenced `invariants` block below as JSON.

```invariants
[
  {
    "id": "INV-PROJECTOR-FROZEN",
    "description": "OBLIGATION-0 projector unmodified since freeze",
    "check": "file-sha256",
    "path": "scripts/project_obligations.py",
    "expected_sha256": "a56a323d47ab8a0c26205dff60ad5744890d0b8d34a8ea3714c5b9ac283ba7a2"
  },
  {
    "id": "INV-RUNBOOK-FROZEN",
    "description": "CHAIN_RUNBOOK_0.md unmodified since freeze",
    "check": "file-sha256",
    "path": "CHAIN_RUNBOOK_0.md",
    "expected_sha256": "7cd353a1f129898a6a0fe340fa745520f9cb86b08fcadc14c3f6c72fc151a378"
  },
  {
    "id": "INV-PRESSURE-VERDICT-FROZEN",
    "description": "LIFE-PRESSURE-002 frozen verdict unmodified",
    "check": "file-sha256",
    "path": "records/LIFE-PRESSURE-002_RUN_VERDICT_FROZEN.md",
    "expected_sha256": "2bcdabc77a8e3b65f809cf14abfce298cded19eff3ae06b4138cc661035e73a8"
  },
  {
    "id": "INV-CLASSIFIER-CONTINUE-FROZEN",
    "description": "classify_auto_continue_0.py unmodified since freeze",
    "check": "file-sha256",
    "path": "scripts/classify_auto_continue_0.py",
    "expected_sha256": "90c133c157320097102518ea6b71ad3981a455457009203f80abb0f74f0a57bd"
  },
  {
    "id": "INV-SNAPSHOT-PUBLISHER-FROZEN",
    "description": "publish_step_snapshot.py unmodified since freeze",
    "check": "file-sha256",
    "path": "scripts/publish_step_snapshot.py",
    "expected_sha256": "0d1efd8964019daaee8126cc26663609ef9ebc56f03809bfd137532830baa33d"
  },
  {
    "id": "INV-AUTHORIZE-DISPATCH-FROZEN",
    "description": "authorize_dispatch.py unmodified since freeze",
    "check": "file-sha256",
    "path": "scripts/authorize_dispatch.py",
    "expected_sha256": "94bbee96719efde0d15b9b49c1ec5d1dd91b9ccc1b818d309a09fd0db61321cf"
  },
  {
    "id": "INV-AGENDA-RECORD-EXISTS",
    "description": "AGENDA_PROPOSAL_0.md constitutional record present",
    "check": "file-exists",
    "path": "AGENDA_PROPOSAL_0.md"
  }
]
```
