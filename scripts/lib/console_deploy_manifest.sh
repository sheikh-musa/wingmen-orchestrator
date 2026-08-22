# console_deploy_manifest.sh — SSOT for WHICH files the deploy_console gate hashes.
#
# Sourced by scripts/deploy_console.sh (and by tests/test_deploy_console_gate.py). It is the
# single place that answers "what content does a console deploy review cover?". Keeping it in one
# sourced seam is deliberate: the review-gate's content-hash and its test must agree by
# construction, not by two copies that can drift.
#
# WHY (item-4b, Nazim #31843/#31865). The gate's review is content-hash-keyed so a stale review of
# a different diff cannot ship. The hash used to cover ONLY five static frontend files. But the
# console is run as `python -m nervous_system.console` (the whole backend package) AND app.py
# serves every file under static/ by route (/, /irsyad, /docs, /media, /static/<file>). So a change
# to the backend OR to any of the ~12 un-gated served static files (app.js SPA logic, index/irsyad/
# docs/media pages, manifest.json, icons/*) shipped to users UNREVIEWED. This seam gates ALL of it.
#
# COVERAGE BOUNDARY (documented, per Nazim #31843 — a KNOWN written boundary is fine; a SILENT one
# is the thing we are killing. cc-quality #31859 caught an earlier cut of this doc overstating
# coverage; this is the honest, now-widened version — Nazim #31865 chose Option 1: gate it all).
#
# WHAT THE HASH COVERS (everything the console ships to users, + its deploy artifact):
#   * BACKEND  — every *.py under nervous_system/console/ (recursive). Complete.
#   * STATIC   — EVERY file under nervous_system/console/static/ (recursive): all html/js/json/css
#                and binary assets (icons/*). Complete — no per-file carve-out (a carve-out would
#                re-introduce exactly the silent-cut blindspot this closes).
#   * PROVENANCE — nervous_system/console/Dockerfile (defines the VPS-portable container artifact).
#
# WHAT IT DOES NOT COVER (the one written cut — NOT silent):
#   * OUTSIDE-PACKAGE IMPORTS — shared libs the console imports from elsewhere (as of item-4b the
#     only one is `scripts.lib.lane_token_resolver`, a fleet-wide token util, not console-serving
#     logic). Left out deliberately (Nazim #31857 confirmed) to avoid over-triggering re-review on
#     unrelated token changes. Widen only for a console-serving shared module, and escalate first.

# console_deploy_files_rel [root] — print the gate's file set as REPO-RELATIVE paths, globally
# sorted (deterministic order feeds the content hash). Covers ALL served static + every backend
# *.py + the Dockerfile; __pycache__ excluded. Relative paths keep the hash portable across
# checkouts/temp dirs (the hash must depend on CONTENT, not on where the tree lives).
console_deploy_files_rel() {
  local root="${1:-$PWD}"
  ( cd "$root" && {
      # served static — EVERY asset app.py can serve by route, recursive (Nazim #31865 opt-1)
      find nervous_system/console/static -type f -not -path '*/__pycache__/*'
      # backend — every *.py under the console package, recursive
      find nervous_system/console -type f -name '*.py' -not -path '*/__pycache__/*'
      # deploy provenance — the Dockerfile that defines the deployed artifact. Guarded with an
      # `if` (NOT `[ -f ] &&`, which would exit non-zero and, under pipefail, fail the whole
      # function on a checkout that lacks the Dockerfile).
      if [ -f nervous_system/console/Dockerfile ]; then
        printf '%s\n' nervous_system/console/Dockerfile
      fi
    } | LC_ALL=C sort )
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
