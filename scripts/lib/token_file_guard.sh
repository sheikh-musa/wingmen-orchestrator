# token_file_guard.sh: refuse an OAuth key file whose NAME and CONTENT disagree.
#
# WHY (orch-console #49099/#49107, 2026-10-02): gzb ~/.wingmen/keys/musa-oauth-token held the SYED
# token. Anything launched "as Musa" from that path ran on Syed, and nothing noticed, because
# every reader trusted the file name. This guard makes the name a checked claim: a file named
# <acct>-oauth-token must hash to <acct>'s fp in scripts/lib/token_fps.map (the ONE place to
# update on a rotation). Files whose name maps to no account are not judged.
#
# token_file_guard <path>: return 0 = OK to use; 1 = REFUSE (reason on stderr, never the token).
# Fail-closed: unreadable/empty token or a missing map refuses. bash-3.2 safe; set -u safe.
# Callers decide what refusal means (exit), but must never fall back to another account.

_TFG_MAP_DEFAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/token_fps.map"

_tfg_sha12() {   # stdin -> first 12 hex of sha256
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 | cut -c1-12; else sha256sum | cut -c1-12; fi
}

_tfg_acct_for_name() {   # basename -> account (echo) if it is <acct>-oauth-token
  case "$1" in *-oauth-token) printf '%s' "${1%-oauth-token}" ;; *) printf '' ;; esac
}

token_file_guard() {
  local path="$1" map="${TOKEN_FPS_MAP:-$_TFG_MAP_DEFAULT}" names="" n acct expected got owner real
  [ -r "$map" ] || { echo "token_file_guard: REFUSING '$path': fp map '$map' missing/unreadable (fail-closed)." >&2; return 1; }
  # Judge by the name the caller used AND the resolved target's name (a symlink can lie either way).
  names="$(basename "$path")"
  real="$(cd "$(dirname "$path")" 2>/dev/null && pwd -P)/$(basename "$path")"
  if [ -L "$path" ]; then
    real="$(readlink "$path")"; case "$real" in /*) ;; *) real="$(dirname "$path")/$real" ;; esac
    names="$names $(basename "$real")"
  fi
  local judged=0
  for n in $names; do
    acct="$(_tfg_acct_for_name "$n")"
    [ -n "$acct" ] || continue
    expected="$(awk -v a="$acct" '!/^#/ && $1==a {print $2; exit}' "$map")"
    [ -n "$expected" ] || continue
    judged=1
    [ -r "$path" ] || { echo "token_file_guard: REFUSING '$path': not readable." >&2; return 1; }
    got="$(printf '%s' "$(cat "$path")" | _tfg_sha12)"
    if [ -z "$got" ] || [ "$got" = "e3b0c44298fc" ]; then
      echo "token_file_guard: REFUSING '$path': empty token file." >&2; return 1
    fi
    if [ "$got" != "$expected" ]; then
      owner="$(awk -v f="$got" '!/^#/ && $2==f {print $1; exit}' "$map")"
      if [ -n "$owner" ]; then owner="= $owner"; else owner="not in the map"; fi
      echo "token_file_guard: REFUSING '$path': named '$acct' (expects fp $expected) but it hashes to $got ($owner). Either: fp map out of date (after a rotation, update scripts/lib/token_fps.map, the ONE place) OR the file is mislabelled. Not using it." >&2
      return 1
    fi
  done
  : "$judged"
  return 0
}

# token_guard_boot_refusal <body> <token-path> <reason>: a SINGLETON boot refused its token. That is
# an OUTAGE (Nazim #49143), so: (1) print the reason on the pane, (2) page orch-console on the bus
# (P1, requires_response), (3) fire the UNGATED operator degrade-alert (nazim_send.sh: its own bot
# token, so it works even when the refused body IS the console; never silenced by lease state),
# (4) HOLD the pane so the reason stays readable, then return 1 so the caller exits. Paging is
# best-effort but never silent: if both pages fail, the pane says so in capitals.
# Overridable for tests: TOKEN_GUARD_BUS_CMD, TOKEN_GUARD_ALERT_CMD, TOKEN_GUARD_HOLD_S.
token_guard_boot_refusal() {
  local body="$1" path="$2" reason="$3" orch py bus alert paged=0 host
  orch="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  py="$orch/.venv/bin/python3"; [ -x "$py" ] || py="python3"
  host="$(hostname -s 2>/dev/null || hostname)"
  echo "" >&2
  echo "██ BOOT REFUSED: $body will NOT start on host $host. Its token file failed the name↔fp check:" >&2
  echo "██ $reason" >&2
  echo "██ Fix: update scripts/lib/token_fps.map (after a rotation) OR fix the mislabelled file; then re-run the boot." >&2
  bus="${TOKEN_GUARD_BUS_CMD:-}"
  if [ -n "$bus" ]; then
    printf '%s\n' "$body boot REFUSED on $host: token file '$path' failed the name<->fp check, so the body is DOWN (an outage, not a warning)." "" "$reason" "" "Fix: after a rotation, update scripts/lib/token_fps.map (the ONE place); otherwise the file is mislabelled. Then re-run the boot. (#49107/#49143)" \
      | "$bus" --to orch-console --type blocker --priority P1 --req --subject "BOOT REFUSED: $body is DOWN on $host (token file name/fp mismatch)" --from "$body" >/dev/null 2>&1 && paged=1
  else
    printf '%s\n' "$body boot REFUSED on $host: token file '$path' failed the name<->fp check, so the body is DOWN (an outage, not a warning)." "" "$reason" "" "Fix: after a rotation, update scripts/lib/token_fps.map (the ONE place); otherwise the file is mislabelled. Then re-run the boot. (#49107/#49143)" \
      | "$py" "$orch/scripts/bus_send.py" --to orch-console --type blocker --priority P1 --req --subject "BOOT REFUSED: $body is DOWN on $host (token file name/fp mismatch)" --from "$body" >/dev/null 2>&1 && paged=1
  fi
  alert="${TOKEN_GUARD_ALERT_CMD:-$orch/scripts/nazim_send.sh}"
  "$alert" "🚨 $body is DOWN on $host: its boot refused a mislabelled/unknown OAuth token file ($path). $reason" >/dev/null 2>&1 && paged=$((paged + 2))
  case "$paged" in
    3) echo "██ Paged orch-console (bus) + operator degrade-alert." >&2 ;;
    1) echo "██ Paged orch-console on the bus; the operator degrade-alert FAILED." >&2 ;;
    2) echo "██ Operator degrade-alert sent; the bus page FAILED." >&2 ;;
    *) echo "██ COULD NOT PAGE ANYONE (bus + degrade-alert both failed). THIS BODY IS DOWN UNNOTICED. Tell orch-console." >&2 ;;
  esac
  local hold="${TOKEN_GUARD_HOLD_S:-21600}"
  if [ "$hold" -gt 0 ] 2>/dev/null; then
    echo "██ Holding this pane ${hold}s so the reason stays visible (Ctrl-C to close)." >&2
    sleep "$hold"
  fi
  return 1
}
