#!/usr/bin/env bash
# propose.sh — file an ideas-up proposal (operator op#13332).
#
# WHY THIS IS ONE COMMAND: the loop only works if filing costs less than not filing. An
# agent that notices a better shape mid-task will not stop to open a doc, so this takes the
# thought straight from where it occurs to where triage can see it.
#
# WHAT BELONGS HERE: "this should be different" — a better architecture, a gate that should
# exist, a recurring failure nobody owns. NOT a bug report (bug_reports / the bus already
# carry those) and NOT a finding without a suggested change: `--proposal` is required and
# the database rejects a blank one. A complaint is not a proposal.
#
# Usage:
#   scripts/propose.sh --problem "<one sentence: what is wrong>" \
#                      --proposal "<what should be different>" \
#                      [--evidence "<pane/log/DB output proving it — verified at source>"] \
#                      [--class "<failure class, e.g. stale-feed>"] \
#                      [--cost operator-caught|agent-caught|near-miss] \
#                      [--agent <agent_id>]
#
#   --cost operator-caught  the OPERATOR found this first. Be honest here: this field is the
#                           loop's north-star metric, and under-reporting it just makes the
#                           number lie rather than making the fleet look better.
#   --cost near-miss        nothing broke, but it nearly did. Worth as much as a real defect.
#
# Triage is cai + orch-console. The operator sees the digest, not the queue.
set -euo pipefail

ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROBLEM=""; PROPOSAL=""; EVIDENCE=""; CLASS="unclassified"; COST="agent-caught"
AGENT="${ORCH_AGENT_ID:-${AGENT_ID:-unknown}}"

while [ $# -gt 0 ]; do
  case "$1" in
    --problem)  PROBLEM="${2:?--problem needs a value}"; shift 2 ;;
    --proposal) PROPOSAL="${2:?--proposal needs a value}"; shift 2 ;;
    --evidence) EVIDENCE="${2:?--evidence needs a value}"; shift 2 ;;
    --class)    CLASS="${2:?--class needs a value}"; shift 2 ;;
    --cost)     COST="${2:?--cost needs a value}"; shift 2 ;;
    --agent)    AGENT="${2:?--agent needs a value}"; shift 2 ;;
    -h|--help)  sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$PROBLEM" ]  || { echo "--problem is required" >&2; exit 2; }
[ -n "$PROPOSAL" ] || { echo "--proposal is required — a finding with no suggested change is a bug report, not a proposal" >&2; exit 2; }
case "$COST" in
  operator-caught|agent-caught|near-miss) ;;
  *) echo "--cost must be operator-caught, agent-caught or near-miss" >&2; exit 2 ;;
esac

cd "$ORCH_DIR"
# PYTHONPATH so nervous_system imports resolve when this is called from a lane's worktree
# (reference_send_scripts_log_needs_pythonpath).
PYTHONPATH="$ORCH_DIR" PROBLEM="$PROBLEM" PROPOSAL="$PROPOSAL" EVIDENCE="$EVIDENCE" \
CLASS="$CLASS" COST="$COST" AGENT="$AGENT" \
"$ORCH_DIR/.venv/bin/python" - <<'PY'
import os
import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.environ["PYTHONPATH"], ".env"))
dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
    cur.execute(
        "INSERT INTO public.fleet_proposals "
        "(from_agent, problem, proposal, evidence, failure_class, cost_signal) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (os.environ["AGENT"], os.environ["PROBLEM"], os.environ["PROPOSAL"],
         os.environ["EVIDENCE"], os.environ["CLASS"], os.environ["COST"]))
    pid = cur.fetchone()[0]
    # Show whether this class has been seen before — a repeat is the signal triage most wants,
    # and telling the author right away is what turns filing into learning.
    cur.execute(
        "SELECT count(*) FROM public.fleet_proposals "
        "WHERE failure_class = %s AND failure_class <> 'unclassified' AND NOT is_test",
        (os.environ["CLASS"],))
    seen = cur.fetchone()[0]
print(f"filed proposal #{pid} (class '{os.environ['CLASS']}', {os.environ['COST']})")
if seen > 1:
    print(f"NOTE: this failure class has now been seen {seen} times — it is a repeat, "
          f"which means the earlier fix did not hold. Say so at triage.")
PY
