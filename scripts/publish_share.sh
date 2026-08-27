#!/usr/bin/env bash
# publish_share.sh — publish a self-contained artifact HTML to share.wingmen.dev.
#
# Usage: publish_share.sh <html-file> <slug|auto> "<title>" [tag] [tenant]
#
#   <slug|auto>  Explicit URL slug (e.g. sushitei) to PIN the path — use when
#                re-publishing an existing page. Pass `auto` (or `-`) for a NEW
#                client-facing page: a high-entropy slug `<tenant>-<10 hex>` is
#                generated so the URL itself is unguessable (defence-in-depth on
#                top of the per-tenant Basic Auth gate).
#   [tag]        Display tag shown on the index (cosmetic).
#   [tenant]     REQUIRED for client-facing pages. Recorded in the manifest so
#                the middleware maps this slug -> tenant and gates it behind that
#                tenant's own credential. Omitting it leaves the page unmapped:
#                once the shared-pw bridge is off, an unmapped page is DENIED
#                (fail-closed). Use `_admin` for operator-only internal pages.
#
# Wraps the artifact (a title+style+body fragment, as produced by the Artifact
# tool) into a standalone page, updates the index manifest, and deploys. Every
# doc is password-gated by the wingmen-share per-tenant Basic Auth middleware.
set -euo pipefail

SRC="${1:?usage: publish_share.sh <html-file> <slug|auto> \"<title>\" [tag] [tenant]}"
SLUG="${2:?slug required (URL path, e.g. sushitei) or 'auto' for a random slug}"
TITLE="${3:?title required}"
TAG="${4:-}"
TENANT="${5:-}"

SHARE_DIR="$HOME/wingmen/wingmen-share"
DOCS_DIR="$SHARE_DIR/public/r"
MANIFEST="$SHARE_DIR/app/docs.json"
SCOPE="wingmen-aa9356e1"

[ -f "$SRC" ] || { echo "no such file: $SRC" >&2; exit 1; }
mkdir -p "$DOCS_DIR"

# 10 hex chars of entropy for auto slugs (openssl, /dev/urandom fallback).
rand_hex10() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 5
  else
    LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 10
  fi
}

# Default NEW pages to an unguessable slug unless an explicit one was given.
if [ "$SLUG" = "auto" ] || [ "$SLUG" = "-" ]; then
  base="${TENANT:-doc}"
  # sanitise the base to slug-safe chars
  base="$(printf '%s' "$base" | LC_ALL=C tr '[:upper:]' '[:lower:]' | LC_ALL=C sed 's/[^a-z0-9]/-/g; s/-\{2,\}/-/g; s/^-//; s/-$//')"
  [ -n "$base" ] || base="doc"
  SLUG="${base}-$(rand_hex10)"
  echo "generated slug: $SLUG"
fi

if [ -z "$TENANT" ]; then
  echo "warn: no tenant given — page will be UNMAPPED (denied once the shared-pw" >&2
  echo "      bridge is disabled). Pass a tenant (5th arg), e.g. irsyad / _admin." >&2
fi

# Wrap the fragment into a valid standalone document.
{
  printf '%s\n' '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow"></head><body>'
  cat "$SRC"
  printf '\n%s\n' '</body></html>'
} > "$DOCS_DIR/$SLUG.html"
echo "wrote $DOCS_DIR/$SLUG.html"

# Upsert into the index manifest (newest first, deduped by slug). The `tenant`
# field is the security-critical slug->tenant binding the middleware reads.
python3 - "$MANIFEST" "$SLUG" "$TITLE" "$TAG" "$TENANT" <<'PY'
import json, sys, datetime
path, slug, title, tag, tenant = sys.argv[1:6]
try:
    docs = json.load(open(path))
except Exception:
    docs = []
docs = [d for d in docs if d.get("slug") != slug]
entry = {"slug": slug, "title": title, "tag": tag, "date": datetime.date.today().isoformat()}
if tenant:
    entry["tenant"] = tenant
docs.insert(0, entry)
json.dump(docs, open(path, "w"), indent=2)
open(path, "a").write("\n")
print("manifest updated:", slug, "(tenant:", tenant or "UNMAPPED", ")")
PY

cd "$SHARE_DIR"
echo "deploying to production…"
DEP=$(vercel deploy --prod --scope "$SCOPE" --yes 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.vercel\.app' | tail -1)
# The custom domain is a per-deployment alias (not an auto-following project prod
# domain), so re-point it at each new deployment or new docs 404.
if [ -n "$DEP" ]; then
  vercel alias set "$DEP" share.wingmen.dev --scope "$SCOPE" >/dev/null 2>&1 \
    || echo "warn: 'vercel alias set $DEP share.wingmen.dev' failed — re-alias manually" >&2
else
  echo "warn: could not capture deployment URL — verify share.wingmen.dev points at the new deploy" >&2
fi
echo "→ https://share.wingmen.dev/r/$SLUG"
