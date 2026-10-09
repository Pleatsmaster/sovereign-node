"""RELATIONAL-0 cells: two persistent logical cells under one governed organism.

Implements the approved v0.3 C0-C1 design (design baseline) and the ARCS
compatibility annex, per GO-1 (authorized 2026-10-09).

Boundaries (binding):
- The organism's authority model (admission, dispatch, STOP semantics,
  revocation rules) is NOT modified. This package only reads it.
- Cell workers get the restricted contract {read_file, write_file,
  fetch_evidence, stop} enforced by CellPolicy at the same validate-then-execute
  boundary as the organism runner. Worker prompt text is advisory only.
- Cells never write shared state directly; publication is an organism act.
- Module identity is fail-closed: cells.run verifies the loaded
  unified_machine against the pinned baseline before executing anything.

Modules:
  registry  - cell/relationship records on state/cell_records.jsonl (W1)
  view      - effective-history view + build_packet (W2)
  policy    - CellPolicy restricted action set + fail-closed identity (W3)
  evidence  - shared artifact store + fetch_evidence (W4)
  exchange  - PermitDisclosure / PermitCommit / organism publication (W5-W7)
  telemetry - per-attempt verification-cost records (W8)
  run       - cell act loop driving a worker under the cell contract
"""
