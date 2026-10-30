# MAP.md — wingmen-orchestrator

Per op#35990: a map so any agent can find code in one lookup. Every path
below was checked against the real repo on 2026-10-30. If a CI check is
failing on this file, a path has moved — fix the path, don't delete the
line.

## 1. What this app is

An always-on Python/Claude-Code fleet that manages builds, deploys, and
jobs across client repos, driven by Telegram commands and a Supabase-backed
agent bus. It is not one program: it is a Claude Code agent session (the
"hub") plus dozens of daemons/watchdogs in `nervous_system/` and `scripts/`
that all read and write one shared Postgres substrate. CLAUDE.md's "Key
Files" list names `wingmen_orch.py` as the entry point — that file does not
exist in this repo; the real entry point is `scripts/boot_orch.sh`, which
boots a tmux Claude Code session, not a standalone Python process.

## 2. Top-level layout

- `scripts/` — operational scripts: bus senders, asks triage, lane
  management, watchdogs-as-scripts, migration apply.
- `scripts/lib/` — shared library code (identity resolution, leases,
  handoff helpers). See section 5 — never hand-roll what's in here.
- `scripts/hooks/` — PreToolUse/PostToolUse guards wired via
  `.claude/settings.json`. See section 6.
- `scripts/git-hooks/` — tracked git hooks (`core.hooksPath`), e.g.
  `post-checkout` pages on a live-checkout branch drift.
- `nervous_system/` — long-running daemons: Telegram ingest/out, the
  operator ask ledger, bus-notify, repo-context writer, watchdogs.
- `migrations/` — direct-psycopg-applied SQL, numbered 001–132 (125 files
  at HEAD; some numbers retired/superseded), plus `drafts/` and `infra/`.
- `launchd/` — secondary watchdog/audit daemon plists.
- `ops/launchd/` — the core daemon plists: ingest, tg-out, the hub itself
  (`dev.wingmen.cc-orch.plist`), fleet-console, fleet-health, lane watchdog.
- `docs/` — runbooks, `docs/data-store-registry.md` (the LAYER-VOCAB-001
  store registry), `docs/superpowers/plans/` (dated plan docs).
- `tests/` — pytest suite: `tests/migrations/` (ephemeral-pg17 wet-proofs),
  `tests/console/`, `tests/exec_broker/`, `tests/fire_drills/`, plus
  hundreds of top-level `test_*.py` files.
- `deploy/` — console/static deploy artifacts and gates.
- `reports/` — generated audit/review/handoff reports; not hand-authored,
  don't edit by hand.
- `config/`, `data/`, `state/`, `logs/`, `manifests/`, `prompts/`,
  `templates/`, `skills/`, `supabase/` — supporting config and runtime state.
- `legacy/` — retired code kept for reference only (e.g. the pre-cutover
  `ingest.py`, old bot managers). Do not build on it or extend it.
- Root docs: `CLAUDE.md` (fleet doctrine, read first), `AGENTS.md`,
  `CTO_PRINCIPLES.md`, `PIPELINE_CONSTRAINTS.md`, `REPOS.json` (managed-repo
  registry), `schema.sql`, `STATUS.md`.

## 3. Main flows

**Operator Telegram message → reply.**
Telegram → `nervous_system/ingest.py` (launchd
`ops/launchd/dev.wingmen.ingest.plist`) → `operator_messages` row +
`nervous_system/operator_log.py` (unread tracking, `operator_asks` capture)
→ agent reads via `operator_log.unprocessed()` → agent replies via
`scripts/tg_send.sh` → `nervous_system/tg_out.py` (launchd
`ops/launchd/dev.wingmen.tg-out.plist`) drains the outbound queue →
Telegram. Agent then stamps `operator_log.mark_handled([ids])`.

**One lane posts to the bus, another lane acts on it.**
`scripts/bus_send.py` (the one sanctioned writer; sets `priority`,
`from_agent`, `requires_response`) → row lands in `agent_messages` →
recipient polls its own inbox via `scripts/lib/my_inbox.py` (checks BOTH
its base and instance agent id) → recipient acts → replies via
`scripts/bus_send.py --reply-to <id>`.

**A migration gets applied.**
Author writes `migrations/NNN_description.sql` (with `-- assert:` lines for
any REVOKE/GRANT) → `scripts/apply_migration.py --silo <project-ref> --gate
<bus-id>` (direct psycopg; the CLI's `supabase db push` shadow path is
local-dev only, never a production store, per CLAUDE.md) →
`scripts/check_additive_migration.py` / `scripts/hooks/ddl_detect.py` gate
the SQL before/after apply.

**An operator ask gets triaged.**
Ask captured into `operator_asks` (via `nervous_system/operator_log.py` or
`scripts/asks_open.py`) → owning agent runs `scripts/asks_triage.py <id>
{ask|not|done}` to set the `triage_state` axis → a DB trigger (migration
114's `work_item_mirror_ask_trg`) mirrors the row into `work_item`,
deriving its SSOT state → `scripts/asks_close.py <id> {confirm|drop}` is
the separate `closed_at` lifecycle axis.

**Fleet console deploy.**
`scripts/deploy_console.sh` is the only sanctioned path — a fail-closed
gate checking version-string parity across `sw.js`/`fleet.js`/`lanes.html`,
a green `tests/console/test_app.py`, rendered-page PNGs, and a committed
`reports/console-deploy/<hash>/cc-quality-review.md` — before it runs
`launchctl kickstart`.

## 4. Data

Store: Supabase project ref **`tscuymavysscrvoberrr`** — the "orchestrator
substrate" (see `docs/data-store-registry.md`, LAYER-VOCAB-001: always name
this ref, never say "production" bare). This is a DIFFERENT store from any
client silo (e.g. the ihsanos multi-tenant DB or an irsyad silo) — this repo
never writes client rows, only fleet-operational rows.

Main tables and where they're read/written:
- `agent_messages` — the bus. Write: `scripts/bus_send.py` (sanctioned;
  never a hand-written INSERT). Read: `scripts/lib/my_inbox.py`.
- `operator_asks` / `operator_messages` — the operator-facing ask/reply
  ledger. Write/read: `nervous_system/operator_log.py`,
  `scripts/asks_open.py` / `asks_triage.py` / `asks_close.py`.
- `work_item` — the SSOT mirror of asks/bus/commitments (migration 114).
  Written ONLY by DB triggers off the source tables above; never insert or
  update it directly.
- `held_commitments` — dated promises/commitments owed to the operator.
  Read/written by `scripts/fleet_health.py`,
  `scripts/priority_sla_watchdog.py`.
- `governance_floor` — standing rules the reconciler enforces (migration
  131). Read by `scripts/hooks/consult_governance_floor_guard.py`.
- `repo_context` — per-repo boot context. Written by
  `nervous_system/repo_context_writer.py`, watched by
  `scripts/repo_context_watchdog.py`. `architecture_summary` is the column
  Memory 2.0 item 2 (section 3 of that plan) will populate from this repo's
  own MAP.md plus every other repo's MAP.md.
- `boot_briefing` — a view read at agent-session boot for fast context.
- `fleet_lanes` — the lane registry. Read/written by `scripts/lanes.sh`,
  `scripts/fleet_health.py`.
- `bot_channels` — Telegram channel registry (migration 014). Read by
  `nervous_system/ingest.py`.

## 5. Shared code — do not duplicate

- `scripts/lib/my_inbox.py` — the one sanctioned inbox read (checks both
  base and instance agent id; a hand-rolled single-id query silently misses
  instance-addressed mail).
- `scripts/bus_send.py` — the one sanctioned bus write.
- `scripts/apply_migration.py` — the one sanctioned migration-apply path.
- `scripts/lib/agent_identity.py`, `scripts/lib/auto_agent_id.py` — the
  `resolve_agent_id` identity-resolution pattern; every script that stamps
  `created_by`/`triaged_by`/`from_agent` should resolve identity through
  these, not read an env var directly.
- `scripts/lib/orch_lease.py`, `scripts/lib/fleet_health_lease.py` — the
  two singleton leases (`orch_lease`, `fleet_health_lease`) gating the pens
  described in CLAUDE.md's ORCH-TOPOLOGY-001.
- `scripts/lib/handoff_find.py`, `scripts/lib/handoff_verify.py` — the
  handoff-file convention; use these to locate/validate a lane's handoff
  rather than guessing a path under `reports/lane-handoffs/`.

## 6. Hazards / do-not-touch

- `scripts/hooks/secrets_transcript_guard.py` — blocks secret literals and
  raw DDL against production silos in any Bash call, fleet-wide; this is
  why direct SQL against a listed production ref gets refused even in a
  one-off diagnostic query — route DDL through `apply_migration.py --gate`.
- `scripts/hooks/console_irsyad_guard.py` — refuses any console-body tool
  call touching the irsyad silo/write DSN/ihsanos checkouts (keyed on
  `ORCH_BODY_ROLE`/`CC_BASE_AGENT_ID`). Reason: console must never execute
  irsyad work directly — see CLAUDE.md's console/irsyad separation rule.
- `scripts/hooks/ddl_detect.py` — the shared DDL-shape detector backing
  several gates. Known gap: a quoted-identifier/E-escape blindspot
  (pre-existing on deployed base, tracked, PR #569) — don't rely on it as
  the only DDL check for a hand-reviewed migration.
- `scripts/git-hooks/post-checkout` + `scripts/live_checkout_branch_watchdog.py`
  — the live checkout at `~/wingmen/orchestrator` runs launchd jobs every
  few minutes; checking out a branch there (instead of a worktree) pages
  orch-console immediately and the watchdog pages again if it drifts off
  `fable/substrate-safe-fixes` for over 2 minutes. Always `git worktree add`
  for PR work, never checkout in the live dir.
- `legacy/` — retired code (e.g. the pre-cutover `ingest.py`, old bot
  managers). Do not extend or import from it; it is kept for reference only
  and is not wired into any running daemon.
- `work_item` table — never INSERT/UPDATE it by hand (section 4); it is a
  trigger-maintained mirror and a direct write will silently diverge from
  its source-of-truth row.

## 7. Run, test, deploy

- **Run**: the hub session boots via `scripts/boot_orch.sh` (launchd
  `ops/launchd/dev.wingmen.cc-orch.plist`). Restart ONLY via
  `scripts/restart_orch.sh` — never `nohup` (CLAUDE.md rule; the script
  actively detects and refuses a HUP-ignored invocation).
- **Test**: `pytest.ini` sets `testpaths = tests`. CI is
  `.github/workflows/ci.yml`, running `python -m pytest tests/ -v` on a
  self-hosted `cubeasht` runner against an ephemeral local pg17 cluster —
  never a repo secret or a production DSN.
- **Deploy**: the fleet console deploys ONLY via `scripts/deploy_console.sh`
  (fail-closed multi-gate, section 3). Migrations deploy via
  `scripts/apply_migration.py --gate <bus-id>` (section 3/4), never
  `supabase db push` against any production store.

## Path manifest (checked by `tests/test_map_md_paths.py` — keep in sync)

One real path per line, repo-root relative. `.env` is deliberately excluded
(never committed, never present in CI). If this test is red, a path below
moved or was deleted — fix the path (here and in prose above), don't just
delete the line.

```
nervous_system
scripts
scripts/lib
scripts/hooks
scripts/git-hooks
scripts/git-hooks/post-checkout
migrations
launchd
ops/launchd
ops/launchd/dev.wingmen.cc-orch.plist
ops/launchd/dev.wingmen.ingest.plist
ops/launchd/dev.wingmen.tg-out.plist
docs
docs/data-store-registry.md
docs/superpowers/plans
tests
tests/migrations
tests/console
tests/console/test_app.py
tests/exec_broker
tests/fire_drills
deploy
reports
reports/lane-handoffs
config
data
state
manifests
prompts
templates
skills
supabase
legacy
CLAUDE.md
AGENTS.md
CTO_PRINCIPLES.md
PIPELINE_CONSTRAINTS.md
REPOS.json
schema.sql
STATUS.md
scripts/boot_orch.sh
scripts/restart_orch.sh
scripts/tg_send.sh
scripts/bus_send.py
scripts/apply_migration.py
scripts/check_additive_migration.py
scripts/deploy_console.sh
scripts/asks_open.py
scripts/asks_triage.py
scripts/asks_close.py
scripts/lanes.sh
scripts/fleet_health.py
scripts/priority_sla_watchdog.py
scripts/live_checkout_branch_watchdog.py
scripts/repo_context_watchdog.py
scripts/lib/my_inbox.py
scripts/lib/agent_identity.py
scripts/lib/auto_agent_id.py
scripts/lib/orch_lease.py
scripts/lib/fleet_health_lease.py
scripts/lib/handoff_find.py
scripts/lib/handoff_verify.py
scripts/hooks/ddl_detect.py
scripts/hooks/secrets_transcript_guard.py
scripts/hooks/console_irsyad_guard.py
scripts/hooks/consult_governance_floor_guard.py
nervous_system/ingest.py
nervous_system/tg_out.py
nervous_system/operator_log.py
nervous_system/repo_context_writer.py
pytest.ini
.github/workflows/ci.yml
.claude/settings.json
```
