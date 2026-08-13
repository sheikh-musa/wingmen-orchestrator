# cc-quality review — token-allowlist switch fix (op#12486, security-adjacent)

**Verdict: ✅ PASS** — all three requested security invariants hold. 2 LOW non-blocking findings (doc drift + a duplicated-discovery consistency wart); neither blocks go-live.

- **Reviewer:** cc-quality (Head of Quality)
- **Request:** bus id 20196 (op#12486), P2 — "kickstart to go live; holding for your PASS"
- **Scope:** `git diff nervous_system/console/app.py`
- **Branch:** `fable/substrate-safe-fixes`
- **Diff SHA-256:** `d36f32e2d4696f521976eff273794908f55bda9542eec80e97a49f4aacd1fdb4`
- **Reviewed (UTC):** 2026-08-13T12:55:32Z
- **Method:** verify-not-assert — read the diff + all `LANE_TOKEN_FILES` consumers, traced the full resolve path, `py_compile`, ran the switch/token test subset (14 passed), and ran a standalone harness exercising the invariants against a controlled temp keys dir (incl. the op#10851 aliased-token scenario and path-traversal probes).

---

## The change

`op#12486`: the operator added `musa2` via `/api/add-token`; it registered and appeared in the `/lanes` dropdown (which scans `~/.wingmen/keys`), but SWITCH rejected it — the switch path gated on the **static** `{musa,syed}` `LANE_TOKEN_FILES` dict. Fix adds `_lane_token_files()`, which merges the static dict + a live glob of `~/.wingmen/keys/*-oauth-token` (forbidden basenames excluded), and repoints `_resolve_lane_token_file` + both switch endpoints (`switch-token`, `switch-group/all`) at it.

## Verify-focus results (the three asks)

### 1. Does gazzabyte still fail closed? — ✅ YES (three independent layers, all intact)
- **Merge-time basename exclusion:** `_lane_token_files()` skips any glob hit whose name ∈ `_FORBIDDEN_TOKEN_BASENAMES` (`{gazzabyte-oauth-token}`), so `gazzabyte` is never added as a dynamic key.
- **Not a static key:** `LANE_TOKEN_FILES` deliberately omits gazzabyte (CAI-729).
- **fp content-guard (UNCHANGED) in the resolver:** `_fp_of_token_file(path) ∈ _FORBIDDEN_TOKEN_FPS` (`13589de86f29`) → `None`, whatever the file is *named* — the alias-proof CAI-729 guard (op#10851, built after `gzb-oauth-token` aliased the same secret).
- **Empirically:** `resolver("gazzabyte") → None`; and for an aliased forbidden **content** under a benign basename, `resolver(alias) → None`.

### 2. Path traversal on `token_name`? — ✅ NO
- `token_name` is used **only** as a dict-key lookup (`_lane_token_files().get(token_name)`) and in the `token_name not in _known_tf` gate. It is **never** string-joined into a filesystem path. Dict *values* are either fixed static Paths or concrete glob results (all within `_KEYS_DIR`).
- **Empirically:** `resolver("../../etc/passwd") → None`, `resolver("..") → None`, `resolver("bogusxyz") → None` — all fail closed.
- The `musa` env-materialize special-case writes only to the fixed `_MUSA_TOKEN_FILE` constant (gated on `token_name == "musa"` exactly), not to anything derived from input.

### 3. Any other consumer of `LANE_TOKEN_FILES` missed? — ✅ NO
- Every non-comment consumer of the **static** dict that gated a switch/relaunch is updated: `_resolve_lane_token_file` (L155), `switch-token` (L1718–1723), `switch-group/all` (L1876–1881).
- The reversible **R2b pointer/registry** path (`/api/set-pointer`, the dropdown) uses its **own** independent `_discover_tokens()` / `_token_registry()` / `_resolve_registry_token()` — it never used the static dict (it was already dynamic; that's why `musa2` showed in the dropdown). Not affected, and gazzabyte is fail-closed there too (basename + fp guard at discovery). It writes a pointer file only — no relaunch, no billing change.
- No remaining `not in LANE_TOKEN_FILES` code gate exists; the other `LANE_TOKEN_FILES` matches are all comments.

## Verification evidence
- `python3 -m py_compile nervous_system/console/app.py` → clean.
- `.venv/bin/pytest tests/console/test_app.py -k "switch or token or gazza or resolve"` → **14 passed** (the asyncio "Task was destroyed" lines are feed-loop fixture teardown noise, not failures).
- Standalone harness (temp keys dir): `switchable = {musa, musa2, syed}`; `resolver(musa2)=path`; `resolver(gazzabyte)=None`; `resolver(bogus)=None`; `resolver("../../etc/passwd")=None`; `resolver("..")=None`. Matches the author's unit-check.

## Findings (both LOW · non-blocking)

- **[LOW · doc-drift · security-adjacent] Stale docstring at L276.** `_discover_tokens()`'s docstring still says *"the ARMED /api/switch-token keeps its own hardcoded LANE_TOKEN_FILES allowlist."* After this fix that is false — the armed switch now uses the dynamic `_lane_token_files()`. Misdescribes the trust boundary for a future reader; update it.

- **[LOW · consistency / duplicated discovery] `_lane_token_files()` is a 3rd parallel discovery that doesn't exactly match the registry it aims to mirror.** Two divergences from `_discover_tokens()`:
  1. **Looser name filter** — `_LANE_SESSION_RE` (`^[A-Za-z0-9._-]{1,64}$`, allows uppercase/dot/underscore, len 64) vs the registry's `_TOKEN_NAME_RE` (`^[a-z0-9][a-z0-9-]{0,30}$`). So a file like `Foo_1-oauth-token` is switchable via the armed path but never shown in the dropdown — the opposite of the fix's stated goal ("the switch path must match the registry").
  2. **No fp/`R_OK` filter at merge time** — unlike `_discover_tokens()`. Consequence (confirmed in the harness): an aliased forbidden token (op#10851 `gzb-oauth-token`, gazzabyte content under a benign basename) — or an unreadable file — is **advertised** in the armed path's `{"allowed": …}` 400 response and passes the first gate, even though `_resolve_lane_token_file` then fails it closed (`None`). The registry excludes such aliases at discovery; the armed `allowed` list would not.

  **Security impact: NONE** — the resolver's fp + basename guards are authoritative and unchanged, so no forbidden token can actually be switched to via either path. This is a code-quality / info-hygiene issue only. **Recommended fast-follow:** have the switch path reuse `_discover_tokens()` (or apply the same fp + `R_OK` filter inside `_lane_token_files()`), giving an exact registry match and not advertising aliased-forbidden/unreadable names in the `allowed` list. Not required for go-live.

## Bottom line

The fix does what it should and preserves every fail-closed invariant: gazzabyte cannot be switched to (basename + alias-proof fp guard), there is no path-traversal surface (input is a dict key, never a path component), and no switch/relaunch consumer of the static allowlist was missed. **Cleared to go live.** Fold the stale comment + the `_discover_tokens()` consolidation into a fast-follow so the armed allowlist and the registry are a single source of truth.

— cc-quality
