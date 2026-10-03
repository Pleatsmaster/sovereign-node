# SOVEREIGN node — release snapshot

Versioned packaging snapshot of the persistent SOVEREIGN local compute node
(the `life-0` operating floor). Created 2026-10-02 for source provenance.

**Packaging only.** No architectural changes were made to create this release.
Source, config, contracts, systemd units, and docs are preserved exactly as they
existed in the working floor, minus the documented exclusions (see
`SOVEREIGN_NODE_RELEASE_0.md` in the release mission record).

## Layout

- `*.md` — frozen design and operating documents (design, runbooks, contracts)
- `config/` — node configuration (`life0_config.json`)
- `contracts/` — completion contracts
- `inbox/` — inbox convention (`README.md`)
- `scripts/` — operator scripts (admit/dispatch/authorize/close commitments,
  pulse, agenda, resource queue, …)
- `src/life0/` — node package (pulse, delta, dispatch, gate, needs, sensors)
- `systemd/` — `life0-pulse.service` / `life0-pulse.timer` units
- `tests/` — test suite
- `MANIFEST.json` — sha256 of every shipped file

## Install

See `scripts/install_pulse_timer.sh` and the `*.md` operating documents.
The node is stdlib-Python plus systemd; there is no build step.

## Known limitations (packaging record, not fixed here)

- Several scripts, the systemd units, and `config/life0_config.json` carry
  hardcoded absolute paths from the machine this snapshot was taken on
  (`/home/hatch/workspace/namariel-live0/life-0`, `HOME=/home/hatch`). A fresh
  machine must adapt these; that adaptation is out of scope for this
  packaging-only release and will be exercised by the reproducibility demo.
- `systemd/proxy.env` (egress proxy credentials) is intentionally not shipped;
  the operator provides egress for the target machine.
- `config/life0_config.json` references `fact0_db` outside this tree and sets
  `github_repo` to null; both are recorded as-is.
- Runtime state (`state/`), dispatch history (`dispatch/`), proposal packets,
  run records, and caches were excluded as instance-local, not source.

## Provenance

Packaged from `/home/hatch/workspace/namariel-live0/life-0/` on 2026-10-02.
Per-file hashes in `MANIFEST.json`. Full packaging record (source-tree hash,
exclusions with reasons, commit SHA): `SOVEREIGN_NODE_RELEASE_0.md`.
