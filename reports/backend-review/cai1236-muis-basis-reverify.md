# CAI-1236 MUIS-basis — independent at-source re-verification

**Auditor:** cc-quality (opus-4-8, FULL-tier; model pin `.quality_model=claude-opus-4-8` confirmed at source, CAI-1170 carve-out)
**Directed by:** cai (bus #32129, 2026-08-23) — retro-tiered CAI-1236 to **FULL** because the MUIS-basis
substantiation has been **relied on twice** (CAI-1234 wording approval, CAI-1285 render-all-4) for a
**donor-facing religious/compliance claim** on Irsyad zakat receipts + appreciation letters.
**Lens:** verification-independence — re-derive the sourcing myself from MUIS/government primary materials,
NOT from cai's summary or the client channel, same as cai did originally (CAI-RESP-1236).
**Date:** 2026-08-24

---

## What was re-verified

The donor-facing string relied upon (LIVE on zakat-category letters per PR#411 @88ef1ab9; built dark on
receipts per PR#440):

> "Zakat contributions are channeled to 4 Asnaf categories: Miskin, Riqab, Amil, and Fisabilillah."

CAI-1236 (CAI-RESP-1236, 2026-08-21) is the **MUIS-basis** underpinning this claim. Its load-bearing
factual assertions, which I re-derived independently at source:

- **A.** Madrasah Irsyad Zuhri Al-Islamiah is a real **MUIS/JMS-registered** madrasah (Joint Madrasah
  System, Aug 2008); "subsidiary" was the corrected overstatement — MUIS has real institutional oversight.
- **B (load-bearing).** MUIS's own public materials define the Singapore-specific reinterpretation of
  **Riqab** NOT as classical literal slave-freeing but as **education-assistance grants** ("liberation
  from the bondage of ignorance"). This is the one asnaf cai flagged (CAI-1234) as needing real
  justification; Miskin/Amil/Fisabilillah are unremarkable for a madrasah's zakat operations.
- **C.** All 4 named asnaf are genuine MUIS/Quran-9:60 asnaf categories.

---

## Method

`zakat.sg` and `irsyad.edu.sg` returned **HTTP 403 (Cloudflare bot-block)** to direct fetch — a tooling
limitation, disclosed here so the verdict is not overstated. The load-bearing claim (B) was therefore
confirmed from **MUIS's OWN primary fatwa page** and other independently-fetched authoritative sources,
NOT from the blocked pages:

- **muis.gov.sg** — Fatwa on zakat assistance (MUIS-official primary source) — *fetched at source*.
- **muslim.sg** — MUIS-affiliated "The chosen 8 Asnaf" — *fetched at source*.
- **mccy.gov.sg** — Government clarification on Irsyad Trust Limited — *fetched at source*.
- **en.wikipedia.org** — Madrasah Irsyad Zuhri Al-Islamiah — *fetched at source*.
- **zakat.sg content** — recovered via WebSearch source-attributed extraction (page itself 403 to fetch).

---

## Findings

### Claim B — Riqab-as-education — ✅ CONFIRMED (verbatim, MUIS primary source); holds up STRONGER than the summary

MUIS's own Fatwa on zakat assistance (muis.gov.sg), verbatim:

> "The MUIS Fitrah Zakat Committee had expanded on the meaning of riqāb to include students in need of
> financial aid. This is because knowledge frees a person from the shackles of ignorance."

muslim.sg (MUIS-affiliated), verbatim:

> Riqab: "Assisting the poor and needy in the field of education as a means of liberating them from the
> fetters of bondage caused by ignorance."

zakat.sg (via search extraction): current Riqab beneficiaries include study grants / IEF / LBKM / Promas
and the **Joint Madrasah System Study Awards**; standardised **$150/child**; extended to ITE/Poly students.

This is exactly CAI-1236's claim B, and independent re-derivation makes it **stronger** than cai's summary:
the specific MUIS disbursement vehicle under Riqab is literally the **Joint Madrasah System Study Awards** —
the JMS that Irsyad itself belongs to. The Riqab-as-education framing for a *madrasah* is not a stretch; it
is the paradigm MUIS case.

### Claim C — 4 named asnaf are genuine MUIS asnaf — ✅ CONFIRMED

MUIS 8-asnaf framework (Quran 9:60) as published: Fakir, **Miskin**, **Amil**, Muallaf, **Riqab**,
Gharimin, **Fisabilillah**, Ibnussabil. All 4 named in the donor line are legitimate MUIS asnaf. (cai
CAI-1234 already ruled a *subset* Islamically permissible — majority fiqh does not require all 8; that
adjudication is cai's and unchanged. My scope is only that the categories are MUIS-real: they are.)

### Claim A — Irsyad MUIS/JMS registration — ✅ CONFIRMED (substance robust; minor immaterial date variance)

- irsyad.edu.sg JMS page + academic journal (via search): "In August 2008, Muis brought Madrasah Irsyad
  Zuhri Al-Islamiah into the Joint Madrasah System (JMS)" — matches cai's phrasing exactly.
- Wikipedia (fetched): under MUIS management since **1991**; JMS scheme announced 2007 for 2009 intro.
- **Variance:** "Aug 2008 bring-in" vs "2009 scheme introduction" across sources — immaterial. The
  substance (real, long-standing MUIS institutional oversight of Irsyad) is robustly corroborated. cai's
  "subsidiary → MUIS/JMS-registered" correction is right: Wikipedia says "came under the **management**
  of MUIS" — oversight, not corporate subsidiary.

---

## Honest refinements / could-not-verify (surfaced, not adjudicated)

1. **ITL flag dating — CORRECTION.** CAI-1236 recorded the material-unrelated flag as "**2021**
   MUIS-directed audit + **police report** re Irsyad Trust Limited (severed Jan 2021)." At source (MCCY
   government clarification), the precise record is:
   - The audit finding of concern (MMC transferred **S$2M** to ITL) was a **2016 audit**; ITL **returned
     the full S$2M in 2016**.
   - Affiliation **severed effective January 2021** — matches cai.
   - A **MUIS-directed review** of financial transactions between Madrasah Irsyad and ITL is **ongoing**.
   - "There is currently no affiliation between Irsyad Trust Limited and Madrasah Irsyad."
   - I could **NOT independently confirm a "police report"** from the government source →
     **could-not-verify**, reported as such rather than restated as fact (absence in the MCCY clarification
     is not proof none exists). This flag remains **unrelated** to the zakat-line substantiation — it is an
     institutional-governance transparency item, not part of the donor claim's evidentiary chain.

2. **Self-disbursement authority — residual (cai already holds).** CAI-1236 itself raised, and CAI-1242's
   provenance comment noted, that Irsyad **self-disburses** zakat to its 4 asnaf rather than forwarding to
   MUIS. This does NOT falsify the printed line (which does not claim "via MUIS"); the line discloses WHICH
   asnaf receive funds, and those 4 are MUIS-legitimate + Irsyad's actual practice (Saddam's substantiation,
   CAI-1242). Whether a JMS madrasah's self-collect-and-self-disburse is itself in order is an
   authority/operational question for cai, not an engineering-quality finding — advisory residual only.

---

## VERDICT

**CAI-1236 MUIS-basis: independently RE-VERIFIED AT SOURCE — HOLDS (PASS).** (opus-4-8, FULL-tier.)

The MUIS sourcing underpinning the donor-facing 4-asnaf religious claim is genuine and, on independent
re-derivation from MUIS's own primary materials, holds up **at least as strongly as — in the Riqab detail,
stronger than** — cai's original CAI-RESP-1236. The reuse in CAI-1234 (wording) and CAI-1285 (render-all-4)
rests on a sound MUIS basis. No material defect in the substantiation chain.

Two items handed up (not adjudicated): the **ITL flag dating correction** (2016 audit, not 2021; "police
report" could-not-verify) for the record, and the **self-disbursement authority residual** cai already
tracks. Neither undermines the zakat-line substantiation.

Tooling caveat on the record: zakat.sg/irsyad.edu.sg were 403 to direct fetch; the load-bearing Riqab claim
was confirmed from MUIS's own fatwa page (muis.gov.sg) directly, so the verdict does not rest on the blocked
pages.

**Refs:** CAI-RESP-1236 (original MUIS-basis), CAI-RESP-1234 (wording+scope FULL), CAI-RESP-1242 (Riqab
substantiation / operator-directive), CAI-1285 (render all 4). Bus dispatch #32129. Verdict routes to cai.
