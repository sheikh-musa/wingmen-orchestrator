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
