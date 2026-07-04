# Fleet Console v1 — Design Spec (2026-06-17)

**Goal:** A single orchestrator-owned, read-only web surface where the operator watches the whole fleet live — the conversation bus + lane statuses — in one place. v1 of the dedicated console cai ruled into being (CAI-RESP-261).

**Status:** operator-approved design (2026-06-17). Pending cai agreement before build (consensus gate).

## Constraints (locked)
- **Orchestrator-OWNED** surface — not a client product (CAI-RESP-261-A). The ihsanos TASK-039 embed is transitional only.
- **Server-mediated** reads — the service-role key stays server-side; the browser never gets a key on the RLS-less bus (CAI-RESP-261-B). No raw client-side Supabase Realtime subscription.
- **Read-only v1.** All write / interaction (the 3-way) is out of scope, gated separately on #111 + an authenticated-`musa` SSO (CAI-RESP-261-C/D).
- **Portable / VPS-ready.** Everything currently runs on the Mac Mini; the fleet will migrate to a VPS. The console must move *with the orchestrator* as one unit — no Mac-specific deps, config-driven, containerizable.

## Architecture
```
browser ──HTTPS/WSS──▶ cloudflared tunnel ──▶ console backend (Python/FastAPI, persistent)
                                                • holds Supabase service key (env, never to browser)
                                                • feeds off the orch's Supabase Realtime subscription
                                                • SSE stream of live bus deltas + read-only JSON API
                                                • operator-token auth middleware
                                                • serves a dependency-light SPA (HTML + EventSource JS)
```
Why server-mediated realtime needs *this* shape: a persistent process must hold the Realtime subscription and relay to browsers; serverless (Vercel) cannot. The orchestrator is already that persistent, Realtime-subscribed process — the console rides it and migrates with it.

## Components (independent module boundaries; each ships incrementally)
- `nervous_system/console/app.py` — FastAPI app: routes + static mount. Runnable standalone via `python -m nervous_system.console` (own port, behind the tunnel). Portable: host/port/DSN/token all from env.
- `nervous_system/console/auth.py` — operator-token middleware (bearer / basic-auth over HTTPS). Rejects unauthenticated requests. v1 read-only gate.
- `nervous_system/console/feed.py` — in-process SSE broadcaster + a bus feeder that publishes new `agent_messages` rows to connected clients (taps the existing Realtime subscription or a lightweight dedicated listener).
- `nervous_system/console/static/` — `index.html` + `app.js`: two panels, `EventSource` for the live feed; no heavy framework.

## Read-only API (all server-side, service key)
- `GET /api/messages?limit&thread&agent` — recent bus rows (initial load + filters).
- `GET /api/lanes` — `agents` ⋈ `agent_status` ⋈ `fleet_lanes`: id, status, current_task, heartbeat age, desired_state vs derived-live.
- `GET /api/stream` (SSE) — live deltas: new bus messages (sub-second), lane-status changes.
- `GET /healthz` — liveness.

## Panels (v1)
1. **Conversation (live).** The `agent_messages` feed — every cc-orchestrator/cai/cc-ihsanos/cc-cosem exchange, newest-first, sub-second via SSE. Filters: by `thread_id`, by `from`/`to` agent. Shows from→to, type, priority, requires_response, subject/body, time. Read-only.
2. **Lane status.** Live rail: each agent's status, current_task, heartbeat age (color by freshness), desired_state vs live. Sourced from `agents`/`agent_status`/`fleet_lanes`.

## Auth (v1)
Operator-token (long random secret in orch `.env`) checked by `auth.py` on every route, over the HTTPS tunnel. Sufficient for read-only, but **mandatory** (the bus is multi-client-sensitive). The future write/3-way path requires the genuine authenticated-`musa` SSO from CAI-RESP-261-D — explicitly NOT in v1.

## Out of scope (growth path)
- Health dashboard panel + decision/work ledger panel (the other two "unified" panels).
- All writes / compose / the live 3-way (gated on #111 + authenticated-`musa`).
- The `sendWingmenMessage`-style write path (stays in the gated future).

## Portability / migration (Mac Mini → VPS)
- No absolute Mac paths; all config via env (`CONSOLE_PORT`, `CONSOLE_TOKEN`, `DATABASE_URL`).
- Runnable as a standalone process (launchd today, systemd/container on the VPS) — moves with the orchestrator.
- Tunnel-agnostic: cloudflared now, nginx/caddy on the VPS later.
- Add a `Dockerfile` for the console module so the VPS migration is a lift-and-shift.

## cai hardenings — BINDING (CAI-RESP-264)
Agreed it satisfies 261; build with these folded in (the lone static token is too thin for an internet-exposed window into every client's bus):
1. **Network/identity layer IN FRONT of the token** — Cloudflare Access (SSO/email-gated, native to cloudflared) OR IP allowlist OR mTLS. The bus must not be reachable by secret-possession alone.
2. **Read-only BY CONSTRUCTION** — connect with a **SELECT-only Postgres role** (`console_readonly`), NOT the read-write service key. A bug/injection structurally cannot write. Role creation via decision-962 dry-run→apply. App reads `CONSOLE_DB_URL` (the read-only role's DSN); never the service key.
3. **Token hygiene** — rotatable, `Authorization` header ONLY (never URL/query), fail-closed 401, **audit-log every access** (who/when/path).
4. **No raw client PII on the bus** — enforce the invariant (agents never put NRIC/full donor PII in `agent_messages` bodies; the bus is coordination, not data). Redact in-view as defense-in-depth if not guaranteed.
5. **Process isolation** — run as a SEPARATE process/port; a console fault (slow SSE consumer, client storm, crash) must NEVER degrade the core orch coordination or the #111 wake path. Watching surface isolated from the doing surface.

## Verification
- Auth: unauthenticated request → 401; valid token → 200.
- API: `/api/messages` returns recent bus rows server-side (no key in any response/asset); `/api/lanes` reflects live agent_status.
- SSE: a new `agent_messages` INSERT appears in a connected browser sub-second; a lane status flip appears live.
- Portability: runs from env config with zero Mac-specific paths; `python -m nervous_system.console` boots clean.

## Update (2026-07-03) — hardening #1 implemented: Tailscale IP-allowlist

Implements the "IP allowlist" option from the cai hardenings list above (§ "Network/identity layer IN FRONT of the token"), **replacing** the static `CONSOLE_TOKEN` password rather than adding to it:

- Primary gate is now `CONSOLE_ALLOWED_IPS` (comma-separated Tailscale IPs), checked against the real TCP peer address (`self.client_address[0]`) — **never** `X-Forwarded-For`, which is client-supplied and spoofable. Fails closed on an empty allowlist.
- `CONSOLE_BREAKGLASS_TOKEN` replaces `CONSOLE_TOKEN` as a **dormant** recovery path only (unset by default), guarding against a Tailscale re-registration locking the operator out with no recovery — the seeded allowlist itself already covers this by including the operator's phone *and* both Macs, not phone-only.
- Every breakglass use is logged loudly and separately from normal per-request audit (`auth.py`'s `_log_breakglass`).
- See `nervous_system/console/auth.py` for the implementation and `tests/console/test_auth.py` for the behavioral spec (including a regression test proving a spoofed `X-Forwarded-For` cannot substitute for the real peer IP).

**Deployment topology consequence (found by cc-reviewer-4, msg 5920/5919, 2026-07-03):** this option makes the original architecture diagram above (§ "Architecture" — `browser → cloudflared tunnel → console backend`) **void**. A locally-terminating tunnel/proxy makes every request's TCP peer `127.0.0.1`, which either locks the operator out entirely or, if `127.0.0.1` is added to the allowlist to compensate, authorizes every tunnel client — the allowlist becomes theater either way, since peer-IP identity and a local reverse hop are fundamentally incompatible (the code deliberately refuses to trust `X-Forwarded-For` to fix this, since that reopens the spoofing hole this whole change exists to close). **There is no tunnel in this deployment** — `CONSOLE_HOST` must be `auto`/`tailscale` (binds the host's own Tailscale IP directly; see `app.py`'s `_resolve_host`), and devices reach the console directly over the tailnet. Cloudflare Access (the other hardening-#1 option from the list above) is the one to reach for if a public/non-tailnet reachability requirement ever comes back — it composes with a real reverse proxy in a way peer-IP allowlisting cannot.
