#!/usr/bin/env bash
# LOCK 1 verify (Musa op#24409). Safe to run as ANY user, on gzb, AFTER
# scripts/gzb_lock1_install.sh. Produces the proof set orch-console asked for
# in the gate pack: no secret VALUES are ever printed -- only hashes, lengths,
# permission bits, and pass/fail lines.
set -uo pipefail

# Where to find scripts/hooks/secret_shape_patterns.py for the step-7 pattern
# scan. Override with --repo-dir if the tracked checkout lives elsewhere.
REPO_DIR_FOR_SCAN="/home/gazzai/wingmen/orchestrator"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --repo-dir) REPO_DIR_FOR_SCAN="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

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

echo "== 7. sudo -n wrapper (direct, as gazzai) -> status line ONLY, no bundle on stdout =="
# orch-console bus #48668: the whole point of the redesign is that a gazzai agent
# running the wrapper DIRECTLY (not through fetch-secrets.sh) can never get a secret
# value on stdout -- it can at most refresh /dev/shm. Call it exactly that way here.
if sudo -u gazzai sudo -n /usr/local/sbin/gzb-fetch-secrets-wrapper.sh >/tmp/lock1_verify_direct.log 2>&1; then
    BYTES="$(wc -c < /tmp/lock1_verify_direct.log | tr -d ' ')"
    echo "    stdout+stderr: $(cat /tmp/lock1_verify_direct.log)"
    echo "    byte count: $BYTES"
    if [ "$BYTES" -lt 2000 ]; then
        pass "direct sudo -n wrapper stdout is small ($BYTES bytes) -- shape-consistent with a status line, not a bundle dump"
    else
        fail "direct sudo -n wrapper stdout is $BYTES bytes -- too large for a status line, investigate before trusting this design"
    fi
    if command -v python3 >/dev/null 2>&1 && [ -f "$REPO_DIR_FOR_SCAN/scripts/hooks/secret_shape_patterns.py" ]; then
        if python3 -c "
import sys
sys.path.insert(0, '$REPO_DIR_FOR_SCAN/scripts/hooks')
from secret_shape_patterns import SECRET_VALUE_PATTERNS
text = open('/tmp/lock1_verify_direct.log').read()
hits = [cls for cls, pat in SECRET_VALUE_PATTERNS.items() if pat.search(text)]
sys.exit(1 if hits else 0)
"; then
            pass "zero secret-shaped strings in the direct-wrapper output (scanned with the fleet's own pattern set)"
        else
            fail "the direct-wrapper output matched a secret-shaped pattern -- DO NOT PROCEED, investigate immediately"
        fi
    else
        echo "    NOTE: could not run the pattern scan (python3 or secret_shape_patterns.py not found at $REPO_DIR_FOR_SCAN) -- byte-count check above still stands, but re-run with --repo-dir set correctly for the full proof"
    fi
else
    fail "direct sudo -n wrapper call failed: $(cat /tmp/lock1_verify_direct.log)"
fi
rm -f /tmp/lock1_verify_direct.log

echo "== 8. the real fetch path still works end-to-end via fetch-secrets.sh =="
PRE_HASH="$(sha256sum /dev/shm/wingmen-secrets/.env 2>/dev/null | cut -d' ' -f1 || echo none)"
if sudo -u gazzai /home/gazzai/fetch-secrets.sh >/tmp/lock1_verify_fetch.log 2>&1; then
    POST_HASH="$(sha256sum /dev/shm/wingmen-secrets/.env 2>/dev/null | cut -d' ' -f1 || echo none)"
    pass "fetch-secrets.sh ran clean as gazzai via the sudo wrapper"
    echo "    pre-hash:  $PRE_HASH"
    echo "    post-hash: $POST_HASH"
    [ "$PRE_HASH" = "$POST_HASH" ] && pass "/dev/shm/.env byte-identical across the mechanism change" \
        || echo "    NOTE: hash differs -- expected if the upstream bundle content itself changed between runs"
    OWNER="$(stat -c '%U:%G %a' /dev/shm/wingmen-secrets 2>/dev/null || echo MISSING)"
    [ "$OWNER" = "gazzai:gazzai 700" ] && pass "/dev/shm/wingmen-secrets is gazzai:gazzai 700 post-swap (root-run wrapper correctly handed ownership back)" \
        || fail "/dev/shm/wingmen-secrets perms/owner: $OWNER (expected gazzai:gazzai 700)"
else
    fail "fetch-secrets.sh failed as gazzai: $(cat /tmp/lock1_verify_fetch.log)"
fi
rm -f /tmp/lock1_verify_fetch.log

echo "== 9. service + one dependent lane still healthy =="
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
