#!/usr/bin/env bash
# LOCK 1 rollback (Musa op#24409). RUNS AS ROOT ON gzb ONLY.
#
# Reverses scripts/gzb_lock1_install.sh: restores the gazzai:gazzai 0600 key,
# removes the sudoers grant + wrapper, and restores the pre-LOCK-1
# fetch-secrets.sh from the newest fetch-secrets.sh.bak-lock1-* backup it
# created. Idempotent-ish: safe to re-run; no-ops on anything already absent.
#
# Usage (as root, on gzb):
#   ./scripts/gzb_lock1_rollback.sh [--dry-run]
set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

GAZZAI_HOME="/home/gazzai"
OLD_KEY_PRIV="$GAZZAI_HOME/.ssh/gzb_to_mini"
OLD_KEY_PUB="$GAZZAI_HOME/.ssh/gzb_to_mini.pub"
NEW_KEY_PRIV="/root/.ssh/gzb_to_mini"
NEW_KEY_PUB="/root/.ssh/gzb_to_mini.pub"
WRAPPER_DST="/usr/local/sbin/gzb-fetch-secrets-wrapper.sh"
SUDOERS_DST="/etc/sudoers.d/wingmen-fetch-secrets-wrapper"
FETCH_DST="$GAZZAI_HOME/fetch-secrets.sh"

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "DRY-RUN: $*"
    else
        "$@"
    fi
}

if [ "$(id -u)" -ne 0 ]; then
    echo "FATAL: must run as root" >&2
    exit 1
fi

echo "== 1/4: restoring gazzai-readable key =="
if [ -f "$NEW_KEY_PRIV" ]; then
    run cp -p "$NEW_KEY_PRIV" "$OLD_KEY_PRIV"
    run chown gazzai:gazzai "$OLD_KEY_PRIV"
    run chmod 600 "$OLD_KEY_PRIV"
    [ -f "$NEW_KEY_PUB" ] && { run cp -p "$NEW_KEY_PUB" "$OLD_KEY_PUB"; run chown gazzai:gazzai "$OLD_KEY_PUB"; run chmod 644 "$OLD_KEY_PUB"; }
else
    echo "NOTE: $NEW_KEY_PRIV absent -- nothing to restore from (is $OLD_KEY_PRIV already present?)"
fi

echo "== 2/4: removing sudoers grant =="
run rm -f "$SUDOERS_DST"

echo "== 3/4: removing wrapper =="
run rm -f "$WRAPPER_DST"

echo "== 4/4: restoring pre-LOCK-1 fetch-secrets.sh =="
LATEST_BAK="$(ls -t "$GAZZAI_HOME"/fetch-secrets.sh.bak-lock1-* 2>/dev/null | head -1 || true)"
if [ -n "$LATEST_BAK" ]; then
    run cp -p "$LATEST_BAK" "$FETCH_DST"
    run chown gazzai:gazzai "$FETCH_DST"
    run chmod 700 "$FETCH_DST"
    echo "restored from: $LATEST_BAK"
else
    echo "WARN: no fetch-secrets.sh.bak-lock1-* backup found -- fetch-secrets.sh left AS-IS, check manually" >&2
fi

if [ "$DRY_RUN" -eq 0 ]; then
    run systemctl restart wingmen-fetch-secrets.service
    sleep 1
    systemctl is-active --quiet wingmen-fetch-secrets.service && echo "service active: yes" || echo "WARN: service not active post-rollback" >&2
fi

echo "== rollback done. NOTE: rotation is out of scope -- same keypair throughout. =="
