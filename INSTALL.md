# INSTALL — SOVEREIGN node

## Requirements

- A Unix-like host with `python3` (the node is stdlib-Python only; no build
  step, no venv, no pip packages).
- `git` to fetch the release.
- No root required for the node itself. No commercial AI API keys. No model calls.

## User-space install (no root)

```sh
git clone https://github.com/Pleatsmaster/sovereign-node.git
cd sovereign-node
sh scripts/install.sh
```

`install.sh` derives all paths from its own location, verifies the tree against
`MANIFEST.json`, and ensures the state directory exists. It makes no network
calls and writes nothing outside the install location.

Run one pulse:

```sh
python3 scripts/life0_pulse.py --once
```

The pulse senses its surfaces, detects world deltas, and records one of three
terminal states: `NO_ACTION`, `STAGED`, or `PULSE_INVALID` (a required surface
could not be observed reliably — never counted as `NO_ACTION`).

## Hourly scheduling (optional, privileged host integration)

The node itself is scheduler-agnostic (oneshot pulses). For hourly systemd
scheduling — explicitly opt-in, requires root:

```sh
sudo scripts/install_pulse_timer.sh --user <run-user>
```

This renders `systemd/life0-pulse.service.template` with the actual install paths
and installs the unit + timer. It never writes credentials.

## Configuration

No machine-specific config is shipped. Portable defaults apply (paths relative
to the install location). To inspect or override:

```sh
python3 scripts/life0_pulse.py --write-default-config --config <path>
python3 scripts/life0_pulse.py --once --config <path>
```

## External inputs (documented, not silently borrowed)

The node degrades gracefully without any of these (the affected surface reports
an error and the pulse is `PULSE_INVALID`, never a false `NO_ACTION`):

- **GitHub lineage sensor** (`github_repo`, default null): read-only `GET`
  against `api.github.com` for the `{login}/unified_machine` mirror. Needs a
  credential helper at `/opt/hatch/skills/skill-creator/bin` on the host;
  without it the sensor reports an error. Set `github_repo` explicitly to skip
  the `/user` discovery call.
- **FACT-0 evidence sensor** (`fact0_db`): needs the frozen v0.13 tree
  (`~/workspace/namariel-live0-v0.13/src`) and the FACT-0 database. Without
  them the surface is unavailable — the expected state on a fresh machine.
- **Egress proxy** (`systemd/proxy.env`, mode 600): only if the host needs a
  proxy to reach `api.github.com`. The operator provides this file; the
  installer never writes credentials. The systemd template references it as
  optional (`EnvironmentFile=-...`).
- **Operator inbox** (`inbox/`): the operator routes genuine work here by
  placing files; the pulse never manufactures inbox content.

## What the installer does not do

- No model downloads, no API keys, no telemetry.
- No writes outside the install location (user-space install).
- The privileged timer script writes only to `/etc/systemd/system` and only
  when explicitly invoked as root with `--user`.
