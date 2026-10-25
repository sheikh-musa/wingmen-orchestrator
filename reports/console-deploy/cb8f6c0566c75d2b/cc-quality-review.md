# cc-quality review — fc-v82 asks-board token strip + fragment deep link (bus #76243, orch-console GO #76226)

- **Content hash:** `cb8f6c0566c75d2b` (recomputed at source via `console_content_hash` on the worktree tree at HEAD = `cb8f6c0566c75d2b` ✓ — keys this review to the exact gated content).
- **Branch:** `lane/console-revamp` @ `1585470` (worktree `orchestrator.wt-console-revamp`).
- **Base:** `f9bd350` (my prior-cleared fc-v81 head). One commit on top (`1585470`), 6 files: asks.js (+53), asks.html (+11), sw.js/fleet.js/lanes.html (version bump only), asks_board.test.js (+65). **Zero `*.py` changed** → the gated backend package is byte-identical to the fc-v81 I already cleared; no backend re-review needed.
- **Reviewer:** cc-quality (ran on claude-opus-4-8) · 2026-10-26
- **Verdict:** **PASS — clear for deploy_console Gate-4.** The fragment-only token-entry design is implemented correctly and the token does not leak on any path; XSS-safe; fail-closed; no regression to the fc-v81 board. No blockers; 2 operational notes (non-blocking, for orch-console's provisioning step, not code defects).

INDEPENDENT pass verified at source on the PR head, not a sign-off on the lane's green.

## The security-critical property — fragment-only token, verified correct
The token-entry path must never leak the console token (it grants full read of the operator-private asks board — all `operator_summary` + ask text). Verified at source:
- **Reads the FRAGMENT, not a query param.** `tokenFromHash(location.hash)` with `/^#t=([^#&\s]{24,})$/` — anchored start+end, so the whole hash must be exactly `#t=<token>`; the token class `[^#&\s]` forbids `#`/`&`/whitespace (no second param, no trailing junk). A URL fragment is **never** sent in the HTTP request line, **never** in `Referer`, and **not fetched by link-preview bots** (Telegram/iMessage fetch the URL *without* the fragment) — so the deep link orch-console hands Musa cannot leak the token to server logs, previews, or referrers. This is exactly orch-console's #76226 rationale and the code honors it (a query param would have leaked; this is not one).
- **Persist-before-read, then stripped.** `localStorage.setItem("console_token", deepTok)` runs BEFORE `var token = localStorage.getItem(...)`, then `history.replaceState(null, "", location.pathname + location.search)` drops the fragment so the token does not linger in the URL bar or the history entry. localStorage is the single source of truth; the deep-token is funneled through it (no divergent path).
- **The API call uses a header, not a URL.** `authHeaders()` sends `Authorization: Bearer <token>` (existing, unchanged) — the token never appears in the `/api/asks-board` request URL, so it is absent from access logs on the round trip too. The full chain (fragment entry → localStorage → Authorization header) is leak-free end to end.
- **24-char floor, consistent.** Both the deep-link regex and the typed `save()` enforce `length >= 24`; `save()` trims whitespace and flags `.bad` on a short paste. **Never `window.prompt`** (iOS PWA suppression) — a `type="password"` input with autocomplete/autocapitalize/autocorrect/spellcheck all off.
- **No ReDoS:** single bounded character class + anchors, no nested quantifiers.

## XSS — safe
`unlockHtml(msg)` is the only new `innerHTML` sink; its sole dynamic value `msg` goes through `esc()` (escapes `& < > "`), and every `msg` passed in is a hardcoded string. The **token input value is never rendered** — it is read via `inp.value` and written to localStorage only. No server/user string reaches innerHTML unescaped. (Node test additionally re-locks the fc-v81 `heroCard` `<img src=x onerror>` escape — still green.)

## Behavior — fail-closed, no doomed fetch
- `load()` short-circuits: `if (!token) { showUnlock(); return Promise.resolve(); }` — tokenless shows the strip with **zero** network calls (no 401 round-trip), returns a resolved promise so awaiting callers don't break.
- 401 path now `showUnlock("That token was refused — paste the current console token.")` instead of the old "go use devtools" dead-end — the operator can re-paste in place. `save()` → `location.reload()` re-runs the module, which re-reads the fresh token (no stale-token bug).

## Empirical (my own, at 1585470)
- Content hash recomputed = `cb8f6c0566c75d2b` ✓. GATE-1 version-sync: sw.js / fleet.js / lanes.html / asks.html all = **fc-v82** ✓.
- **node `asks_board.test.js` → OK** (heroCard, tokenFromHash, tokenFromHash flow, unlockHtml). The new asserts lock the security properties: reads `#t=`, rejects `<24`, ignores `#x=` non-token fragments, empty→null, no percent-decode, and the flow asserts persist-BEFORE-read + `history.replaceState` strip.
- **Aesthetic eyeball (mine per the gate — lane is GLM-routed/image-blocked; I'm co-located on this host):** both PNGs are true 390px device width (1170px / 3×).
  - `asks-strip-iphone-fcv82.png`: the paste-once strip reads clean — consistent panel/hairline/mono styling, green fc-v82 pill, "Token required." sub, plain-language prompt, a comfortably-sized (~44px) password input + adjacent `save`. Reads as part of the page, not bolted on.
  - `asks-live-iphone-fcv82.png`: **no regression** — sub "289 open · 11 in progress · 25 need Musa", the `NEEDS MUSA 25` hero with sanitized `operator_summary` cards (dates-only meta, rose accent, no code-names), tabs + topic chips + breakdown all intact, no overflow. The gate sits in front of the unchanged fc-v81 board.

## Operational notes (non-blocking — for orch-console's provisioning, not code defects)
1. **The deep-link URL carries the token in its fragment**, so the link itself is a bearer credential. The fragment design defeats referrer/preview leaks, but anyone who sees the raw link (e.g. if it were forwarded into a shared chat) has the token. Deliver it to Musa over the private 1:1 channel only (as #76226 intends), and consider the token rotatable if a link is ever mis-shared. This is handling discipline on the provisioning side; the code does the right thing.
2. **Safari private mode / PWA quota:** if `localStorage.setItem` throws (caught), the deep-token is silently dropped and the fragment still stripped → Musa lands on the typed strip and pastes manually. Acceptable fail-closed (no leak, degraded to one paste); flagged only so it's not a surprise.

**Bottom line:** PASS — the token never leaks (fragment-only entry, replaceState strip, header-only API), XSS-safe, fail-closed, board unregressed. Clear for Gate-4 at content hash `cb8f6c0566c75d2b`. Ship it so Musa gets his chase-list link.
