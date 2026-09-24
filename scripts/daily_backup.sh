#!/bin/bash
# Daily Supabase backup — orchestrator substrate (tscuymavysscrvoberrr) PLUS
# client silos ihsanos-ceayj (IHSANOS_PROD_RO_DATABASE_URL) and irsyad-goumlyne
# (GOUMLYNE_RO_DATABASE_URL). READ-ONLY DSNs only for client silos — a backup
# is read-only by nature and must never even have the capability to write; the
# write-capable DSN vars must never be referenced here. Substrate files land at
# the top level of the day's dir (layout preserved); each client silo dumps
# into its own subdir. Same dump + row-count-assertion logic for every store
# (backup_one). A missing/unusable client-silo DSN is a FAILURE (non-zero exit
# + loud alert), not a silent skip — op#42886 (2026-09-24): a stale/absent RO
# var silently skipped both client silos for 6 of the last 7 days with exit 0,
# undetected until an unrelated host decommission surfaced it. REST fallback is
# substrate-only (client silos have no REST fallback path).
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
# Reusable per-store: backup_one STORE_NAME DSN OUTDIR
#   - STORE_NAME  labels log lines and failure names (e.g. "irsyad-goumlyne").
#   - DSN         the postgres connection string for that store.
#   - OUTDIR      where this store's _full_public.dump + per-table NDJSON land.
# Accumulates into the GLOBAL FAILED/BACKED/FAIL_NAMES so the final fail-LOUD
# gate aggregates across ALL stores. Failure names are STORE-scoped, e.g.
# "irsyad-goumlyne/donations(1200/1201)". Also prints a per-store tally.
# ---------------------------------------------------------------------------
backup_one() {
  local STORE="$1" DSN_L="$2" OUTDIR="$3"
  local ST_BACKED=0 ST_FAILED=0
  local TABLE LIVE OUT GOT SIZE
  local TABLES=()

  mkdir -p "$OUTDIR"
  echo "--- [$STORE] pg_dump + per-table NDJSON (psql \\copy) → $OUTDIR"

  # (A) Authoritative full public-schema dump (schema + data, no row cap).
  echo -n "  [$STORE][full] pg_dump -Fc public schema... "
  if "$PG_DUMP" "$DSN_L" --schema=public --no-owner --no-privileges \
        -Fc -f "$OUTDIR/_full_public.dump" 2>"$OUTDIR/_pg_dump.err"; then
    SIZE=$(wc -c < "$OUTDIR/_full_public.dump" | tr -d ' ')
    rm -f "$OUTDIR/_pg_dump.err"
    echo "✓ ($SIZE bytes)"
  else
    echo "✗ pg_dump FAILED"
    cat "$OUTDIR/_pg_dump.err" || true
    FAILED=$((FAILED + 1)); ST_FAILED=$((ST_FAILED + 1))
    FAIL_NAMES="$FAIL_NAMES $STORE/_full_public.dump(pg_dump)"
  fi

  # (B) Enumerate ALL base tables in the live public schema, then back up each
  #     to NDJSON and assert line count == live count(*).
  # bash 3.2 (macOS /bin/bash) has no `mapfile` — read into an array via while.
  while IFS= read -r t; do [ -n "$t" ] && TABLES+=("$t"); done < <("$PSQL" "$DSN_L" -tAc \
    "SELECT table_name FROM information_schema.tables
       WHERE table_schema='public' AND table_type='BASE TABLE'
       ORDER BY table_name;")

  echo "  [$STORE] discovered ${#TABLES[@]} base tables in public schema."

  for TABLE in "${TABLES[@]}"; do
    [ -z "$TABLE" ] && continue
    echo -n "  [$STORE] $TABLE... "

    # SAME-SNAPSHOT count + dump (op#42888): the count and the \copy used to
    # run as two SEPARATE psql sessions, so a live write landing between them
    # on an actively-written table (e.g. substrate/pool_usage_history, an
    # append-only metrics table) made GOT (post-write) != LIVE (pre-write),
    # failing LOUD on a false positive every time a row happened to land in
    # that window. Fix: one psql session, one REPEATABLE READ transaction —
    # the count and the \copy see the IDENTICAL snapshot, so a concurrent
    # write literally cannot cause a mismatch. A real truncation/corruption
    # still fails loud (ON_ERROR_STOP aborts the whole session non-zero; a
    # genuine short dump still shows GOT != LIVE against that same snapshot).
    OUT="$OUTDIR/$TABLE.ndjson"
    TXN_OUT=$(mktemp)
    if ! printf '%s\n' \
          "BEGIN ISOLATION LEVEL REPEATABLE READ;" \
          "SET statement_timeout=0;" \
          "\\copy (SELECT row_to_json(t) FROM \"public\".\"$TABLE\" t) TO '$OUT'" \
          "SELECT count(*) FROM \"public\".\"$TABLE\";" \
          "COMMIT;" \
        | "$PSQL" "$DSN_L" -tAq -v ON_ERROR_STOP=1 > "$TXN_OUT" 2>"$OUTDIR/$TABLE.err"; then
      echo "✗ dump/count transaction failed"
      cat "$OUTDIR/$TABLE.err" || true
      FAILED=$((FAILED + 1)); ST_FAILED=$((ST_FAILED + 1)); FAIL_NAMES="$FAIL_NAMES $STORE/$TABLE(txn)"
      rm -f "$TXN_OUT"
      continue
    fi
    rm -f "$OUTDIR/$TABLE.err"

    # -tAq suppresses headers/footers and BEGIN/COPY/COMMIT command tags, so
    # stdout should hold exactly the count(*) result — take the last
    # non-empty line defensively in case any stray notice leaks through.
    LIVE=$(grep -v '^[[:space:]]*$' "$TXN_OUT" | tail -1)
    rm -f "$TXN_OUT"
    if [ -z "$LIVE" ] || ! [[ "$LIVE" =~ ^[0-9]+$ ]]; then
      echo "✗ count(*) unparseable (got: '$LIVE')"
      FAILED=$((FAILED + 1)); ST_FAILED=$((ST_FAILED + 1)); FAIL_NAMES="$FAIL_NAMES $STORE/$TABLE(count)"
      continue
    fi

    # Completeness assertion: backed-up rows must equal the SAME-SNAPSHOT count.
    GOT=$(wc -l < "$OUT" | tr -d ' ')
    if [ "$GOT" != "$LIVE" ]; then
      echo "✗ TRUNCATION: backed up $GOT but live (same snapshot) has $LIVE"
      FAILED=$((FAILED + 1)); ST_FAILED=$((ST_FAILED + 1)); FAIL_NAMES="$FAIL_NAMES $STORE/$TABLE($GOT/$LIVE)"
      continue
    fi

    gzip -f "$OUT"
    echo "✓ $GOT rows (matches live, same-snapshot)"
    BACKED=$((BACKED + 1)); ST_BACKED=$((ST_BACKED + 1))
  done

  echo "  [$STORE] backed up $ST_BACKED tables, $ST_FAILED failed."
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

# ---------------------------------------------------------------------------
# Substrate (tscuymavysscrvoberrr): pg_dump primary, REST fallback if the DB
# client / DSN is unavailable. Its files land DIRECTLY in $TODAY_DIR (top-level
# layout preserved — existing restore expectations must not break).
# ---------------------------------------------------------------------------
if [ -n "$DSN" ] && [ -x "$PG_DUMP" ] && [ -x "$PSQL" ]; then
  backup_one "substrate" "$DSN" "$TODAY_DIR"
else
  rest_backup
fi

# ---------------------------------------------------------------------------
# Client silos (TENANT-RESIDENCY / LAYER-VOCAB): pg_dump/DSN ONLY — NO REST
# fallback (the REST path is substrate-URL specific). Each silo dumps into its
# OWN subdir. READ-ONLY DSNs only (see header) — the write DSN is never
# referenced. A missing DSN or missing pg_dump/psql is a FAILURE (counted into
# the global FAILED/FAIL_NAMES gate below, which fires the loud alert + non-zero
# exit) — op#42886: this used to silently `return 0`, which is exactly how a
# week of missing-DSN skips went undetected.
#   name              DSN env var                     subdir
#   ihsanos-ceayj     IHSANOS_PROD_RO_DATABASE_URL    $TODAY_DIR/ihsanos-ceayj
#   irsyad-goumlyne   GOUMLYNE_RO_DATABASE_URL        $TODAY_DIR/irsyad-goumlyne
# ---------------------------------------------------------------------------
backup_client_silo() {
  local NAME="$1" SILO_DSN="$2"
  if [ -z "$SILO_DSN" ]; then
    echo "--- [$NAME] FAILED: DSN env var is empty/unset."
    FAILED=$((FAILED + 1)); FAIL_NAMES="$FAIL_NAMES $NAME(missing-dsn)"
    return 0
  fi
  if [ ! -x "$PG_DUMP" ] || [ ! -x "$PSQL" ]; then
    echo "--- [$NAME] FAILED: pg_dump/psql unavailable (no REST fallback for client silos)."
    FAILED=$((FAILED + 1)); FAIL_NAMES="$FAIL_NAMES $NAME(no-pgtools)"
    return 0
  fi
  backup_one "$NAME" "$SILO_DSN" "$TODAY_DIR/$NAME"
}

backup_client_silo "ihsanos-ceayj"   "${IHSANOS_PROD_RO_DATABASE_URL:-}"
backup_client_silo "irsyad-goumlyne" "${GOUMLYNE_RO_DATABASE_URL:-}"

# ---------------------------------------------------------------------------
# OFF-SITE PUSH (Musa op#21244 "dump it in gzbai"): mirror the CLIENT-silo
# backups to the Gazzabyte VPS (hub-vps = root@91.107.235.77) so an
# irreplaceable client-data copy survives a Mac Mini disk loss. CLIENT SILOS
# ONLY — the substrate's ~360MB dump over the home uplink (~37KB/s observed)
# would take hours nightly, and it is already covered by Supabase's own daily
# backup + the local dump. Best-effort with a LOUD alert on failure: the local
# dump above is the amanah guarantee, so a push failure must NOT fail the local
# backup, but MUST be surfaced (a stale off-site copy is a real risk).
# ---------------------------------------------------------------------------
GZB_KEY="$HOME/.ssh/wingmen_vps"
GZB_HOST="hub-vps"                 # root@91.107.235.77, from ~/.ssh/config
GZB_REMOTE_DIR="wingmen/backups"
if [ -f "$GZB_KEY" ]; then
  GZB_SSH="ssh -i $GZB_KEY -o ConnectTimeout=20 -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
  PUSH_FAIL=0
  $GZB_SSH "$GZB_HOST" "mkdir -p $GZB_REMOTE_DIR/$DATE" 2>/dev/null || true
  for SILO in ihsanos-ceayj irsyad-goumlyne; do
    [ -d "$TODAY_DIR/$SILO" ] || continue
    echo -n "  [off-site] rsync $SILO → $GZB_HOST... "
    if rsync -az --delete -e "$GZB_SSH" \
          "$TODAY_DIR/$SILO" "$GZB_HOST:$GZB_REMOTE_DIR/$DATE/" 2>"$TODAY_DIR/_gzb_$SILO.err"; then
      rm -f "$TODAY_DIR/_gzb_$SILO.err"; echo "✓"
    else
      echo "✗ push failed"; cat "$TODAY_DIR/_gzb_$SILO.err" || true; PUSH_FAIL=$((PUSH_FAIL + 1))
    fi
  done
  # Mirror the 7-day retention on gzb too (prune day-dirs older than 7 days).
  $GZB_SSH "$GZB_HOST" "find $GZB_REMOTE_DIR -maxdepth 1 -type d -mtime +7 -exec rm -rf {} \\;" 2>/dev/null || true
  if [ "$PUSH_FAIL" -gt 0 ]; then
    alert "⚠️ Daily backup: off-site push to gzb FAILED for $PUSH_FAIL client silo(s) — LOCAL backup is OK, but the off-site copy is stale. Check $TODAY_DIR/_gzb_*.err"
  fi
else
  echo "  [off-site] SKIPPED: gzb key $GZB_KEY not found (no off-site push)."
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
