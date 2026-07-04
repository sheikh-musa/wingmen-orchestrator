# P5 — Autonomous-window blast radius + kill-switch (CAI-RESP-361)

**Window:** opens 2026-07-02 21:15:58Z (CAI-RESP-360 close; 014 grant 21:05:57Z).
**Executor:** cc-orchestrator, dangerous mode, tmux `orch` on the interim MacBook host.
**Posted to cai on the CAI-RESP-361 thread AT window open — cai's check of this doc is part of the grant.**

## Scope (exhaustive — nothing else is in-window)
1. Provision Supabase project `wingmen-personal` (org default, region ap-southeast-1) via Mgmt API.
2. Apply `migrations/drafts/life_graph_p1.sql` to wingmen-personal — RE-HASH first, abort unless
   sha256 == 63c41d01216af41fa31314dce855abe51deeac42b7b50b39073d2d2d92509262; NNN assigned at apply (334).
   **F-a companion (cc-ihsanos ACK #5121, keeps the frozen sha intact — apply-step, not draft edit):**
   immediately after apply, in the same session: `GRANT EXECUTE ON FUNCTION life_recall, life_neighbours,
   life_purge_entity, life_isolation_audit TO <operator-context service role>;` then ASSERT no other role
   holds EXECUTE (aclexplode over pg_proc for the four fns) — F1 becomes encoded invariant, not comment
   convention. F-b noted for later: any future commitments-read fn MUST reuse life_slice_can_see.
3. Apply 012-equivalent mamadah schema to wingmen-personal; pg_dump mamadah_* from monolith →
   RESTORE-TEST into a fresh ephemeral DB + row-count parity (P6) → restore into wingmen-personal →
   row-count parity again → repoint DSN consumers → 7-day frozen monolith copy → (drop is D+7, NOT in-window).
4. Apply `migrations/014_bot_channels_ingest.sql` to the MONOLITH — RE-HASH first, abort unless
   sha256 == 1dea9a559a44a3d3316e2141f136db0ffa1965caf3b62f9e3601ac5612133663; after cai's 21:05Z grant message exists.
5. P1 channel cutover: populate operator-orch + cai-channel allowlists (MUSA_TELEGRAM_ID from .env),
   enable rows, load dev.wingmen.{ingest,tg-out} launchd units, verify a live round-trip, THEN
   retire dev.wingmen.tg-bridge (old bridge off only after the new path is proven).

## Blast-radius env (what the window's processes may hold)
- `DATABASE_URL` (monolith/substrate) — 014 apply + mamadah dump + bot_channels ops.
- `PERSONAL_DATABASE_URL` (new, wingmen-personal) — life_graph + mamadah restore. Operator-placed
  or Mgmt-API-derived; service key never leaves .env.
- `SUPABASE_ACCESS_TOKEN` — project provisioning only.
- **EXCLUDED (hard):** `IHSANOS_PROD_DATABASE_URL`, `GOUMLYNE_DATABASE_URL`, all client silo DSNs.
  Execution scripts run with an explicit env allowlist (constructed env, not inherited .env) so the
  excluded DSNs are ABSENT from the process, not merely unused.

## Kill-switch (any of, no coordination needed)
1. `tmux kill-session -t orch` — halts the executor immediately (bridge falls back to headless-log).
2. `launchctl bootout gui/501/dev.wingmen.ingest` (+ tg-out) — halts the new daemons; old tg-bridge
   is untouched until step 5, so operator comms never depend on in-window components.
3. Supabase dashboard → pause project `wingmen-personal` — freezes the new DB wholesale.
4. Telegram: message the bridge; every step logs to the CAI-RESP-361 thread before/after, so the
   last durable log line IS the resume/rollback point.

## Abort discipline
- Any sha mismatch, row-count mismatch, restore-test failure, or unexpected error class → STOP,
  post state to the thread, no improvisation. Partial-apply rollback = drop the affected objects in
  wingmen-personal (it is new/empty — dropping there can never touch existing data) or, for 014,
  the migration is transactional (single file, single psycopg apply).
- The mamadah monolith copy is NEVER dropped in-window (D+7, separate operator-visible step).
