# console_deploy_manifest.sh — SSOT for WHICH files the deploy_console gate hashes.
#
# Sourced by scripts/deploy_console.sh (and by tests/test_deploy_console_gate.py). It is the
# single place that answers "what content does a console deploy review cover?". Keeping it in one
# sourced seam is deliberate: the review-gate's content-hash and its test must agree by
# construction, not by two copies that can drift.
#
# WHY (item-4b, Nazim #31843). The gate's review is content-hash-keyed so a stale review of a
# different diff cannot ship. The hash used to cover ONLY the five static frontend files, but the
# console is run as `python -m nervous_system.console` — the whole backend package. So a
# backend-only change left the hash unchanged and the backend shipped UNREVIEWED. This seam folds
# every *.py under the console package into the same hash so any backend change forces a fresh
# cc-quality review.
#
# COVERAGE BOUNDARY (documented, per Nazim #31843 — a KNOWN written boundary is fine; a SILENT one
# is the thing we are killing. cc-quality #31859 caught the first cut of this doc overstating
# coverage; this is the honest version).
#
# WHAT THE HASH COVERS:
#   * BACKEND — every *.py under nervous_system/console/ (recursive, sorted). Complete.
#   * STATIC  — ONLY these 5 files: fleet.html, fleet.js, lanes.html, lanes.js, sw.js.
#
# WHAT IT DOES NOT COVER (the written cuts — NOT silent):
#   * OTHER SERVED STATIC — app.py serves ~12 more static files by route (/, /irsyad, /docs,
#     /media, and /static/<file> for ANY asset): index.html, irsyad.html, irsyad.js, docs.html,
#     docs.js, media.html, media.js, app.js (the SPA logic), manifest.json, icons/*. A change to
#     any of these currently ships WITHOUT a fresh review — the same class item-4b closed for the
#     backend. Whether to gate them (and which — html/js certainly; icons/manifest are judgment)
#     is a WIDEN decision for Nazim (console owner); cc-quality #31859 flagged it, does not
#     adjudicate. Do NOT widen the static set here unilaterally — escalate, then build on his OK.
#   * OUTSIDE-PACKAGE IMPORTS — shared libs the console imports from elsewhere (as of item-4b the
#     only one is `scripts.lib.lane_token_resolver`, a fleet-wide token util, not console-serving
#     logic). Left out deliberately (Nazim #31857 confirmed) to avoid over-triggering re-review on
#     unrelated token changes. Widen only for a console-serving shared module, and escalate.
#   * Dockerfile — deploy-provenance, not served content; conscious exclusion pending Nazim.

# console_deploy_files_rel [root] — print the gate's file set as REPO-RELATIVE paths, in a
# deterministic order (static frontend in fixed order first, then every backend *.py under the
# console package, RECURSIVE + sorted, __pycache__ excluded). Relative paths keep the hash
# portable across checkouts/temp dirs (the hash must depend on CONTENT, not on where the tree
# lives). Order is fixed so the hash is stable run-to-run.
console_deploy_files_rel() {
  local root="${1:-$PWD}"
  # static frontend — fixed order (order feeds the content hash)
  printf '%s\n' \
    nervous_system/console/static/fleet.html \
    nervous_system/console/static/fleet.js \
    nervous_system/console/static/lanes.html \
    nervous_system/console/static/lanes.js \
    nervous_system/console/static/sw.js
  # backend — every *.py under the console package, recursive + sorted, no __pycache__
  ( cd "$root" && find nervous_system/console -type f -name '*.py' \
      -not -path '*/__pycache__/*' | LC_ALL=C sort )
}

# console_content_hash [root] — 16-char sha256 of the gate's content. The digest is taken over
# the RELATIVE MANIFEST (so adding/removing/renaming a gated file moves the hash even if total
# bytes are unchanged) followed by each file's CONTENT in manifest order (so any edit moves it).
# Each file's content is PREFIXED by a NUL-framed marker carrying its rel path — without a per-file
# boundary, back-to-back cat lets content move across an adjacent-file seam (top of one module ->
# bottom of the alphabetically-preceding one) with the manifest AND the byte stream unchanged, a
# hash collision that misses a real change (cc-quality #31859 LOW). The marker fixes each file's
# bytes to its own slot, so any cross-file move re-frames and moves the hash.
# This is the dead-man's-switch: a stale review keyed to the old hash cannot match a changed tree.
console_content_hash() {
  local root="${1:-$PWD}"
  {
    console_deploy_files_rel "$root"
    printf '\0--console-content--\0'
    local rel
    console_deploy_files_rel "$root" | while IFS= read -r rel; do
      printf '\0FILE\0%s\0' "$rel"   # per-file boundary: frames each file's bytes to its own slot
      cat "$root/$rel"
    done
  } | shasum -a 256 | cut -c1-16
}
