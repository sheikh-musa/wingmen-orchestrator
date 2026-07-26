#!/usr/bin/env bash
# composer_capture.sh — shared "what is staged in the agent's composer?" extraction
# for scripts/reset_orch.sh and scripts/reset_cai.sh.  Sourced, not executed.
#
# WHY THIS EXISTS (2026-07-26): both reset scripts used
#     capture-pane -p | grep '^❯' | tail -1
# which captures ONLY THE FIRST LINE of a staged entry. Empirically verified in a
# throwaway tmux session against Claude Code v2.1.198, the composer box renders:
#
#     ──────────────────────────────   <- top border, col 0, U+2500
#     ❯<U+00A0>first line of the entry
#       second line (2-space indent, NO ❯)   <- hard newline OR soft wrap
#                                            <- an empty line inside the entry is truly empty
#       third line
#     ──────────────────────────────   <- bottom border, col 0
#
# So a wrapped >width entry AND a hard-newline entry both lose everything after
# line 1, and the preserved-input log then reads as a complete verbatim record of
# a partial capture. That is a partial reported as a whole — the exact failure
# class this log exists to eliminate. This lib captures the whole box instead,
# and where it CANNOT be sure it says so out loud instead of guessing.
#
# NBSP trap: the prompt is '❯' + U+00A0, not '❯' + space. Any pattern written
# with an ordinary space matches nothing (this already cost us a guard that never
# fired), so we normalise NBSP -> space before matching.
#
# Public entry points (both take/produce values in the CALLING shell — never call
# them on the right-hand side of a pipe, that runs them in a subshell and the
# results vanish):
#   composer_parse       "$pane_text"
#   composer_parse_pane  TMUX_BIN  PANE
# Both set these globals:
#   CC_N        number of content lines (0 = composer empty)
#   CC_RAW      the content, verbatim, newline-separated ('' when CC_N=0)
#   CC_FLAT     same content flattened to ONE line (newlines -> ' / ').
#               ALWAYS use this for anything fed to `tmux send-keys -l`: a raw
#               newline inside a send-keys payload submits the message early.
#   CC_MULTILINE  1 if the entry spans >1 rendered line
#   CC_PARTIAL    'ok' | 'noborder' | 'noprompt' — capture confidence
#   CC_UNSURE     1 if CC_PARTIAL != ok OR CC_MULTILINE=1 (i.e. the log must
#                 carry the [MULTI-LINE ENTRY — CAPTURE MAY BE PARTIAL] marker)
#   CC_EMPTY      1 if nothing was staged (empty, or a grey TUI placeholder)
#   CC_PH_BASIS   how the placeholder call was made: 'no-content' | 'dim-sgr' |
#                 'literal-fallback' (+'(real-text)' when it decided NOT empty)
#   CC_BYTES      byte length of the staged entry incl. line separators; used to
#                 size the backspace wipe (bytes >= chars, so it over-wipes, and
#                 over-wiping an empty composer is a verified no-op)
#   CC_MARKER     '' or the honest-partial marker string

CC_MARKER_TEXT='[MULTI-LINE ENTRY — CAPTURE MAY BE PARTIAL]'
CC_MARKER_NOPROMPT='[PROMPT LINE NOT FOUND — CAPTURE UNRELIABLE, COMPOSER MAY HAVE HELD TEXT]'

# ---------------------------------------------------------------------------
# IS THE BODY BUSY?  (added 2026-07-26 after orch-console #11719)
#
# Lives HERE, in the shared lib, on purpose. The defect it fixes was created by
# the two reset scripts drifting: reset_cai.sh had a mid-task guard, reset_orch.sh
# did not, and the hardening pass PORTED the existing guard instead of asking what
# else "busy" looks like. One definition, both callers, no drift.
#
# WHAT WENT WRONG: the guard keyed only on `esc to interrupt`, the FOREGROUND-turn
# marker. A body blocked on background agents renders
#     ✻ Waiting for 4 background agents to finish
# and shows NO `esc to interrupt` at all — so it read as IDLE and a reset would
# have proceeded and destroyed four agents' in-flight output, silently. Caught by
# Nazim at the moment he was about to reset cai. The guard was not wrong; ITS
# EVIDENCE WAS NARROWER THAN ITS CLAIM.
#
# 🔴 WHY EACH MARKER IS SCOPED RATHER THAN GREPPED WHOLE-PANE — measured, not
# theorised. The transcript above the composer is full of text ABOUT these
# markers; this very investigation printed both strings into the hub's own pane.
#   orch, whole pane : `esc to interrupt`                 -> 2  (1 real footer, 1 transcript)
#   orch, whole pane : `Waiting for N background agents`  -> 1  (transcript only, body idle)
# So the obvious fix — grep the whole pane for either string — would have marked
# the hub PERMANENTLY BUSY and un-resettable. Each marker is therefore pinned to
# the region where it actually RENDERS:
#   * `esc to interrupt`  -> footer, last 4 lines
#   * `Waiting for N ...` -> status region above the composer box (last 12), AND
#     anchored to a line that starts with a spinner glyph + space, because the
#     TUI indents transcript output by two spaces and a quote can never match.
#
# ASYMMETRY THAT DECIDES THE DESIGN: a false BUSY costs one exit-5 and an
# explicit RESET_FORCE=1. A false IDLE destroys work that cannot be recovered.
# So this fails CLOSED — any evidence of activity wins.
#
# DELIBERATELY NOT A MARKER: the `◯ general-purpose (+3) …` agent-list footer.
# It appears to be persistent chrome rather than a transient state, and a guard
# that fires forever gets habitually force-overridden, which is how a control
# dies. Not added without measuring that it clears. Revisit with evidence.
#
# Sets: CC_BUSY (0|1) and CC_BUSY_REASON (human-readable, '' when idle).
# Sets CC_BUSY_STALE=1 when a busy marker was PRESENT but the render was frozen,
# so the caller can say "proceeding despite a stale busy marker" out loud rather
# than silently treating a frozen body as idle.
pane_busy() {   # $1 = tmux bin, $2 = pane
  local txt
  txt="$("$1" capture-pane -t "$2" -p 2>/dev/null)"
  CC_BUSY=0; CC_BUSY_REASON=''; CC_BUSY_STALE=0
  if printf '%s\n' "$txt" | tail -4 | grep -q 'esc to interrupt'; then
    CC_BUSY=1; CC_BUSY_REASON="foreground turn in progress ('esc to interrupt')"
    return 0
  fi
  # 🔴 LOCALE TRAP, found by testing rather than by reading (2026-07-26).
  # The spinner glyph is '✻' — THREE BYTES of UTF-8. An anchor written as
  #     ^[^[:space:]][[:space:]]Waiting for ...
  # matches under a UTF-8 locale (one glyph, then the space) and FAILS under
  # C/POSIX, where [^[:space:]] consumes only the FIRST BYTE (0xE2) and the next
  # byte (0x9C) is not a space. It tested green in an interactive shell and dead
  # inside a bare `bash` — and these scripts are invoked BY NAZIM OVER SSH, where
  # the locale is routinely unset. The guard would have been silently dead in the
  # only environment that matters.
  # Fix: never depend on multibyte-aware bracket matching. Require only that the
  # line does NOT start with whitespace (the TUI indents transcript output by two
  # spaces, so a quoted mention can never match) and let '.*' cover the glyph.
  # LC_ALL=C is pinned so behaviour is identical everywhere rather than inherited.
  if printf '%s\n' "$txt" | tail -12 \
       | LC_ALL=C grep -qE '^[^[:space:]].*Waiting for [0-9]+ background agents'; then
    # 🔴 LIVENESS TEST — a busy marker read from a RENDER is a claim about what was
    # last PAINTED, not about what is true (found 2026-07-26, cai's pane).
    # cai sat showing "Waiting for 4 background agents" with the agent timer reading
    # EXACTLY '26m 42s' at 05:26Z, 07:00Z and 09:31Z — four hours, same second — while
    # agent_status said IDLE, its own last output said "this is a good place to reset
    # me", and it had been silent on the bus for 3.5h. The pane had stopped repainting.
    # Presence of the marker was true; the STATE it asserted was long gone.
    # A genuinely-waiting pane ANIMATES: the spinner cycles and that timer ticks every
    # second. So require the render to prove it is alive before believing it. If the
    # pane is byte-identical across the sample window, the busy claim is UNVERIFIABLE
    # and we must not report it as busy — otherwise the guard blocks every future reset
    # of a frozen body forever, and a control that can only be got past with
    # RESET_FORCE becomes a control nobody respects.
    # Cost: BUSY_LIVENESS_S seconds on the background-agent path only. The
    # `esc to interrupt` path above returns early and never pays it.
    local t2 secs="${BUSY_LIVENESS_S:-3}"
    sleep "$secs"
    t2="$("$1" capture-pane -t "$2" -p 2>/dev/null)"
    if [ "$txt" = "$t2" ]; then
      CC_BUSY=0
      CC_BUSY_REASON=''
      CC_BUSY_STALE=1
      return 0
    fi
    CC_BUSY=1; CC_BUSY_STALE=0
    CC_BUSY_REASON="$(printf '%s\n' "$txt" | tail -12 \
      | LC_ALL=C grep -oE 'Waiting for [0-9]+ background agents[a-z ]*' | tail -1)"
    CC_BUSY_REASON="blocked on background agents — ${CC_BUSY_REASON:-waiting for background agents}"
    return 0
  fi
  return 0
}

# ---------------------------------------------------------------------------
# PLACEHOLDER DETECTION — structural, not a string list.
#
# The mirror failure of under-capture is OVER-capture: the TUI paints a grey
# hint into an EMPTY composer ('Try "how does <filepath> work?"', 'Press up to
# edit queued messages'), and the old code — which excluded exactly ONE literal —
# would log the other as rescued operator text and then tell a fresh agent it had
# that hint "staged UNSENT". A fabricated line in this log is worse than a missed
# one, because a human reads it verbatim and trusts it.
#
# A deny-list of hint strings rots silently the next time the TUI changes a hint,
# and rots in the FABRICATING direction. So we use the render instead. Verified
# empirically (Claude Code v2.1.198, tmux capture-pane -p -e):
#
#   placeholder : \x1b[39m❯<NBSP>\x1b[2mTry "how does <filepath> work?"\x1b[0m
#   real input  : \x1b[39m❯<NBSP>Try "how does <filepath> work?"
#
# i.e. the hint is wrapped in SGR 2 (dim/faint) and genuinely-typed text — even
# text byte-identical to a hint — is not. Typing '/cle' (slash-command prefix)
# produced no dim run either, so autocomplete ghost text is not a false positive
# on this version; and if a future version DOES dim part of a real entry, the
# rule below still classifies it as real because non-dim content remains.
#
# _cc_dim_only: 0 (true) iff the composer content is ENTIRELY dim.
_cc_dim_only() {   # $1 = raw pane text WITH escape sequences
  local E=$'\033' l after remainder nbsp=$'\302\240'
  l="$(printf '%s\n' "$1" | grep '❯' | tail -1)"
  [ -n "$l" ] || return 1
  after="${l#*❯}"
  after="${after#"$nbsp"}"
  after="${after# }"
  case "$after" in "${E}["*"m"*) ;; *) return 1 ;; esac
  case "$after" in "${E}[2m"*) ;; *) return 1 ;; esac
  # drop the dim run, then any remaining SGR noise; anything printable left over
  # means real text is present alongside the dim part -> NOT a placeholder.
  remainder="$(printf '%s' "$after" \
    | sed -E "s/${E}\[2m[^${E}]*(${E}\[0m|${E}\[22m)?//" \
    | sed -E "s/${E}\[[0-9;]*[A-Za-z]//g" | tr -d '[:space:]')"
  [ -z "$remainder" ]
}

# FALLBACK ONLY — used when the pane text carries no SGR data at all (e.g. a
# plain `capture-pane -p` fixture, or a tmux/terminal that returns none). This IS
# a deny-list and it WILL rot; it exists so the no-escape path is no worse than
# the old behaviour, never as the primary discriminator.
_cc_is_placeholder_literal() {
  case "$1" in
    'Press up to edit queued messages') return 0 ;;
    'Try "'*'"')                        return 0 ;;
    *)                                  return 1 ;;
  esac
}

# stdin: pane text.  stdout: 'STATUS <partial> <n>' then n content lines.
# Escape sequences are stripped FIRST: with `capture-pane -e` the box borders
# arrive as \x1b[38;5;244m──── , which would defeat the column-0 border test.
_cc_extract() {
  LC_ALL=C sed -E "s/$(printf '\033')\[[0-9;]*[A-Za-z]//g" \
  | LC_ALL=C sed 's/\xc2\xa0/ /g' | awk '
    { line[NR] = $0 }
    END {
      start = 0
      for (i = NR; i >= 1; i--) { if (index(line[i], "❯") == 1) { start = i; break } }
      if (start == 0) { print "STATUS noprompt 0"; exit }

      s = line[start]
      sub(/^❯/, "", s)
      sub(/^ /, "", s)            # the NBSP, normalised to a space above
      n = 1; c[1] = s
      partial = "ok"; endfound = 0

      # Everything between the ❯ line and the bottom border is composer content.
      # The border starts at column 0; continuation lines are indented 2 spaces,
      # so an operator who literally types a row of ─ cannot fake a border.
      for (i = start + 1; i <= NR; i++) {
        if (index(line[i], "─") == 1) { endfound = 1; break }
        t = line[i]
        sub(/^  /, "", t)
        n++; c[n] = t
      }
      if (!endfound) partial = "noborder"

      while (n > 1 && c[n] ~ /^[ \t]*$/) n--          # drop trailing blank rows
      if (n == 1 && c[1] ~ /^[ \t]*$/) n = 0          # empty composer

      print "STATUS " partial " " n
      for (i = 1; i <= n; i++) print c[i]
    }'
}

# composer_parse "<pane text>"
composer_parse() {
  local out status_line
  out="$(_cc_extract <<<"${1-}")"
  status_line="${out%%$'\n'*}"

  CC_PARTIAL="$(printf '%s' "$status_line" | awk '{print $2}')"
  CC_N="$(printf '%s' "$status_line" | awk '{print $3}')"
  [ -n "$CC_PARTIAL" ] || CC_PARTIAL='noprompt'
  [ -n "$CC_N" ] || CC_N=0

  if [ "$CC_N" -gt 0 ] 2>/dev/null; then
    CC_RAW="${out#*$'\n'}"
  else
    CC_RAW=''
  fi

  # Flatten for any send-keys payload; ' / ' keeps the line boundary visible.
  CC_FLAT="$(printf '%s' "$CC_RAW" | awk 'NR>1{printf " / "} {printf "%s", $0} END{print ""}' \
             | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"

  CC_MULTILINE=0; [ "$CC_N" -gt 1 ] 2>/dev/null && CC_MULTILINE=1
  CC_UNSURE=0
  [ "$CC_MULTILINE" = 1 ] && CC_UNSURE=1
  [ "$CC_PARTIAL" != 'ok' ] && CC_UNSURE=1

  CC_MARKER=''
  [ "$CC_MULTILINE" = 1 ] && CC_MARKER="$CC_MARKER_TEXT"
  [ "$CC_PARTIAL" = 'noprompt' ] && CC_MARKER="$CC_MARKER_NOPROMPT"
  [ "$CC_PARTIAL" = 'noborder' ] && CC_MARKER="$CC_MARKER_TEXT"

  # Placeholder test: structural (dim SGR) when the capture carries escape data,
  # literal deny-list only when it does not. CC_PH_BASIS records which fired, so
  # a reader of this log can tell how the "nothing was staged" claim was reached.
  CC_EMPTY=0; CC_PH_BASIS='n/a'
  if [ "$CC_N" = 0 ]; then
    CC_EMPTY=1; CC_PH_BASIS='no-content'
  elif [ "$CC_N" = 1 ]; then
    case "${1-}" in
      *$'\033'*) CC_PH_BASIS='dim-sgr'; _cc_dim_only "${1-}" && CC_EMPTY=1 ;;
      *)         CC_PH_BASIS='literal-fallback'; _cc_is_placeholder_literal "$CC_FLAT" && CC_EMPTY=1 ;;
    esac
    # BELT AND SUSPENDERS (hub review, 2026-07-26). The dim-SGR test is the right
    # primary discriminator, but it was only verified against the fresh-session
    # hint; 'Press up to edit queued messages' could not be reproduced without
    # burning a metered prompt, so ITS dim-ness is inferred, not observed. If the
    # SGR path says "real text" and the literal list recognises it anyway, believe
    # the literal list. The two failure modes are NOT symmetric:
    #   false EMPTY  -> we drop a placeholder that was never typed        (harmless)
    #   false STAGED -> we FABRICATE a line in a load-bearing log and tell
    #                   a fresh agent it has an unexecuted instruction    (the defect)
    # So on disagreement we take the harmless direction. A human typing one of
    # these strings verbatim loses it — accepted, and recorded in CC_PH_BASIS so
    # the log says which test decided.
    if [ "$CC_EMPTY" != 1 ] && _cc_is_placeholder_literal "$CC_FLAT"; then
      CC_EMPTY=1; CC_PH_BASIS="${CC_PH_BASIS}+literal-override"
    fi
    [ "$CC_EMPTY" = 1 ] || CC_PH_BASIS="${CC_PH_BASIS}(real-text)"
  fi
  # (a 'noprompt' capture stays flagged even though it reports no content — we do
  # not know that the composer was empty, only that we could not read it)
  [ "$CC_EMPTY" = 1 ] && [ "$CC_PARTIAL" = 'ok' ] && { CC_MARKER=''; CC_UNSURE=0; }

  # Bytes >= characters for any UTF-8 input, and one separator per line break.
  CC_BYTES=$(( $(printf '%s' "$CC_RAW" | LC_ALL=C wc -c | tr -d ' ') + CC_N ))
  [ "$CC_EMPTY" = 1 ] && CC_BYTES=0
  return 0
}

# composer_parse_pane <tmux-bin> <pane>
# -e keeps the SGR data the placeholder test needs; _cc_extract strips it before
# parsing. If a tmux without -e ever shows up, fall back to the plain capture and
# the literal deny-list.
composer_parse_pane() {
  local _txt
  _txt="$("$1" capture-pane -t "$2" -p -e 2>/dev/null)"
  [ -n "$_txt" ] || _txt="$("$1" capture-pane -t "$2" -p 2>/dev/null)"
  composer_parse "$_txt"
}
