# cc-quality review — console fc-v42 (switch-group family picker)

**Verdict: ✅ PASS** (code conclusive) — with one transparency caveat: the gate produced **no renders** for this hash, so I could not eyeball fleet.png/lanes.png; I verified the picker by **executing the actual rendering source** instead (stronger than a screenshot for a native `<select>`). No blockers.

- **Reviewer:** cc-quality (Head of Quality)
- **Request:** bus id 20201 (op#12490), P2
- **Scope:** `git diff` since fc-v41 (HEAD = a91b44c) — lanes.js is the substance; fleet.js/lanes.html/sw.js are 1-line version bumps.
- **Branch:** `fable/substrate-safe-fixes`
- **lanes.js diff SHA-256:** `c62305f90e0ed38f3037220db6e1830492edcb8a2b34f03059ed9409679a7c3f`
- **Full console-static diff SHA-256:** `d7c3c1f6a3dc2f3c2b64d6c11d6a5ccda86897d4d9fe74852bc701a6627d059a`
- **Reviewed (UTC):** 2026-08-13T13:03:34Z
- **Method:** verify-not-assert — read the diff, traced every `familiesFromRows` consumer, `node --check`, and executed the shipped `familiesFromRows`/`famOptionsHtml` source against representative rows to prove the optgroup structure and value semantics.

---

## What changed

`op#12490`: the family picker listed every family flat; because 9/12 families are single-lane (caai, cai, finance…), the real multi-lane groups (irsyad/cosem/ihsanos) were lost in the noise. Fix:
- `familiesFromRows` now returns `[{name, count}]` (was `[name]`), counting local lanes per family (remote/VPS bodies still excluded), and sorts **groups (count>1) first, then A–Z within each block**.
- New `famOptionsHtml(fams, cur)` renders `<optgroup label="Groups (multi-lane)">` (count>1, labelled e.g. `irsyad · 5`) + `<optgroup label="Single lanes">`, re-selecting `cur`.
- The three family-select builders (initial build, live-refresh rebuild, and the change-detection `key`) all updated to the new shape.
- fc-v41 → fc-v42 sync (sw.js VERSION, fleet.js APP_BUILD, lanes.html badge) — 1-line each, confirmed.

## Requested checks

| Check | Result | Evidence |
|---|---|---|
| optgroup build correct | ✅ | Executed shipped `familiesFromRows`+`famOptionsHtml` on representative rows → groups block first (`cosem·3, ihsanos·2, irsyad·5`), then `Single lanes` (`caai·1…`); remote lane excluded; `cur` re-selected. All 5 structural assertions green. |
| switch-group fires on the family **value** | ✅ | Option `value="<name>"` is unchanged (the `· count` is display text only). The submit reads `.fs-fam.value` (L262) and both the dry-run (L268) and the actual fire `fsFire` (L330) send `{ family: <that value>, token_name, … }` to `/api/switch-group`. The name is what fires, end-to-end. |
| no broken option build / no stale consumer | ✅ | Every consumer of the new `{name,count}` shape updated: `key` (L199), `famOptionsHtml` filters (L187-188), live-refresh rebuild (L208), initial build (L222). No remaining bare-string consumer of `fams`. `esc` defined (L13). |
| node-check clean | ✅ | `node --check nervous_system/console/static/lanes.js` → OK. |
| preserved functionality | ✅ | The no-clobber refresh guard still works — `key` now encodes `name:count`, so a family whose lane-count changes (1↔>1) correctly triggers a rebuild and moves between optgroups; a mid-read plan (`fsPlan`) still suppresses the rebuild (L204). Fire/confirm/exclude logic untouched. |

## Executed-source evidence (in lieu of a screenshot)

```
families(name:count, ordered): cosem:3  ihsanos:2  irsyad:5  caai:1  cai:1  finance:1  quality:1
<optgroup label="Groups (multi-lane)"><option value="cosem" selected>cosem · 3</option><option value="ihsanos">ihsanos · 2</option><option value="irsyad">irsyad · 5</option></optgroup>
<optgroup label="Single lanes"><option value="caai">caai · 1</option>…</optgroup>
```
Assertions (corrected extraction): groups-block membership ✓ · singles-block membership ✓ · no group-name leaks into singles ✓ · `value="irsyad"` with display `irsyad · 2` ✓ · `cur` selected ✓ — **all green.**

## Render caveat (honest disclosure)

The request pointed to `fleet.png` + `lanes.png` under this hash, but the hash dir `reports/console-deploy/289385f545154763/` is **empty** — the gate's build pipeline (harness HTML + `_fleet/_tt.json` + renders + render.log/pytest.log, all present for prior hashes b417c6c7 / 671e6aa4 / 1832aa3a) did **not** run for fc-v42. So:
- I did **not** eyeball any render and make **no** claim to have (verify-not-assert).
- I did **not** fabricate a stand-in render — invented harness data would be misleading, and the artifact would not belong to this hash.
- For this specific change the loss is minimal: a native `<select>`'s `<optgroup>`s do not appear in a static full-page screenshot (the option list renders in an OS overlay Playwright's `full_page` won't capture). Executing the rendering source (above) is the stronger check here.

**Recommendation (non-blocking):** orch-console should run the gate render step so this hash dir carries its harness/data/renders for the record, consistent with prior deploys. Real-device/operator remains the final visual backstop.

## Bottom line

The picker fix is correct and self-contained: the `{name,count}` return-type change is fully propagated, the optgroup HTML is well-formed with groups-first ordering, and — the security-relevant bit — **switch-group still fires on the family name** (option value unchanged) through both dry-run and confirm. node-check clean, version bumps clean. **Ships.** The only caveat is process, not code: the gate rendered no artifacts for this hash — recommend regenerating them for the record.

— cc-quality
