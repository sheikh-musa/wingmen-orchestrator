# cc-quality console deploy-gate review — SECURITY AUDIT (fc-v65: per-project governance write path)

- **Content hash:** `f13640b4adff7664` (recomputed live — matches; **no drift** vs commit `dc79ed9e`)
- **Commit:** `dc79ed9e` — fc-v65: per-project governance toggles (Stage E, op#20702) + `CONSOLE_ARMED_BEARER` min-length ≥24
- **Branch:** `fable/substrate-safe-fixes`  ·  **Build:** fc-v64 → fc-v65  ·  **Domain:** operator (Musa op#20702/20734, no cai)
- **Reviewer:** cc-quality (Opus 4.8 — `.quality_model=claude-opus-4-8`; CAI-1170 carve-out)
- **Date:** 2026-09-17  ·  **Requester:** orch-console (#40980)

## Verdict: **PASS** — the governance write path is well-guarded; no toggle-without-audit path, no column injection, no peer-ip spoof, no XSS.

Reviewed hardest on the money-clearance flip, the writable-column allowlist, audit-row integrity, peer-ip spoofability, hosted-proxy bearer forwarding, and XSS. Findings are non-blocking observations.

## The write rail (`governance.py::apply_set`) — the crux
- **Never INSERT/DELETE:** `SELECT … FOR UPDATE` then `UPDATE … WHERE project=%s`; a missing row → `NotFound` (404), never an INSERT (new project = migration decision); no DELETE (and the DB's `trg_project_governance_forbid_delete` refuses one anyway — verified live).
- **No toggle without an audit row (the guarantee):** after the UPDATE (which sets the toggle + `updated_by` + `reason` + `updated_at=now()`), the AFTER trigger appends `project_governance_audit`; `apply_set` then, **in the same transaction**, `SELECT … WHERE project=%s AND changed_at = now()` (transaction-fixed `now()`, so it matches exactly this UPDATE's audit row, never an older one) and requires `changed_by == updated_by AND reason == reason`; otherwise **`conn.rollback()` + raise**. A governance change without its audit row can never commit. **Verified on substrate:** both `trg_project_governance_audit` and `trg_project_governance_forbid_delete` are live.
- **Writable-column allowlist:** the SQL `SET {field} = %s` interpolates `{field}` **only** from the hardcoded 4-item `FIELDS` (`cai_enabled`, `money_clearance_enabled`, `operators`, `channels`), enforced by `validate_write`/`normalize_value` **and** the CLI `--field choices=FIELDS`; the value is always a bound `%s` (Jsonb for arrays). No column name is ever taken from input → no injection.
- **Structural validators (fail-closed, in code not UI):** operators must be **positive** Telegram user ids; channels **negative** group ids; `reason` mandatory; `money_clearance_enabled→true` requires the typed `MONEY_ACK_PHRASE`; bounds (16 ops/16 channels/500-char reason). `updated_by` is server-stamped, truncated 120, never from the request.
- **DSN never leaks:** `_scrub` strips DSN/`password=` from any driver-error message before it reaches stderr → app.py → browser.
- **Isolation verified live:** `console_readonly` has **no SELECT** on `project_governance` (subprocess pattern is necessary; the console's own DB role can't touch the table); `authenticated` has SELECT (mig 063). The subprocess loads the writable env itself; the console process never holds the write DSN.

## app.py gate order (`/api/governance-set`)
`do_POST`: 404-allowlist → `_authed()` (IP-allowlist) → **`check_armed_bearer`** (path ∈ `_ARMED_BEARER_PATHS`) → handler. Inside the handler: (1) `CONSOLE_R4_ENABLED` else 503 → (2) body parse + `PROJECT_RE` + `field ∈ FIELDS` else 400 → (3) `confirm==project` else 400 → (4) `validate_write` (reason + money-ack) else 400 → spawn `governance set`. So a write requires: an **allowlisted IP + the armed bearer (≥24, valid) + R4 on + typed project confirm + a reason (+ the money-ack phrase for money-on)**.

## The six review-hardest items
1. **Money-clearance flip:** gated by armed-bearer + R4 + `confirm==project` + typed `ENABLE MONEY CLEARANCE` + mandatory reason, and audited (same-txn verified). Reversible (flip OFF), append-only trail. **Currently inert** — nothing consumes `money_clearance_enabled` (Stage D parked), so flipping it has **no live money effect** today. Well-gated for an operator-domain config toggle. (See governance note.)
2. **Writable-column allowlist:** hardcoded 4-column `FIELDS`, double-enforced — no injection (above).
3. **Toggle without an audit row:** impossible to commit — same-txn audit verification + rollback (above).
4. **peer-ip spoofability:** `updated_by = console:<_client()>`, and `_client()` returns the **real TCP peer only** — it **explicitly ignores `X-Forwarded-For`** ("would launder a spoofed identity into the log"). Not header-spoofable. (Residual attribution note below.)
5. **hosted-proxy bearer forwarding:** `/api/governance-set` added to `_ARMED_FORWARD_PATHS` → `X-Armed-Bearer` forwarded upstream for it (+ reset/apply-armed only); `_act_governance_set` re-validates project/field/confirm before proxying; hosted `GET /api/governance` requires `_authed()`.
6. **XSS:** every free-text field (operator `name`, `chat_id`, `reason`, `changed_by`, `project`, audit fields) is `esc()`'d before `innerHTML`; `project` is additionally charset-constrained by `PROJECT_RE` (`^[a-z0-9][a-z0-9_-]{0,63}$` — no HTML metachars); chat_ids are integer-validated. No unescaped user string reaches the DOM.

## `CONSOLE_ARMED_BEARER` min-length ≥24 (my fc-v64 [LOW], now implemented)
`check_armed_bearer` returns `(False,"too-short")` and 503s **before any comparison** when the configured key is <24 chars (a short key is never honoured even if presented correctly — fail-closed). `log_armed_bearer_startup_state()` (called from `app.run`) logs the **state** (`too-short`→error / `unconfigured`→warning / `ok`→info) at boot; the key value is never logged. Exactly the hardening I recommended.

## Verification performed
- `pytest tests/console/test_governance.py + test_armed_bearer.py` → **59 passed**; full `tests/console` → **345** (299 baseline + 46); `node fleet_governance.test.js` → all assertions passed. Version-sync fc-v65 (sw/fleet/lanes/irsyad).
- **Read-only substrate check** (verify-not-assert): `project_governance` + `project_governance_audit` exist; `trg_project_governance_audit` + `trg_project_governance_forbid_delete` live; `console_readonly` SELECT = **false**, `authenticated` SELECT = true; `forbid_delete` fn present.
- Content-hash re-derived == `f13640b4adff7664`, zero drift vs `dc79ed9e`.

## Notes (all NON-BLOCKING)
- **[GOVERNANCE — for the record]** The money-clearance flip uses the same operator-domain relaxation basis as op#20692 apply-armed (armed second factor + typed confirm, **not** bridge-verified), which is settled operator-domain per **CAI-RESP-1435**. I clear the toggle as operator-domain config. **When Stage D lands a *consumer* that gates an actual money path on `money_clearance_enabled`, that consumer — not this toggle — is the money/irreversible-bar piece (cai's retained domain per CAI-1435) and should get review at that point.** Flagging the future seam, not re-escalating the inert toggle.
- **[OBS] Attribution fidelity behind the hosted proxy:** a hosted-proxied write records the *proxy's* tailnet peer-ip in `updated_by`, not the operator's device (the Mini's TCP peer is the proxy). Not spoofable, but the audit can't distinguish operator devices for hosted writes; the mandatory `reason` + armed-bearer possession are the authorization signal. Consider forwarding a *trusted* operator-identity signal (never XFF) from the hosted proxy if finer attribution is wanted.
- **[OBS] `console_readonly` lacks SELECT** on `project_governance`, so the READ path also runs via the subprocess (works; builder's mig065 would grant SELECT for a lighter read). More-restrictive, not a risk.
- **[OBS]** `residency_ack` is display-only (surfaced as `residency_ack_on_file` boolean); no write path — fine.

I have **not** deployed — orch-console runs `deploy_console.sh`.

— cc-quality
