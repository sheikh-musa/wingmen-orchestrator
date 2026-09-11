# Lawfulness Assessment — Cross-Border Storage of ADCDA Personal Data in Singapore

> **DRAFT — for cai governance review + operator approval. NOT for sending to the client.**
> **This is NOT legal advice.** It is a structured, good-faith desk assessment built from secondary
> legal sources (law-firm briefings and guides), not from a certified copy of the UAE gazette text
> or from a UAE-qualified lawyer. Every legal claim is cited. Where a provision could not be verified
> to a primary source, that is stated. **A definitive answer for this data class requires a formal
> opinion from qualified UAE counsel** — see §6.

**Question presented.** Is it lawful, under UAE law, for **ADCDA (Abu Dhabi Civil Defence Academy — a
UAE government body)** to have its trainees' personal data — including **government / staff /
military-linked identifiers, rank, and names (Arabic + English)** — processed and stored in
**Singapore (AWS `ap-southeast-1`)** by Wingmen as processor?

**Bottom line up front (see §5 and §6 for the reasoning and caveats):** This should **NOT** proceed to
a real data write on the strength of the DPA + consent alone. The single most important finding is that
the UAE PDPL appears to **exclude government data and government entities from its scope** — so the
PDPL's own cross-border-transfer mechanism (adequacy / consent / contractual safeguards) may **not be
the governing regime at all**, and a **separate public-sector / government-data regime** (federal and/or
Abu Dhabi) likely applies. Combined with the government/military-linked nature of the data and known
Abu Dhabi government-data-localization expectations, this data class plausibly **mandates a formal
UAE-counsel opinion before any real data write**, and may be subject to a **UAE data-residency
requirement that Singapore storage cannot satisfy.** Confidence: moderate on the framework shape, low
on the specific outcome. Do not overstate.

---

## 1. What the UAE PDPL requires to transfer/store personal data outside the UAE

The UAE's federal personal-data law is **Federal Decree-Law No. 45 of 2021 on the Protection of Personal
Data ("PDPL")**, in force since **2 January 2022**. [1][2]

The cross-border-transfer regime is set out in **Articles 22 and 23** (article numbering per multiple
law-firm summaries; not verified against the official gazette — see §6):

- **Article 22 — transfer to jurisdictions with an "adequate" level of protection.** Personal data may
  be transferred to a country/territory that has data-protection legislation, or that has acceded to
  bilateral/multilateral data-protection agreements, providing an adequate level of protection, as
  determined by the UAE Data Office. [1][3]
- **Article 23 — transfer in the absence of adequacy.** Where the destination lacks adequate
  protection, a transfer may still be lawful via one of several mechanisms, reported as: (a) a **contract
  or agreement that binds the recipient to the protections, measures, controls and conditions of the
  UAE PDPL** (including provision for supervisory/judicial oversight) — i.e. a contractual-safeguard
  mechanism analogous to standard contractual clauses; (b) the **data subject's express consent** to the
  transfer (in a manner not conflicting with public/security interests); (c) transfer **necessary for
  performance of a contract** or pre-contractual steps; (d) transfer **necessary for international
  judicial cooperation**; or (e) transfer to **protect the public interest**. [1][3][4]

**Two structural gaps at the federal level, as at 2025–2026:**

- **No adequacy list has been published** by the UAE Data Office — there is no official determination
  that Singapore (or any country) is "adequate." [1][5]
- **No official standard contractual clauses (SCCs) have been issued**, and the PDPL's **Executive
  Regulations remain unissued** (still outstanding as of early-to-late 2025 reporting), so the
  operational detail of Articles 22–23 (and breach-notification timeframes, penalties, etc.) is not
  finalised. [1][5][6][7]

**Implication for the general (private-sector) case:** absent an adequacy finding, a lawful transfer would
have to rest on **Article 23** — practically, a **PDPL-compliant contractual safeguard (the DPA) plus, in
most conservative readings, express consent**. That is the mechanism the draft DPA (clause 7) is built to
provide. **But see §2 — this general analysis may not even apply to ADCDA.**

## 2. Does government / military-linked data carry heightened restrictions? (The decisive open issue)

**Yes — and, more fundamentally, the PDPL may not apply to ADCDA at all.**

**2.1 Scope exclusion for government data / government entities.** Multiple sources state that the PDPL
**does not apply** to, among others, **government data, government / public entities that control or
process personal data, and personal data held by security and judicial authorities.** [8][9][10] (The
scope-exclusion provision is cited variously as **Article 2(2)** or **Article 4** across secondary
sources — this discrepancy is itself unresolved here; see §6.) ADCDA is an **Abu Dhabi government body**,
and the data set is the personal data of a **government / civil-defence academy's personnel/trainees**.
On its face, this pulls the arrangement **outside the federal PDPL's cross-border regime** and into a
**separate government-data governance regime**.

**2.2 What governs instead (federal + emirate level).** If the PDPL is disapplied, government-data rules
apply, which are generally **more** restrictive, not less. Relevant instruments reported in the sources
include:
- **Federal Cabinet Resolution No. 21 of 2013 on the Regulation of Information Security in Federal
  Authorities** (federal information-security regime for government bodies). [9]
- **Abu Dhabi Government Data Management Policy and Standards** and **Abu Dhabi data-classification
  standards** (ADDA / Abu Dhabi Digital Authority), which govern how Abu Dhabi government entities
  collect, store, process and share data, **including data-residency requirements**. [11][12]

**2.3 Data-localization expectation for public-sector / sensitive government data.** Sources indicate
that Abu Dhabi / UAE public-sector and "sensitive" government data is typically expected to be **stored
on servers physically located within the UAE (often specifically within Abu Dhabi)**, as part of the
UAE's data-sovereignty strategy. [11][13] If a binding data-classification / localization rule of that
kind applies to ADCDA's trainee data, **Singapore storage would be non-compliant** regardless of any DPA
or consent — this is the scenario the DPA's migration/region-change clause (clause 13) is a contingency
for.

**2.4 National-security sensitivity.** Independent of formal classification, **civil-defence / military-
linked identifiers of a government body's personnel** are the kind of data a state commonly treats as
security-sensitive. Even if a pure-PDPL analysis technically permitted the transfer, the
**government-data and national-security overlay realistically dominates** and is the reason this cannot
be resolved on a private-sector reading. **This is honestly outside what the PDPL text alone answers.**

## 3. Would Singapore be treated as "adequate"?

**No basis to assume so.** There is **no published UAE adequacy list**, so Singapore has **not** been
determined adequate by the UAE Data Office. [1][5] Singapore does have a general data-protection law
(the **Singapore PDPA 2012**) which is relevant to how the data is protected once there, and which binds
Wingmen's Singapore operations — but the existence of the Singapore PDPA does **not** equal a UAE
adequacy determination. In the absence of adequacy, any lawful private-sector transfer would fall to the
**Article 23** consent / contractual-safeguard route (§1) — **and, per §2, even that route is in doubt
because the governing regime for ADCDA may be the government-data regime, not the PDPL.**

## 4. Assessment of the "good-faith documented basis" the DPA is built on

The draft DPA (clause 7) constructs an **Article 23-style basis**: a contract binding the processor and
its sub-processors to UAE-PDPL-consistent protections, plus the controller's express written
acknowledgment/consent (clause 8), plus honest disclosure of the Singapore location (clause 0). If the
PDPL were the governing regime and ADCDA were an ordinary controller, this would be a **reasonable,
defensible good-faith construction** given the missing SCCs/adequacy list — imperfect only because the
federal detail is unissued. **However**, because §2 puts the governing regime itself in question, the DPA
should be read as a **necessary but not sufficient** artifact: it is the right instrument to have, and it
is honest, but **it does not by itself make the arrangement lawful** if a government-data-residency rule
prohibits foreign storage.

## 5. Bottom-line recommendation

**Do not write real ADCDA personal data to the Singapore store on the strength of the DPA + consent
alone.** This data class — a UAE government body's personnel/trainee data, with military-linked
identifiers — **mandates a formal UAE-counsel opinion before any real data write.** Reasons:

1. The PDPL likely **excludes government entities/data**, so the very mechanism the DPA relies on (PDPL
   Art. 23) may not govern; a **separate, generally stricter public-sector regime** applies (§2).
2. There is a **realistic prospect of a UAE / Abu Dhabi data-localization requirement** that Singapore
   storage cannot satisfy (§2.3) — in which case the answer is not "consent fixes it" but "the data must
   reside in the UAE."
3. No adequacy finding and no issued SCCs exist to lean on (§1, §3).

**What CAN proceed now, safely:** completing and circulating this DPA draft and this assessment for
governance review; obtaining ADCDA's own position and its data classification of the trainee data;
retaining qualified UAE counsel; and (if desired) standing up the technical controls in a **UAE-region
store** so that a compliant path exists whichever way the opinion lands. **What must NOT proceed:** any
real ADCDA data write to `ap-southeast-1`, or representing to ADCDA that the Singapore arrangement is
confirmed-lawful, until counsel has ruled.

This aligns with the fleet's standing pre-live residency gate (**TENANT-RESIDENCY-001**): verify the
write-target silo *before* any client data path goes live, and treat a residency exception as requiring
a joint operator + cai grant with an expiry.

## 6. Specific open legal questions for qualified UAE counsel

1. **Scope:** Does the UAE PDPL apply to ADCDA at all, or is ADCDA's trainee data excluded as
   "government data" / data of a "government entity" (and/or a security authority)? Confirm the exact
   exclusion provision — sources cite it as **Article 2(2)** *and* as **Article 4**; which is correct in
   the enacted text?
2. **Governing regime if excluded:** If the PDPL does not apply, which federal and Abu Dhabi
   government-data rules do (e.g. Federal Cabinet Resolution No. 21 of 2013; ADDA / Abu Dhabi Government
   Data Management Policy & classification standards), and what do they require for storage location?
3. **Data localization:** Is there a binding requirement that this data (or its classification tier)
   **reside within the UAE / Abu Dhabi**? Does the military/civil-defence linkage raise the
   classification tier and trigger localization or a national-security prohibition on foreign storage?
4. **If the PDPL does apply:** Confirm the Article 22/23 mechanisms as enacted; confirm that a
   contractual-safeguard + express-consent route is available and sufficient absent an adequacy list and
   issued SCCs; confirm the current status of the **Executive Regulations** and any interim UAE Data
   Office guidance.
5. **Approvals/notifications:** Which UAE / Abu Dhabi authority, if any, must **approve or be notified**
   of this cross-border arrangement before go-live? Is Data Office (or ADDA) sign-off a precondition?
6. **Consent validity:** Can ADCDA validly consent as controller to foreign-jurisdiction storage of its
   personnel's data, or is that authority constrained by government-data rules? Who at ADCDA can bind the
   body for this purpose?
7. **Singapore-side exposure:** Advice (UAE + Singapore counsel) on Singapore lawful-access powers over
   the data while at rest in `ap-southeast-1`, and whether that alone is disqualifying for this data
   class.

---

## Sources

Secondary legal/industry sources consulted (law-firm briefings and guides). **None is a certified copy
of the enacted Arabic gazette text**; article numbers should be confirmed against the official text by
counsel (§6).

1. Securiti — Overview of UAE's Federal Decree-Law No. (45) of 2021 (PDPL): https://securiti.ai/uae-personal-data-protection-law/
2. Pandectes — Understanding the UAE's Personal Data Protection Law: https://pandectes.io/blog/understanding-the-uaes-personal-data-protection-law/
3. Kayrouz & Associates — Cross-Border Data Transfers Under UAE Law (2026): https://www.kayrouzandassociates.com/insights/cross-border-data-transfers-under-uae-law-in-2026
4. China Briefing — UAE Data Protection Obligations and Cross-Border Data Transfer for Businesses: https://www.china-briefing.com/china-outbound-news/uae-data-protection-obligations-and-cross-border-data-transfer-for-businesses
5. U.S. Dept. of Commerce / trade.gov — UAE allows cross-border data flows of personal data: https://www.trade.gov/market-intelligence/united-arab-emirates-allows-cross-border-data-flows-personal-data
6. DLA Piper — Data Protection Laws of the World, UAE (General): https://www.dlapiperdataprotection.com/countries/uae-general/law.html
7. Chambers and Partners — Data Protection & Privacy 2026, UAE (Trends & Developments): https://practiceguides.chambers.com/practice-guides/data-protection-privacy-2026/uae/trends-and-developments
8. Global Legal Post — Data Protection Law Guide, United Arab Emirates: https://www.globallegalpost.com/lawoverborders/data-protection-law-guide-1072382791/united-arab-emirates-1827256483
9. Modulos — UAE PDPL framework reference (scope exclusions; Federal Cabinet Resolution No. 21 of 2013): https://docs.modulos.ai/frameworks/uae-pdpl
10. CookieYes — UAE PDPL: A Comprehensive Guide: https://www.cookieyes.com/blog/uae-data-protection-law-pdpl/
11. Abu Dhabi Government Data Management Policy (ADSIC / Abu Dhabi Government): https://data.abudhabi/opendata/sites/default/files/AD-Gov-Data-Management-Policy-EN-v1.0.pdf
12. Gulf Business — Abu Dhabi Data Program (ADDA government-entity data management): https://gulfbusiness.com/new-abu-dhabi-data-program-to-help-emirates-government-entities-leverage-data-to-drive-growth/
13. InCountry — Data sovereignty in the UAE: https://incountry.com/blog/data-sovereignty-in-the-uae/

*Prepared as a desk assessment for internal governance review. Not legal advice. Requires a formal
UAE-counsel opinion before any real ADCDA personal-data write — see §5 and §6.*
