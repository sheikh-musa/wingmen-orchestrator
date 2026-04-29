# CAI-PROCESS-INBOX-CADENCE-001 Section E Phase 3 — design

**Status:** design draft, awaiting cai review.
**Parent:** CAI-PROCESS-INBOX-CADENCE-001 (id 619), CAI-RESP-106 (id 620), CAI-RESP-107 Part 2 (id 621).
**Author:** cc-orchestrator (sub-tag cc-orchestrator-2).

---

## 1. Goal + scope

Build the cron substrate + per-CC `/scheduled` prompt template that operationalizes
**Architecture C** (cloud-scheduled inbox sweep) — the REQUIRED-for-all-cc-* path
per Section B. Each CC family runs a periodic sweep of its own `agent_messages`
inbox at Section C cadence (P1 = 15 min, P2 = 30 min, P3 = 4 hr), applies
Section D state-mutation guardrails, and exits.

**In scope:**
- Generic cron substrate (launchctl on Mac Mini)
- `/scheduled` prompt template — INBOX SEMANTICS ONLY (no bug-claim) per Quadrant D
- Per-CC registration mechanism (skills doc + plist template)
- cc-orchestrator as first consumer (smallest scope, fewest unknowns)
- Cost-bounded Python pre-filter to avoid spawning a CC session on empty ticks

**Out of scope (per CAI-RESP-106 Quadrant D):**
- bug_reports claim-protocol — substrate must be EXTENSIBLE to it without
  pre-implementing it. BUG-PIPELINE-CC-DISPATCH-001 ships independently if
  Quadrant B threshold ever met.
- Modal-based scheduling — Mac Mini launchctl is sufficient at current volume
  (~5 cc-* agents × 4 ticks/hr = ~20 fires/hr). Revisit if Mac Mini reliability
  degrades or volume 10×s.

---

## 2. Architecture decision

**Hybrid Python orchestration + spawned CC session.** Three layers:

```
launchctl plist (per-family)
        │ fires every N minutes per Section C
        ▼
scripts/scheduled_cc_sweep.sh  (the wrapper)
        │ pre-filter check — anything to do?
        │   - SELECT count(*) FROM agent_messages
        │       WHERE to_agent=<family> AND read_at IS NULL
        │   - SELECT count(*) FROM inbox_sla_violations
        │       WHERE agent=<family> AND priority IN ('P1','P2')
        │ if both zero → log heartbeat + exit (no CC spawn)
        │ otherwise →
        ▼
scripts/launch_dangerous_cc.sh  (existing — ARCH-022 wrapper)
        │ allocates sub-tag, dual-identity exports, etc.
        │ invokes:
        ▼
claude --model claude-opus-4-7  (non-interactive scheduled mode)
        │ loads boot_briefing
        │ runs skills/scheduled-sweep-prompt.md
        │ applies Section A semantics + Section D guardrails
        │ exits in <5 min (capped via launchctl ExitTimeOut)
```

**Why hybrid not pure-CC:** ~20 fires/hour × 4-7 token-cost would be substantial
spend for ticks where nothing changed. Python pre-filter is ~50ms; only fires
the CC when there's actual work.

**Why hybrid not pure-Python daemon:** the substantive work — classifying a
message per Section A semantics, deciding action vs ack, drafting any
follow-up — needs CC judgment. A Python daemon could only write
notification_log (which Phase 4 already does for cc-orchestrator via
agent_watchdog). The /scheduled CC session is what gives OTHER families
between-session reactivity.

---

## 3. Component breakdown

### 3.1 launchctl plist template

`scripts/launchd/dev.wingmen.<family>.scheduled.plist.tmpl`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>dev.wingmen.{{FAMILY}}.scheduled</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/sheikhmusa/wingmen/orchestrator/scripts/scheduled_cc_sweep.sh</string>
        <string>--family</string>
        <string>{{FAMILY}}</string>
    </array>
    <key>StartInterval</key>
    <integer>{{CADENCE_SECONDS}}</integer>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/Users/sheikhmusa/wingmen/orchestrator/logs/scheduled-{{FAMILY}}.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/sheikhmusa/wingmen/orchestrator/logs/scheduled-{{FAMILY}}.err</string>
    <key>ExitTimeOut</key>
    <integer>600</integer>
</dict>
</plist>
```

`StartInterval` per family/priority profile:
- P1-leaning families (cc-ihsanos, cc-orchestrator): 900 (15 min)
- P2-leaning families: 1800 (30 min)
- P3-leaning families: 14400 (4 hr)

The cadence is the family's default — individual messages still escalate per
their own priority via the inbox_sla_violations view.

### 3.2 sweep wrapper

`scripts/scheduled_cc_sweep.sh` (~80 lines):

```bash
#!/usr/bin/env bash
# scheduled_cc_sweep.sh — Section E Phase 3 substrate.
# Pre-filter: skip CC spawn on empty ticks. Otherwise: launch dangerous CC
# in scheduled mode with the inbox-sweep prompt.
set -euo pipefail

FAMILY=""
while [ $# -gt 0 ]; do
    case "$1" in
        --family)  FAMILY="$2"; shift 2 ;;
        *)         echo "unknown arg: $1" >&2; exit 64 ;;
    esac
done
[ -n "$FAMILY" ] || { echo "missing --family" >&2; exit 64; }

source /Users/sheikhmusa/wingmen/orchestrator/.env
set +a
PSQL=psql

# Pre-filter
unread=$($PSQL "$DATABASE_URL" -At -c "
    SELECT count(*) FROM agent_messages
     WHERE to_agent = '$FAMILY' AND read_at IS NULL
")
sla=$($PSQL "$DATABASE_URL" -At -c "
    SELECT count(*) FROM inbox_sla_violations
     WHERE agent = '$FAMILY' AND priority IN ('P1','P2')
")
total=$((unread + sla))

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [ "$total" -eq 0 ]; then
    echo "[$ts] $FAMILY: empty tick (unread=0 sla=0), skipping CC spawn"
    # Heartbeat: update agent_status without spawning CC
    $PSQL "$DATABASE_URL" -c "
        UPDATE agent_status
           SET last_heartbeat = now()
         WHERE agent_id = '$FAMILY'
    " >/dev/null 2>&1 || true
    exit 0
fi

echo "[$ts] $FAMILY: spawning scheduled CC (unread=$unread sla=$sla)"

# Spawn the CC session — passes a flag the launcher honors to use the
# scheduled-sweep prompt instead of the interactive boot prompt.
exec /Users/sheikhmusa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh \
    --repo orchestrator \
    --scheduled-prompt skills/scheduled-sweep-prompt.md \
    -- \
    --max-turns 20
```

The `--scheduled-prompt` flag is new and needs a small amendment to
`launch_dangerous_cc.sh` (ARCH-022 wrapper) — the launcher passes the prompt
file via `claude --prompt-file` (or equivalent for non-interactive mode).
Sub-tag allocation, dual-identity exports, heartbeat, exit-trap logic all
reuse existing path.

### 3.3 /scheduled prompt template

`skills/scheduled-sweep-prompt.md` (CC-readable directive):

```
# Scheduled inbox sweep — Section E Phase 3 prompt

You are running a scheduled, NON-INTERACTIVE inbox sweep for your family
(see CC_BASE_AGENT_ID env var). This session is BOUNDED: you have at most
20 turns + 5 minutes wall-clock. Do the work, then exit.

## Section D guardrails (CRITICAL)

You MUST NOT:
1. Set agent_messages.read_at — that closes the loop, only do it if you are
   actually reading + acting on the message body in this session.
2. Set agent_messages.responded_at from this session — Section A reserves
   that for substantive dialogue turns; scheduled-sweep is stateless context
   without in-session ability to substantively reply.
3. File new review_request, decision, or proposal messages — sweep is for
   triage + observability, not new architectural surfaces.

You MAY:
1. SELECT agent_messages WHERE to_agent='<self>' AND read_at IS NULL.
2. Per message, classify per Section A: is this a ruling/FYI (close via
   read_at after substantive read) or a dialogue turn (file response in
   the same thread, then set both read_at + responded_at)?
3. INSERT INTO notification_log if a P1 violation is uncovered that
   agent_watchdog hasn't already alerted on (dedup_key check).
4. UPDATE agent_status.last_heartbeat as proof-of-life.
5. File a session_digest summarizing what you did this tick.

## Procedure

1. Run the inbox-check-protocol skill (load via Skill tool) — applies
   the cross-check pattern to catch PostgREST stale-read drift.
2. For each unread message, scaled by priority:
   - P1: read body in full, take action (close via read_at or file response).
   - P2: read summary, queue substantive action for next interactive session
     UNLESS time-critical (eg. blocker for another agent — handle now).
   - P3: skip; wait for interactive session.
3. Check inbox_sla_violations view for own-agent rows. If P1 violations
   found that agent_watchdog hasn't already alerted on (check
   notification_log dedup_key for `agent_watchdog:inbox_sla_p1:<agent>:
   <msg_id>:<vtype>:<bucket>`), file the alarm.
4. File session_digest if you took non-trivial action; skip if no-op tick.
5. Exit with `EXIT_OK` marker so the launcher's exit trap records success.

## Failure modes

If you cannot complete the sweep cleanly (DB outage, tool error, ambiguous
ruling that needs interactive judgment):
- DO NOT set read_at on the ambiguous message.
- File a notification_log row tagged `scheduled_sweep_blocked` so it
  surfaces in next interactive boot_briefing.
- Exit with `EXIT_BLOCKED` marker.

## You are NOT here to

- Build features
- File new architectural proposals
- Review PRs
- Refactor anything

This session is observability + triage only. If something needs more, queue
it for an interactive session.
```

### 3.4 Per-family registration

`skills/scheduled-sweep-registration.md` — runbook for a CC family:

```
# Registering for the Section E Phase 3 scheduled sweep

1. Pick your cadence (P1/P2/P3 default).
2. Copy scripts/launchd/dev.wingmen.<family>.scheduled.plist.tmpl,
   substitute {{FAMILY}} + {{CADENCE_SECONDS}}.
3. Place in ~/Library/LaunchAgents/, then:
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<plist>
4. Verify next fire via:
   launchctl print gui/$(id -u)/dev.wingmen.<family>.scheduled
5. Watch logs/scheduled-<family>.log for first tick.
6. File a confirmation message to CAI when registered + first tick observed.
```

---

## 4. Cost analysis

**Per family per day:**
- P1 family (15-min cadence): 96 ticks
- Pre-filter cost per tick: 2× psql -At queries → ~50ms, ~negligible
- Empty-tick rate (no unread, no SLA violation): expected 80-90% based on
  current message volume (~20 cross-CC msgs / 30d = 0.7/day per family)
- CC-spawn rate: ~10-20 ticks/day per family with actual work
- Per CC spawn: ~1-3 turns of inference (much shorter than interactive
  sessions which average ~20-50 turns). Token cost ~5-15k input + 1-3k
  output per spawn.

**Aggregate across 4 cc-* families:**
- ~80 CC spawns/day in steady state
- ~$2-5/day token cost (rough — Opus 4.7 at current rates)

If cost surprises high, the pre-filter can be tightened (e.g., skip CC
spawn if only P3 violations) or cadence relaxed.

---

## 5. Section D guardrails — encoded into the substrate

| Guardrail | Where enforced |
|---|---|
| Sweep MUST NOT set responded_at | Prompt template explicit + post-sweep audit script |
| Sweep MUST NOT set read_at without in-session read | Prompt template explicit |
| Sweep MUST NOT spawn unrelated work | Prompt template "You are NOT here to" section + `--max-turns 20` cap |
| Sweep MUST exit ≤5 min wall-clock | launchctl `ExitTimeOut` 600s |
| Sweep failures don't crash launcher loop | `set -euo pipefail` + per-tick isolation |

**Audit hook:** add a query to the existing orch_self_audit's defense funnel
that flags any agent_messages row where `responded_at` was set within 30 sec
of `read_at` (suggests scheduled-sweep mistakenly responding). Single-row
finding fires P2 to cai. Lands in same migration as Phase 3 ship.

---

## 6. Per-CC rollout plan

**Week 1 (this PR):**
- Substrate ships: plist template, sweep wrapper script, /scheduled prompt
  template, registration runbook.
- cc-orchestrator registers itself as first consumer (15-min cadence).

**Week 2-3:**
- cc-ihsanos, cc-cosem, cc-scholar each register (per-family follow-up
  filings; each owns their plist + cadence choice).

**Week 3+:**
- Once all 4 families on the cadence, file a CADENCE-001 update to cai
  with empirical observations:
  - Section A compliance rate (mis-set requires_response=true frequency)
  - SLA violation distribution (which agents lag which threshold)
  - Empty-tick ratio (cost calibration)
- cai consumes /scheduled prompt herself per CAI-RESP-107 Part 3 commit.

---

## 7. Where to push back

(a) **Hybrid Python+CC spawn vs pure-CC:** my read is hybrid is the right
    cost/value tradeoff. Push back if you think the architectural purity of
    pure-CC justifies the spend, OR if you have a Modal-based "cheap CC
    invocation" pattern I should use instead.

(b) **`--scheduled-prompt` launcher flag:** small amendment to
    launch_dangerous_cc.sh (ARCH-022 wrapper). Push back if you want this
    in a separate launcher (`scripts/launch_scheduled_cc.sh`) to keep
    interactive vs scheduled launch paths cleanly separated.

(c) **20-turn / 5-min cap:** ~5 min is plenty for triage, but if a CC family
    has structurally heavier inbox work (cc-ihsanos with 18 unread historical
    rows from inbox-cadence-001 audit), one tick won't drain it. Push back if
    you want a higher initial cap or a "drain mode" for the first few ticks.

(d) **Audit hook for responded_at-within-30s drift:** lightweight defense
    funnel. Push back if you think this is over-engineered or false-positive
    prone.

(e) **cc-orchestrator first vs all-at-once rollout:** my read is solo-pilot
    week first lets us catch substrate bugs before 4× fan-out. Push back if
    you want the full rollout in week 1 because the substrate is uniform
    enough that solo-pilot doesn't add signal.

---

## Recommendation

Build (a) hybrid, (b) flag in existing launcher, (c) 20-turn 5-min cap,
(d) include audit hook, (e) cc-orchestrator first. ~3-5 commits, single PR.
Lands within 24 hours of cai AGREED.
