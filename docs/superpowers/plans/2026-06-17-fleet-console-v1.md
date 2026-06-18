# Fleet Console v1 — Implementation Plan

> Execute with TDD. Spec: `docs/superpowers/specs/2026-06-17-fleet-console-v1-design.md`. cai-agreed CAI-RESP-264 with 5 binding hardenings (folded in below).

**Goal:** orchestrator-owned, read-only, server-mediated web console — live conversation feed + lane status — VPS-portable.

**Architecture:** separate FastAPI process (`nervous_system/console/`), own port behind cloudflared; connects with a SELECT-only DB role (never the service key); SSE relay off the bus; operator-auth + network layer in front; dependency-light SPA.

---

### Task 1 — Read-only DB role (`console_readonly`) [OWNER: cc-orchestrator; decision-962]
- Migration `migrations/004_console_readonly.sql`: `CREATE ROLE console_readonly LOGIN PASSWORD :pw; GRANT CONNECT; GRANT USAGE ON SCHEMA public; GRANT SELECT ON agent_messages, agents, agent_status, fleet_lanes` — and NOTHING else (no INSERT/UPDATE/DELETE, no other tables).
- Apply script (dry-run default → --apply). Dry-run output posted to cai before apply (decision-962). DSN → `.env` as `CONSOLE_DB_URL`.
- Verify: connect as the role; `SELECT` on the 4 tables works; `INSERT`/`UPDATE` on any table → permission denied; no access to client tables (donations/pos_*).

### Task 2 — Backend skeleton + auth middleware [TDD]
- `nervous_system/console/app.py` (FastAPI), `auth.py`. Token from `CONSOLE_TOKEN` env; `Authorization: Bearer` ONLY (reject token in URL/query); fail-closed 401; audit-log every request (who/when/path) to `logs/console_access.log`.
- Test: no/extra-bad header → 401; valid header → 200; token-in-query → 401; every request audited.

### Task 3 — Read-only API [TDD]
- `GET /api/messages?limit&thread&agent`, `GET /api/lanes` (agents⋈agent_status⋈fleet_lanes), `GET /healthz`. All via `CONSOLE_DB_URL` (read-only role). PII-redaction pass on message bodies (defense-in-depth, condition 4).
- Test: endpoints return shaped rows; no service key in any response; a write attempt via the role raises (proves read-only-by-construction).

### Task 4 — SSE live relay [TDD]
- `feed.py`: in-process async broadcaster; a feeder pushes new `agent_messages` to connected clients. `GET /api/stream` (SSE). Slow-consumer-safe (bounded queue, drop+resync, never block the orch).
- Test: a published row reaches a subscribed client; a stalled client doesn't block others.

### Task 5 — SPA frontend
- `static/index.html` + `app.js`: two panels — Conversation (EventSource live feed, thread/agent filters) + Lane status (poll/stream). No heavy framework. Auth via header (fetch with token).

### Task 6 — Process isolation + portability + ops
- Standalone `python -m nervous_system.console` on its own port (NOT in the orch process — a console fault can't touch coordination/#111). Own launchd plist `dev.wingmen.fleet-console.plist`. `Dockerfile` for VPS lift. Config: `CONSOLE_PORT/CONSOLE_TOKEN/CONSOLE_DB_URL` from env.
- Network layer in front: Cloudflare Access (email-gated) on the console hostname — documented in the plist/README (ops step, condition 1).

### Task 7 — Final review + STATUS/digest
- cc-reviewer pass (security dimension: auth, read-only-role, PII, isolation). Update STATUS.md + session_digest.

---
**Out of v1:** health + ledger panels; ALL writes/3-way (gated on #111 + authenticated-musa). **Money/PII never on this surface beyond the bus's coordination content.**
