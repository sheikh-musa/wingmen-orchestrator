#!/usr/bin/env bash
# Sort logs/tg_media screenshots/assets into per-project subfolders by filename prefix.
# Idempotent + re-runnable: run it any time to tidy newly-arrived (flat) screenshots.
# New screenshots from tg_send/lanes land flat in tg_media/; this files them by project.
set -euo pipefail
MEDIA="$(cd "$(dirname "$0")/.." && pwd)/logs/tg_media"
cd "$MEDIA"

# folder <- glob patterns (case-insensitive prefix match). Order matters (first match wins).
declare -a RULES=(
  "irsyad|irsyad-* irsyad_* frs_* tabung_*"
  "cosem-adcda|adcda_* adcda-* Employment_Contracts__ADCDA*"
  "fastrans|fastrans* Fastrans*"
  "shipforge|shipforge_* woo_* woocommerce_* jomsembang_* modestyhut*"
  "storefront|merchant_* dookana_* m_products_* customer_per_line_note*"
  "hr|HR_* hr_*"
  "_operator-inbound|photos_file_*"
  "_personal|Musa_* Sheikh?Musa* Sheikh_Musa*"
)

shopt -s nullglob nocaseglob
moved=0
for rule in "${RULES[@]}"; do
  dest="${rule%%|*}"; pats="${rule#*|}"
  mkdir -p "$dest"
  for pat in $pats; do
    # find -maxdepth 1 handles spaces in filenames safely (unquoted globs word-split on them)
    while IFS= read -r -d '' f; do
      f="${f#./}"
      [ "$f" = "$dest" ] && continue
      if [ -d "$f" ] && [[ "$f" != *_assets ]]; then continue; fi
      mv -n -- "$f" "$dest/" && moved=$((moved+1)) || true
    done < <(find . -maxdepth 1 -iname "$pat" -print0 2>/dev/null)
  done
done
shopt -u nocaseglob

# Pre-existing cosem template/logo subfolders -> under cosem-adcda
for d in gtemplates logo_shots; do
  [ -d "$d" ] && { mkdir -p cosem-adcda; mv -n -- "$d" cosem-adcda/ 2>/dev/null || true; }
done

echo "moved $moved item(s). Current structure:"
for d in */; do
  n=$(find "$d" -type f | wc -l | tr -d ' ')
  echo "  $d ($n files)"
done
echo "--- still loose at root (uncategorized) ---"
find . -maxdepth 1 -type f | sed 's|^\./||' | sort || echo "(none)"
