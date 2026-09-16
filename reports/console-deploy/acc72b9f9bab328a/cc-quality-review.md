# cc-quality console deploy-gate review — SECURITY AUDIT (fc-v64: bearer-on-armed + KEYS/model chips)

- **Content hash:** `acc72b9f9bab328a` (recomputed live — matches requester; **no drift** vs commit `dcbe102`)
- **Commit:** `dcbe102` — fc-v64: KEYS counts singletons w/ split, per-row key+model chips (op#20715/20716) + **bearer required on armed endpoints even from allowlisted IPs (CAI-RESP-1434)**
- **Branch:** `fable/substrate-safe-fixes`  ·  **Build:** fc-v63 → fc-v64
- **Reviewer:** cc-quality (Opus 4.8 — `.quality_model=claude-opus-4-8` confirmed; CAI-1170 carve-out)
- **Date:** 2026-09-16  ·  **Requester:** orch-console (bus #40769)  ·  operator-blocking (R4 restore gated on this review)

## Verdict: **PASS** — the bearer-on-armed hardening closes the co-located exposure I found; no unguarded co-located/allowlisted path to a live money/irreversible action.

This build ships the exact fix for the exposure I surfaced on fc-v63 (CAI-RESP-1434): the two armed endpoints now require a second factor beyond the IP allowlist. Reviewed the full guard set; three non-blocking notes below.

## (1) Bearer-on-armed — the core hardening (VERIFIED)
`auth.check_armed_bearer(headers)`: reads `CONSOLE_ARMED_BEARER` at **request time**; `hmac.compare_digest` (constant-time); accepts the key from `X-Armed-Bearer` **or** `Authorization: Bearer`; returns `unconfigured`/`missing`/`mismatch`/`bearer`.
`app.py do_POST` ordering (confirmed at source): 404-allowlist → **`_authed()` (401)** → **`if path in _ARMED_BEARER_PATHS: check_armed_bearer`** → *then* handler dispatch. So for **both** `/api/apply-armed` **and** `/api/reset` (`_ARMED_BEARER_PATHS`), for **every** caller including an allowlisted/co-located peer: **unconfigured → 503 (fail-closed)**, **missing/mismatch → 401 + audit**, evaluated **before** the R4 flag and body parse (an unkeyed caller learns nothing past 401/503). This is exactly the mitigation CAI-RESP-1434 required.
- `tests/console/test_armed_bearer.py` → **18 passed**, incl. the **handler-not-called mock** (proves a no-bearer POST never reaches `_handle_apply_armed`/`_handle_reset`).
- **Live empirical check — COMPLETED post-deploy (2026-09-16 15:3xZ, fc-v64 live):** deployed sha `b794fb5` (cherry-picked onto fable after an SRE branch mix-up) — I confirmed its gated-file tree is **byte-identical to the reviewed `dcbe102`** (diff empty; content hash unchanged `acc72b9f9bab328a`), so this PASS applies to what is actually live. From a **co-located process on the Mini (an allowlisted peer IP)** with **no bearer**: `POST /api/apply-armed {}` → **401 "armed bearer required"** and `POST /api/reset {}` → **401**; with a **wrong** `X-Armed-Bearer` → **401** on both (bearer is validated, not presence-checked). **The co-located exposure I found on fc-v63 is CLOSED in production** — the identical probe that reached the endpoint past auth on v63 is now blocked at 401. `CONSOLE_ARMED_BEARER` is provisioned (orch-console; never printed), R4 flag = 1, so R4-on arrived with the hardening active as designed.

## (2) hosted_server — armed-key forwarding scoped, no fp leak (VERIFIED)
`_ARMED_FORWARD_PATHS = ("/api/reset", "/api/apply-armed")`: the operator's `X-Armed-Bearer` is extracted and forwarded upstream **only** for those two routes (double-guarded — the dispatch passes it only for those paths, and `_proxy` re-checks `if armed and path in _ARMED_FORWARD_PATHS`). lane-boot/lane-down/dry-run/assign/ask-close **never** receive it. The upstream `Authorization: Bearer <CONSOLE_UPSTREAM_TOKEN>` is unchanged. `_strip_fps` is unchanged; the new payload keys `model`/`model_src`/`pool` are labels, not fingerprints. No new proxy reach (still `session → agent_status.host → env upstream map`; unmapped → 503).

**fp-leak proof (production `_strip_fps` over the v64 render payloads):** `_tt.json` (hosted-stripped) — zero known-fp, zero 12-hex runs, zero residual `*_fp` keys. `_fleet.json` is the **local** payload (carries `auth_fp` by design — internal surface); after strip its only 12-hex runs are `ctx_session_id`/`ctx_current_session_id` **session UUIDs** (opaque identifiers, pre-existing fc-v56, authorize nothing — **not** token fingerprints), verified individually. No fp leak on the hosted surface.

## (3) Model resolver — read-only, no token leak (VERIFIED)
`_proc_models()` reads `panes.token_ground_truth(include_remote=False)` (Mini-local rows only), cached ~20s, and any failure returns the last-good map (or `{}`) — **never** an exception into `/api/fleet`. Precedence proc → boot-string regex (`session-launch model=<m>`) → `fleet_lanes.model` → None. No new shell beyond the existing `ps eww`/tmux reads; the `model` field only ever holds a model-name string (comment + code: "Never puts auth_fp in the model field") — confirmed no raw token/fp in `model`/`model_src`.

## (4) UI armed-key handling (VERIFIED)
`fc.armedKey` lives in `localStorage` **only** (`armedKey()` wrapped in try/catch), surfaced as `X-Armed-Bearer` via `armedHeaders()` and attached **only** to the two armed POSTs (Recycle/reset L981, apply L1036). No `console.log`, no URL/query-string use. `node fleet_keyroll` → **10 passed**, incl. "armedHeaders reads localStorage('fc.armedKey') ONLY and rides X-Armed-Bearer".

## Co-located / allowlisted → destructive-action enumeration
- **apply-armed + reset** (billing / disruptive-context-wipe): **now require `CONSOLE_ARMED_BEARER`** even co-located → 401/503 without it → **the exposure I found on fc-v63 is CLOSED.**
- **lane-boot / lane-down:** still IP-allowlist + typed `confirm==session` (no armed-bearer) — acceptable: reversible (winddown gates fail closed, never `--force`), non-billing.
- **assign / ask-close:** DB-only reversible bus rows.
- **set-pointer / add-token / switch-*:** Mini keeps existing guards; hosted = 403. A staged pointer is **inert until a now-bearer-gated apply fires**, so no live billing effect is reachable without the armed bearer.
→ **No unguarded co-located/allowlisted path to a live money/irreversible action.**

## Verification performed
- `tests/console/test_armed_bearer.py` **18**, `test_hosted_server.py + test_lane_actions.py + test_pools.py` **97**, full `tests/console` **299** (matches claim), `node fleet_keyroll` **10** — all green. Version-sync sw=fleet=irsyad=lanes = **fc-v64**. Content-hash re-derived == `acc72b9f9bab328a`, zero drift vs `dcbe102`.

## Deploy-ordering note (safe)
The R4 restore (op#20734) is gated behind **this** deploy, so R4 comes back ON only via the same redeploy that loads v64's armed-bearer gate — **R4-on arrives with the hardening active**. Precondition: `CONSOLE_ARMED_BEARER` must be provisioned in the Mini `.env` and the operator's phone armed-key set; if unconfigured, the gate **fail-closes to 503** (armed ops safely disabled, never exposed).

## Notes (all NON-BLOCKING)
- **[LOW]** No minimum-length check on `CONSOLE_ARMED_BEARER` (the hosted `_authed` requires `len≥24`; the armed bearer accepts any non-empty value). Empty is correctly fail-closed, but a very short key would be accepted-as-configured — recommend min-length parity (`≥24`). Strength-of-configured-key only, not fail-open.
- **[OBS]** `apply-dry-run` is intentionally exempt from the armed-bearer (preview, non-mutating) — fine.
- **[OBS]** Cadence: fc-v64 is the 5th same-day console bump (advisory, not a gate).
- **[GOVERNANCE]** R4 being back ON is op#20734 (Musa, operator-domain) overriding CAI-RESP-1434 — recorded in the fc-v63 review addendum. **cai reconciled this as CAI-RESP-1435:** verified op#20734 at source, accepted the override, and withdrew the enable-gate as jurisdictional overreach (console/reversible/internal = operator-domain). So bearer-on-armed here is operator-authorized **hardening**, not a re-enable precondition — which is how this review treats it. This review clears the **code** (including the hardening); the enable is the operator's recorded decision, not my gate.

I have **not** deployed — orch-console runs `deploy_console.sh`.

— cc-quality
