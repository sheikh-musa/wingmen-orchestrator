# cc-quality review — ihsanos PR #307 (CAI-928 Tabung-history import + Umum stats)

**Verdict: ✅ PASS (merge-ready).** The code faithfully implements the CAI-928 constraints and is safe to merge DORMANT: segregation is structurally enforced, PII is encrypt-only + fail-closed, real ingest is hard-blocked behind controller-auth, and the reporting RPC is properly secured. Verified the four high-risk cruxes directly + ran the unit tests. The migration-apply and real-ingest are cai-governed gates correctly OUT of this PR — I approve the CODE, not the apply. One coordination item flagged (documented).

- **Reviewer:** cc-quality (Head of Quality) — merge-readiness review, routed by cc-irsyad-coord (CI-green → cc-quality → merge). PII + money + migration sensitive.
- **PR:** #307 `lane/tabung-history` @ `aede6ec`, +1765/-1 across 24 files. CI green (unit + lint:all + tabung-correctness + tabung-synthtest + irsyad-frs; tsc 0). Prior opus whole-branch review PASS on all 6 CAI-928 constraints (2 mig182 findings fixed).
- **Reviewed (UTC):** 2026-08-15T14:55:54Z — read the two migrations + import core, ran the CAI-928 unit tests, via authoritative `gh pr diff` / `FETCH_HEAD`.

## Cruxes verified (direct code read + tests)

**(A1) Segregation / no-double-count — structurally enforced ✅**
`tabung_donor_lifetime_aggregates` (mig180): `basis TEXT NOT NULL DEFAULT 'imported-not-transacted' CHECK (basis = 'imported-not-transacted')` — the basis is **hard-pinned by a CHECK constraint**, so a row can never claim to be a transacted donation. No `donation_id`, separate table, never receipted, `NUMERIC(15,2)`, RLS on, `UNIQUE(org_id, import_ref)` idempotency, soft-delete, public_id. The `segregation.test.ts` actively asserts (a) no mapper-output key matches `/donation|receipt/i` (import can't fabricate a per-event donation/receipt), (b) every aggregate key is a real mig180 column (schema-drift guard), (c) basis pinned. CAI-928 B/b1 + CAI-903 I3 satisfied.

**(A2) PII — encrypt-only, NRIC never plaintext ✅**
`classifyLegacyId` splits NRIC/FIN (`/^[STFGM]\d{7}[A-Z]$/`) from benign; `map-row` routes NRIC → `encryptNric(nric)` on `persons.nric_encrypted` (+ `nric_source`), and sets `legacy_donor_ref = null` for NRIC — so an NRIC can **never** land in the benign ref, and no plaintext NRIC is persisted (encrypt-on-ingest). Data-minimised. CAI-928 a2 / [Satr] satisfied.

**(A3) Fail-closed DORMANT ingest gate ✅**
`assertIngestAllowed` (every ingest path passes through it): dry-run (`commit===false`) always allowed; a real `--commit` **hard-throws** ("INGEST BLOCKED (CAI-928 A)") unless BOTH `--pii-basis-ref` (documented Irsyad-controller authorization artifact) AND `--controller-confirmed` are present — "never silently downgrades to dry-run, never proceeds partially." Since the controller-auth artifact is a gate NOT in this PR, real ingest cannot run → ships DORMANT.

**(B) mig182 `umum_stats_by_type` RPC — properly secured ✅**
`LANGUAGE sql STABLE SECURITY INVOKER SET search_path = public, pg_temp` — INVOKER (not DEFINER — respects caller RLS, no privilege escalation) + explicit search_path (injection-hardened). SGT boundary correct: `(counted_at AT TIME ZONE 'Asia/Singapore')::date BETWEEN p_from AND p_to`. In-function `org_id = p_org_id` cross-org filter. `REVOKE ALL FROM PUBLIC, anon, authenticated; GRANT EXECUTE TO service_role` — server-action-only. Aggregate-only output (tin_type/count/total), no donor PII. Reads only.

**Tests:** ran the CAI-928 unit suite (`history-import/__tests__/*`, `tabung-umum-stats`, `umum-labels`) → **7 files, 25 tests passed** (segregation, ingest-gate, classify, map, tabung-type, stats, labels). CI green; migs wet-proven both silos (goumlyne + ceayj, unapplied).

## Governance boundary (correctly OUT of this PR)
The migration-apply (cai §6.6) and the real donor-PII ingest (documented Irsyad-controller authorization) are **cai-governed gates, deliberately not in this PR** — and rightly so. I am advisory-approving the CODE's merge-readiness; I do not (and per charter should not) adjudicate the PII-import authorization — cai cleared that (CAI-RESP-928), and the code faithfully fail-closes real ingest behind it. Merge sequencing per the PR: merge AFTER cai applies mig183→180→182 + PGRST reload.

## Coordination item to confirm (documented, not a PR defect)
mig180 adds `tabung_donor_lifetime_aggregates.person_id` — a NEW operational FK to `persons.id`. The donor merge/dedup RPC must **re-parent** this FK on merge (`UPDATE … SET person_id = survivor WHERE person_id = loser`), and it is correctly registered on the CAI-927 re-parent list (documented in `docs/clients/irsyad/tabung-history-merge-fk-note.md`, classified re-parent-not-freeze). **Ensure the merge-RPC re-parent update is coordinated with the mig180 apply** (land it with/after mig180, or have the merge-RPC guard for the table's presence) so a donor merge can't fall in the gap between mig180-apply and the merge-RPC update (an unhandled FK on a re-parent could fail or orphan an aggregate). The PR flags this; just confirm it lands in the apply sequence.

## Bottom line
A careful, governance-respecting PR. Segregation is enforced at the schema level (basis CHECK), PII is encrypt-only and data-minimised, real ingest is hard-blocked behind the controller-auth gate (dormant), and the reporting RPC is INVOKER + search_path-hardened + service_role-only + SGT-correct. Cruxes verified directly, 25 unit tests green, CI green, migs wet-proven. **Merge-ready** — subject to the stated cai-apply sequencing and the merge-RPC re-parent coordination. The migration-apply and real ingest remain cai's gates, as designed.

— cc-quality
