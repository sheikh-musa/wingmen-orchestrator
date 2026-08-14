# Fleet Console Overhaul — Approaches (brainstorm / options stage)

**Operator brief (verbatim):** *"full ihsanification and unification so i only see one page but i need all the info reorganized so that its clean and i can see everything and manage lanes quickly"* — and, on which lane actions he wants one-tap: *"everything."*

**Central tension:** "see everything at a glance" pulls toward density; "clean, one page" pulls toward restraint; "manage lanes quickly / everything" pulls toward surfacing a large action set. No single layout maximizes all three — each approach below picks a different primary and manages the others.

**What we're unifying (today's split):** `/fleet` (monitor: pulse headline, needs-you, top-bloat glance, pool usage, coordinators, lanes list, backlog swipe) + `/lanes` (the only real write surface: per-lane token/model pointer, add-token, dry-run→armed apply-relaunch, bulk switch) + `/irsyad` (scoped) + `/classic` (feed). Goal: ONE page.

**Actions inventory (drives the "manage quickly" bar):**
- *Exist today:* peek (live pane), reset the 3 singleton coordinators only, per-lane token/model pointer + dry-run/armed apply-relaunch, bulk switch-group / switch-ALL, backlog swipe.
- *Net-new the brief implies* (design the affordance now; endpoints are follow-up work): **recycle any lane**, **message/retask a lane**, **attach / jump into pane**, **boot**, **stand-down**, and lighter ones (mute, pin-ask). Marked `NEW` in the mockups.

**Non-negotiable constraints all three respect:** the 3-constant version-sync (sw.js VERSION == fleet.js APP_BUILD == the build badge — badge stays visible in the header), the iOS-PWA one-shot-reload / offline SW behavior, and every existing POST endpoint (`/api/reset`, `/api/set-pointer`, dry-run/armed apply, bulk switch) stays reachable. All three reuse the current "Calm Command" tokens verbatim (jade = all-clear, warm only where attention is due, SF Pro structure / mono for data).

---

## Approach A — Priority Feed
**File:** `mockup-priority-feed.html`

**Core organizing idea.** One continuous vertical stream, ranked strictly by *does this need me right now.* No sections you navigate — the *order* is the information architecture: Needs-you → attention lanes (bloated/flagged) → working → coordinators → resting (collapsed). A segmented filter (`All · Needs · Working · Idle · Your asks`) is the only chrome. Everything is a card in one rhythm.

**Resolves the tension by RANKING.** "See everything" becomes "see the *important* things first, scroll for the rest." "Clean" comes from a single-column cadence and one card grammar — nothing competes for the top.

**Manage-quickly bar.** Per-card: tap any lane to expand a **4-up action tray** in place (Peek · Retask · Recycle · Attach · Model · Token · Boot · Stand-down). Needs-you cards carry inline quick-replies (Approve / Peek / Reply) so the most common action is zero-navigation.

**Above the fold (390px).** Pulse headline ("2 things need you") + pool micro-strip + the first needs-you card. The operator's triage decision is made without scrolling.

**Tradeoffs.**
- ✅ Best for triage / "what's on fire" — matches an attention-first operator who opens the app to check, not to administer.
- ✅ Cleanest visually; least new UI vocabulary (closest evolution of today's `/fleet`).
- ⚠️ "Manage *all* lanes quickly" means scrolling to each lane and expanding it — no birds-eye grid, no multi-select. Bulk actions feel bolted-on.
- ⚠️ Ranking hides structure: the operator can't see the whole fleet's shape at once (how many on Syed's token, who's where) without scrolling.

---

## Approach B — Sectioned Dashboard
**File:** `mockup-sectioned-dashboard.html`

**Core organizing idea.** One page divided into labeled **collapsible sections** (Needs you · Lanes · Coordinators · Your asks). Each collapsed section shows a one-line **summary of counts** in its header (`3 work · 2 idle · 1 off`), so the page is short but nothing is missing. Needs-you and Lanes open by default; the rest fold to summaries. The `/lanes` Lane Manager is folded *into* each lane row as an expandable control drawer — token/model selects + dry-run/armed apply live exactly where today's manager puts them, plus the new action grid.

**Resolves the tension by COLLAPSE.** "See everything" is satisfied at the *summary* level (every section's counts are visible even when folded); "clean / one page" is satisfied because content is folded until you ask for it. It's the most conservative unification — least behavior change, safest to ship, remembers fold state.

**Manage-quickly bar.** Each lane row expands to a **full control drawer**: the existing token/model pointer selects + Preview / Apply-relaunch, *then* a 3-wide action grid (Peek · Retask · Recycle · Attach · Boot · Stand-down). A pinned **bulk-switch toolbar** sits at the bottom of the Lanes section (switch-group / switch-ALL) — the current tool, in place.

**Above the fold.** Pulse + pool strip + the Needs-you section (open) + the Lanes section header with its live counts. You see the fleet's *shape* (counts) immediately; you open a lane to act.

**Tradeoffs.**
- ✅ Lowest-risk migration: preserves every current control's exact placement/mental-model, just unified onto one page. Fastest to build; least regression surface against the version-sync + PWA gates.
- ✅ Scales cleanly as lanes grow — folded sections keep the page finite.
- ⚠️ "See everything at a glance" is only *partly* true: you see counts, not content, until you expand. More taps to reach any given lane's live activity.
- ⚠️ Accordion-of-accordions (section → lane → drawer) can feel like drilling; the least "designed," most utilitarian option.

---

## Approach C — Command Surface (lanes-as-spine) — **RECOMMENDED**
**File:** `mockup-command-surface.html`

**Core organizing idea.** Flip the priority: **the lanes ARE the page.** A compact dense grid of lane tiles is the spine — each tile is a context ring + status dot + one-line activity + token owner. Monitoring (pulse, pool, needs-you) compresses into an ambient **command strip + stat row** up top (`3 work · 2 bloat · 2 idle · 1 off`). Tapping any lane raises a **bottom action sheet** carrying the *full* action set at once; multi-select drives bulk. Management is primary; monitoring is the frame around it.

**Resolves the tension by SEPARATING glance from act.** "See everything" = the stat row + pool row + needs callout give the whole fleet's state in the top third, always. "Clean" = the action complexity that would clutter cards lives in the sheet, hidden until summoned. "Manage everything quickly" = the sheet is the literal answer to *"everything"* — one tap on a lane surfaces Peek · Retask · Recycle · Attach · Boot · Stand-down · Mute · Pin, plus token/model + Preview/Apply, without leaving the page.

**Manage-quickly bar.** THE defining feature — a persistent bottom **dock** (resting) that becomes a full **action sheet** on lane-select. `◎ multi-select` turns tiles into checkboxes and the dock into a bulk bar (reusing the existing switch-group / switch-ALL endpoints). This is where "everything one-tap" actually lands.

**Above the fold.** Command strip (headline + build badge) → 4-cell stat row → pool row → needs callout → first ~3 lane tiles. The operator sees fleet health *and* the top lanes, and every lane is one tap from its full action set.

**Tradeoffs.**
- ✅ Directly answers the two loudest signals in the brief: "manage lanes quickly" and "*everything*" one-tap. The bottom sheet is the cleanest known pattern for a large action set on a phone.
- ✅ Best glanceability of fleet *shape* (stat row + context rings) without scrolling.
- ✅ Multi-select generalizes today's bulk-switch to any future bulk action (bulk recycle, bulk stand-down).
- ⚠️ Highest craft/build cost (bottom-sheet + multi-select state machine, context-ring SVGs) and the most net-new endpoints to be worth it.
- ⚠️ Monitoring is ambient, not central — a heavily glance-driven operator could find needs-you less prominent than in A. Mitigated by the pinned needs callout, but it's a real trade.
- ⚠️ Densest option; ring + tile + tok-chip must stay legible at 390px (they do in the mockup, but it's the tightest budget).

---

## Recommendation: **Approach C (Command Surface)**, borrowing B's summary-counts for glanceability.

**Reasoning.**
1. **It answers the operator's own emphasis.** He didn't just say "unify" — asked which actions he wants one-tap he said *"everything."* That's a management-first brief. C is the only approach whose primary structure is *acting on lanes*; A and B treat management as a secondary drawer. A bottom action sheet is the phone-native way to hold a large action set without cluttering the glance view — exactly the "clean but everything" square to circle.
2. **It best resolves the stated tension.** By physically separating the *glance* layer (top strip: stat row + pools + needs) from the *act* layer (sheet), neither has to compromise. A collapses content to counts; B collapses sections; C collapses only the *action complexity* — which is the part that was genuinely making the page busy.
3. **It generalizes.** Multi-select + sheet is one pattern that absorbs today's bulk-switch and every future bulk action, instead of a one-off toolbar. As the fleet grows toward the target autonomy topology, an operator commanding N lanes wants select-and-act, not scroll-and-expand.

**The honest hedge — steal from B.** C's one weakness is that monitoring goes ambient. The mockup already pins the needs callout and shows a live stat row; if the operator is more "check on it" than "drive it," the safe fallback is **B**, which is also the *lowest-risk build* against the version-sync + iOS-PWA gates and preserves every current control's exact placement. If he wants the calmest, most familiar unification, ship B; if he wants a genuine cockpit, ship C. **A** is the right pick only if triage ("what needs me *now*") is the whole job and lane administration is rare.

**Suggested path:** show all three on his phone. If he reacts to C's sheet as "yes, that's the fast management I meant," build C with B's summary-count headers grafted onto the top strip. Net-new endpoints (recycle/retask/attach/boot/stand-down) are staged follow-ups behind the same dry-run→armed pattern the token/model applies already use — the UI ships first with those affordances wired to the existing reset/pointer endpoints and clearly-marked `NEW` stubs.

---

## Deliverable files
- `reports/console-overhaul/approaches.md` — this report
- `reports/console-overhaul/mockup-priority-feed.html` — Approach A (open in a phone browser / 390px)
- `reports/console-overhaul/mockup-sectioned-dashboard.html` — Approach B
- `reports/console-overhaul/mockup-command-surface.html` — Approach C (recommended)

All mockups are self-contained (inline CSS/JS, no deps), dark "Calm Command" palette pulled from the live `fleet.html` `:root`, framed in a 390px phone viewport with realistic fake fleet data (2 needs-you incl. 1 crit; 3 working / 2 bloated / 2 idle / 1 offline lane; hub/console/cai coordinators; Musa 62% / Syed 88% pools; a small Your-asks list). Light interactivity only (tap-to-expand / open-sheet) to convey the philosophy — representative static state, rough by intent.
