#!/usr/bin/env bash
# render_pdf_remote.sh — headless xlsx/docx/etc -> PDF via LibreOffice on gzb.
#
# The Mini cannot convert headless from a tmux session (no WindowServer /
# Accessibility for LibreOffice.app / Numbers — the GUI launch never starts).
# This routes each file to gzb (Linux x86_64), which runs a *userland*
# LibreOffice 26.8 at ~gazzai/opt/soffice (installed without root; Arabic fonts
# in ~gazzai/.fonts). See ~gazzai/opt/lo_install.log on gzb.
#
# Usage:  scripts/render_pdf_remote.sh <file1> [file2 ...]
#
# For each input: scp -> a fresh gzb temp dir, `soffice --headless
# --convert-to pdf` under a 120s watchdog with a per-run isolated user profile,
# copy the PDF back *beside the source*, then delete the remote temp copy.
#
# Discipline: fails LOUD. Any failure (unreachable host, missing converter,
# scp error, timeout, empty/absent output) aborts non-zero and leaves every
# source file untouched. The remote temp dir is always removed on exit.
set -euo pipefail

GZB="${RENDER_PDF_HOST:-gzb}"
REMOTE_SOFFICE='$HOME/opt/soffice'   # expanded on gzb, not locally
WATCHDOG="${RENDER_PDF_TIMEOUT:-120}"
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)

die(){ echo "render_pdf_remote: FATAL: $*" >&2; exit 1; }

[ "$#" -ge 1 ] || die "no input files given (usage: render_pdf_remote.sh <file...>)"

# Validate all inputs up front (fail before touching the remote host).
for f in "$@"; do
  [ -f "$f" ] || die "input not found: $f"
done

# Verify the remote converter exists ONCE, up front — not mid-batch.
ssh "${SSH_OPTS[@]}" "$GZB" "test -x $REMOTE_SOFFICE" \
  || die "gzb converter not found/executable at $REMOTE_SOFFICE (userland LibreOffice missing?)"

REMOTE_TMP="$(ssh "${SSH_OPTS[@]}" "$GZB" 'mktemp -d /tmp/renderpdf.XXXXXX')" \
  || die "could not create remote temp dir on $GZB"
[ -n "$REMOTE_TMP" ] || die "empty remote temp dir path"

# Dead-man cleanup: always remove the remote temp copy, on success OR failure.
cleanup(){
  ssh "${SSH_OPTS[@]}" "$GZB" "rm -rf -- '$REMOTE_TMP'" 2>/dev/null \
    || echo "render_pdf_remote: WARN: could not remove remote temp $GZB:$REMOTE_TMP" >&2
}
trap cleanup EXIT

fail=0
for f in "$@"; do
  base="$(basename -- "$f")"
  stem="${base%.*}"
  srcdir="$(dirname -- "$f")"
  echo "== $f =="

  scp "${SSH_OPTS[@]}" -q -- "$f" "$GZB:$REMOTE_TMP/$base" \
    || die "scp failed for: $f"

  # Convert with watchdog + per-run isolated profile (avoids a shared-profile
  # lock hang). timeout -k also SIGKILLs a soffice that ignores SIGTERM.
  if ! ssh "${SSH_OPTS[@]}" "$GZB" \
      "timeout -k 10 ${WATCHDOG}s \$HOME/opt/soffice --headless \
        -env:UserInstallation=file://'$REMOTE_TMP/profile' \
        --convert-to pdf --outdir '$REMOTE_TMP' '$REMOTE_TMP/$base'"; then
    die "conversion failed or exceeded ${WATCHDOG}s watchdog for: $f"
  fi

  # Confirm the PDF exists and is non-empty on the remote before copy-back.
  ssh "${SSH_OPTS[@]}" "$GZB" "test -s '$REMOTE_TMP/$stem.pdf'" \
    || die "no (or empty) PDF produced on gzb for: $f"

  out="$srcdir/$stem.pdf"
  scp "${SSH_OPTS[@]}" -q -- "$GZB:$REMOTE_TMP/$stem.pdf" "$out" \
    || die "copy-back failed for: $stem.pdf"
  [ -s "$out" ] || die "copied-back PDF is empty: $out"

  echo "   -> $out ($(du -h -- "$out" | cut -f1))"
done

echo "render_pdf_remote: OK — $# file(s) converted."
