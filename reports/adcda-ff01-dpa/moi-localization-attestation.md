# Ministry of Interior — Data-Governance Attestation
## Cross-Border Hosting of ADCDA Trainee Personal Data

> **SHAPE PRE-BLESSED by cai (CAI-RESP-1406) — cleared to go to the MoI signatory. The COMPLETED, SIGNED document must return to cai (who signed, exactly what was ticked, and the reference if (B)) BEFORE any real data write — template approval is not document approval.**
> **Purpose:** This attestation exists to answer ONE question that a Data Processing Agreement and ordinary consent cannot answer: **is this specific data class permitted, under the controlling UAE / Abu Dhabi government-data rules, to be stored and processed OUTSIDE the UAE (in Singapore)?** It is to be completed by an authorized representative of the Ministry of Interior (MoI) / Abu Dhabi Civil Defence Academy (ADCDA) who can speak to MoI's own data-classification and governance rules. It is relied upon by Wingmen (the processor) as the client's authoritative statement of its own regulatory position.

---

**Controller (government body):** [Ministry of Interior — United Arab Emirates / Abu Dhabi Civil Defence Academy (ADCDA), a body under the MoI] — confirm exact legal entity name.

**Processor:** [Wingmen legal entity], operator of the cosem training-examination platform.

**Data in scope (the "Data"):** the personal data of ADCDA trainees processed on the cosem platform — specifically: staff / military-linked identification numbers, rank, and names (Arabic and English), together with training-examination records associated with those individuals.

**Proposed hosting location:** Singapore — Amazon Web Services region `ap-southeast-1` — which is **outside the territory of the United Arab Emirates**. (Data is logically isolated to a single tenant, encrypted at rest and in transit, org-scoped access, service-role-only writes, append-only audit log — full terms in the accompanying Data Processing Agreement.)

---

### Attestation

The undersigned, as an authorized representative of the Ministry of Interior, having considered the Ministry's own data-classification, information-security, and data-governance rules as they apply to the Data described above, attests that **ONE** of the following is true (tick the applicable statement — do not sign if none is true):

- [ ] **(A) No localization requirement applies.** Under the controlling UAE / Abu Dhabi government-data rules, the Data described above is **not subject to any requirement that it reside within the United Arab Emirates**, and it is **permitted to be stored and processed in Singapore (AWS `ap-southeast-1`)** as proposed.

- [ ] **(B) A requirement applies and has been satisfied.** A localization or cross-border-transfer requirement does apply to the Data, and the competent UAE / Abu Dhabi authority has **formally approved or authorized** the storage and processing of the Data in Singapore as proposed. **Provide a checkable reference to that approval — all three fields:** approval / document number: ______________________; date of approval: ________________; issuing office / authority: ______________________________. *(A vague or unverifiable internal-approval assurance does not satisfy (B) — the reference must be one another party could check.)*

The undersigned further confirms:
1. The Data's classification under the Ministry's own rules has been considered specifically for this cross-border arrangement (this is not a general assurance).
2. The undersigned is **authorized to make this specific data-classification / localization determination on behalf of the Ministry of Interior**, and this determination **reflects consultation with, or originates from, the Ministry's competent data-governance / information-security authority — it is NOT the undersigned's own personal assessment.** *(General authority to sign documents for the Ministry is not sufficient for this attestation: the determination must come from, or be confirmed by, the part of the Ministry responsible for data classification. If the undersigned cannot attest to this specific point, the form must not be signed.)*
3. The undersigned understands that Wingmen relies on this attestation as the Ministry's authoritative statement of its own regulatory position, and that Wingmen will **not** write any real trainee data to the Singapore store unless statement (A) or (B) above is truthfully attested.

---

**Signed for and on behalf of the Ministry of Interior / ADCDA:**

Name: ______________________________  Title / Role: ______________________________

Signature: __________________________  Date: ________________

---

*Notes (delete before finalizing): (1) This attestation is a companion to the Data Processing Agreement, not a replacement — the DPA governs HOW the data is processed; this attestation answers WHETHER it may be hosted outside the UAE at all. (2) This is not legal advice; it records the client's own regulatory determination. If the signatory is unable to attest (A) or (B) truthfully, the arrangement cannot proceed in Singapore and the data must reside in the UAE (the Core42 / UAE-sovereign path). (3) Confirm the exact MoI/ADCDA legal entity name and the signatory's title before sending.*
