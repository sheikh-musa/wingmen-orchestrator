# scripts/lib/tg_safe_upload.sh — a curl -F upload value safe against real filenames
# (bus #44576). Sourced, not executed.
#
# curl's own `-F field=@path` parser reads a literal ',' in `path` as the
# multi-file separator (`@file1,file2`) and a ';' as the start of a
# `;type=`/`;filename=` modifier — neither is escapable inside the @-path
# itself. A real deliverable filename containing either (seen live:
# "Batch 20_21 Overview (op22917 fix, DRAFT v1.0).xlsx") makes curl try to
# open a bogus second "file" and exit 26 (READ_ERROR) with no informative
# output — and under `set -euo pipefail`, that non-zero exit aborts the
# caller's script on the `code=$(curl ...)` / `resp=$(curl ...)` line, BEFORE
# the durable "--undelivered" log write ever runs. The caller sees nothing.
#
# Fix, part 1 (this file): never point `-F field=@...` at the real path.
# Symlink it under a fresh mktemp dir with a generated, syntax-safe basename,
# upload THAT, and restore the real name for the recipient via an explicit
# `;filename="..."` modifier (curl's own escape for that value is `\"`; a
# stray `;`/`,` inside a QUOTED filename= value does not re-trigger the outer
# -F parser — only an unescaped `"` does).
#
# Fix, part 2 (each caller): `code=$(curl ...) || code="curl_exit_$?"` (or the
# `resp=` equivalent), so a curl failure is captured as data instead of
# aborting the script under `set -e`.
#
# NOTE: tg_safe_upload_stage sets globals in the CALLING shell — it must be
# invoked as a plain function call, NOT `x=$(tg_safe_upload_stage ...)`. A
# command substitution forks a subshell, so any variable it sets (including
# the tmpdir path needed for cleanup) would never reach the caller and the
# staged tmpdir would leak on every call.
#
# Usage:
#   source "$ORCH_DIR/scripts/lib/tg_safe_upload.sh"
#   tg_safe_upload_stage document "$REAL_PATH" || exit 1
#   trap '[ -n "${TG_SAFE_UPLOAD_TMPDIR:-}" ] && rm -rf "$TG_SAFE_UPLOAD_TMPDIR"' EXIT
#   curl ... -F "$TG_SAFE_UPLOAD_FORM" ...

tg_safe_upload_stage() {
  local field="$1" src="$2"
  [ -f "$src" ] || { echo "tg_safe_upload_stage: not a file: $src" >&2; return 1; }

  local dir
  dir=$(mktemp -d) || return 1

  local base ext=""
  base=$(basename -- "$src")
  case "$base" in *.*) ext=".${base##*.}" ;; esac
  local safe="$dir/upload${ext}"
  local abs_dir
  abs_dir=$(cd "$(dirname -- "$src")" && pwd) || return 1
  if ! ln -s "$abs_dir/$base" "$safe" 2>/dev/null; then
    cp -- "$src" "$safe" || return 1
  fi

  local esc="${base//\\/\\\\}"
  esc="${esc//\"/\\\"}"

  # Globals for the caller — see NOTE above on why these are not returned via stdout.
  TG_SAFE_UPLOAD_TMPDIR="$dir"
  TG_SAFE_UPLOAD_FORM=$(printf '%s=@%s;filename="%s"' "$field" "$safe" "$esc")
}
