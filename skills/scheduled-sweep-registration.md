# Registering for the Section E Phase 3 scheduled sweep

CAI-PROCESS-INBOX-CADENCE-001 Section B Architecture C requires every cc-*
agent to run a scheduled inbox sweep. This runbook walks through the
per-family registration on Mac Mini.

## When to invoke

Each cc-* family runs this once at family launch (or when adopting Phase 3
post-bootstrap). cc-orchestrator is the pilot family per CAI-RESP-108 axis
(e); other families register after cc-orchestrator's solo-pilot week
confirms the substrate.

## Pre-flight

- Mac Mini accessible via shell
- `~/wingmen/orchestrator/.env` has `DATABASE_URL` set
- `~/wingmen/orchestrator/scripts/scheduled_cc_sweep.sh` is executable
- `~/wingmen/orchestrator/skills/scheduled-sweep-prompt.md` exists
- Your family is registered in `agents` table with `repo_scope` matching
  the launcher's family-map (per GOVERNANCE-CLEANUP-001 Step 3)

## Procedure

### 1. Pick your cadence

Based on your family's typical inbound priority distribution:

| Profile | Cadence | StartInterval (s) | Use case |
|---|---|---|---|
| P1-leaning | 15 min | 900 | cc-orchestrator (platform escalations), cc-ihsanos (active dev) |
| P2-leaning | 30 min | 1800 | cc-cosem (slower-cadence platform work) |
| P3-leaning | 4 hr | 14400 | cc-scholar (substantive but non-urgent rulings) |

Cadence is just the family's default tick — individual messages still trigger
agent_watchdog P1 alarm via `inbox_sla_violations` independently.

### 2. Generate plist from template

```bash
ORCH=/Users/sheikhmusa/wingmen/orchestrator
FAMILY=cc-orchestrator
CADENCE=900

sed -e "s/{{FAMILY}}/${FAMILY}/g" \
    -e "s/{{CADENCE_SECONDS}}/${CADENCE}/g" \
    "${ORCH}/scripts/launchd/dev.wingmen.scheduled.plist.tmpl" \
    > "${HOME}/Library/LaunchAgents/dev.wingmen.${FAMILY}.scheduled.plist"
```

### 3. Bootstrap into launchd

```bash
launchctl bootstrap "gui/$(id -u)" \
    "${HOME}/Library/LaunchAgents/dev.wingmen.${FAMILY}.scheduled.plist"
```

If you see `Bootstrap failed: 5: Input/output error` on macOS 14+, that's a
known SIP-related quirk — the service still loads. Verify next.

### 4. Verify registration

```bash
launchctl print "gui/$(id -u)/dev.wingmen.${FAMILY}.scheduled" | head -30
```

You should see your `Label`, `ProgramArguments` pointing at
`scheduled_cc_sweep.sh --family ${FAMILY}`, and `StartInterval` matching
your cadence.

### 5. Watch the first tick

```bash
tail -F "${ORCH}/logs/scheduled-${FAMILY}.log"
```

Within `CADENCE` seconds you should see one of:

- `[<ts>] ${FAMILY}: empty tick (unread=0 sla=0) — heartbeat + skip CC spawn`
  → expected when your inbox is clean. Substrate is working.
- `[<ts>] ${FAMILY}: spawning scheduled CC (unread=N sla=M)` followed by the
  launcher header → expected when you have unread or SLA violations.

### 6. File confirmation to CAI

Once the first non-empty tick completes successfully, file a brief
`update` (`requires_response=false`) message to cai noting:

- Family registered
- Cadence chosen
- First tick outcome (empty / non-empty + counts)

This gives cai cross-CC visibility into the rollout per CAI-RESP-108
"empirical observations" line.

## Unregistering / changing cadence

```bash
# Unload
launchctl bootout "gui/$(id -u)/dev.wingmen.${FAMILY}.scheduled"

# Edit the plist or regenerate from template with new cadence

# Reload
launchctl bootstrap "gui/$(id -u)" \
    "${HOME}/Library/LaunchAgents/dev.wingmen.${FAMILY}.scheduled.plist"
```

## Troubleshooting

**Sweep wrapper exits with `DATABASE_URL missing from .env`:** the launcher
sources `.env` directly; check the file exists at
`/Users/sheikhmusa/wingmen/orchestrator/.env` and contains `DATABASE_URL=...`.

**No CC spawn ever — always "empty tick":** verify your family has unread
messages or SLA violations:
```bash
psql "$DATABASE_URL" -At -c "SELECT count(*) FROM agent_messages
                              WHERE to_agent='${FAMILY}' AND read_at IS NULL"
```

**Launcher fails on `--scheduled-prompt: unknown arg`:** older launcher
deployed. Check `scripts/launch_dangerous_cc.sh` for the
`--scheduled-prompt` arg in the parser block.

**CC session hangs past 10 min:** the launchd `ExitTimeOut=600` will
hard-kill it. If this fires repeatedly, the prompt is doing too much —
file a CADENCE-001 amendment proposal to cai.

## Audit hook

`nervous_system/orch_self_audit.py` runs a defense funnel per CAI-RESP-108
axis (d): flags any `agent_messages` row where `responded_at` was set
within 30 sec of `read_at`, suggesting scheduled-sweep silently violating
Section D. Fires P2 alert to cai. No action required at registration —
the audit runs on the existing 10-min orchestrator audit cadence.

## References

- `scripts/launchd/dev.wingmen.scheduled.plist.tmpl` — plist template
- `scripts/scheduled_cc_sweep.sh` — pre-filter wrapper
- `scripts/launch_dangerous_cc.sh` — CC launcher with --scheduled-prompt
- `skills/scheduled-sweep-prompt.md` — the bounded sweep prompt
- CAI-PROCESS-INBOX-CADENCE-001 (id 619) — Section B + Section D
- CAI-RESP-108 (id 622) — Phase 3 build authorization
