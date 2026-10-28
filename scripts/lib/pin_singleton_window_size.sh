#!/usr/bin/env bash
# pin_singleton_window_size.sh — SOURCE this from a singleton body's boot script (fleet-health,
# nazim, cai, cc-quality) to pin its OWN tmux window to 200x50 before claude starts (orch-console
# #79940).
#
# Why: the tmux server's default-size is 80x24, so any detached `new-session` without -x/-y
# (switch_singleton_token.sh, cold restarts, recycles) comes up at 80x24 — and boot_nazim_session's
# own -x 220 -y 50 does not survive those other paths (nazim read 80x24 on 2026-10-28). At 80
# columns, stale render fragments land on the composer border; on 2026-10-28 11:22-11:54Z that left
# cc-fleet-health deaf to every lane_nudge for ~30 min. Pinning here, inside the boot, covers EVERY
# creation path, because they all run the boot script. resize-window also sets window-size=manual,
# so a later client attach/detach can't shrink it back.
#
#   source "$ORCH_DIR/scripts/lib/pin_singleton_window_size.sh"
#
# Never blocks a boot: outside tmux it is a logged no-op; a failed resize is LOUD on stderr but the
# body still boots (a deaf-to-nudges body is bad; a body that refuses to boot over its window size is
# worse).

_PSWS_W="${SINGLETON_WINDOW_W:-200}"
_PSWS_H="${SINGLETON_WINDOW_H:-50}"
if [ -z "${TMUX_PANE:-}" ]; then
    echo "▶ window size: not under tmux (no TMUX_PANE) — nothing to pin"
elif tmux resize-window -t "$TMUX_PANE" -x "$_PSWS_W" -y "$_PSWS_H" 2>/dev/null; then
    echo "▶ window size pinned = ${_PSWS_W}x${_PSWS_H} ($(tmux display-message -p -t "$TMUX_PANE" '#S' 2>/dev/null), window-size=manual)"
else
    echo "⚠️  WARN: could not pin this tmux window to ${_PSWS_W}x${_PSWS_H} — it may stay at the server default (80x24), where stale render residue on the composer border makes lane_nudge refuse (#79940). Fix by hand: tmux resize-window -t \"$TMUX_PANE\" -x $_PSWS_W -y $_PSWS_H" >&2
fi
unset _PSWS_W _PSWS_H
