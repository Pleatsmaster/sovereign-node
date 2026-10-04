# config/

No machine-specific configuration is shipped. The node uses portable defaults
(paths relative to the install location; see `src/life0/config.py`).

To inspect or customize the effective configuration:

    python3 <life0-root>/scripts/life0_pulse.py --write-default-config --config <path>
    python3 <life0-root>/scripts/life0_pulse.py --once --config <path>

See INSTALL.md for the documented external inputs (proxy.env, fact0_db, repo identity).
