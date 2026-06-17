# #111 — Realtime → agent wake (read path)

**Goal:** close the auto-wake gap. Today the Supabase Realtime subscriber
(`nervous_system/agent_messages_realtime.py`) pushes bus INSERTs to the operator's
Telegram sub-second but makes "no autonomous decisions" — it never wakes the
*recipient agent*. So inter-agent messages sit until a human couriers them
(send-keys nudge). #111 automates that nudge.

**Constraint (CAI-RESP-255 #2):** send-keys carries an **OS-level wake SIGNAL only**
("check your inbox"), never message content. The bus remains the content channel.

## Mechanism (non-contingent — build now, wire later)

`nervous_system/agent_wake.py`:
- `resolve_tmux_session(agent_id) -> str | None` — map a live agent to its tmux
  session. v1: derive via cwd — find the tmux session whose `claude` pane cwd sits
  inside one of the agent family's `repo_scope` repos (reuses the lane_watch
  cwd-match). Target state: lanes self-register their `$TMUX` session name into a
  new `agent_status.tmux_session` column at boot (deterministic; drops the
  derivation). Documented as a follow-up, not v1-blocking.
- `wake_agent(agent_id, reason) -> bool` — send a FIXED signal line to the session
  (`[wake] new inbox item — read agent_messages and act`), then Enter. Never the
  message body.
- **Debounce / storm guard:** a per-agent watermark (`scripts/.agent_wake/`) — do
  not re-wake an agent within `WAKE_DEBOUNCE_S` (default 45s) if already signalled,
  and skip if the session is mid-turn ("thinking"/"esc to interrupt" in the pane).
  Prevents A-wakes-B-wakes-A storms.

## Activation (contingent — gated on cai)

A small addition to `_route_single_message` in the realtime subscriber: when an
INSERT is addressed to a **live CC agent** (not the operator) and matches the
wake policy, call `wake_agent(to_agent, ...)` IN ADDITION to (or instead of) the
existing Telegram routing. This is the line that turns autonomy on, so it ships
only after cai ratifies the policy below.

## Open policy questions for cai (the autonomy fork)

1. **Trigger scope** — which INSERTs auto-wake the recipient? Rec: wake when
   `to_agent` is a live CC agent AND (`requires_response=true` OR `message_type IN
   (blocker, question, review_request, decision)`). Skip pure `update` advisories
   (no wake for FYIs). Skip `is_test`, P3.
2. **Act vs escalate-only** — the wake only says "read your inbox"; what the agent
   then does is bounded by its EXISTING role + 257/258 boundaries (engineers
   build, reviewer reviews, orchestrator relays/escalates, cai adjudicates). Rec:
   the wake grants NO new authority — confirm that's the model.
3. **cc-orchestrator wake** — cc-orchestrator runs operator-attached (a plain
   terminal, not tmux), so it can't be send-keys-woken like the lanes. Fork: (a)
   auto-wake worker lanes only, keep cc-orchestrator human-driven; or (b) run a
   cc-orchestrator tmux lane for autonomous coordination (bigger autonomy step).
   Rec: (a) now; (b) is a separate decision.
4. **Loop guard** — confirm debounce (45s) + mid-turn skip is sufficient, or set
   a hard per-agent wake budget per window.
5. **Retire lane_watch?** — once Realtime notify (live) + #111 wake exist,
   `lane_watch.py` is redundant. Rec: retire it (Realtime already covers operator
   notification sub-second), or demote to a pure WS-disconnect fallback.

## Test plan

- `resolve_tmux_session` against the live fleet (read-only): cc-ihsanos-1→ihsanos,
  cc-cosem-1→cosem, cai→cai.
- `wake_agent` dry-run (no send): asserts the signal string + debounce skip.
- Storm guard: two rapid `wake_agent` calls → second is debounced.
