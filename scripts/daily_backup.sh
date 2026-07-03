#!/bin/bash
# Daily Supabase backup — wingmen-ops substrate (tscuymavysscrvoberrr).
# Runs via launchd at 3 AM SGT (dev.wingmen.daily-backup). Keeps last 7 days.
#
# OPS-HEALTH-338 #4 overhaul (amanah-critical):
#   (A) DYNAMIC COVERAGE — backs up EVERY base table in the live `public`
#       schema (enumerated from the catalog), not a 25-entry hand-list. The old
#       hand-list silently dropped 22 governance tables (strategic_decisions,
#       agent_messages, operator_messages, repo_context, fleet_lanes, …).
#   (B) NO SILENT TRUNCATION — pg_dump/\copy have no PostgREST max_rows=1000 cap.
#       Each table's backed-up row count is asserted == live SELECT count(*).
#       ANY mismatch fails LOUD (non-zero exit + Telegram alert + names in log).
#       (Old bug: ayat/tafsir_entries/asbab_nuzul each saved exactly 1000 rows.)
#
# Approach: pg_dump (-Fc, authoritative restorable artifact) + per-table NDJSON
# via psql \copy (row_to_json) for human-readable, line-countable verification.
# Falls back to paginated REST only if pg_dump/psql/DSN are unavailable.
#
# OUT OF SCOPE: off-site/GCS/PITR (gated on a Wingmen-owned bucket).

set -euo pipefail

BACKUP_DIR="$HOME/wingmen/backups"
DATE=$(date +%Y-%m-%d)
TODAY_DIR="$BACKUP_DIR/$DATE"
ENV_FILE="$HOME/wingmen/orchestrator/.env"
SUPABASE_URL="https://tscuymavysscrvoberrr.supabase.co"

# shellcheck disable=SC1090
source "$ENV_FILE"
KEY="$SUPABASE_SERVICE_KEY"
DSN="${SUPABASE_DB_URL:-${DATABASE_URL:-}}"
# Alert via the operator bot (@wingmennorchbot), same as scripts/tg_send.sh.
# `|| true` keeps these robust under `set -e` if a line is absent.
BOT_TOKEN="$(grep '^WINGMEN_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2- || true)"
CHAT_ID="$(grep '^MUSA_TELEGRAM_ID=' "$ENV_FILE" | cut -d= -f2- || true)"

mkdir -p "$TODAY_DIR"

# Resolve postgres client binaries (launchd's PATH may omit /usr/local/bin).
PG_DUMP="$(command -v pg_dump || echo /usr/local/bin/pg_dump)"
PSQL="$(command -v psql || echo /usr/local/bin/psql)"

# NOTE on statement_timeout: Supabase's pooler enforces a default that a large
# table's JSON \copy (e.g. hadith_embeddings = 36k embedding vectors, ~475MB
# JSON) can exceed → the \copy fails spuriously. The pooler ignores PGOPTIONS /
# connection-startup `options`, but DOES honour a session-level `SET`. So each
# per-table dump runs `SET statement_timeout=0;` then the \copy in the SAME psql
# session (fed via stdin). pg_dump's COPY isn't affected by this.

alert() {
  # $1 = message; never blocks the run.
  [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ] || return 0
  curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
    -d "chat_id=$CHAT_ID" --data-urlencode "text=$1" > /dev/null || true
}

echo "=== Supabase Backup — $DATE ==="

FAILED=0
BACKED=0
FAIL_NAMES=""

# ---------------------------------------------------------------------------
# Primary path: pg_dump + per-table NDJSON with completeness assertions.
# ---------------------------------------------------------------------------
db_backup() {
  echo "Mode: pg_dump + per-table NDJSON (psql \\copy)"

  # (A) Authoritative full public-schema dump (schema + data, no row cap).
  echo -n "  [full] pg_dump -Fc public schema... "
  if "$PG_DUMP" "$DSN" --schema=public --no-owner --no-privileges \
        -Fc -f "$TODAY_DIR/_full_public.dump" 2>"$TODAY_DIR/_pg_dump.err"; then
    SIZE=$(wc -c < "$TODAY_DIR/_full_public.dump" | tr -d ' ')
    rm -f "$TODAY_DIR/_pg_dump.err"
    echo "✓ ($SIZE bytes)"
  else
    echo "✗ pg_dump FAILED"
    cat "$TODAY_DIR/_pg_dump.err" || true
    FAILED=$((FAILED + 1))
    FAIL_NAMES="$FAIL_NAMES _full_public.dump(pg_dump)"
  fi

  # (B) Enumerate ALL base tables in the live public schema, then back up each
  #     to NDJSON and assert line count == live count(*).
  # bash 3.2 (macOS /bin/bash) has no `mapfile` — read into an array via while.
  TABLES=()
  while IFS= read -r t; do [ -n "$t" ] && TABLES+=("$t"); done < <("$PSQL" "$DSN" -tAc \
    "SELECT table_name FROM information_schema.tables
       WHERE table_schema='public' AND table_type='BASE TABLE'
       ORDER BY table_name;")

  echo "Discovered ${#TABLES[@]} base tables in public schema."

  for TABLE in "${TABLES[@]}"; do
    [ -z "$TABLE" ] && continue
    echo -n "  $TABLE... "

    # Live authoritative count.
    LIVE=$("$PSQL" "$DSN" -tAc "SELECT count(*) FROM \"public\".\"$TABLE\";" 2>/dev/null || echo "ERR")
    if [ "$LIVE" = "ERR" ]; then
      echo "✗ count(*) failed"
      FAILED=$((FAILED + 1)); FAIL_NAMES="$FAIL_NAMES $TABLE(count)"
      continue
    fi

    # Dump every row as newline-delimited JSON (no max_rows cap). Lift the
    # statement timeout in-session (see note above) by feeding SET + \copy to
    # one psql via stdin.
    OUT="$TODAY_DIR/$TABLE.ndjson"
    if ! printf '%s\n' \
          "SET statement_timeout=0;" \
          "\\copy (SELECT row_to_json(t) FROM \"public\".\"$TABLE\" t) TO '$OUT'" \
        | "$PSQL" "$DSN" -tA > /dev/null 2>"$TODAY_DIR/$TABLE.err"; then
      echo "✗ \\copy failed"
      cat "$TODAY_DIR/$TABLE.err" || true
      FAILED=$((FAILED + 1)); FAIL_NAMES="$FAIL_NAMES $TABLE(copy)"
      continue
    fi
    rm -f "$TODAY_DIR/$TABLE.err"

    # Completeness assertion: backed-up rows must equal live count.
    GOT=$(wc -l < "$OUT" | tr -d ' ')
    if [ "$GOT" != "$LIVE" ]; then
      echo "✗ TRUNCATION: backed up $GOT but live has $LIVE"
      FAILED=$((FAILED + 1)); FAIL_NAMES="$FAIL_NAMES $TABLE($GOT/$LIVE)"
      continue
    fi

    gzip -f "$OUT"
    echo "✓ $GOT rows (matches live)"
    BACKED=$((BACKED + 1))
  done
}

# ---------------------------------------------------------------------------
# Fallback path: paginated REST (only if no DB client / DSN). Still asserts
# completeness against a count(*)-equivalent via Content-Range, fails loud.
# ---------------------------------------------------------------------------
rest_backup() {
  echo "Mode: paginated REST fallback (pg_dump/psql/DSN unavailable)"
  alert "⚠️ Daily backup: pg_dump/psql/DSN unavailable — using paginated-REST fallback (no full dump artifact)."

  # Enumerate tables via PostgREST OpenAPI root (definitions = tables/views).
  TABLES=()
  while IFS= read -r t; do [ -n "$t" ] && TABLES+=("$t"); done < <(curl -s "$SUPABASE_URL/rest/v1/" \
      -H "apikey: $KEY" -H "Authorization: Bearer $KEY" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print("\n".join(sorted(d.get("definitions",{}).keys())))')

  echo "Discovered ${#TABLES[@]} REST resources."
  local PAGE=1000

  for TABLE in "${TABLES[@]}"; do
    [ -z "$TABLE" ] && continue
    echo -n "  $TABLE... "
    local OFFSET=0 TOTAL="" PART_DIR HDRS HTTP RANGE GOT=0 OK=1
    PART_DIR=$(mktemp -d)
    while :; do
      HDRS=$(mktemp)
      HTTP=$(curl -s -o "$PART_DIR/p_$OFFSET.json" -D "$HDRS" -w "%{http_code}" \
        "$SUPABASE_URL/rest/v1/$TABLE?select=*" \
        -H "apikey: $KEY" -H "Authorization: Bearer $KEY" \
        -H "Range-Unit: items" -H "Range: $OFFSET-$((OFFSET + PAGE - 1))" \
        -H "Prefer: count=exact" 2>/dev/null)
      if [ "$HTTP" != "200" ] && [ "$HTTP" != "206" ]; then
        echo "✗ HTTP $HTTP"; OK=0; rm -f "$HDRS"; break
      fi
      # Content-Range: items 0-999/24944  → total after the slash.
      RANGE=$(grep -i '^content-range:' "$HDRS" | tr -d '\r' | awk -F/ '{print $2}')
      [ -n "$RANGE" ] && TOTAL="$RANGE"
      rm -f "$HDRS"
      local N; N=$(python3 -c "import json;print(len(json.load(open('$PART_DIR/p_$OFFSET.json'))))" 2>/dev/null || echo 0)
      GOT=$((GOT + N))
      [ "$N" -lt "$PAGE" ] && break
      OFFSET=$((OFFSET + PAGE))
    done
    if [ "$OK" = 1 ]; then
      # Merge pages into one JSON array.
      python3 - "$PART_DIR" "$TODAY_DIR/$TABLE.json" <<'PY'
import json, sys, glob, os
part_dir, out = sys.argv[1], sys.argv[2]
rows = []
for f in sorted(glob.glob(os.path.join(part_dir, "p_*.json")),
                key=lambda p: int(p.split("p_")[-1].split(".")[0])):
    rows.extend(json.load(open(f)))
json.dump(rows, open(out, "w"))
PY
      gzip -f "$TODAY_DIR/$TABLE.json"
      if [ -n "$TOTAL" ] && [ "$GOT" != "$TOTAL" ]; then
        echo "✗ TRUNCATION: backed up $GOT but live has $TOTAL"
        FAILED=$((FAILED + 1)); FAIL_NAMES="$FAIL_NAMES $TABLE($GOT/$TOTAL)"
      else
        echo "✓ $GOT rows (REST, paginated to completion)"
        BACKED=$((BACKED + 1))
      fi
    else
      FAILED=$((FAILED + 1)); FAIL_NAMES="$FAIL_NAMES $TABLE(http$HTTP)"
    fi
    rm -rf "$PART_DIR"
  done
}

if [ -n "$DSN" ] && [ -x "$PG_DUMP" ] && [ -x "$PSQL" ]; then
  db_backup
else
  rest_backup
fi

# Cleanup old backups (keep 7 days)
find "$BACKUP_DIR" -maxdepth 1 -type d -mtime +7 -exec rm -rf {} \;

echo ""
echo "=== Done: $BACKED tables backed up, $FAILED failed ==="
echo "Location: $TODAY_DIR"

# Fail LOUD on any failure/truncation: alert + non-zero exit.
if [ "$FAILED" -gt 0 ]; then
  echo "FAILURES:$FAIL_NAMES"
  alert "⚠️ Daily backup: $FAILED failure(s) — possible truncation/missing tables.$FAIL_NAMES — check $TODAY_DIR"
  exit 1
fi
