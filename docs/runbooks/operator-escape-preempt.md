# Operator Escape-first preemption (nudge_session)

Context: Musa op#36085/36087/36098, 2026-10-30. An URGENT operator reply
during a long tool chain got queued behind it instead of actually
interrupting — the console only read it at the next natural pause, which is
what prompted this fix. See PR #675.

## What it does

`nudge_session()` in `nervous_system/ingest.py`, when called with
`urgent=True` and `target` in `ESCAPE_PREEMPT_TARGETS`, sends a tmux `Escape`
keypress to the target pane BEFORE its usual clear (`C-u`) + nudge-text +
`Enter` sequence.

**Escape means "stop waiting, read input now." It does not kill running
work.** Verified on a disposable throwaway tmux pane, 2026-10-30, both
required cases:

- **Mid-Bash-tool-call**: the pane returns to an idle, input-ready prompt
  within ~1s. The underlying shell command is NOT force-killed — it
  continues running detached to its own natural completion. A long migration
  or deploy left running this way finishes on its own; the agent is simply
  free to read the operator's message immediately instead of waiting on it.
- **Mid permission-confirmation dialog**: Escape cleanly rejects that one
  pending action and returns to idle, no corruption.

## Current scope

`ESCAPE_PREEMPT_TARGETS` (env-overridable, comma-separated) defaults to
`nazim` only — the console body's own session. The hub's `orch` pane is
deliberately NOT in this set yet; a bad interrupt on the hub's live pane is a
different, higher-blast-radius risk than on the console's, and ungating it
needs its own explicit go-ahead.

## If you need to extend or roll this back

- Add a target: `ESCAPE_PREEMPT_TARGETS=nazim,orch` in `.env` (or the
  process env for whichever daemon runs `nervous_system/ingest.py`).
- Roll back entirely: set `ESCAPE_PREEMPT_TARGETS=` (empty) — `urgent=True`
  nudges then behave exactly as before this change (no Escape sent).
- The same tightened timing (`OPERATOR_ACK_AFTER_SEC`,
  `OPERATOR_MAX_DEFER_SEC`) applies only to `OPERATOR_CHANNEL_TAGS`
  (`orch-channel`, `nazim-console`) and is independent of the Escape scope
  above — rolling back one does not roll back the other.
