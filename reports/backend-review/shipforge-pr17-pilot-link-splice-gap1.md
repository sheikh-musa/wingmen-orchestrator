# cc-quality review — shipforge PR #17: pilot link-splice GAP-1 fix

**Verdict: ✅ PASS to merge** (DORMANT — nothing deploys until operator go). Sound 3-layer defense (extraction-suppress → apply-refuse → harness-invariant), TDD RED-first, 12 pilot_site + 54 bot tests green (verified by execution). Answers to the 3 asked questions below; findings are hardening recommendations, not merge blockers — but (3b) + (Q1/3a) should land before the editor goes LIVE.

- **Reviewer:** cc-quality (Head of Quality) — re-routed shipforge review (cc-reviewer down).
- **PR:** #17 `fix/pilot-link-splice-gap1` @ `6800ba6`, base main, +133/-5, MERGEABLE. Files: bot/pilot_site.py, bot/pilot_manage_harness.py, bot/test_pilot_site.py.
- **Bug:** a plain-English text edit could silently DELETE a functional link (nav/Reserve CTA/mailto) — a field whose text lived in an attributed child recorded the whole child as its span, and the fail-loud harness only checked new-text-present → false-greened a link-destroying edit.
- **Reviewed (UTC):** 2026-08-14T22:09:12Z
- **Method:** verify-not-assert — read all 3 parts, reasoned adversarially about bypasses, ran the suites at the PR head, restored the repo.

---

## The fix (verified, sound)

Three layers, defense in depth:
1. **extract_fields** suppresses a wrapper field whose whole inner is a single attributed child (`_wraps_single_attributed_child`) — but ONLY when a covering child field exists (`any(b.start>=f.start and b.end<=f.end ...)`), so addressability is never lost (the child edits the label in place, keeping the href).
2. **apply_field_edit** refuses a splice whose `field.inner` carries a functional attr (`_FUNC_ATTR_RE`) — even if a wrapper slipped through extraction, applying an edit to it fails loud.
3. **assert_structure_preserved** (new, wired into `pilot_manage_harness`) fails loud if any functional-attr COUNT decreased (href/src/srcset/action/formaction) — the invariant the old new-text-present assert missed.

`class`/`style` correctly excluded (cosmetic inline `<em>` stays editable). Tests are substantive and TDD RED-first (label-edit-preserves-href, mixed-content-guarded, cosmetic-still-editable, structure-flags-dropped-link). **12 pilot_site + 54 bot pass** (ran them under the orchestrator venv, stdlib-only).

## Answers to the 3 questions

### (1) Is the functional-attr set right? should data-*/id/value be included?
The set (href/src/srcset/action/formaction/xlink:href) is **correct and sufficient for the NATIVE links/assets in the recovered static HK pilot content** (verified mailto/nav/CTA). Recommendations:
- **data-*** — the main gap: if any pilot CTA is JS-driven (`data-href`/`data-action`/onclick) rather than a native `href`/`formaction`, it is invisible to ALL three layers → a link-dropping edit on it slips through (see 3a). Confirm pilot content is native-functional-only, or add `data-*` (or specific `data-href`/`data-action`) before LIVE.
- **id** — an anchor TARGET (`#id`); dropping it breaks an in-page anchor, a secondary link-integrity concern, not a link/asset deletion. Not required for GAP-1 (and noisy — many elements carry ids). Note only.
- **value** — form-input defaults; inputs are void (no editable inner text), low reachability. Not required.

### (2) The single-child suppression regex on real-world markup edge cases
Robust — every edge behavior errs FAIL-SAFE:
- Greedy multi-sibling (`<a>x</a><a>y</a>`) mis-parses as one wrapper, but suppression only fires when covering child fields exist (the individual links), so it errs toward suppressing the multi-link wrapper while the links stay individually addressable.
- `data-x="href=…"` (func-string in a value) over-flags → errs toward suppression/refusal.
- Mixed "text + link" (`Email <a>x</a> today`) is NOT caught by extraction (the regex needs the single element to span the whole stripped inner) but IS caught by the apply-guard (layer 2) — layer 2 covers layer 1's narrow scope, exactly what `test_mixed_content_with_attributed_child_is_guarded` proves. No unsafe edge found.

### (3) Can a link-dropping edit slip past BOTH the apply-guard AND the harness invariant?
Yes — two ways, both worth closing before LIVE:
- **(3a) Non-native functional attr.** Any "link" not in the set (`data-*`, `ping`, JS `onclick`) is invisible to both `_FUNC_ATTR_RE` and the harness count → a drop slips past. Real IFF pilot CTAs are JS-driven. Mitigate per (1).
- **(3b) Raw-substring matching [the key finding].** Both `_FUNC_ATTR_RE` and `assert_structure_preserved` match `\b(href|…)\s*=` as a BARE substring — so they also match `href=` occurring in **text content**, not only real tag attributes. `apply_field_edit` escapes new text with `_html.escape(quote=False)`, which does NOT neutralize the literal `href=`; so an edit whose new text contains `href=` inflates the harness's `after` count. Consequence: the invariant `a < b` can be **masked** (a real attribute drop offset by a fake text `href=`) — a fail-UNSAFE direction. In the CURRENT wired flow this is masked by the upstream apply-guard (a field with a func-attr inner is refused, so a drop can't originate there), so it is **not exploitable today** — but it is fragile: if the attr set has a gap (3a) or the harness is ever used independently, text pollution could hide a drop. **Recommend making both matchers tag-aware** (require the attr inside a tag, e.g. `<[^>]*\bhref\s*=`), which removes the harness masking risk AND the apply-guard's over-refusal on text containing `href=`.
- **Minor parity:** `xlink:href` is in `_FUNC_ATTR_RE` but NOT in `assert_structure_preserved`'s loop — an SVG `xlink:href` drop is caught by the apply-guard/extraction but not counted by the harness. Add it to the loop for parity.

## Bottom line
A thoughtful, well-tested fix that correctly closes the link-destroying-edit false-green with three independent layers. **PASS to merge (dormant).** Before the pilot editor goes LIVE, land two hardenings — both cheap: (3b) tag-aware attribute matching (so a real drop can't be masked by `href=` in text, and cosmetic text isn't over-refused), and (Q1/3a) confirm the pilot CTAs are native-functional-only or extend the set to the data-*/JS forms they use — because a masked or missed link-drop is the exact bug class this PR exists to prevent.

— cc-quality
