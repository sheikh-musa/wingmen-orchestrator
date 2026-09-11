# Data Processing Agreement (DPA) — ADCDA ↔ Wingmen (cosem training-exam platform)

> **DRAFT — for cai governance review + operator approval. NOT for sending to the client.**
> This is a practical first draft, **not legal advice**. A cross-border transfer of UAE
> government / military-linked personal data to Singapore is involved — see the companion
> **`uae-pdpl-lawfulness-assessment.md`** in this folder. Several clauses below are gated on
> open legal questions that require **qualified UAE counsel** to answer before any real ADCDA
> personal data is written to the Singapore store. Do not treat the existence of this DPA as
> a substitute for that opinion.

---

## 0. CROSS-BORDER STORAGE DISCLOSURE (read this first — it is the heart of this Agreement)

**Plain statement, deliberately not buried:** The personal data ADCDA entrusts to Wingmen under
this Agreement **will be stored and processed in Singapore**, on Amazon Web Services infrastructure
in the **`ap-southeast-1` (Singapore) region**, operated through the Supabase managed-database
platform. **Singapore is a jurisdiction outside the United Arab Emirates.** The data does **not**
reside in the UAE and is **not** stored on infrastructure physically located in the UAE or Abu Dhabi.

This means:

- ADCDA's trainee personal data physically leaves the UAE and is at rest in Singapore.
- The data is subject to the laws of Singapore (including lawful-access powers of Singapore
  authorities) in addition to the UAE laws that bind ADCDA as controller.
- This is a **cross-border transfer** of personal data for the purposes of the UAE's data-protection
  framework, and ADCDA's acknowledgment and agreement to it is recorded expressly in **clause 8**.

ADCDA should read clauses **7 (cross-border transfer basis)** and **8 (controller's express
acknowledgment)** together with this disclosure before signing.

---

**This Data Processing Agreement** ("Agreement") is entered into between:

- **Abu Dhabi Civil Defence Academy ("ADCDA")**, a government body of the Emirate of Abu Dhabi,
  United Arab Emirates, of **[official address]** (the **"Controller"**); and
- **[Wingmen legal entity name]**, **[jurisdiction of incorporation + registration number]**, of
  **[registered address]**, operator of the "cosem" training-examination platform (the **"Processor"**).

**Effective date:** **[____]**

It governs the Processor's processing of personal data on the Controller's behalf in connection with
the operation of the cosem training-examination platform ("the Service"), a Next.js + Supabase
application used to administer and record training examinations for the Controller's trainees.

**Governing data-protection framework.** As between the parties, the **controller-side** law is the
UAE's **Federal Decree-Law No. 45 of 2021 on the Protection of Personal Data ("UAE PDPL")** and any
public-sector / government-data rules applicable to ADCDA as an Abu Dhabi government body (see the
companion lawfulness assessment — this is an open question, clause 15.3). The **processor-side**
Singapore operations are additionally subject to the **Singapore Personal Data Protection Act 2012
("Singapore PDPA")**. Where those frameworks impose different obligations, the Processor will apply
the **more protective** standard to the Controller's data.

---

### 1. Roles

1.1 The **Controller (ADCDA)** determines the purposes and means of processing the personal data and
owns the trainee personal data. The **Processor (Wingmen)** processes that personal data **only on
the Controller's documented instructions**, as set out in this Agreement.

1.2 The Controller confirms that, **subject to the open questions in clause 15**, it has the lawful
authority under UAE law and any applicable Abu Dhabi government-data rules (a) to hold the personal
data described in clause 3, (b) to engage the Processor to process it for the purpose in clause 2,
and (c) to authorise the cross-border storage described in clause 0 and clause 7. *(Drafting note:
this is a controller representation; it does not relieve the parties of confirming clause 15 with
UAE counsel before go-live.)*

### 2. Purpose & scope of processing (purpose limitation)

2.1 The Processor will process the personal data **solely** to operate the cosem Service for the
Controller: administering training examinations, recording candidate identity for exam integrity,
scoring, results, and reporting to the Controller.

2.2 The Processor will **not**: use the data for marketing; sell, rent, or disclose it to any third
party except the sub-processors in clause 6; use it for any other client, tenant, or product; train
any model on it; or use it for any purpose beyond clause 2.1.

2.3 The Processor processes on documented instruction only and will notify the Controller if, in the
Processor's view, an instruction appears to breach applicable data-protection law.

### 3. Categories of data and data subjects

3.1 **Data subjects:** the Controller's trainees (and, as applicable, examination staff) at the Abu
Dhabi Civil Defence Academy.

3.2 **Personal data (categories, described generically):**
- Names, in **Arabic and English** script.
- **Government / staff / military-linked identifiers** — e.g. staff or service identification
  numbers.
- **Rank / grade** and organisational unit.
- Examination records: enrolment, candidate–exam linkage, responses, scores, results, and timestamps.

3.3 The parties acknowledge that, because the data set includes **government / military-linked
identifiers of a UAE government body's personnel**, it is **potentially sensitive from a national /
governmental standpoint** independent of ordinary personal-data sensitivity. This bears directly on
the open questions in clause 15 and on the Controller's own classification of the data under any
applicable Abu Dhabi government-data-classification standard. **No special-category / biometric data
is in scope** unless separately agreed in writing and re-reviewed.

### 4. Processor obligations

The Processor will:

4.1 Process only on the Controller's documented instructions (this Agreement).

4.2 Ensure persons authorised to process the data are bound by confidentiality.

4.3 Implement and maintain the technical and organisational security measures in clause 5.

4.4 Assist the Controller in meeting its obligations to data subjects (access, correction, and any
rights under the UAE PDPL / applicable rules) within a reasonable period (clause 11).

4.5 Notify the Controller **without undue delay and in any case within 72 hours** of becoming aware
of a personal-data breach affecting the data, with the information the Controller needs to meet its
own notification obligations to the UAE Data Office and/or any Abu Dhabi authority (clause 10).

4.6 Make available to the Controller the information necessary to demonstrate compliance with this
Agreement, and submit to audit / evidence requests on reasonable notice.

4.7 Not engage a new sub-processor, and not change the storage region in clause 5.1 / clause 7,
without the Controller's prior written consent (clause 6, clause 7.4).

### 5. Security measures

5.1 **Storage location & tenancy:** the personal data is stored in **Singapore (AWS
`ap-southeast-1`)** via Supabase (clause 0). The Controller's data is held in a **single-tenant /
logically isolated** store, separated from every other client's data.

5.2 **Encryption:** encryption **at rest** for the stored data (including the government/staff
identifiers) and encryption **in transit** (TLS) for all data movement.

5.3 **Access control:** **organisation-scoped row-level access** so that only the Controller's
authorised roles can read the Controller's rows; **writes restricted to the service role** (no
end-user or anonymous write path to the data); least-privilege administrative access on the
Processor side.

5.4 **Audit:** security-relevant and access events are recorded in an **append-only, tamper-evident
audit log** (hash-chained), retained for the audit period and available to the Controller.

5.5 **Data minimisation:** only the data necessary for the purpose in clause 2 is collected and
retained in identifiable form; identifiers are not duplicated into logs or analytics in clear form.

5.6 **No unnecessary egress:** the data is not copied out of the Singapore store other than as
required to operate the Service and as disclosed in this Agreement.

### 6. Sub-processors

6.1 The Controller authorises the following sub-processors:
- **Supabase** (managed database / platform) and its underlying infrastructure provider
  **Amazon Web Services (AWS)**, region **`ap-southeast-1` (Singapore)** — hosting, database, storage.
- **[any others — list, e.g. email/notification provider — or state "none"]**.

6.2 The Processor imposes on each sub-processor data-protection obligations no less protective than
this Agreement, and remains liable to the Controller for each sub-processor's performance.

6.3 The Processor will give the Controller **prior written notice** of any proposed new sub-processor
or any change of storage region, and the Controller may object on reasonable grounds; absent
resolution, the Controller may terminate for that change (clause 13).

### 7. Cross-border transfer basis (UAE PDPL)

7.1 The parties record that the storage described in clause 0 is a **cross-border transfer of personal
data outside the UAE**, and that the intended lawful basis for it under the UAE PDPL is **[to be
finalised with UAE counsel — see clause 15]**, expected to rest on one or more of:
(a) the Controller's **express written consent / authorisation** as controller and, where required,
data-subject consent; **and/or**
(b) a **contract that binds the Processor (and its sub-processors) to protections consistent with the
UAE PDPL** — this Agreement being intended to serve as that contractual safeguard.

7.2 The Processor undertakes, through this Agreement and its sub-processor terms (clause 6), to apply
protections to the transferred data **consistent with the UAE PDPL standard**, and to submit to the
Controller's audit and to applicable supervisory/judicial oversight in respect of the data.

7.3 The parties acknowledge that, as at the effective date, the UAE has **not published an official
list of "adequate" jurisdictions**, and the UAE PDPL **Executive Regulations remain unissued**;
accordingly the transfer basis is documented on a **good-faith, contract-and-consent** footing and is
**subject to revision** when the Data Office issues adequacy determinations or standard contractual
clauses, or when UAE counsel advises otherwise (clause 15).

7.4 The Processor will **not** move the data to a different region or country, or add a storage
location, without the Controller's prior written consent and a re-assessment of the transfer basis.

### 8. Controller's express acknowledgment of foreign-jurisdiction storage

**8.1 The Controller (ADCDA) expressly acknowledges and agrees that:**
- (a) the personal data described in clause 3 **will be stored and processed in Singapore (AWS
  `ap-southeast-1`)**, a jurisdiction **outside the UAE**, as set out in clause 0;
- (b) it has been informed of this **clearly and in advance**, and that this fact has **not** been
  buried or glossed over;
- (c) the data will be subject to Singapore law, including the lawful-access powers of Singapore
  authorities, in addition to UAE law;
- (d) it authorises this cross-border storage on the basis in clause 7, **as controller of the
  data**, having satisfied itself (with its own legal advisers) of its authority to do so under UAE
  law and any applicable Abu Dhabi government-data rules.

**8.2** This acknowledgment is given by a signatory **authorised to bind ADCDA** (see the signature
block and drafting note). The Controller may withdraw this authorisation on written notice, triggering
the return/deletion and, if applicable, migration provisions (clauses 9 and 13).

### 9. Retention & deletion

9.1 The Processor retains examination records for **[retention period — to be confirmed with ADCDA;
align to ADCDA's own records-retention rules]**.

9.2 Personal data **not required** for that retained record is purged or de-identified once no longer
needed for the purpose in clause 2.

9.3 On termination, the Processor will, at the Controller's choice, **return or securely delete** all
personal data (and existing copies, including from the Singapore store and backups) within **[30]
days**, and **certify deletion**, except where retention is required by applicable law.

### 10. Breach notification

10.1 The Processor notifies the Controller of a personal-data breach **within 72 hours** of becoming
aware (clause 4.5), with: nature of the breach, categories and approximate number of records affected,
likely consequences, and remediation taken/planned.

10.2 The Controller remains responsible for any onward notification to the **UAE Data Office** and/or
any Abu Dhabi authority; the Processor assists as needed.

### 11. Data-subject rights

11.1 The Processor assists the Controller in responding to data-subject requests recognised under the
UAE PDPL / applicable rules (e.g. access, correction, objection, and — where applicable — erasure and
portability), by providing the necessary technical means within a reasonable period. The Controller
remains the party that adjudicates and responds to such requests.

### 12. Data portability / no lock-in

12.1 The Controller may request an export of its data in a structured, commonly used, machine-readable
format **at any time**, at no unreasonable cost, including a full export to support **migration of the
data to a UAE-resident store** should that be required by law or by ADCDA (clause 15 / clause 13).

### 13. Migration / region-change right (residency contingency)

13.1 The parties acknowledge that UAE counsel's opinion (clause 15) or a subsequent UAE / Abu Dhabi
requirement may **mandate that the data reside in the UAE**. If so, the Controller may require the
Processor to **migrate the data to a UAE-resident store** (or to cease processing and delete), and the
Processor will cooperate to do so on a reasonable timeline at **[cost allocation — to be agreed]**.
This clause exists because the current Singapore storage is **contingent** on the lawfulness
assessment, not a settled endpoint.

### 14. Liability, term & termination

14.1 This Agreement takes effect on the effective date and continues for the duration of the Service.
Either party may terminate on **[30] days'** written notice, or immediately for a material unremedied
breach, or under clauses 6.3 / 8.2 / 13.

14.2 Each party complies with its respective obligations under applicable data-protection law. Nothing
in this Agreement limits a data subject's rights under applicable law.

### 15. Open legal questions gating go-live (must be resolved before real data is written)

15.1 Whether the UAE PDPL's cross-border-transfer regime (its adequacy / consent / contractual-safeguard
mechanisms) **even applies** to ADCDA, given that the PDPL appears to **exclude government data and
government entities from its scope** — in which case a **separate public-sector / government-data regime**
governs, and this DPA's clause 7 basis must be re-grounded on that regime.

15.2 Whether the government / military-linked nature of the data (clause 3.3) triggers **data-localization
or national-security restrictions** — including Abu Dhabi government-data-management and data-classification
rules — that could **prohibit storage outside the UAE** altogether.

15.3 Which UAE / Abu Dhabi authority (if any) must **approve or be notified of** this arrangement, and
whether a formal UAE-counsel opinion is a **precondition** to any real data write.

**Until clause 15 is resolved by qualified UAE counsel, this Agreement is a documented good-faith
framework only; the parties should not treat signature as authority to write real ADCDA personal data
to the Singapore store.** *(This mirrors the bottom line of the companion lawfulness assessment.)*

### 16. Governing law & dispute resolution

16.1 **[To be agreed — flag for counsel.]** Governing law and forum are **not** pre-decided in this
draft: a UAE government controller will typically require **UAE / Abu Dhabi law and forum**, whereas the
processor-side template defaults to Singapore. This must be settled with counsel and is **not** a
drafting default to be inherited from the Singapore template.

---

**Signed for and on behalf of the Controller (Abu Dhabi Civil Defence Academy):**

Name: **[signatory]** ____________  Title: **[signatory title — TBD]** ____________
Signature: ____________  Date: ________

**Signed for and on behalf of the Processor ([Wingmen entity]):**

Name: **[signatory]** ____________  Title: **[signatory title]** ____________
Signature: ____________  Date: ________

---

*Drafting notes (delete before sending):*
1. **Controller signatory MUST be a person legally authorised to bind ADCDA** (an authorised ADCDA
   government official), **not** a vendor-side coordinator, project liaison, or any Wingmen-adjacent
   contact. Verify the delegation of authority before signature. A signature by anyone who cannot bind
   the government body does not create the controller consent this arrangement depends on.
2. Confirm the exact **Wingmen legal entity**, its jurisdiction of incorporation, and registration
   number.
3. **Resolve clause 15 with qualified UAE counsel before any real data write** — see
   `uae-pdpl-lawfulness-assessment.md`. The residency direction (Singapore vs mandatory UAE) is genuinely
   open and may be decided against Singapore.
4. Confirm retention (clause 9) against ADCDA's records rules; confirm governing law (clause 16).
5. This is a practical processor agreement, **not legal advice**. Both a UAE-side and a Singapore-side
   qualified review are advisable given the cross-border, government-data posture.
