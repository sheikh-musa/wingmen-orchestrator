# cc-quality review — console content `c99a135457e956b8` (fc-v86 rev2, home asks-card removed)

- **Reviewer:** cc-quality (Head of Quality). Ran on claude-opus-4-8.
- **Target:** content hash `c99a135457e956b8` = lane tip `d50dc95` (feature `3731cd1` fc-v86 rev2 + render evidence `d50dc95`), worktree `orchestrator.wt-console-revamp`. The remote lane pointer is stale (pre-push review gate blocked both pushes); the worktree is the source of truth.
- **Supersedes:** my review of `787fff4488e8d123` (fc-v86 rev1, dc59c2c) per cc-console-revamp bus **#78360** — orch-console eyeball #78335 + Musa op#34852 changed the design mid-review (home "Your asks" card REMOVED, not filtered).
- **Request:** bus 78360 (supersedes 78326).
- **Scope:** engineering correctness, security/token-gating, derivation correctness, NO-DROP/deployment completeness. **Not** the aesthetic eyeball (orch-console's per 77019 — includes the "sheet closed on load" claim).
- **Date:** 2026-10-28.

## Verdict: CODE **PASS** — cleared to deploy. Two MEDIUM alerts (neither blocking).

The gated content at `c99a135457e956b8` is correct, token-gated, and NO-DROP-clean. I independently recomputed the content hash and re-ran the gate-critical suites on the quiesced tree. Two MEDIUM findings below are **alerts, not deploy blockers** (one is in an out-of-hash script; the other is a badge/label consistency issue, not a board-blank) — unlike rev1's migration-120 blocker, nothing here silently drops an operator surface.

---

## What I verified (evidence)

**Content hash — PASS.** Recomputed via the manifest seam (`scripts/lib/console_deploy_manifest.sh`, `console_content_hash`): `c99a135457e956b8` ✓ matches the target. The hash covers the whole console backend package + all served static + Dockerfile. Working tree quiesced at `d50dc95`; the only dirty path is `reports/console-deploy/787fff4488e8d123/_fleet-stub.json` (the stale rev1 report, **outside** the hash) — the gated content set is clean.

**Auth — PASS.** `/api/asks-waiting` and all `/api/*` GETs sit below the `_authed()` bearer gate (app.py:2136 → 401 tokenless); locked by `test_api_asks_waiting_requires_auth`. The ported confirm POSTs the **existing** `/api/ask-close` (below the POST bearer gate at app.py:2359), unchanged by rev2; `asks.js closeAsk()` sends `authHeaders()`, is busy-guarded per id, re-enables the button on failure, and prints honest error copy ("close may not have run") on a network drop. No new server surface.

**NO-DROP — PASS.** The home card is gone, but nothing an operator can act on is lost:
- The swipe-to-confirm authoritative close is **ported** to `/asks` — `delegate_done` ("review") rows carry ✓ landed / dismiss, same `POST /api/ask-close` rail the home card used.
- The +ask / Retask composer **survives collapsed** in a "Delegate an ask" `<details>`; the Retask rail keeps working.
- `/api/fleet` dropping the `your_asks` rows is pure mobile-payload budget (nobody renders them; the full board is `/api/asks-board` at `/asks`).
- The three top-bar links (Asks / Lane manager / irsyad) are preserved (moved from footlinks to the sticky bar in rev1, retained in rev2).

**Tests — PASS (re-run on the quiesced d50dc95 tree).** Node harness **12/12** suites green. Python gate-critical `test_app.py` + `test_db.py` + `test_status_board.py` + `test_asks_triage.py` = **171 passed** (ephemeral PG, `env -u DATABASE_URL`; the prod guard correctly refuses a prod-DSN run). Consistent with the lane's 490-pytest / 12-node claim for the superset.

---

## MEDIUM 1 (alert, not blocking) — the Asks badge and the `/asks` hero disagree on "needs Musa"

`pulse.asks_waiting` and `/api/asks-waiting` both count only operator-surface `status=='waiting_on_musa'`, and `build_asks_query` derives that from `a.waiting_on_operator` **alone**. But migration 118's `needs_operator_decision` flag — added precisely because `waiting_on_operator` missed 20/24 genuine Musa-decision items (#75387) — is **ignored** by that derivation. The `/asks` board the badge opens onto leads with its Needs-Musa hero, which (`build_fleet_asks_query.needs_musa`) uses `waiting_on_operator OR needs_operator_decision`, **cross-surface**.

Live, at my read (2026-10-28):
- Top-bar badge (`asks_waiting`) = **11**.
- `/asks` Needs-Musa hero (`needs_musa`) = **33**.
- Open `ask` rows flagged `needs_operator_decision=true` but NOT `waiting_on_operator` = **20**.

So the badge the operator taps says "11 waiting on you" while the board it opens shows 33 — a ~3× divergence on live, by **two** axes (surface scope + the mig-118 flag). rev2's "badge == hero count" render assert holds only because the shared stub has no `needs_operator_decision`-only rows; on live they diverge. Not a board-blank (every row is present on `/asks`), and WHAT the badge counts is orch-console's product call — but a badge labelled the waiting-on-you number while pointing at a board that defines needs-Musa differently is a real inconsistency, and the mig-118 flag is silently dropped from the one at-a-glance Musa surface. **Recommend:** align the badge derivation with the hero (`OR needs_operator_decision`, and settle the surface scope), or relabel the badge so it doesn't imply it is the needs-Musa count of the board it opens. The tests feed pre-set `status` values, so they bypass the SQL derivation and cannot catch this.

## MEDIUM 2 (alert, not blocking; OUT-OF-HASH) — `scripts/asks_triage.py --answers` idempotency is broken at the CLI level

`scripts/asks_triage.py` is **outside** the content hash (standalone operator script, not a console-imported module) so it does **not** gate this deploy, but it ships in the same lane commits (dc59c2c; untouched by rev2). Wet-proven on an ephemeral PG (throwaway test, deleted):

`triage()` returns only the **last** `UPDATE`'s `rowcount`; with `--answers` set that is the answers-close (`WHERE id=%s AND closed_at IS NULL`). On an **already-closed target** that matches 0 rows, so `main()` takes its `n != 1` branch and **exits 2** with `error: 0 rows updated (id ... not found or not eligible?)` — although the DB is correctly left closed. A sweeper/operator re-running the same `--answers` command (the idempotent path the code comment + test claim is OK) gets a hard failure. Second face: when the **main** triage UPDATE hits 0 rows (nonexistent / already-closed item on `done`/`not`) but the answers-close hits 1, `main()` prints "ok" and the triage half silently no-ops. The existing test misses both — it calls `triage()` directly and ignores the return, never driving `main()`. **Recommend:** track the two rowcounts separately, treat an already-closed target as success, and base the exit code on the primary action's rowcount. (Detailed in bus #78363.)

---

## Not covered by me
- Aesthetic eyeball incl. the "sheet closed on load" claim (rev1 mid-page sheet = full-page-capture artifact; rev2 viewport shot `fleet-390-viewport.png`): orch-console's per 77019. The structural asserts (sheet rect below fold, scrim 0, no `open` class) exist in `render-harness.py`; the visual sign-off is not mine.
- The full 490-pytest superset (needs a local ephemeral PG; prod guard correctly refuses) — I re-ran the gate-critical subset (171) + the 12 node suites.

## Bottom line
CODE **PASS** on correctness / security / token-gating / NO-DROP / derivation for `c99a135457e956b8` — **cleared to deploy**. MEDIUM 1 (badge vs hero needs-Musa) and MEDIUM 2 (`asks_triage --answers` idempotency, out-of-hash) are alert-level follow-ups, not deploy blockers. Aesthetic sign-off remains orch-console's.

Ran on claude-opus-4-8.
