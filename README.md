# SOVEREIGN node

Versioned release of the persistent SOVEREIGN local compute node (the `life-0`
operating floor).

## Layout

- `*.md` — frozen design and operating documents (design, runbooks, contracts)
- `config/` — configuration convention (no machine-specific config shipped)
- `contracts/` — completion contracts
- `inbox/` — inbox convention (`README.md`)
- `scripts/` — operator scripts (install, pulse, admit/dispatch/authorize/close
  commitments, agenda, resource queue, …)
- `src/life0/` — node package (pulse, delta, dispatch, gate, needs, sensors)
- `systemd/` — `life0-pulse.service.template` / `life0-pulse.timer` (rendered by
  the privileged installer; not installed directly)
- `tests/` — test suite
- `INSTALL.md` — install procedure and documented external inputs
- `MANIFEST.json` — sha256 of every shipped file

## Install

```sh
git clone https://github.com/Pleatsmaster/sovereign-node.git
cd sovereign-node
sh scripts/install.sh        # user-space, no root
python3 scripts/life0_pulse.py --once
```

See `INSTALL.md` for the full procedure, hourly systemd scheduling (opt-in,
requires root), configuration, and documented external inputs. The node is
stdlib-Python; there is no build step.

## Known limitations

- `systemd/proxy.env` (egress proxy credentials) is intentionally not shipped;
  the operator provides it if the host needs a proxy (see INSTALL.md).
- The FACT-0 sensor needs the frozen v0.13 tree and FACT-0 database; without
  them the surface degrades cleanly per the documented contract (see INSTALL.md).
- The GitHub sensor needs the host credential helper; without it the sensor
  reports an error rather than crashing the pulse.
- Runtime state (`state/`), dispatch history (`dispatch/`), proposal packets,
  run records, and caches are instance-local, not source, and are not shipped.

## Provenance

Packaged from the operational working floor on 2026-10-02; installation
portability remediated 2026-10-03 (see `INSTALL_PORTABILITY_0.md` in the
mission record). Per-file hashes in `MANIFEST.json`.
