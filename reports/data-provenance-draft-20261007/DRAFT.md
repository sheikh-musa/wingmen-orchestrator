# data_provenance draft — the 13 client refs (bus #58159 item 4a)

**STATUS: DRAFT. NOTHING IN THIS FILE HAS BEEN APPLIED.** Per the item-4a
instruction ("DON'T apply: classification goes to orch-console to confirm
first"), this is a proposal for orch-console to confirm, edit, or reject —
not a migration, not an INSERT that ran. Owner: cc-substrate. Due 2026-10-09.

## Why

E-client-lanes-quality.md §2/§4 (reports/fable-audit-substrate-20261006/):
`data_truth.py classify <ref>` was tried against 13 distinct client refs in
the audit window; 12 came back UNCLASSIFIED. Investigating each one (this
file) shows the 12 split into three very different cases — the fix is NOT
"add 12 more data_provenance rows for 12 more literal strings." Doing that
would itself be a LAYER-VOCAB-001 violation: several of those 12 strings are
bare product/channel names, not project refs, and `classify()` is a project_ref
lookup with no alias resolution (`scripts/data_truth.py` — no name-inference,
by design, bus #51670).

## The 13 refs, resolved

| # | Ref tried by callers | What it actually is | Backing project_ref | Already classified? |
|---|---|---|---|---|
| 1 | `irsyad` | irsyad silo | `goumlynecruxrlmzlntp` | **Yes** — REAL, migration 089 |
| 2 | `cosem` | bare product name for cosem-platform | `ywrpttpxwfcoodovxhsr` | **Yes** (store-level MIXED, migration 089) — caller used the bare alias instead of the ref |
| 3 | `cosem-platform` | same store as #2 | `ywrpttpxwfcoodovxhsr` | **Yes** — same as #2 |
| 4 | `cosem-exams` | the exams app lives in the cosem-platform demo/dev project — it is NOT a separate silo (confirmed: `scripts/apply_migration.py` `PRODUCTION_SILOS` lists exactly 5 refs and there is no 6th cosem-exams entry; D10's "gated production silo" for PR#293's `skill_assessments` policy is this same `ywrpttpxwfcoodovxhsr`) | `ywrpttpxwfcoodovxhsr` | **Yes** — same store-level MIXED row covers it; no new row needed |
| 5 | `cosem-adcda` | bare name for the real ADCDA Firebase app | `cosem-adcda-cb6d9` (Firebase) | **No — proposed below** |
| 6 | `cosem-tdu` | bare name for the TDU Firebase app(s) | `tdu-tools-prod` + `tdu-tools-staging` (Firebase) | **No — proposed below** |
| 7 | `mamadah` | alias for the operator's private Zahidah/second-brain data | `brrgastulcffamlbggyu` (wingmen-personal) | **Yes** — docs/data-store-registry.md literally names "Zahidah second-brain (mamadah)" as this store; migration 089's store-level REAL row already covers it |
| 8 | `second-brain` | same thing as #7, different alias for the same product | `brrgastulcffamlbggyu` | **Yes** — same row as #7 |
| 9 | `oeh` | the offshoreentertainment.events site clone (migration 071) — **no database**. 071's own header: a static-first Next.js rebuild, no supabase/firebase ref anywhere in its onboarding | none provisioned | N/A — no data store exists to classify yet |
| 10 | `angullia` | SG brochure/booking site (migration 070) — **no database**. 070's own header: "no personal data in scope yet — the site holds none unless/until a booking form is added" | none provisioned | N/A — no data store exists to classify yet |
| 11 | `coffeemedia` | the Coffee Media GLM lane (`nervous_system/operator_log.py` line ~172, op#24578/24592) — talks to Musa over Telegram (`@wingmendevbot`), comms-only, no DB reference found anywhere in the repo | none provisioned | N/A — no data store exists to classify yet |
| 12 | `finance` | `finance-console` bot channel, `audience='internal'` (migration 091) — this is the operator's own internal business data, not a client/tenant store | **unresolved — see below** | **No — flagged, not drafted** |

That's 12 distinct strings resolving to: 1 already-classified ref (irsyad,
not actually unclassified — it's the one that worked), 5 that alias
ALREADY-classified rows (cosem/cosem-platform/cosem-exams → one store;
mamadah/second-brain → one store), 3 with no data store at all, 3 genuinely
new refs needing a row (cosem-adcda-cb6d9, tdu-tools-prod,
tdu-tools-staging), and 1 I could not resolve with evidence (finance).

## Proposed new data_provenance rows (DRAFT — not applied)

Evidence bar per migration 089's schema comment: "must cite a
script/commit/migration/bus-msg id — never a slug or name." All three below
meet that bar from `docs/data-store-registry.md`'s existing Firebase table
and its op# citations.

```sql
-- DRAFT ONLY. Do not apply without orch-console confirmation (bus #58159 item 4a).
-- If confirmed, this becomes migrations/09X_data_provenance_firebase_apps.sql,
-- applied via the gated apply path (scripts/apply_migration.py --gate), never
-- a raw psycopg INSERT against the substrate.

insert into data_provenance
  (project_ref, org_id, org_name, alias, classification, evidence, owner, created_by)
values
  ('cosem-adcda-cb6d9', '', null, 'cosem-adcda app (Firebase)',
   'REAL',
   'docs/data-store-registry.md "cosem-tdu subject data" section + CAI-RESP-1340 containment note: real ADCDA gov-PII (Emirates ID, DOB) for real minors; this is the Firebase project backing the cosem-adcda repo (REPOS.json firebase_project=cosem-adcda-cb6d9)',
   'cosem', 'cc-substrate'),

  ('tdu-tools-prod', '', null, 'cosem-tdu app, prod (Firebase)',
   'REAL',
   'docs/data-store-registry.md: "cosem-tdu app (prod, default) — TDU — production", region confirmed op#22426 (2026-09-26); REPOS.json firebase_project=tdu-tools-prod for the cosem-tdu repo; staff/trainee records (faceDescriptor, phoneNumber, idNumber fields per firestore.rules)',
   'cosem', 'cc-substrate'),

  ('tdu-tools-staging', '', null, 'cosem-tdu app, staging (Firebase)',
   'REAL',
   'docs/data-store-registry.md flags this project''s region as "unverified — PERMISSION_DENIED checking region (op#22426, 2026-09-26); do not retry or attempt to bypass, escalate instead." No evidence it holds only synthetic data, and a staging DB that mirrors prod''s schema is treated as REAL per the fail-safe direction (classify() comment: unregistered defaults to REAL, never to "safe to treat as synthetic") until someone actually checks its contents.',
   'cosem', 'cc-substrate')
on conflict (project_ref, org_id) do nothing;
```

## Unresolved — needs orch-console input, not drafted

**`finance`**: confirmed internal (`bot_channels.audience='internal'` per
migration 091, alongside `nazim-console`/`operator-orch`/`cai-channel`/
`war-room`), so this is the operator's own business data, not a
client/tenant residency question — but I could not find which actual store
holds it (no Supabase/Firebase project ref, no migration, no schema
reference for "finance" anywhere in this repo). I am NOT drafting a row
keyed by the literal string `finance` — doing that would be exactly the
bare-name-as-ref anti-pattern this whole exercise exists to fix. orch-console:
please name the backing store (or confirm there isn't one yet, same as
oeh/angullia/coffeemedia) and I'll draft the row.

## Recommendation beyond this draft (not actioned here — scope is drafting only)

5 of the 12 unclassified refs (`cosem`, `cosem-platform`, `cosem-exams`,
`mamadah`, `second-brain`) are not missing data — they're callers (and/or
`bus_send.py`'s own WARN-check) passing a bare alias where `classify()`
needs a project_ref. Adding more data_provenance rows can't fix that; the
real fix is an alias→project_ref resolution step in front of
`scripts/data_truth.py classify`, so a caller can type the name they
actually know (per E's own fix item 4 / SYNTHESIS item 10, owned by
cc-substrate separately from this draft). Flagging it here so it doesn't
get lost, not implementing it in this PR.
