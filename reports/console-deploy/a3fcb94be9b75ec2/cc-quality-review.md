# cc-quality review — musa2 → irsyad pool-nickname rename (op#32599)

- **Content hash:** `a3fcb94be9b75ec2` (console manifest-gated path set)
- **Branch:** `cc-substrate/rename-musa2-to-irsyad` @ `b8b8aa4af777e889b8640f9f597f92e53f00ab96`
- **Base:** `origin/fable/substrate-safe-fixes`
- **Reviewer:** cc-quality (ran on claude-opus-4-8) · 2026-10-21
- **Verdict:** **PASS (eng-quality)** — code rename complete + correct — **with ONE required deploy-sequencing step** (data, not code). Not a code blocker.

## Scope reviewed
Fleet-wide rename of the irsyad OAuth-pool **nickname** `"musa2"` → `"irsyad"`. The
token **fingerprint** (`e1dfa48eec85`) and all key material are unchanged — this is a
display/label/match-string rename only. I reviewed the **full branch diff (31 files)**,
not just the 9-file `nervous_system/console/` subset the content hash gates, because
rename *completeness* is the whole correctness question and the load-bearing match
string (`app.py`) is coupled to a writer (`weekly_limit_monitor.py`) outside the console
path.

## What I verified

1. **Rename completeness — COMPLETE in live code.** Every *functional* `musa2` reference
   is renamed or safely aliased. A fleet-wide grep's surviving `musa2` hits are all
   **inert**: historical op-provenance comments (`pools.py`, `fleet.js`,
   `weekly_limit_monitor.py`), immutable *applied*-migration history (053/045/069),
   test-fixture strings, telegram/report logs, and a backend-review doc. None are live
   dependencies.

2. **Token routing is fingerprint/family-based, not nickname-based.**
   `lane_token_resolver` resolves family `irsyad` (the family was *always* `irsyad`,
   never `musa2`) → `.group_default_token.irsyad` pointer → key file → fp. The nickname
   never participates in routing. Additionally a **deliberate back-compat alias** keeps
   stale references working: `token_fps.map` now has BOTH `irsyad  e1dfa48eec85` and
   `musa2  e1dfa48eec85` (same fp), and `~/.wingmen/keys/musa2-oauth-token` is now a
   symlink → `irsyad-oauth-token`. So no routing path can break on a lingering `musa2`.

3. **Console static files** — pure string/CSS-class/comment renames. The `.tok.musa2` /
   `.collapsed .cs.musa2` classes → `.irsyad`, paired consistently between `fleet.html`
   (CSS definitions) and `fleet.js` `POOL_CLS` (class emission). `irsyad.js`/`irsyad.html`
   are display-only (inline color, no class dependency). No logic change.

4. **The one load-bearing data-coupled line** — `app.py:_irsyad_payload`
   `next(p for p in pool_rows if str(p.get("pool")) == "irsyad")` (was `"musa2"`). This
   matches the `pool_usage.pool` row UPSERTed by `weekly_limit_monitor.py`, whose `POOLS`
   key is renamed `musa2`→`irsyad` **in the same commit** (`b8b8aa4`). Code-consistent. ✓

5. **Tests** — ran the renamed Python suites here (DSN cleared to satisfy the prod-guard):
   `test_pools.py` + `test_account_for_fp.py` + `test_lane_token_resolver.py` +
   `test_panes.py` + `test_token_file_guard.py` → **122 passed** (includes the
   `KNOWN_POOLS` set + fp→name mapping assertions on the new `irsyad` name). The JS
   console tests (`fleet_keyroll.test.js`, `fleet_pace.test.js`) I **could not run**
   (no vitest on this host) — their diffs are assertion-string renames; relying on CI.

## REQUIRED deploy step (data, not code — does NOT block the code merge)

Live `pool_usage` **right now** holds `pool="musa2"` (fresh) and **no `"irsyad"` row**.
The monitor UPSERTs `ON CONFLICT (pool)`, so its first post-deploy poll INSERTs a **new**
`"irsyad"` row and **orphans** the old `"musa2"` row (frozen, ages out, never auto-deleted).
Consequences until that poll + cleanup:
- the `/irsyad` weekly-pool card (matches `=="irsyad"`) renders **empty**;
- the fleet view (renders every `pool_usage` row) shows a **stale ghost `"musa2"` row**.

**Do at deploy:** (a) deploy monitor + console *together* (they are one commit — good);
(b) let the renamed monitor complete one poll; (c) one-time
`DELETE FROM pool_usage WHERE pool='musa2';` to drop the orphan (optional:
`pool_usage_history` housekeeping). This is the SRE/console deploy's step.

## Non-blocking notes
- The back-compat alias (token_fps.map `musa2` line + key symlink) is a safe transition
  aid; keeping it is harmless (same fp). Suggest a follow-up to drop it once nothing
  references `musa2` — author's call.
- The worktree has an untracked `reports/console-deploy/1700334a743a2955/` dir — a
  different hash than this review's `a3fcb94be9b75ec2`. Likely a stale prior gate attempt;
  confirm the deploy gate consumes the correct hash dir.
