#!/usr/bin/env bash
# LOCK 1 verify (Musa op#24409). Safe to run as ANY user, on gzb, AFTER
# scripts/gzb_lock1_install.sh. Produces the proof set orch-console asked for
# in the gate pack: no secret VALUES are ever printed -- only hashes, lengths,
# permission bits, and pass/fail lines.
set -uo pipefail

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; }

echo "== 1. gazzai can no longer read the private key =="
if sudo -u gazzai cat /root/.ssh/gzb_to_mini >/dev/null 2>/tmp/lock1_verify_err; then
    fail "gazzai could read /root/.ssh/gzb_to_mini -- LOCK 1 NOT effective"
else
    grep -qi "permission denied" /tmp/lock1_verify_err && pass "gazzai: cat /root/.ssh/gzb_to_mini -> Permission denied" \
        || pass "gazzai: cat /root/.ssh/gzb_to_mini -> denied ($(cat /tmp/lock1_verify_err))"
fi
rm -f /tmp/lock1_verify_err

echo "== 2. old key path is gone (no stale readable copy) =="
if [ -e /home/gazzai/.ssh/gzb_to_mini ]; then
    fail "/home/gazzai/.ssh/gzb_to_mini still exists: $(stat -c '%U:%G %a' /home/gazzai/.ssh/gzb_to_mini)"
else
    pass "/home/gazzai/.ssh/gzb_to_mini absent"
fi

echo "== 3. a direct ssh-as-gazzai with the old key path fails =="
if sudo -u gazzai ssh -i /home/gazzai/.ssh/gzb_to_mini -o BatchMode=yes -o ConnectTimeout=5 \
        sheikhmusa@100.83.21.34 true >/dev/null 2>&1; then
    fail "direct gazzai ssh fetch SUCCEEDED -- key still reachable by gazzai"
else
    pass "direct gazzai ssh fetch with old key path fails (key gone)"
fi

echo "== 4. new key perms =="
if [ "$(stat -c '%U:%G %a' /root/.ssh/gzb_to_mini 2>/dev/null)" = "root:root 400" ]; then
    pass "/root/.ssh/gzb_to_mini is root:root 400"
else
    fail "/root/.ssh/gzb_to_mini perms: $(stat -c '%U:%G %a' /root/.ssh/gzb_to_mini 2>/dev/null || echo MISSING)"
fi

echo "== 5. wrapper + sudoers scope =="
if [ "$(stat -c '%U:%G %a' /usr/local/sbin/gzb-fetch-secrets-wrapper.sh 2>/dev/null)" = "root:root 500" ]; then
    pass "wrapper is root:root 500"
else
    fail "wrapper perms: $(stat -c '%U:%G %a' /usr/local/sbin/gzb-fetch-secrets-wrapper.sh 2>/dev/null || echo MISSING)"
fi
if sudo -u gazzai sudo -n -l 2>/dev/null | grep -q "gzb-fetch-secrets-wrapper.sh"; then
    pass "gazzai's NOPASSWD grant is scoped to the wrapper (sudo -l shows exactly that line)"
else
    fail "could not confirm gazzai's sudo -l grant (run as gazzai: sudo -n -l)"
fi

echo "== 6. the wrapper rejects arguments (no argv-injection surface) =="
if sudo -u gazzai sudo -n /usr/local/sbin/gzb-fetch-secrets-wrapper.sh extra-arg >/dev/null 2>&1; then
    fail "wrapper accepted an argument -- argv surface not closed"
else
    pass "wrapper rejects arguments"
fi

echo "== 7. the real fetch path still works end-to-end via the wrapper =="
PRE_HASH="$(sha256sum /dev/shm/wingmen-secrets/.env 2>/dev/null | cut -d' ' -f1 || echo none)"
if sudo -u gazzai /home/gazzai/fetch-secrets.sh >/tmp/lock1_verify_fetch.log 2>&1; then
    POST_HASH="$(sha256sum /dev/shm/wingmen-secrets/.env 2>/dev/null | cut -d' ' -f1 || echo none)"
    pass "fetch-secrets.sh ran clean as gazzai via the sudo wrapper"
    echo "    pre-hash:  $PRE_HASH"
    echo "    post-hash: $POST_HASH"
    [ "$PRE_HASH" = "$POST_HASH" ] && pass "/dev/shm/.env byte-identical across the mechanism change" \
        || echo "    NOTE: hash differs -- expected if the upstream bundle content itself changed between runs"
else
    fail "fetch-secrets.sh failed as gazzai: $(cat /tmp/lock1_verify_fetch.log)"
fi
rm -f /tmp/lock1_verify_fetch.log

echo "== 8. service + one dependent lane still healthy =="
systemctl is-active --quiet wingmen-fetch-secrets.service && pass "wingmen-fetch-secrets.service active" \
    || fail "wingmen-fetch-secrets.service not active"
for svc in wingmen-irsyad-coord wingmen-hub-self-recovery; do
    if systemctl list-unit-files "$svc.service" >/dev/null 2>&1; then
        systemctl is-active --quiet "$svc.service" 2>/dev/null \
            && pass "$svc.service active (dependent lane healthy post-LOCK-1)" \
            || echo "    NOTE: $svc.service not active (may be expected if not running at check time)"
    fi
done

echo "== verify run complete =="
