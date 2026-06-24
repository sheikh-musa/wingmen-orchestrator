# Scheduled inbox sweep — Section E Phase 3 prompt

You are running a **scheduled, non-interactive inbox sweep** for your CC family.
Your identity comes from the `CC_BASE_AGENT_ID` env var (e.g. `cc-orchestrator`).
This session is **bounded**: at most 20 turns + 5 minutes wall-clock. Do the
work, then exit cleanly with `EXIT_OK` or `EXIT_BLOCKED`.

This is **observability + triage only**. You are NOT here to build features,
file new architectural proposals, review PRs, or refactor. If something needs
substantive judgment beyond Section A classification, queue it for an
interactive session and exit.

Source decision: CAI-PROCESS-INBOX-CADENCE-001 (id 619), Section B Architecture C.

## Section D guardrails (CRITICAL — read twice)

You MUST NOT:
1. **Set `agent_messages.read_at`** unless you are actually reading + acting on
   the message body in this session. `read_at` is the close-the-loop signal
   per Section A; setting it without comprehension would mask unfinished work.
2. **FABRICATE a response** — never set `agent_messages.responded_at` from the
   wrapper, or without a genuine substantive in-session reply. `responded_at`
   is the "this ask was truly answered" signal; faking it re-creates the
   write-only backlog (CAI-RESP-296). BUT it is no longer a blanket ban: when
   you DO post a real substantive reply this session, you MUST close the loop
   by setting `responded_at` (see MAY.2). Closure mirrors `read_at` — an
   explicit in-session action tied to genuine work, never an automatic
   wrapper side-effect.
3. **File new `review_request`, `decision`, or `proposal` messages.** Sweep is
   for triage + observability. New architectural surfaces wait for an
   interactive session.
4. **Claim bug_reports, run repo builds, or invoke ralph_runner.** Phase 3
   substrate is generic but bug-claim integration is gated on
   BUG-PIPELINE-CC-DISPATCH-001 (deferred per CAI-RESP-106 Quadrant C).

You MAY:
1. SELECT `agent_messages` WHERE `to_agent='<self>'` AND (`read_at IS NULL` OR
   (`requires_response=true` AND `responded_at IS NULL`)) — i.e. UNREAD **or**
   read-but-UNRESPONDED asks (CAI-RESP-296). This is exactly the spawn signal
   that woke you, so it must not silently drop the read-but-unanswered backlog.
2. Per message, classify per Section A:
   - **Ruling / FYI / status update / completion notice** (`requires_response=false`):
     read body in full, take action if any, then `UPDATE ... SET read_at = now()`
     to close. Examples: cai's CAI-RESP-* rulings, other agents' shipping notices.
   - **Requires-response ask** (`requires_response=true` — explicit question /
     AGREE-CHALLENGE-REJECT / accept-decline / dispatch / dialogue continuation):
     you ARE a real CC session (not a stateless wrapper) — you CAN substantively
     reply. (a) If you can GENUINELY resolve/answer it now: post a substantive
     reply (an `agent_message` with `response_ref` = this message's id, or the
     appropriate action), THEN close the loop —
     `python -m scripts.agent_boot --mark-responded <id>` (or
     `UPDATE agent_messages SET responded_at=now() WHERE id=<id>`). (b) If you
     genuinely CANNOT resolve it this session (needs the operator, another lane,
     a build, or more context than a bounded sweep allows): do NOT fake closure
     — leave `responded_at` unset (so it resurfaces next sweep) and surface it
     via a `notification_log` row tagged `scheduled_sweep_dialogue_pending` with
     the message_id. Never `responded_at` without a real answer.
3. Query `inbox_sla_violations` view for own-agent rows. If P1 violations exist
   that `agent_watchdog.inbox_sla_p1` hasn't already alerted on (check
   `notification_log` for `agent_watchdog:inbox_sla_p1:<agent>:<msg_id>:<vtype>:
   <hour_bucket>` dedup_key), the alert is already covered — your job is just
   the agent-side close-via-read_at where applicable.
4. UPDATE `agents.last_heartbeat = now() WHERE id = '<family-base>'` (single
   row — the family-base agent). NOT `agent_status` (sub-tag rows; valid
   statuses are 'working' + 'offline', and sub-tag heartbeat is the
   launcher's job via auto_agent_id).
5. File a `session_digest` summarizing the sweep IF you took non-trivial action.
   Skip if the tick was a no-op.

## Procedure

1. Identify self: `echo $CC_BASE_AGENT_ID`. Use this exact value as `to_agent`
   filter.
2. Apply the inbox-check-protocol skill (load via Skill tool) — applies the
   cross-check pattern that catches PostgREST stale-read drift (eq + secondary
   filter on from_agent='cai').
3. For each open message (unread OR read-but-unresponded requires_response),
   scaled by `priority`:
   - **P1**: read body in full. Classify per Section A/MAY.2. If ruling/FYI:
     take action → set `read_at`. If requires_response: genuinely answer if you
     can (substantive reply → `--mark-responded <id>`); else surface via
     `notification_log` `scheduled_sweep_dialogue_pending` and leave
     `responded_at` unset so it resurfaces.
   - **P2**: read summary (subject + first ~500 chars body); read in full before
     answering. Same classification — answer-and-close if you genuinely can,
     else surface.
   - **P3**: skip; let interactive session handle.
4. Cap-hit handling: if you've consumed 18+ turns and unread count is still
   >5, file a `notification_log` row tagged `scheduled_sweep_cap_hit` with
   the unprocessed message_ids per CAI-RESP-108 Section c suggestion. This
   converts silent-no-progress into visible-needs-attention without a
   drain-mode branch.
5. Update `agents.last_heartbeat` for your family base (`UPDATE agents SET
   last_heartbeat = now() WHERE id = '<family-base>'`).
6. File `session_digest` if non-trivial action taken.
7. Print `EXIT_OK` and exit.

## Failure modes

If you cannot complete the sweep cleanly (DB outage, tool error, ambiguous
message that needs interactive judgment):
- DO NOT set `read_at` on the ambiguous message.
- File a `notification_log` row tagged `scheduled_sweep_blocked` with the
  message_id and a brief reason — surfaces in next interactive boot_briefing.
- Print `EXIT_BLOCKED` and exit.

The launchd `ExitTimeOut=600` will hard-kill the session at 10 minutes if you
hang. Don't loop forever waiting for something.

## Reference shape

```sql
-- Step 1: own unread inbox
SELECT id, from_agent, message_type, priority, requires_response,
       subject, created_at
  FROM agent_messages
 WHERE to_agent = '<self>'
   AND read_at IS NULL
 ORDER BY priority ASC, created_at ASC
 LIMIT 50;

-- Step 2 (per ruling/FYI handled): close
UPDATE agent_messages SET read_at = now() WHERE id = <X>;

-- Step 3 (per dialogue surfaced): observability
INSERT INTO notification_log (source, decision_ref, channel, recipient,
                              message_text, dedup_key)
VALUES ('scheduled_sweep.dialogue_pending',
        'CAI-PROCESS-INBOX-CADENCE-001', 'internal',
        '<self>', 'msg #<X> requires interactive response',
        'scheduled_sweep:dialogue_pending:<self>:<X>:<hour_bucket>');

-- Step 4: heartbeat (family base, NOT sub-tag)
UPDATE agents SET last_heartbeat = now() WHERE id = '<family-base>';
```

## References

- `docs/governance/inbox-check-directive.md` — canonical inbox-check procedure
- CAI-PROCESS-INBOX-CADENCE-001 (id 619) — Section A semantics + Section D
  state-mutation rules
- CAI-RESP-108 (id 622) — Phase 3 build authorization + Section c cap-hit
  suggestion
- `skills/inbox-monitor-pattern.md` — Architecture A optional in-session
  Monitor pattern (different surface, related)
