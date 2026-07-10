#!/usr/bin/env bash
# publish_share.sh — publish a self-contained artifact HTML to share.wingmen.dev.
#
# Usage: publish_share.sh <html-file> <slug> "<title>" ["<tag>"]
#
# Wraps the artifact (which is a title+style+body fragment, as produced by the
# Artifact tool) into a standalone page, updates the index manifest, and deploys.
# Every doc is password-gated by the wingmen-share Basic Auth middleware.
set -euo pipefail

SRC="${1:?usage: publish_share.sh <html-file> <slug> \"<title>\" [\"<tag>\"]}"
SLUG="${2:?slug required (URL path, e.g. sushitei)}"
TITLE="${3:?title required}"
TAG="${4:-}"

SHARE_DIR="$HOME/wingmen/wingmen-share"
DOCS_DIR="$SHARE_DIR/public/r"
MANIFEST="$SHARE_DIR/app/docs.json"
SCOPE="wingmen-aa9356e1"

[ -f "$SRC" ] || { echo "no such file: $SRC" >&2; exit 1; }
mkdir -p "$DOCS_DIR"

# Wrap the fragment into a valid standalone document.
{
  printf '%s\n' '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow"></head><body>'
  cat "$SRC"
  printf '\n%s\n' '</body></html>'
} > "$DOCS_DIR/$SLUG.html"
echo "wrote $DOCS_DIR/$SLUG.html"

# Upsert into the index manifest (newest first, deduped by slug).
python3 - "$MANIFEST" "$SLUG" "$TITLE" "$TAG" <<'PY'
import json, sys, datetime
path, slug, title, tag = sys.argv[1:5]
try:
    docs = json.load(open(path))
except Exception:
    docs = []
docs = [d for d in docs if d.get("slug") != slug]
docs.insert(0, {"slug": slug, "title": title, "tag": tag, "date": datetime.date.today().isoformat()})
json.dump(docs, open(path, "w"), indent=2)
open(path, "a").write("\n")
print("manifest updated:", slug)
PY

cd "$SHARE_DIR"
echo "deploying to production…"
vercel deploy --prod --scope "$SCOPE" --yes >/dev/null
echo "→ https://share.wingmen.dev/r/$SLUG"