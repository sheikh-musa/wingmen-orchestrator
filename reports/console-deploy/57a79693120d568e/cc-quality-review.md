# cc-quality review — fc-v83 token-floor fix 24→8 (bus #76264; fixes the fc-v82 defect I cleared)

- **Content hash:** `57a79693120d568e` (recomputed at source via `console_content_hash` on the worktree at HEAD = `57a79693120d568e` ✓).
- **Branch:** `lane/console-revamp` @ `cfecd35` (worktree `orchestrator.wt-console-revamp`).
- **Base:** `cb8f6c05` (my fc-v82 head). One commit (`cfecd35`), 6 files: asks.js (+comments + two `24`→`8`), asks.html (comment + badge), sw.js/fleet.js/lanes.html (version bump only), asks_board.test.js (regression lock). **Zero `*.py` changed** → gated backend byte-identical to fc-v82.
- **Reviewer:** cc-quality (ran on claude-opus-4-8) · 2026-10-26
- **Verdict:** **PASS — clear for deploy_console Gate-4.** The fix is correct and minimal; the new floor matches the real credential length, verified at source this time. **Supersedes my fc-v82 review at `cb8f6c0566c75d2b`** (whose floor was wrong — see the self-accounting below).

INDEPENDENT pass verified at source on the PR head, not a sign-off on the lane's green.

## Self-accounting — my fc-v82 miss (owned)
My fc-v82 PASS verified the 24-char floor was *internally consistent* (same in `tokenFromHash` and `save()`) and reasoned about regex/XSS/leak — but I did **not** check the floor against the **actual** token length. The lane specced 24 by copying the hosted console's separate bearer invariant; the Mini's real read token is 8 chars, so the deployed strip rejected the production credential forever and the deep link no-oped. The lane caught it by live end-to-end verification post-deploy and flagged it honestly. **Lesson (recorded to memory):** a client-side credential-length floor must be validated against the REAL credential's length (measure the length, never the value — `${#VAR}`/`len()`), not just internal consistency or the spec'd invariant. That is the check I ran this time.

## Root cause verified at source (the check I should have run on fc-v82)
Measured the console token env lengths on this host — **length only, values never printed** (`secrets_transcript_guard` also enforced this; I used `len()` sinks):
- `CONSOLE_TOKEN` = **8 chars** — the Mini console's real read credential. Clears the new floor (8 ≥ 8) ✓, and the **old floor 24 WOULD have rejected it** (8 < 24) — the fc-v82 defect, reproduced at source.
- `CONSOLE_ARMED_BEARER` = 64, `CONSOLE_BEARER_TOKEN` = 43 — the ≥24 tokens the wrong floor was copied from (second-factor / hosted-phone bearer).

So the diagnosis and the fix are exactly right: the read-side credential is 8 chars; floor 8 admits it and rejects ≤7-char junk.

## The fix — correct, minimal, non-regressive
- `tokenFromHash`: `/^#t=([^#&\s]{24,})$/` → `/^#t=([^#&\s]{8,})$/`; `save()`: `v.length < 24` → `v.length < 8`. The two paths stay consistent (both 8).
- **Everything security-relevant is untouched** (confirmed in the diff): fragment-only entry (`location.hash`, not a query param), persist-before-read, `history.replaceState` strip, `Authorization: Bearer` header (never URL), `esc()` on the only dynamic innerHTML value, fail-closed tokenless→strip-no-fetch / 401→strip. Only the length class changed.
- **Regression lock added** (so this never recurs): an 8-char token (`REAL = "w1ngmen8"`) is asserted to ride BOTH the deep-link and typed paths and to persist; a 7-char fragment (`#t=1234567`) stays rejected. This is the exact property whose absence let fc-v82 ship — now test-pinned.

## Empirical (my own, at cfecd35)
- Content hash recomputed = `57a79693120d568e` ✓. GATE-1 version-sync: sw.js / fleet.js / lanes.html / asks.html all = **fc-v83** ✓.
- **node `asks_board.test.js` → OK** (heroCard, tokenFromHash incl. the 8-char accept + 7-char reject, flow, unlockHtml).
- Real-token length measured = 8 (above), confirming the floor.
- **Aesthetic eyeball (mine — lane is GLM/image-blocked):** both PNGs true 390px (1170/3×). The paste-once strip reads clean + consistent, fc-v83 pill; the live board is **unregressed** — sub "289 open · 11 in progress · 25 need Musa", NEEDS MUSA 25 hero with sanitized `operator_summary` cards, tabs/chips/breakdown intact, no overflow.

## Server-auth scope + one non-blocking observation
- `/api/asks-board` is gated by `_authed()` → `auth.check_access` = **IP-allowlist (`CONSOLE_ALLOWED_IPS`, set) OR the breakglass token** (`CONSOLE_BREAKGLASS_TOKEN`, dormant in this checkout's `.env`). The exact deployed auth path Musa's phone takes (tailnet IP vs the pasted token) and the running process's env are the console's own deployment state — **settled by the lane's live end-to-end test with the production credential** (which is how the fc-v82 defect was caught, and which the lane commits to re-running post-pass). fc-v83 does not touch any of this; it only fixes the client floor. I do not gate on re-deriving the deployed auth topology.
- **Non-blocking (pre-existing, NOT introduced by fc-v83):** an 8-char token is low-entropy for a surface exposing operator-private ask text/summaries. It is acceptable here because it sits **behind the tailnet + IP-allowlist perimeter** (a secondary/recovery factor, not an internet-facing sole credential). Flag for orch-console's awareness only: if the board's exposure is ever broadened, lengthen the read credential. This is the `CONSOLE_TOKEN` invariant, not a property of this diff.

**Bottom line:** PASS — the floor now matches the real 8-char credential (verified at source), the fix is minimal and leaves the fragment-only/escaping/fail-closed design intact, a regression lock prevents recurrence, and the board is unregressed. Clear for Gate-4 at content hash `57a79693120d568e`. Proceed with the lane's live end-to-end re-verification with the production credential before all-clear, as planned.
