#!/bin/sh
# install_pulse_timer.sh — PRIVILEGED host integration: install the LIFE-0
# systemd pulse timer. Must run as root.
#
# This is HOST INTEGRATION, not node installation. The user-space node install
# is scripts/install.sh (no root). Run this script only when hourly systemd
# scheduling on this host is wanted; it writes to /etc/systemd/system.
#
# Usage: sudo scripts/install_pulse_timer.sh --user <run-user> [--life0-root <path>]
#   --user       account the pulse runs as (required; no default).
#   --life0-root life-0 root (default: derived from this script's location).
#
# Renders systemd/life0-pulse.service.template with the actual paths; the
# template's EnvironmentFile for proxy.env is optional (prefix "-"): if the
# host needs an egress proxy, the operator provides
# <life0-root>/systemd/proxy.env (mode 600) separately — this script never
# writes credentials.
#
# Safe to re-run: it rewrites the same units and re-enables the same timer.
set -eu

RUN_USER=""
LIFE0_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
while [ $# -gt 0 ]; do
    case "$1" in
        --user) RUN_USER="$2"; shift 2 ;;
        --life0-root) LIFE0_ROOT="$2"; shift 2 ;;
        -h|--help)
            echo "usage: sudo $0 --user <run-user> [--life0-root <path>]"
            exit 0 ;;
        *) echo "FATAL: unknown argument: $1 (usage: sudo $0 --user <run-user> [--life0-root <path>])" >&2; exit 2 ;;
    esac
done

[ -n "$RUN_USER" ] || { echo "FATAL: --user is required" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "FATAL: must run as root (writes /etc/systemd/system)" >&2; exit 1; }
id "$RUN_USER" >/dev/null 2>&1 || { echo "FATAL: unknown user: $RUN_USER" >&2; exit 1; }
[ -d "$LIFE0_ROOT/scripts" ] || { echo "FATAL: not a life-0 root: $LIFE0_ROOT" >&2; exit 1; }

RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
[ -n "$RUN_HOME" ] || { echo "FATAL: cannot resolve home directory for $RUN_USER" >&2; exit 1; }

TEMPLATE="$LIFE0_ROOT/systemd/life0-pulse.service.template"
[ -f "$TEMPLATE" ] || { echo "FATAL: template not found: $TEMPLATE" >&2; exit 1; }

echo "rendering systemd unit for user=$RUN_USER home=$RUN_HOME root=$LIFE0_ROOT"
sed -e "s|@RUN_USER@|$RUN_USER|g" \
    -e "s|@RUN_HOME@|$RUN_HOME|g" \
    -e "s|@LIFE0_ROOT@|$LIFE0_ROOT|g" \
    "$TEMPLATE" > /etc/systemd/system/life0-pulse.service
chmod 644 /etc/systemd/system/life0-pulse.service

install -m 644 "$LIFE0_ROOT/systemd/life0-pulse.timer" /etc/systemd/system/life0-pulse.timer

systemctl daemon-reload
systemctl enable --now life0-pulse.timer
systemctl is-active life0-pulse.timer
systemctl list-timers life0-pulse.timer --all --no-pager
