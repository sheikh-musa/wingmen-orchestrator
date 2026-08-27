# "YOUR ASKS" board — design (replaces the DRAIN BOARD)

Design-only spec. Nothing here is applied to the DB or shipped. Audience: the
operator (Musa) + whoever builds this on the console body (Nazim).

## 1. What the operator actually asked for

Verbatim, from the live log (`operator_messages` #13250, channel `nazim-console`,
2026-08-15):

> "…but again **this cannot be stale info**. the things that i ask of you and you
> delegate. **can we see those?**"

He explicitly picked **option (a)**: his open asks + their live status — and that
"live status" bucket already contains *blocked on you / needs your decision*.

The current **DRAIN BOARD** (fc-v52) answers a different question. It shows
`agent_messages` grouped by `to_agent` where `read_at IS NULL` — i.e. every
fleet body's unread bus inbox depth. That is an SRE/fleet-internal lens ("who
has undrained mail"), not "the things Musa asked and Nazim delegated." Wrong
axis for this operator.

## 2. Audit — the real data flow (verified against code + live DB)

### 2a. How an operator ask arrives
`nervous_system/ingest.py` → `nervous_system/operator_log.py::log()` inserts into
**`operator_messages`**. Operator asks to the console body are inbound rows on the
console's scope (`nervous_system/operator_log.py::_channel_scope_sql()`,
`role=='console'`):

- `channel = 'tmux-console'` (Musa types into Nazim's terminal), **or**
- `tag = 'nazim-console'` (Musa DMs @nazim_cto_bot).

Relevant columns: `id`, `direction` (`inbound`/`outbound`), `channel`, `tag`,
`text`, `created_at`, `handled_at` (timestamptz — the reconciliation cursor;
NULL = Nazim hasn't answered it yet), `from_user_id`/`from_name` (sender).
`handled_at IS NULL` means "Nazim hasn't even replied," **not** "the work is
done" — the two are different and that distinction is the whole problem.

### 2b. How the console body delegates
`orch-console` (Nazim) delegates by writing a **real bus row** into
**`agent_messages`**. The canonical path is already built:

`POST /api/assign` (`nervous_system/console/app.py` ~L1824) → shells out to
`scripts/console_assign.py`, which does exactly one INSERT:

```
INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body,
  priority, requires_response, is_test)
VALUES ('orch-console', <agent>, 'directive', <subj>, <body>, <P#>, true, false)
RETURNING id
```

`agent_messages` columns used for status: `id`, `thread_id`, `from_agent`,
`to_agent`, `message_type`, `subject`, `body`, `priority`, `requires_response`,
`responded_at`, `read_at`, `created_at`, `sub_tag`. Live movement of a
delegation is fully observable here:

| bus state | meaning |
|---|---|
| `read_at IS NULL` | lane hasn't picked it up → **pending** |
| `read_at` set, `responded_at IS NULL` | lane opened it, working → **in-progress** |
| `responded_at` set | lane replied → **delegate reported done** |

Live sample of real `orch-console` delegations (2026-08-15): directives to
`cc-fleet-health` and `cc-quality`, mix of `read_at`/`responded_at` set —
confirming the status signal is real and queryable today.

### 2c. What renders today
`nervous_system/console/app.py::_fleet_payload()` fires ~9 concurrent reads;
`db.fetch_inbox_backlog()` (`db.py::build_inbox_backlog_query`) feeds
`_drain_board()` → `drain_board` in the `/api/fleet` payload →
`static/fleet.html` "Drain board" section (`.dbody` cards). There is **also** an
existing `operator_backlog` table + `db.fetch_backlog()` ("Your asks",
migration `035_operator_backlog.sql`) that pre-dates the drain board and is still
in the payload but no longer the primary UI.

### 2d. The gap that makes a naive board stale
- `operator_backlog` (the existing "Your asks") is **manually curated** — Nazim
  hand-writes/edits rows. Its `status`/`note` are stored text. It goes stale the
  instant Nazim forgets to edit it. That is precisely the "cannot be stale"
  failure the operator is objecting to.
- `agent_messages` delegations are **live** but there is **no link** from an
  operator ask (`operator_messages.id`) to the delegated directive
  (`agent_messages.id`). So today you cannot join "what he asked" to "what the
  lane is doing about it."

## 3. Freshness mechanism — derive-live vs explicit-ledger

**Recommendation: a thin explicit ledger that stores only immutable facts, with
status DERIVED LIVE at render-time from the linked bus row.** Store the *link*,
never the *status*.

Why not the two extremes:

- **Pure derive (no new table).** Impossible cleanly: there is no link column
  joining an operator ask to its delegation, and an ask is not 1:1 with a single
  bus row — Nazim distils an ask into a directive he crafts. Nothing to join on.
- **Pure stored-status ledger** (what `operator_backlog` is today). Fails the
  hard requirement: a stored `status` is stale the moment the lane moves and
  nobody re-writes the row. This is the current bug.

**The hybrid.** A small `operator_asks` table stores three immutable things: the
ask text, the originating `operator_messages.id`, and a **`thread_id` link** to
the delegation. The mutable thing — status — is **never stored**; it is computed
on every `/api/fleet` poll from the live `agent_messages` on that thread. Because
status is recomputed from the bus each load, it structurally *cannot* be stale.
"updated Xm ago" is the age of the **real last bus movement** on the thread, not
the age of a manual edit — so staleness, if a lane genuinely goes quiet, is shown
honestly instead of hidden behind an edited timestamp.

### Linkage mechanism: `thread_id` anchoring
`agent_messages` already has `thread_id`. Extend the delegation write so the
outbound directive carries a thread anchor, and require the lane's reply to ride
the same `thread_id` (lanes already thread replies). Then:

- `console_assign.py` sets `thread_id = <token>` on the directive and writes one
  `operator_asks` row storing that same `thread_id` (+ the ask text + the source
  `operator_messages.id` if the assign was raised from a specific inbound ask).
- At render, the board finds **all** `agent_messages WHERE thread_id = <token>`,
  takes the newest, and derives status from its direction + answered-state.

This makes **"blocked on you / needs your decision"** derivable for free: it is
the state where the newest message on the thread is a reply **back to
`orch-console`** that is `requires_response = true AND responded_at IS NULL`. The
lane bounced a decision back to Nazim/the operator and nobody has answered it —
that is exactly "needs your decision," detected from live bus state, not a manual
flag.

Only `console_assign.py` writes the ledger (one extra INSERT in the same
transaction it already runs), so new state is minimal and the console DB role
stays read-only (writes go through the vetted script, like `/api/reset`).

## 4. Schema sketch — migration 044 (UNAPPLIED)

Apply pattern per CLAUDE.md decision-962: direct psycopg (`scripts/apply_*.py`),
**never** `supabase db push`. RLS/grants mirror `035_operator_backlog.sql`.

```sql
-- 044_operator_asks.sql — link an operator ask to its delegation. Status is
-- NEVER stored here; it is derived live from agent_messages at render time.
BEGIN;

CREATE TABLE IF NOT EXISTS public.operator_asks (
  id            bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  ask           text        NOT NULL,             -- the operator's ask, distilled (immutable)
  source_msg_id bigint,                           -- operator_messages.id that raised it (nullable → "asked Xm ago")
  thread_id     text,                             -- agent_messages.thread_id link (nullable → not yet delegated = on-nazim)
  delegated_to  text,                             -- denormalized to_agent for display (cheap; from the directive)
  created_at    timestamptz NOT NULL DEFAULT now(),
  closed_at     timestamptz,                      -- operator swiped "done"/confirmed; done ≠ delegate-replied
  closed_reason text                              -- 'operator_done' | 'dropped'
);

CREATE INDEX IF NOT EXISTS operator_asks_open_idx
  ON public.operator_asks (created_at) WHERE closed_at IS NULL;

ALTER TABLE public.operator_asks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS operator_asks_service_only ON public.operator_asks;
CREATE POLICY operator_asks_service_only ON public.operator_asks
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS operator_asks_console_ro ON public.operator_asks;
CREATE POLICY operator_asks_console_ro ON public.operator_asks
  FOR SELECT TO console_readonly USING (true);

REVOKE ALL ON public.operator_asks FROM anon, authenticated, PUBLIC;
GRANT ALL    ON public.operator_asks TO service_role;
GRANT SELECT ON public.operator_asks TO console_readonly;

COMMIT;
```

Companion change (not a migration): `scripts/console_assign.py` sets a
`thread_id` on its INSERT and writes the matching `operator_asks` row in the same
transaction. `/api/assign` optionally accepts a `source_msg_id` so an assign
raised from a specific inbound ask carries the "asked Xm ago" origin.

## 5. `/api/asks` — endpoint + live status derivation

Fold into `/api/fleet` as `your_asks` (one round-trip, like `drain_board`), or a
dedicated `/api/asks`. Status + freshness are computed in SQL so nothing mutable
is cached. One row per open ask, joined to the **latest** message on its thread:

```sql
WITH latest AS (
  SELECT DISTINCT ON (thread_id)
         thread_id, from_agent, to_agent, requires_response,
         responded_at, read_at, created_at
  FROM agent_messages
  WHERE thread_id IS NOT NULL AND is_test IS NOT TRUE
  ORDER BY thread_id, id DESC
)
SELECT a.id, a.ask, a.delegated_to,
       -- live-derived status, never stored:
       CASE
         WHEN a.thread_id IS NULL                                THEN 'on_nazim'      -- not yet delegated
         WHEN l.from_agent <> 'orch-console'
              AND l.to_agent = 'orch-console'
              AND l.requires_response AND l.responded_at IS NULL THEN 'needs_you'     -- lane bounced a decision back
         WHEN l.responded_at IS NOT NULL                         THEN 'delegate_done' -- lane replied (review it)
         WHEN l.read_at IS NOT NULL                              THEN 'in_progress'   -- lane opened, working
         ELSE                                                          'pending'      -- delegated, not yet picked up
       END AS status,
       -- freshness = age of the REAL last bus movement (or the ask, if undelegated):
       round(extract(epoch FROM (now() - COALESCE(l.created_at, a.created_at)))/60)::int AS updated_age_m,
       round(extract(epoch FROM (now() - a.created_at))/60)::int AS asked_age_m
FROM operator_asks a
LEFT JOIN latest l ON l.thread_id = a.thread_id
WHERE a.closed_at IS NULL
ORDER BY
  CASE  -- needs_you pinned to top, then freshest movement
    WHEN a.thread_id IS NULL THEN 1
    WHEN l.from_agent <> 'orch-console' AND l.to_agent='orch-console'
         AND l.requires_response AND l.responded_at IS NULL THEN 0
    WHEN l.responded_at IS NOT NULL THEN 2
    ELSE 3
  END,
  COALESCE(l.created_at, a.created_at) DESC;
```

Payload row shape: `{id, ask, delegated_to, status, updated_age_m, asked_age_m}`.
The client renders `status` → badge, `updated_age_m` → "updated Xm ago", and pins
`needs_you` to the top (mirrors the existing `needs-you` hero + `.bltag.wait`
styling). `delegate_done` rows invite a review swipe → `POST` sets `closed_at`
via a vetted writer script (same read-only-console pattern as
`backlog_swipe.py`), because **delegate-replied is not operator-confirmed done**.

## 6. Limitations — what genuinely can't be non-stale

Honesty section (the operator explicitly distrusts hidden staleness):

1. **Nazim-does-it-himself work is invisible.** If the console body actions an
   ask in-terminal without a bus delegation, there is no thread to observe — the
   ask sits at `on_nazim` with age = time-since-ask, even if real progress was
   made. Mitigation: route delegations through `console_assign.py`/`/api/assign`
   (it's already the drain-board path); a non-bus action should still `closed_at`
   the ask when done.
2. **`delegate_done` ≠ shipped.** A lane can stamp `responded_at` (replied)
   without the outcome truly landing in production. So the board must render this
   state as **"delegate reported done — review"**, not a hard green done; the
   operator's swipe (`closed_at`) is the only authoritative "done."
3. **`updated_age_m` reflects bus movement, not effort.** A lane grinding for an
   hour without writing a bus row shows a growing "updated 60m ago." This is a
   feature (a genuinely quiet thread *should* look quiet) but it is not a
   liveness probe of the lane process — pair it with the existing lane-live spine
   if "is the worker actually alive" is needed.
4. **Multi-lane / re-delegated asks.** One ask fanned to several lanes needs
   either one thread per ask (status = worst-of) or an ask→N-thread child table.
   v1 keeps one `thread_id` per ask (the primary delegation); note it as a known
   simplification.
5. **Scope is console-only.** Asks Musa sends to the **hub** (not Nazim) aren't
   the console's to show; the ledger is written only by `orch-console` delegations
   (`ORCH-TOPOLOGY-001` body scoping).

## 7. What the drain board becomes
Demote it, don't delete it. The per-body unread-inbox view is still useful as an
SRE glance — move it under a collapsed **"fleet chatter"** affordance below YOUR
ASKS (shown in the mockup), so the operator's primary surface is his asks and the
raw bus traffic is one tap away.
