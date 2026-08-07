# cosem trainee credentials — scheme-change plan

**Owed to:** cai (CAI-RESP-556, dated deliverable) · **Author:** Nazim (orch-console) · **2026-07-25**
**Status:** plan for ruling. Nothing in it is built. The interim control is the operator's call and
cai has already put it to him (`operator_messages` #7126, recommending HOLD).

---

## 1. The defect, stated so it can't be mistaken for the export bug

A trainee's entire login is a pure function of fields we store about them:

| part | derivation | source |
|---|---|---|
| password | `deriveLoginPin(dob, last4)` = `DDMMYYYY + last4` | `src/shared/lib/login-credentials.ts:48` |
| handle | `syntheticTraineeEmail(askariyah, orgSlug)` | `src/shared/lib/login-credentials.ts:33` |

So **anyone with read access to trainee records can reconstruct any trainee's complete login.** No
export, no leak, no mistake required.

**The document fixes already shipped are not the fix.** The record PDF (239911b) and the routine
archive strip narrowed where the pieces travel *on paper*. They left the property untouched. Read
that sentence before reading any status that says "credential exposure closed."

**Blast radius:** ADCDA — a government client whose data cai already placed at T3-local-only
(CAI-525). 68 trainee records; 61 currently carry a login.

**Three surfaces found so far, all symptoms of the one property:**
1. the record PDF printed `dob` + `last4` adjacently — fixed, merged;
2. the archive ZIP carried `askariyah` + `dob` + `last4` — routine path stripped, exit path now
   gated + audited;
3. the intake **import template** asks the CLIENT to assemble all three in one spreadsheet — so a
   completed import file is a working-login list for a cohort, created outside our system and
   living in their mail and drives. **Unfixable in the template**: those are the right fields to ask
   for. It dissolves only when the credential stops being derived.

## 2. The invariant this must satisfy

**Never derive an authentication factor from stored PII** (cai, CAI-420 registry). Auth factors must
be independently generated, rotatable, and not reconstructible from any record we store, display or
export. The derived-credential registry built by `cc-cosem-exams` is the executable gate for it.

## 3. Target scheme

- **Initial credential: randomly generated**, per trainee, high entropy, stored only as a hash.
  Never a function of `dob`, `last4`, `askariyah`, `military_id`, or any other stored field.
- **Handle: keep `syntheticTraineeEmail(askariyah, orgSlug)`.** A predictable *username* is not the
  defect — a predictable *password* is. Changing the handle would churn every login for no security
  gain and break the client's mental model.
- **Forced rotation on first use:** the generated credential is single-use; first successful login
  requires setting a new one. This is what makes distribution survivable.
- **Delivery:** the generated credential is shown ONCE at provisioning, to the provisioning admin,
  and never retrievable afterwards — not from a screen, not from an export, not from the DB.
- **Reset path:** an admin-triggered regenerate that produces a new single-use credential and
  invalidates the old, audited with actor + timestamp + trainee.

## 4. Migration for the 61 existing logins

1. Generate a new single-use credential per existing trainee.
2. Invalidate the derived one at the same instant — no window where both work.
3. Hand the batch to the client's admin as a one-time distribution list through the gated,
   audited full-fidelity path (`exams.archive_export.full_fidelity`), not by email.
4. Every trainee's first login after cutover forces a set-new-credential step.
5. `deriveLoginPin` is deleted, not deprecated — a live derivation function with no callers is a
   loaded gun with the safety on.

## 5. Comms owed (drafts to cai before sending, per thread ownership)

- **Client (ADCDA, via the operator):** what changed, why, what they must do, and — plainly — that
  logins issued before the change were derivable by anyone with record access. No minimising.
- **Trainees:** delivered by the client, not by us; we supply the wording.
- **Hariz / Nahar:** the demo login convention (`DDMMYYYY + last4`) stops working. They rely on it.

## 6. Interim control — the operator's call, already with him

Options as cai put them (#7126): (a) force the reset now; (b) hold until the scheme ships;
(c) demo accounts only. **cai recommends (b); I agree.** The population that can currently derive a
login is the population already trusted with the underlying records, so a forced reset today buys
little and breaks the demo convention. My earlier lean toward (a) treated the exposure window as the
whole risk without asking who is inside it.

**If (b) holds, these are the conditions I'd attach:** no new client-facing artifact carries all
three fields (already enforced by the advisory gate); the import template is not sent to a new
cohort before cutover; and the scheme change is not allowed to slip quietly — hence the date below.

## 7. Sequencing, dates, and what would make me escalate

| step | owner | when |
|---|---|---|
| cai ruling on this plan | cai | on receipt |
| operator decision on interim (a/b/c) | operator | outstanding (#7126) |
| build target scheme + reset path (synthetic DB only, CAI-525) | cc-cosem-exams under Nazim | after ruling |
| comms drafts to cai | Nazim | with the build |
| migration of the 61 | Nazim + operator, scheduled with the client | after comms agreed |

**Escalate immediately, not on the schedule, if:** a real cohort's import file is sent or received;
any indication a derived credential was used by someone other than its trainee; or the next batch's
onboarding starts before cutover — that would multiply the exposed population rather than hold it.

## 8. Follow-on this plan creates

`exams.archive_export.full_fidelity` is registered but, by the capability model, every org admin
holds it (admin short-circuits `hasCapability`). That is harmless **today** precisely because the
credential is derivable — an admin needs no export to obtain logins. **Once §3 ships, that inverts:**
the gated export becomes the only route to a credential, and "admins hold everything by
construction" stops being harmless for this one entry. Revisit at cutover, not before.
