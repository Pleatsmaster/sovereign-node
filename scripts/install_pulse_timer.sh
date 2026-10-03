#!/bin/sh
# install_pulse_timer.sh — idempotent installer for the deterministic LIFE-0
# pulse timer. Must run as root. Safe to re-run: it rewrites the same units
# and re-enables the same timer.
#
# It also snapshots the current egress proxy environment into
# systemd/proxy.env (mode 600, root-owned): the pulse's GitHub sensor needs
# those variables, and systemd units do not inherit the installer's env.
# Re-run this script if the egress proxy configuration changes.
#
# Part of SCHEDULER-AUDIT-0 (scheduler-boundary repair). Apparatus, not organism.
#
# NOTE on VM replacement: /etc/systemd/system does not persist across VM
# replacement (only ~ survives). The canonical units live in
# ~/workspace/namariel-live0/life-0/systemd/ (persistent). After a replacement,
# re-run this script as root. The life0-pulse reporter cron checks timer
# health hourly and raises it if the timer is missing.
set -eu

SRC="/home/hatch/workspace/namariel-live0/life-0/systemd"

# Snapshot egress proxy vars (values may contain credentials: 600 root-owned).
{
    for v in http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY ALL_PROXY all_proxy; do
        val="$(eval "printf '%s' \"\${$v:-}\"")"
        if [ -n "$val" ]; then
            printf '%s=%s\n' "$v" "$val"
        fi
    done
} > "$SRC/proxy.env"
chmod 600 "$SRC/proxy.env"

install -m 644 "$SRC/life0-pulse.service" /etc/systemd/system/life0-pulse.service
install -m 644 "$SRC/life0-pulse.timer"   /etc/systemd/system/life0-pulse.timer
systemctl daemon-reload
systemctl enable --now life0-pulse.timer
systemctl is-active life0-pulse.timer
systemctl list-timers life0-pulse.timer --all --no-pager
