#!/usr/bin/env bash
# Real-tmux validation of the ghost-probe wrapper's SHIPPED keystroke path.
# The 54 unit tests use a fake-tmux stub; this exercises the ACTUAL tmux
# send-keys / BSpace / capture-pane round-trip that the stub cannot prove
# (the #23421 "gate test != shipped-path" lesson). SCRATCH pane only — never
# a live lane. No Enter is ever sent, so nothing executes.
set -uo pipefail

SESS="sre-probe-validate-$$"
ORCH="$HOME/wingmen/orchestrator"
CAP="$ORCH/scripts/lib/composer_capture.sh"
FW="$ORCH/scripts/lib/fire_window.py"
PY="$ORCH/.venv/bin/python3"; [ -x "$PY" ] || PY=python3
PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

cleanup(){ tmux kill-session -t "$SESS" 2>/dev/null; "$PY" "$FW" release "$SESS" 2>/dev/null; }
trap cleanup EXIT

# micro-sleep without the `sleep` binary (foreground sleep is blocked here)
napp(){ perl -e 'select(undef,undef,undef,0.08)' 2>/dev/null || true; }
# poll capture until the last non-empty line == want, or give up
lastline(){ tmux capture-pane -pt "$SESS" 2>/dev/null | awk 'NF{l=$0} END{print l}'; }
wait_last(){ local want="$1" i; for i in $(seq 1 40); do [ "$(lastline)" = "$want" ] && return 0; napp; done; return 1; }

echo "=== scratch session: $SESS (bash readline pane; real in-place line editing) ==="
tmux new-session -d -s "$SESS" -x 120 -y 40 "bash --norc --noprofile" 2>/dev/null || { echo "cannot create scratch session"; exit 2; }
napp; napp
# quiet, deterministic prompt so before/after diffing is exact
tmux send-keys -t "$SESS" -l 'PS1="RP>"; clear'; tmux send-keys -t "$SESS" Enter
napp; napp
for i in $(seq 1 40); do [ "$(lastline)" = "RP>" ] && break; napp; done
BEFORE="$(lastline)"
echo "before-line: [$BEFORE]"
[ "$BEFORE" = "RP>" ] || { echo "prompt not stable; abort"; exit 2; }

echo "--- Check 1: send-keys -l '~' types a LITERAL tilde (append == _probe_verdict 'real' shape) ---"
tmux send-keys -t "$SESS" -l '~'
if wait_last "RP>~"; then
  AFTER="$(lastline)"
  [ "$AFTER" = "${BEFORE}~" ] && ok "literal '~' appended: after == before+sentinel ([$AFTER])" \
                              || bad "after != before+sentinel ([$AFTER])"
else
  bad "sentinel never landed as literal '~' (after=[$(lastline)]) — send-keys -l may be misinterpreting it"
fi

echo "--- Check 2: BSpace reverts BYTE-IDENTICAL (_probe_revert_ok shape, cond#2) ---"
tmux send-keys -t "$SESS" BSpace
if wait_last "$BEFORE"; then
  REV="$(lastline)"
  [ "$REV" = "$BEFORE" ] && ok "revert byte-identical to before ([$REV])" \
                         || bad "revert NOT byte-identical ([$REV] vs [$BEFORE])"
else
  bad "BSpace did not restore before-line (got [$(lastline)])"
fi

echo "--- Check 3: _probe_verdict pure core agrees on the REAL captured strings ---"
V="$(bash -c "source '$CAP'; _probe_verdict '$BEFORE' '${BEFORE}~' '~'")"
[ "$V" = "real" ] && ok "_probe_verdict(before, before+~, ~) = real" || bad "_probe_verdict = [$V], expected real"
G="$(bash -c "source '$CAP'; _probe_verdict '' '~' '~'")"
[ "$G" = "ghost" ] && ok "_probe_verdict('', ~, ~) = ghost (replace shape)" || bad "ghost-shape verdict = [$G]"
if bash -c "source '$CAP'; _probe_revert_ok '$BEFORE' '$REV'"; then ok "_probe_revert_ok(before, revert) exit 0"; else bad "_probe_revert_ok returned nonzero on identical strings"; fi

echo "--- Check 4: fire_window integration on a REAL lock (held => wrapper 'locked', never types) ---"
"$PY" "$FW" release "$SESS" 2>/dev/null
if "$PY" "$FW" check "$SESS" >/dev/null 2>&1; then bad "check reports HELD before any hold (should be free/fail-open)"; else ok "check = free before hold"; fi
"$PY" "$FW" hold "$SESS" --ttl 20 --reason 'sre-probe-realpane-validate' >/dev/null 2>&1
if "$PY" "$FW" check "$SESS" >/dev/null 2>&1; then ok "after hold: check = HELD (wrapper would set CC_PROBE=locked, refuse without typing)"; else bad "hold did not register as held"; fi
"$PY" "$FW" release "$SESS" >/dev/null 2>&1
if "$PY" "$FW" check "$SESS" >/dev/null 2>&1; then bad "still held after release"; else ok "after release: check = free"; fi

echo "--- Check 5: pane_is_busy on a REAL pane whose bottom line shows 'esc to interrupt' ---"
# Type the marker as the composer's bottom line WITHOUT Enter (faithful to a busy CC
# pane; pane_busy greps tail -4 of capture-pane). Then Ctrl-U clears the line -> idle.
tmux send-keys -t "$SESS" -l "esc to interrupt"
for i in $(seq 1 40); do case "$(lastline)" in *"esc to interrupt") break;; esac; napp; done
if bash -c "source '$CAP'; pane_is_busy tmux '$SESS'"; then ok "pane_is_busy = TRUE when 'esc to interrupt' on bottom line"; else bad "pane_is_busy missed the busy marker on a real pane (last=[$(lastline)])"; fi
tmux send-keys -t "$SESS" C-u
if wait_last "RP>"; then :; fi
if bash -c "source '$CAP'; pane_is_busy tmux '$SESS'"; then bad "pane_is_busy = TRUE on a cleared idle pane (false positive, last=[$(lastline)])"; else ok "pane_is_busy = FALSE on cleared idle pane"; fi

echo
echo "===== REAL-PANE VALIDATION: $PASS passed, $FAIL failed ====="
[ "$FAIL" -eq 0 ]
