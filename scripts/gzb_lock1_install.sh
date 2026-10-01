#!/usr/bin/env bash
# LOCK 1 install (Musa op#24409, "never again"). RUNS AS ROOT ON gzb ONLY.
#
# Moves the gzb_to_mini private key from gazzai:gazzai 0600 (readable by the
# same OS user every lane runs as -- incident (a)'s exact path) to root:root
# 0400, installs a narrow root-owned wrapper + ONE scoped sudoers NOPASSWD
# line for that wrapper only, and deploys the one-line fetch-secrets.sh change
# (ssh -> sudo -n wrapper). /dev/shm output is byte-identical: same keypair,
# same forced-command target on the Mini, only the OS-level access path to the
# private key changes.
#
# Per orch-console's spec (bus #48628): this script is handed over for
# orch-console to RUN or SUPERVISE on gzb. It is NOT self-executing from a
# lane. No secret values are ever printed -- only hashes/lengths/paths.
#
# Usage (as root, on gzb):
#   ./scripts/gzb_lock1_install.sh [--dry-run] [--repo-dir DIR]
#
# Idempotent: safe to re-run. Pairs with scripts/gzb_lock1_rollback.sh and
# scripts/gzb_lock1_verify.sh (the latter is safe to run as any user).
set -euo pipefail

DRY_RUN=0
REPO_DIR="/home/gazzai/wingmen/orchestrator"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --repo-dir) REPO_DIR="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

GAZZAI_HOME="/home/gazzai"
OLD_KEY_PRIV="$GAZZAI_HOME/.ssh/gzb_to_mini"
OLD_KEY_PUB="$GAZZAI_HOME/.ssh/gzb_to_mini.pub"
NEW_KEY_PRIV="/root/.ssh/gzb_to_mini"
NEW_KEY_PUB="/root/.ssh/gzb_to_mini.pub"
WRAPPER_SRC="$REPO_DIR/deploy/gzb-fetch-secrets-wrapper.sh"
WRAPPER_DST="/usr/local/sbin/gzb-fetch-secrets-wrapper.sh"
SUDOERS_SRC="$REPO_DIR/deploy/wingmen-fetch-secrets-wrapper.sudoers"
SUDOERS_DST="/etc/sudoers.d/wingmen-fetch-secrets-wrapper"
FETCH_SRC="$REPO_DIR/deploy/fetch-secrets.sh"
FETCH_DST="$GAZZAI_HOME/fetch-secrets.sh"
FETCH_BAK="$GAZZAI_HOME/fetch-secrets.sh.bak-lock1-$(date -u +%Y%m%dT%H%M%SZ)"

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "DRY-RUN: $*"
    else
        "$@"
    fi
}

if [ "$(id -u)" -ne 0 ]; then
    echo "FATAL: must run as root (sudo -i, then run this script)" >&2
    exit 1
fi

for f in "$WRAPPER_SRC" "$SUDOERS_SRC" "$FETCH_SRC"; do
    [ -f "$f" ] || { echo "FATAL: missing tracked source: $f (is $REPO_DIR on the lock1 PR's branch/commit?)" >&2; exit 1; }
done

echo "== LOCK 1 install: pre-state =="
echo "old key: $(stat -c '%U:%G %a' "$OLD_KEY_PRIV" 2>/dev/null || echo 'MISSING')"
echo "old key sha256: $(sha256sum "$OLD_KEY_PRIV" 2>/dev/null | cut -d' ' -f1 || echo 'MISSING')"
echo "fetch-secrets.sh sha256 (live, pre-change): $(sha256sum "$FETCH_DST" | cut -d' ' -f1)"

echo "== 1/5: moving private key to root:root 0400 =="
run mkdir -p /root/.ssh
run chmod 700 /root/.ssh
if [ -f "$OLD_KEY_PRIV" ]; then
    run cp -p "$OLD_KEY_PRIV" "$NEW_KEY_PRIV"
    run chown root:root "$NEW_KEY_PRIV"
    run chmod 400 "$NEW_KEY_PRIV"
    [ -f "$OLD_KEY_PUB" ] && { run cp -p "$OLD_KEY_PUB" "$NEW_KEY_PUB"; run chown root:root "$NEW_KEY_PUB"; run chmod 444 "$NEW_KEY_PUB"; }
    # Remove gazzai's copy LAST, only after the root copy is confirmed in place --
    # closes incident (a)'s exact path (gazzai-readable key).
    if [ "$DRY_RUN" -eq 0 ]; then
        [ -f "$NEW_KEY_PRIV" ] && sha256sum "$OLD_KEY_PRIV" "$NEW_KEY_PRIV" | awk '{print $1}' | uniq | wc -l | grep -q '^1$' \
            || { echo "FATAL: key content mismatch after copy, aborting before removing gazzai's copy" >&2; exit 1; }
    fi
    run rm -f "$OLD_KEY_PRIV" "$OLD_KEY_PUB"
else
    echo "NOTE: $OLD_KEY_PRIV already absent (prior install?) -- skipping move, checking root copy exists"
    [ -f "$NEW_KEY_PRIV" ] || { echo "FATAL: neither old nor new key location has the key" >&2; exit 1; }
fi

echo "== 2/5: installing root-owned wrapper =="
run install -o root -g root -m 0500 "$WRAPPER_SRC" "$WRAPPER_DST"

echo "== 3/5: installing scoped sudoers fragment (validated before activation) =="
TMP_SUDOERS="$(mktemp)"
cp "$SUDOERS_SRC" "$TMP_SUDOERS"
if ! visudo -cf "$TMP_SUDOERS"; then
    echo "FATAL: sudoers fragment fails visudo syntax check, NOT installing" >&2
    rm -f "$TMP_SUDOERS"
    exit 1
fi
run install -o root -g root -m 0440 "$TMP_SUDOERS" "$SUDOERS_DST"
rm -f "$TMP_SUDOERS"
# Re-validate the live sudoers tree as a whole post-install.
visudo -cf /etc/sudoers || { echo "FATAL: /etc/sudoers.d tree invalid post-install -- investigate before proceeding" >&2; exit 1; }
for d in /etc/sudoers.d/*; do visudo -cf "$d" >/dev/null || echo "WARN: $d fails standalone check (may be fine if included relatively)"; done

echo "== 4/5: deploying fetch-secrets.sh (one-line ssh -> sudo wrapper change) =="
run cp -p "$FETCH_DST" "$FETCH_BAK"
run install -o gazzai -g gazzai -m 0700 "$FETCH_SRC" "$FETCH_DST"

echo "== 5/5: restarting wingmen-fetch-secrets.service and verifying =="
run systemctl restart wingmen-fetch-secrets.service
if [ "$DRY_RUN" -eq 0 ]; then
    sleep 1
    systemctl is-active --quiet wingmen-fetch-secrets.service || { echo "FATAL: service not active after restart" >&2; exit 1; }
    echo "service active: yes"
    echo "post-install /dev/shm/.env sha256: $(sha256sum /dev/shm/wingmen-secrets/.env 2>/dev/null | cut -d' ' -f1 || echo 'MISSING')"
fi

echo "== done. Run scripts/gzb_lock1_verify.sh next for the full proof set. =="
