# cc-quality review — console fc-v43 (Lane-Manager grouping, op#12600)

**Verdict: ✅ PASS** — grouping + group-token logic correct, attention-first preserved, no regressions. 2 minor by-design observations (non-blocking). Independent reviewer: cc-fleet-health did not self-attest; I verified from source + renders + tests.

- **Reviewer:** cc-quality (Head of Quality)
- **Request:** bus id 20351 (op#12600), P2 — Gate-4 (content-hash-keyed) sign-off for `deploy_console.sh`
- **Content hash:** `f3ced9931eb3747f`
- **Scope:** `git diff -- nervous_system/console/static` since fc-v42 (HEAD = 91ac3d7) — lanes.js + lanes.html are the substance; fleet.js/sw.js are 1-line version bumps.
- **Branch:** `fable/substrate-safe-fixes` (uncommitted working tree)
- **Full console-static diff SHA-256:** `f996abc05592f14a5e0041503b85076b34c99076f4461e8af841a713bc3f9baf`
- **lanes.js diff SHA-256:** `898343a286469d4cad232087fdf2dfd7a04182c9b3cf8a11b0f6a50d38086381`
- **Reviewed (UTC):** 2026-08-13T16:28:09Z
- **Method:** verify-not-assert — read the diff, confirmed CSS-var + helper definitions, `node --check`, **executed the shipped `famKey`/`groupToken` source** on representative rows (to exercise the amber path the live render can't), eyeballed both renders, and independently re-ran the console test suite.

---

## What changed (op#12600)

- **lanes.js:** new `famKey(r)` (session prefix to first `-`, same convention as fc-v42's `familiesFromRows`) + `groupToken(rows)`. `render()` now keeps **`Needs attention` pinned FLAT at top**, then organizes the REST by family: each multi-lane family heads a `<fam> · <count>` block with a group-token badge; single lanes bucket under `Single lanes · N`.
- **`groupToken`** = consensus of **verified, non-metered, non-remote** lanes only: one shared account → green badge (e.g. `Musa`); a split → amber `mixed (musa2·4, syed·1)` badge (dominant account first), **surfaced not hidden** (operator's explicit ask).
- **lanes.html:** `.pin.grp` + `.grptok`/`.grptok.mixed` CSS. All referenced CSS vars exist (`--jade`, `--jade-soft`, `--amber`, `--amber-soft` at lanes.html L21/L23).
- **Version bumps** fc-v42→fc-v43 (sw.js VERSION, fleet.js APP_BUILD, lanes.html badge) — confirmed 1-line each.

## Checks

| Check | Result | Evidence |
|---|---|---|
| Attention-first preserved | ✅ | `attn = rows.filter(isAttn)` (`isAttn = metered \|\| !verified \|\| mismatch`) is still pinned flat before any grouping; only the `rest` block changed. Executed source: metered/unverified/mismatch lanes never enter a group. Render: 5 irsyad lanes sit in `NEEDS ATTENTION`, flat. |
| Grouping correct | ✅ | `rest` partitioned by `famKey`; `groupKeys` (count>1) sorted A–Z, then a single `Single lanes · N` bucket. Executed source: `groupKeys=[cosem, ihsanos, irsyad]`, `singleKeys=[finance, quality]`. |
| Group-token consensus (green + **amber**) | ✅ | Executed shipped `groupToken`: cosem→`{Musa,mixed:false}` (green); ihsanos→`Musa` with a **remote** lane correctly EXCLUDED from consensus; irsyad(split)→`{mixed (musa2·4, syed·1), mixed:true}` (amber, dominant-first). The amber path the render can't show is **code-proven**. |
| Escaping / XSS | ✅ | `esc(famKey)` and `esc(gt.label)` both applied; badge label round-trips unchanged. |
| No stale consumer / regression | ✅ | `famKey`/`groupToken` used only inside `render()`; the old `All lanes` heading exists only in a comment now; `isAttn`/`rowHtml` intact; `node --check` clean. |
| Version sync | ✅ | fc-v43 across sw/fleet/lanes.html (Gate-1 parity). |

## Executed-source evidence (amber path, in lieu of the render)
```
group cosem  ·4  token={"label":"Musa","mixed":false}
group ihsanos·2  token={"label":"Musa","mixed":false}   (1 remote lane excluded from consensus)
group irsyad ·5  token={"label":"mixed (musa2·4, syed·1)","mixed":true}
attn (flat): scholar-2, caai(metered), cai(mismatch)   — none grouped
singles: finance, quality
```
All assertions green (green-single, amber-mixed dominant-first, remote-excluded, attention-not-grouped, singles-bucketed, label-escaped).

## Renders (present & eyeballed this time)
- **lanes.png (iPhone 13):** fc-v43 badge; `NEEDS ATTENTION` flat at top (5 irsyad lanes off-account); **`COSEM · 4` [Musa]** and **`IHSANOS · 2` [Musa]** group headings with green token badges; **`SINGLE LANES · 10`** bucket with per-row inline account badges. Matches design.
- **fleet.png:** unchanged vs fc-v42 (fleet.js is version-bump-only) — no regression.

## Independent verification (did not take the requester's word)
- `node --check nervous_system/console/static/lanes.js` → OK.
- `.venv/bin/pytest tests/console/test_app.py` → **27 passed** (the asyncio "Task was destroyed" lines are feed-loop fixture teardown noise, not failures). Matches Gate-2.
- Both renders present with a clean `render.log`; eyeballed directly.

## Observations (non-blocking, by-design)
1. **Amber `mixed` path not visually exercised in THIS render** — confirmed. Cause is a **separate, pre-existing** token-registry gap: musa2's fp (`e1dfa48eec85`) isn't a named account, so every musa2 lane reads `Max (unknown acct)` and the whole irsyad family is (correctly) pinned in `Needs attention`, leaving no split group to render. **Not introduced by fc-v43** (fc-v43's grouping is correct; it simply can't show a split family until musa2 is registered — ties to op#12501). The amber path is proven by source execution above. Recommend the musa2-registry fix be tracked separately (agree with cc-fleet-health).
2. **A group's `· count` reflects only its non-attention lanes** — a partially-flagged family shows a group count smaller than its true size (its flagged members sit up in `Needs attention`). This is the correct consequence of attention-first + per-row inline accounts; noting it so the count isn't misread as total family size. A long `mixed (…)` badge is `white-space:nowrap; flex:none`, so on a very narrow viewport a 3+-account split could clip — cosmetic only.

## Bottom line

The grouping mirrors the fc-v42 switch picker's family convention, preserves attention-first, and surfaces token ambiguity exactly as the operator asked (green consensus / amber mixed-with-counts). Logic verified by executing the shipped source (both green and amber), renders eyeballed, 27 tests reproduced green. **Ships.** The one unexercised visual (amber) is a downstream token-registry gap, not a defect in this change.

— cc-quality
