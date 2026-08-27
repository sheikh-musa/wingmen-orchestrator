# cc-quality review — GAP-B per-group token pointers (console fc-v44, op#12606)

**Verdict: ✅ PASS (CONDITIONAL)** — the GAP-B design, security, and the expected==boot crux are correct and safe to deploy (verified by executing the shipped resolver + real launcher command). **Two required cleanups before the ship is clean** (neither changes the security verdict): (1) commit the fc-v44 version bump into GAP-B, (2) fix 2 red CLI-shim tests (test-cwd typo). Token/billing-sensitive — independent review.

- **Reviewer:** cc-quality (Head of Quality) — cc-fleet-health did not self-attest.
- **Request:** bus id 20434 (op#12606), P2 — `deploy_console.sh` Gate-4.
- **Content hash:** `0eacc5f7ebdbe158`
- **Reviewed commit:** `27cb46e` (parent `30182ca`) on `fable/substrate-safe-fixes`; `git show` sha256 `44c46f883277499ba8898701276f945838f384c6f39a4c0c5332203e0ee372de`.
- **Reviewed (UTC):** 2026-08-13T21:08:44Z
- **Method:** verify-not-assert — read the full 9-file diff, executed the shipped `resolve_lane_token_path` across all tiers/edge-cases, ran the **real** launcher CLI command, proved CLI-stdout == python-API, independently re-ran the test files, and eyeballed the renders.

---

## The crux — expected==boot (verified, holds)

ONE canonical resolver `scripts/lib/lane_token_resolver.py::resolve_lane_token_path` owns pointer tiers 2-4 (per-session → per-group → fleet default). BOTH consumers consult it:
- **Display:** `panes.py::_expected_fp` imports it (`_resolve_lane_token_path(session, orch_dir=_ORCH_DIR)`).
- **Boot:** `launch_dangerous_cc.sh` calls the CLI shim `"$VENV_PY" -m scripts.lib.lane_token_resolver --session "$_LANE_SESSION"`.

Confirmed the two **cannot diverge**:
- Same orch_dir: `panes._ORCH_DIR` (L451, `dirname³` → repo root) == the resolver's `_default_orch_dir()` (`dirname³` of `scripts/lib/x.py` → repo root); the shell relies on the module default from the same tree.
- Same output handling: both treat the resolved path identically — readable → use it, else → `.env` default (tier 5). `_read_token_fp` (panes) and `_token_fp_at`/`cat` both operate on the same path.
- **Executed proof:** for `irsyad-coord`, `cosem-exams`, `nazim`, `cai` the display-fp == boot-fp (MATCH=True across all body classes); the **real** command `cd $ORCH_DIR && .venv/bin/python3 -m scripts.lib.lane_token_resolver --session <s>` returns rc=0 and the correct key path, and `CLI stdout == resolve_lane_token_path()` for resolving, None, session-pointer, and worker-lane cases.

## Security / correctness (all verified by executing the shipped resolver)

| Property | Result | Evidence |
|---|---|---|
| Back-compat (no group file) | ✅ byte-identical | all lanes → fleet default; zero silent flips vs pre-GAP-B. |
| Tier-3 group pin scoping | ✅ | `irsyad→musa2` applies to `irsyad-coord` + `cc-irsyad-1`; `cosem` unaffected (`family_of` strips `cc-`, splits first `-`). |
| Forbidden-fp refusal | ✅ | a group pin whose target fingerprints to gazzabyte (13589de86f29) FALLS THROUGH to fleet default — never boots a lane onto it. |
| Fail-open | ✅ | missing / unreadable / empty pointer target → fleet default, never raises (blanket `except → None`). |
| `/api/set-group-pointer` path-escape | ✅ | `family` gated by `_SESSION_RE` (`^[A-Za-z0-9._-]{1,64}$`, no `/`); `_REPO_ROOT / ".group_default_token.<family>"` is always a single in-repo filename (dots only yield literal `.group_default_token.<x>`, never a bare `..`). |
| Endpoint token guard (incl. alias) | ✅ **3 layers** | `_resolve_registry_token → _discover_tokens` applies the **fp guard at discovery** (app.py L290) → an aliased-forbidden token can't even be *named* at the endpoint; + basename check; + the resolver's boot-time fp guard. Stronger than "basename-only" — an explicit endpoint fp check would be redundant, not a gap. |
| set-group-pointer reversible / no relaunch / audited | ✅ | pin writes `str(tokfile)` (absolute), unpin = `unlink`; audit row on **both** pin AND unpin; no relaunch. |
| switch-group PERSIST | ✅ | on real (non-dry) group switch writes `.group_default_token.<family>` with `family` already `_LANE_SESSION_RE`-validated (L1981); absolute path; persist failure is LOUD (audit 500 + warn) and never undoes completed switches. |
| **Break-glass fix** | ✅ | switch-token: `force = payload.get("force") is True`; `BREAK_GLASS = "1" if force else "0"` and `--force` appended IFF force. Bulk path: `BREAK_GLASS="0"` hardcoded, never `--force` (BUSY lanes skipped rc5). So P1 break-glass fires only on a genuine gate-bypass; routine switches are quiet P3 — exactly the op#12486 intent. |
| VENV_PY defined before use | ✅ | `launch_dangerous_cc.sh` L44 def, L137 use. |
| `~`-expansion parity (weigh) | ⚠ latent (non-blocking) | resolver-fp + panes `expanduser` a target, but the launcher reads the resolver's stdout path with `[ -r "$v" ]`/`cat` (no `~` expansion). A `~`-containing pointer file would diverge (display shows it, lane boots on .env). **Cannot occur via the console** (set-group-pointer / switch-group / set-pointer all write absolute `str(tokfile)` from the registry). Recommend the resolver emit an already-expanduser'd absolute path (or the pins stay absolute, as today) so a hand-written `~` pin can't reintroduce a display-vs-boot split. |

## Independent test run (did NOT take the requester's word — and found a discrepancy)

`.venv/bin/pytest tests/test_lane_token_resolver.py tests/console/test_panes.py tests/console/test_app.py` → **84 passed, 2 FAILED**. The gate's own `pytest.log` shows only **"41 passed"** (a narrower scope that did not include the resolver CLI-shim tests), so the "86/all pass" claim did not reproduce.

**The 2 failures are a TEST bug, not a code bug** (root-caused + proven):
- `test_cli_shim_matches_python_api`, `test_cli_shim_prints_nothing_for_none` compute `repo_root = dirname(dirname(R.__file__))` — that's **2 levels up = `.../scripts`**, not the repo root (needs 3). Their subprocess runs `python -m scripts.lib.lane_token_resolver` from inside `scripts/` → `ModuleNotFoundError: No module named 'scripts'` → rc=1 → assert fails.
- The **real** launcher command (`cd <repo root> && .venv/bin/python3 -m scripts.lib.lane_token_resolver --session …`) returns rc=0 and the correct path. I substituted direct verification for exactly what these tests assert (CLI==API for resolving/None/session-pointer/worker cases — all MATCH). So the deploy is safe; the tests are broken.

## Renders (eyeballed)
- `lanes.png` (fc-v44): fc-v43 grouping intact (COSEM·4 [Musa], IHSANOS·2 [Musa] green badges, SINGLE LANES·10, NEEDS ATTENTION flat); irsyad lanes now labelled `musa2`/`Syed` (the op#12501 registry catching up) but still off-account (`expected Musa`). The new GAP-B "Token · <family> group" tier label is **not yet exercised** (no `.group_default_token.*` file exists until first-use pins irsyad→musa2) — consistent with the design; its wiring is proven in code (`_enrich_token_pointers` sets `token_group` only when the family file exists). No regression.

## Required cleanups (before a clean ship — neither changes the PASS on GAP-B logic)

1. **[REQUIRED · version/deploy coherence] Commit the fc-v44 bump.** Commit `27cb46e` is **fc-v43** (sw.js/fleet.js) — GAP-B carries **no** version bump. The fc-v44 bump (sw.js VERSION + fleet.js APP_BUILD + lanes.html badge, all consistent, version-string only) is sitting **UNCOMMITTED** in the working tree, and the render was taken from it. GAP-B changes `lanes.js` (client-visible), so it **needs** the fc-v44 cache-bust — but shipping `27cb46e` as-is (fc-v43) would serve the new JS under the old PWA cache key. Fold the version bump into GAP-B (commit it), so **deployed == reviewed commit** and the content hash covers what ships; then re-run Gate-1/Gate-4.
2. **[REQUIRED · restore the crux regression guard] Fix the 2 CLI-shim tests.** Change their `repo_root` from `dirname(dirname(...))` to `dirname(dirname(dirname(...)))` (or reuse the module's own repo-root). Until fixed, the suite is RED and the exact billing-critical invariant (CLI==API) has **no passing automated guard** — a future real break of the shim would hide behind an already-red test.

## Bottom line

The GAP-B substance — the shared-resolver expected==boot crux, `/api/set-group-pointer` guards (path-escape + 3-layer token/fp refusal + audit + reversibility), switch-group persistence, fail-open, forbidden-fp refusal, and the break-glass fix — is **correct and safe to deploy**, proven by executing the shipped code and the real launcher path. I am **clearing the code**. Before the ship is clean, please (1) commit the fc-v44 bump so the deployed artifact matches the reviewed commit and the client cache busts, and (2) fix the 2 red CLI-shim tests so the crux keeps a live regression guard. Both are trivial and don't require a re-review of the logic — re-run Gate-4 after. First-use (pin irsyad→musa2) is covered by the verification above.

— cc-quality
