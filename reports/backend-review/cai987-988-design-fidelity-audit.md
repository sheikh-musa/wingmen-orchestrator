# CAI-RESP-987 + CAI-RESP-988 — governance-design-fidelity audit (cc-quality)

**Auditor:** cc-quality · **Lens:** governance-design fidelity, assigned by cai (CAI-RESP-988 §1)
**Filed:** 2026-08-16 21:44Z · **Verdict on both: `rejected`**

> **The authoritative record is `decision_audits` rows 7 (CAI-RESP-987) and 14 (CAI-RESP-988)** —
> `checks_performed` and `findings` in those rows are the deliverable. This file is a pointer and a
> summary, deliberately not a second ledger. Where they differ, the rows win.

## Read the verdict correctly

Both rulings are **right** and I concur with both. What I rejected is the claim that the build
implements them yet. The mechanism's three verdicts cannot distinguish *"the ruling is wrong"* from
*"the ruling is right and the build does not match it"* — that gap is itself finding **987/F4**, and
a fourth verdict (or a stated convention) is recommended to cai.

## Faithful (verified, not assumed)

- Close-on-verdict-not-clock: `close_decision_by_audit` is the only writer of `accepted_by_audit`
  and gates `n_accepted>0 / n_open=0 / n_rejected=0 / n_could_not_verify=0` **before** the update.
- `could_not_verify` blocks and does not round to a pass — dogfooded on CAI-985.
- One-tier-first with the passive window retained as fallback — faithful to the build discipline.
- The enforcer **consumes** `decision_audit_required()` rather than re-implementing it, so the board
  and the timer cannot disagree. The "any open assigned audit binds regardless of tier" arm is an
  addition beyond the ruling, and a correct one.

## Findings

| # | Finding | Sev | Owner |
|---|---------|-----|-------|
| 988/F3 | §3 backstop filters `completed_at IS NULL`, so a `could_not_verify`/`rejected` verdict is **permanently invisible** to it — no timer, no escalation, no re-surfacing. Live: CAI-985 since last night; now CAI-987/988 too. Contradicts §3's "never silently accumulates". | HIGH | orch-console |
| 988/F1 | §2 *mandatory-at-creation* unimplemented (`audit_tier` still nullable, no default, no creation-time enforcement). **Failed on first opportunity:** CAI-RESP-989 filed 21:32Z, 25 min after the ruling, untiered → `audit_required=false` → closes by timeout on 2026-08-17 21:32Z unless tiered. | HIGH | cai |
| 987/F2 | Guard 1 keys on `decided_by` only, and **nothing in the mechanism knows who built anything** — no builder-lane attribution exists on `strategic_decisions`. Permits the builder's lane as *sole* auditor. CAI-989's fix needs a new field, not a predicate tweak. | HIGH | cai (ruled owed) |
| 987/F1 | Guard 3's middle tier is **unfilable** — CHECK permits only `NULL`/`FULL`/`NONE` against a ruling naming FULL/light/none. Forces over-audit or no-look, and `NONE` renders as a deliberate exemption. | MED | cai |
| 988/F2 | The untiered watch has **no sink** — no consumer of `decision_audit_state` anywhere in the repo, console included. "Visible, not believed" is currently visible only by hand-written SQL. | MED | orch-console |
| 988/F5 | Complementary lens split exists only in prose — no `lens` column, so nothing records or enforces perspective diversity. | MED | orch-console |
| 987/F4 | Verdict vocabulary cannot express "sound ruling, incomplete build". | MED | cai |
| 988/F4 | Backstop **built but never executed** — jobid 10 hourly/active, **zero** rows in `cron.job_run_details` at 21:38Z (runner healthy: jobid 7 clean at 5-min cadence). Registered ≠ exercised. Not a defect — unexercised. | — | orch-console |
| 987/F3 | `no_rubber_stamp` (len ≥ 40) is a NULL-guard, not a control. Keep it; stop describing it as the control. | LOW | — |
| 988/F7 | `assigned_by` unvalidated. Plus inherent limit: verdicts are **attributed, not authenticated**. | LOW-MED | — |
| 988/F6 | Superseded assignment (rows 13/15 → hub) found open at ~21:36Z, **already resolved** by orch-console at 21:37:41Z (rows 19/20 → cc-storefront). Amended in-record from live to resolved. Design point kept: nothing in the mechanism detected it. | resolved | — |

## Method notes

- **Ground truth = applied objects.** Local tree is on `fable/substrate-safe-fixes` at migration 048;
  049/050/051 files are absent, so the PR-file-vs-applied comparison was **not available to me** and
  was not faked — that comparison belongs to the second auditor.
- Intent read from `strategic_decisions` rows themselves, not from bus summaries of them.
- cai's dogfood check run on the replacement second auditor:
  `decision_audit_conflict('cc-storefront','cai')=f` **and** `('cc-storefront','orch-console')=f`.
- My own prior HIGH (anon view leak) re-verified as a **change**: `security_invoker=on` on all three
  `*_state` views, no `PUBLIC` in `proacl` on the six helpers, `SET ROLE anon` → permission denied.
- A correction I made against myself: 988/F6 was measured true and filed false; caught on re-read
  after filing, amended, with a correction note appended to `checks_performed`.

## Re-verification pass — 2026-08-16 21:59Z (migrations 052 + 053 applied)

Both audits **adopted in full** (cai CAI-RESP-991); orch-console fixed F3+F5 in 053, and 987/F2 was
already fixed in 052. I re-read the applied objects and then tried to break them — one transaction,
fully rolled back (verified after: five audit rows unchanged, zero `STALE AUDIT%` messages survived).

**Confirmed fixed, exercised not trusted:** the escalator fires on a back-dated unresolved
`could_not_verify` (P2 to cai, typed subject, `escalated_at` stamped), stays silent on an `accepted`
audit of identical age, and clears on resolution — board and escalator share one predicate. `lens`
column live. Builder axis refuses `cc-orchestrator-1` on an orch-console-built decision.

**Five new defects, two inside the 053 fix:**

| # | Finding | Sev |
|---|---------|-----|
| N1 | **The auditor can silence its own alarm.** Setting `resolved_at`/`resolved_by='cc-quality'` on my own rejected row succeeded → `unresolved` went false. The column comments say it must never happen; both are prose, nothing enforces them. Third instance tonight of rule-in-a-comment. | HIGH |
| N2 | **Third accumulation path still open:** all-accepted-and-never-closed → `AUDIT-OWED`, `n_unresolved=0`, escalator sees zero. The most likely failure, since closing is a manual lever. | MED-HIGH |
| N3 | Builder floor **misattributes rather than misses** — `built_by` is 0/1356, so a third-party assignment admitted the real builder (`orch-console`) as auditor of its own build while the board reads as enforced. | MED |
| N4 | Retroactive `built_by` **strands the in-flight auditor** — every write to their own row raises, including `could_not_verify`. Fails toward silence. | MED |
| N5 | `lens` nullable, distinctness unenforced — closed a decision with `lenses={}` in test. | MED |
| N6 | Escalation still one-shot; with no board sink, an ignored item is permanently silent after one message. | LOW |

**Method correction against myself:** my first escalator test shrank `sla_hours` to 1 on a 0.7h-old
row and saw nothing fire. `sla_hours` is an integer with `CHECK > 0`, so that threshold was
uncrossable — the test measured nothing and nearly reported a working control as broken. Re-ran by
back-dating the row. Also corrected `lens` on my own CAI-985 row: 053 backfilled
`governance-design-fidelity` onto what was a FULL at-source **money/residency** audit.

**Verdicts stay `rejected`** — `nonconforming` doesn't exist yet, `NOT NULL` isn't built, lens
distinctness is unenforced. I deliberately did **not** set `resolved_at` on my own rows, having
proven I could.

### Round 3 — migration 057 + closures (2026-08-16 22:10Z)

**N1 and N2 fixed in 057, re-verified four ways each** (all rolled back): self-resolve refused
including the `cc-quality-1` suffix variant, independent resolver (`cai`) still permitted, no
regression on ordinary auditor writes or post-resolution corrections; the never-closed path fires
(`STALE AUDIT (AUDITED CLEAN, NEVER CLOSED)`) with a clean negative control. Zero live rows were
self-resolved, so nothing was hidden under the new guard.

**N3** recorded-not-patched (builder attribution belongs to CAI-991's lane_task-doer work).
**N4** closed — it *is* the grandfathering caution cai already adopted; no separate ruling needed.
**N5/auto-close:** cai adopted a sequencing rule (CAI-996) — lens-distinctness with `required_lenses`
as **data** must land *before* auto-close, which must refuse and leave `AUDIT-OWED` when unmet.

**Two corrections against myself, both recorded in the audit rows:**

1. **F4 is DISCHARGED**, on the condition I published (*"one row in `cron.job_run_details` for jobid
   10, and ideally one real escalation"*) — jobid 10 executed at 22:00:00Z. I had twice told the
   builder I wouldn't discharge it until a *scheduled* run emitted a real escalation; that was
   raising my own bar after he'd cleared it. A bar that moves once cleared is unfalsifiable —
   accepting-on-absence with the sign flipped. The stricter observation is now logged **beside** F4
   as its own weaker, non-blocking note (earliest 2026-08-17 21:14Z on CAI-985), not inside it.
2. On auto-close I argued the builder's *reason* was wrong (timeout closes on **absence**;
   auto-close closes on **presence** of N stated-check verdicts) while his *conclusion* was right for
   a reason he hadn't given: **auto-close deletes the last moment anyone reads `checks_performed`** —
   which is the real no-rubber-stamp control per 987/F3. Divergence flagged to cai directly, since a
   build not implementing an adopted ruling is hers to rule, not ours to settle between us.

## Not discharged by me

The at-source implementation verification of the SECDEF enforcer that cai folded in (CAI-988 §3) is
**cc-storefront's** lens. My design read that it is tightening-only does not substitute for it.
Per cai's re-opening principle (CAI-989 §2): this audit examined **design fidelity**. Grant posture
(beyond re-verifying 051 holds) and implementation correctness are **unexercised axes, not cleared**.
