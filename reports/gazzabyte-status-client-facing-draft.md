# Madrasah Irsyad DMS — Complete Progress Picture

*This is everything you've asked for since we started — grouped by where each piece stands and the order we're working through them. No fixed dates: the groups show what's done, what's being built now, what we need from you, and what's designed next. If anything you remember isn't here, tell me and I'll add it straight away.*

---

## ✅ Live now — done and usable

**Recently live:**
- **Donor thank-you emails** — automatic thank-you now sends when a tin is counted and a donor email is on it, from *Madrasah Irsyad Zuhri Al-Islamiah &lt;Fundraising@irsyad.edu.sg&gt;* (new counts going forward; past donations aren't mass-emailed — you stay in control of any bulk sending).
- **Tin search** — find a Tabung Umum tin by its **number or the donor's name**, in one Search box.
- **Export your donations yourself** — Donations → Download → date range + category → CSV (with a Category column) for your bank-statement work.
- **Tabung Keluarga report** showing $0 / missing totals — fixed (you confirmed the totals show).
- **Weekly Reports list on mobile** — the "open" button is reachable without desktop mode (you confirmed).
- **Account menu + dashboard layout** on mobile — fixed.

**Also delivered:**
- Automatic receipt email switched OFF — you choose when to send.
- Finance report in the bank-statement format (finance viewer + preparer).
- New bank-import donations create receipts automatically on commit.
- Non-tax-deductible (non-IPC) notice on receipts.
- Tabung Umum tins show the issued date.
- Admin two-factor login (MFA) — Settings → Organization → Two-Factor Authentication.

*(Plus the large base already live: tin scan / count / bank with denomination breakdown, class-completion tracker, two-signature sign-off + locking, OCBC bank-statement import with keyword-matching + review filters + duplicate-skip + multi-month + donor fuzzy-match, staff logins + roles + audit trail, IC masking, the storefront, and the Qurban / SWAF / Wakaf / Ramadhan / Infaq campaign categories.)*

## 🔄 In progress — being built now

- **Receipts for the imported tabung donations** — recorded but bulk-imported without receipts; we're setting up a one-time bulk issue that creates the receipt records (won't auto-email your donors).
- **Receipt PDF in the "View as Preparer" preview** — the "failed to generate" error appears **only** in that admin preview; downloading receipts normally (real preparer login, or admin) works. Fix in review.
- **Linking imported / anonymous donors to their profiles** — so receipts on imported bank donations can go out by name.
- **Committed-donation correction flow** — voiding/correcting a posted donation's category or donor, with audit + admin approval.
- **Tabung Fajar** — reconciling the loaded historical records against your files (on our side). Your Fajar money is counted in your totals — not missing.
- **Your imports** — the one-time Stripe import and the bank statements, on our side.
- **A visual workflow reference** for your users (tin lifecycle + approval steps).
- **Behind-the-scenes hardening** — ongoing permissions + security improvements.

## ⏳ Waiting on you

- **GIRO list, January → now** — send the file and we'll set up the import.
- **Bank statements, February onward** — January is done; the tool handles multiple months.
- **Which fund the Stripe donations belong to** — to attribute the one-time Stripe import correctly.
- **The signed data-processing authorization** (with Saddam / your data-controller) — for the full donor-level reconciliation on the historical records.
- **Your fee structure** — we capture it with you in the walkthrough (no old-system access needed; just talk us through it).

## 📋 Planned — designing the approach first (so we build it right)

- **Tabung Jumaat, tin-by-tin** — currently a single combined total; you've asked to extend it to a full tin-by-tin dashboard like Tabung Umum (issue, search by number/name), usable + editable by admin **and** preparer — plus **including Tabung Jumaat in the weekly report**. One design piece we're scoping.
- **Search across everything** — extending the tin/serial + name search beyond Tabung Umum to all tabung and fees.
- **Scan a tin's number when issuing** — quicker, less error-prone.
- **Tabung Fajar count-completion thank-you letter** (with Mudirah's signature) — including *when* it sends (see the question below).
- **Appreciation Letter** — auto-fill the donor details + send.
- **School Fees payment** and **NETS at the counter (POS)** — new modules; need your pricing/category sign-off + the provider setup.
- **Smaller enhancements** — manual tin numbering, preparer editing a category after posting, the Zakat-allocation note on receipts.

## 🗂️ Also tracked (earlier requests + smaller items — nothing forgotten)

- **Bulk retro-receipts for older donations** (Jan → now) — part of the imported-tabung receipts work above.
- **"Top donors" ranking report** — a dedicated donor-ranking view, on the list to build.
- **A single all-streams summary table** (date-range across General / Infaq / Waqaf / Keluarga / Fajar / Kedai / Masjid / Qurban / SWAF / seasonal) — building on the current per-category dashboard.
- **GIRO unsuccessful handling** — failed GIRO is recorded separately (not counted as a donation); showing a donor's failed-GIRO history on their profile is the remaining enhancement.
- **"Misc" fees category** (teacher carpark + student ad-hoc) — a lightweight flexible category, separate from the School Fees module.
- **Zakat fund-velocity / asnaf distribution reporting** — later reporting.
- **Parked at your call:** retiring the FR website into the DMS; automatic WhatsApp thank-you for no-email donors; the September additions (e.g. Nasi Mandhi welfare option); the school-records remainder (P6 archival, teacher upload role, class-change import); and the SOP / access documentation.

---

### A few quick questions
- **Tabung Fajar thank-you letter** — send after a tin is **"counted"**, or after the report is **"checked"** (verified)?
- **Tabung Jumaat** — a walkthrough time to record a real entry together.
- **The "who's holding which tins" drill-down** — show everyone holding that tin type, or just currently-issued ones?
