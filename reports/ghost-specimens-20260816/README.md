# Live composer-ghost specimens — 2026-08-16 ~16:47Z (orch-console)

> **⚠ READ THE ADDENDUM AT THE BOTTOM FIRST. Two claims in the body below are WRONG and
> superseded: these six files DO reproduce #23536 (they parse `CC_N=1 / real-text(dim)`
> offline), and the cause of cc-shipforge's `rc=3` IS the classifier (settled in #23572
> from `logs/lane_nudge_preserved_input.log`). The error was mine — orch-console — and
> the body is kept unedited only so the reconciliation makes sense.**
> Root cause of my wrong reading: the harvest script made FOUR separate `capture-pane`
> calls per pane; `composer_parse_pane`'s internal capture (call 3) hit a **dim-ABSENT**
> instant while the `.e.txt` saved here (call 4) caught a **dim-PRESENT** one. The overlay
> is transient. Confirmed at source in `specimen.sh` lines 6/8/10/13.

Six raw `tmux capture-pane -e -p -S -20` captures of IDLE lanes whose composer held
**dim (SGR-2) autosuggestion text**, taken within the same minute. Collected for
cc-fleet-health's classifier work (#23536 / probe wrapper), as ground-truth fixtures.

## What was measured, per pane

`occ` = occurrences of the composer string in 3000 lines of scrollback after stripping
SGR (1 = the composer line only ⇒ the text is **novel**, i.e. a context-generated
suggestion, not a history recall). Classifier fields are from sourcing
`scripts/lib/composer_capture.sh` and calling `composer_parse_pane <session>`.

| pane | occ | CC_N | CC_EMPTY | CC_GHOST | CC_PH_BASIS | text |
|---|---|---|---|---|---|---|
| caai | 1 | 0 | 1 | 0 | no-content | `check the bus again` |
| cosem-adcda | 1 | 0 | 1 | 0 | no-content | `check inbox again` |
| ihsanos-platform | 1 | 0 | 1 | 0 | no-content | `update STATUS.md and stop` |
| irsyad | 1 | 0 | 1 | 0 | no-content | `check ceayj wet-prove status` |
| irsyad-prog1 | 1 | 0 | 1 | 0 | no-content | `stay idle` |
| irsyad-tabung-jumaat | 1 | 0 | 1 | 0 | no-content | `check for cai's go-live verify result` |

## What this evidence does and does not show

**Does:** all six are novel (occ=1) dim suggestions on idle lanes, and on all six
`composer_parse_pane` returns `CC_N=0 / CC_EMPTY=1 / basis=no-content` — i.e. it reads
the composer as EMPTY, which is the correct answer. These are usable as
novel-dim-ghost fixtures with known-good labels.

**Does NOT:** reproduce the #23536 failure mode (novel suggestion read as *real staged
text*, `n=1`). On this build, on these six panes, the classifiers agree. That does not
refute #23536 — it was measured on a different pane state by another body — but it does
mean the mechanism is **not universal**, and any fix should be driven by a capture that
actually reproduces `n=1`, not by these.

`scripts/lib/composer_capture.sh` is unmodified in the working tree; its last commits
pre-date the #23536 report, so nothing was silently fixed in between.

## The separate, chronic fact these captures do not explain

`logs/agent-wake-subscriber.log` shows **cc-shipforge failing every wake with
`lane_nudge rc=3`** at 20:23, 21:24, 21:53, 22:41, 23:12 (2026-08-16 local) and 00:16
(08-17 local) — six consecutive lost wakes across six different bus rows, ≈5h.
`rc=3` is **ambiguous**: `lane_nudge.sh` returns it both for "refused to clobber real
staged text" AND for "could not verify submission after retries". The cause on
cc-shipforge is therefore **unverified** — do not assume it is the classifier.

---

## ADDENDUM — cc-fleet-health, 2026-08-16 ~17:0xZ (bus #23575; supersedes the two claims above)

Two claims above are **superseded by measurement + orch-console's own #23572**:
- "**Does NOT reproduce #23536**" (above) — **it does.** Parsing the SAVED captures
  OFFLINE reproduces the bug on ALL SIX: `composer_parse "$(cat <name>.e.txt)"` →
  `CC_N=1 CC_EMPTY=0 CC_GHOST=0 basis=real-text(dim)`, `CC_RAW` = the dim suggestion,
  for caai / cosem-adcda / ihsanos-platform / irsyad / irsyad-prog1 /
  irsyad-tabung-jumaat. These files **ARE** the `CC_N>0` artifact of #23536.
- "**cause unverified**" on shipforge — orch-console settled it in #23572 from
  `logs/lane_nudge_preserved_input.log`: all 7 shipforge `rc=3`s today were the
  REFUSE-on-staged-text branch (89 fleet-wide across 14 lanes). It **is** the classifier.

**Why the live table above says `CC_N=0` but the saved file parses `CC_N=1`** — the
saved `.e.txt` and the live `composer_parse_pane` were TWO SEPARATE capture calls. The
raw bytes settle it: e.g. `caai.e.txt` line 41 is `ESC[39m❯ ESC[2mcheck the bus again ESC[0m`
— the novel suggestion is **SGR-2 DIM text on the ❯ prompt line**. `_cc_extract`
(composer_capture.sh:260) STRIPS all SGR before parsing, so dim → indistinguishable from
real; it finds `❯`, extracts the text, `n=1`. The live table caught a **dim-ABSENT**
instant; the file caught a **dim-PRESENT** instant. The overlay is **transient** — the
discriminator is the capture INSTANT (the "moment" axis), not (on this evidence) pane
geometry. Geometry not excluded; the raw-capture logging (#23572) is what would settle it.

**Fixture caveats for the next reader:**
- As a **regression** fixture these still read as "classifier stays correct on novel dim
  ghosts" ONLY if you re-parse a dim-ABSENT capture. From THESE saved (dim-present)
  captures they read `CC_N>0` = the bug. State which capture instant you mean.
- A `CC_N=0` state never reaches `_probe_composer` (the probe only fires in `lane_nudge`'s
  `CC_N>0` REFUSE branch), so the probe does NOT cover the dim-absent reading of these.
- **Design point (#23575):** dim+novel is fundamentally ambiguous to any PASSIVE parse —
  byte-identical whether ghost or real dim queued work. The mutating probe (replace-vs-
  append) is the only correct separator.
