# Madrasah Irsyad DMS — Progress Update

*Where things stand across everything you've asked for — grouped by what's live now, what's actively being built, what we need from you, and what we're designing next. (No fixed dates — the groups show the order and how far along each piece is.)*

---

## ✅ Live now — you can use these today

**Just went live:**
- **Tin search** — on Tabung Umum you can now find a tin by its **number or the donor's name**, in one Search box (works across every page + alongside the status filter).
- **Donor thank-you emails** — automatic thank-you emails now send when a tin is counted and a donor email is on it, from *Madrasah Irsyad Zuhri Al-Islamiah &lt;Fundraising@irsyad.edu.sg&gt;*. (New counts going forward — past donations aren't mass-emailed; you stay in control of any bulk sending.)
- **Export your donations yourself** — Dashboard → Donations → Download → pick a date range + category (e.g. "Tabung") → downloads a CSV (with a Category column) for your bank-statement work.

**Recently fixed (you've confirmed these):**
- Account menu and dashboard layout fixed on mobile.
- Weekly Reports list works on mobile — the "open" button is reachable without desktop mode.
- Tabung Keluarga report showing $0 / missing totals — fixed (you confirmed the totals now show).

**Also delivered:**
- Automatic receipt email switched OFF — you choose when to send one.
- Finance report in the bank-statement format (for your finance viewer + preparer).
- New bank-import donations create receipts automatically on commit.
- Non-tax-deductible (non-IPC) notice on receipts.
- Tabung Umum tins show the issued date.
- Admin two-factor login (MFA) — under Settings → Organization → Two-Factor Authentication.

*(Plus the large base already live: tin scan / count / bank with denomination breakdown, class-completion tracker, two-signature sign-off + locking, OCBC bank-statement import with keyword-matching + review filters + duplicate-skip + multi-month + donor fuzzy-match, staff logins + roles + audit trail, IC masking, the storefront.)*

## 🔄 In progress — actively being built

- **Receipts for the imported tabung donations** — those donations are recorded but were bulk-imported without receipts; we're setting up a one-time bulk issue that creates the receipt records (it won't auto-email your donors).
- **Receipt PDF in the "View as Preparer" preview** — the "failed to generate" error appears **only** in the admin "View as Preparer" preview; downloading receipts normally (as a real preparer login, or as admin) works. The fix is in review.
- **Linking imported / anonymous donors to their profiles** — so receipts on imported bank donations can go out by name.
- **Your imports** — the one-time Stripe import and the bank statements, on our side.
- **Tabung Fajar** — reconciling the loaded historical records against your files (on our side). Your Fajar money is counted in your totals — it isn't missing.
- **A visual workflow reference** for your users (the tin lifecycle + approval steps) — we're preparing it.
- **Behind-the-scenes hardening** — ongoing permissions + security improvements.

## ⏳ Waiting on you

- **GIRO list, January → now** — send us the file and we'll set up the import.
- **Bank statements, February onward** — January is done; the tool handles multiple months.
- **Which fund the Stripe donations belong to** — so we attribute the one-time Stripe import to the right category.
- **The signed data-processing authorization** (with Saddam / your data-controller) — so we can do the full donor-level reconciliation on the historical records.

## 📋 Planned — designing the approach first (so we build it right)

- **Search across everything** — extending the tin/serial + name search beyond Tabung Umum to all tabung and fees, in one place.
- **Tabung Jumaat, tin-by-tin** — Tabung Jumaat is currently entered as a single combined total. You've asked to extend it to a full tin-by-tin dashboard like Tabung Umum (issue a tin, search by number/name), usable + editable by admin **and** preparer accounts — plus **including Tabung Jumaat in the weekly report**. That's the design piece we're scoping to get right.
- **Scan a tin's number when issuing** — so issuing is quicker and less error-prone.
- **Tabung Fajar count-completion thank-you letter** (with Mudirah's signature) — including *when* it should send (after "counted" or after the report is "checked" — see the question below).
- **School Fees payment** and **NETS at the counter (POS)** — new modules; need your pricing/category sign-off + the provider setup.
- **Smaller enhancements** — manual tin numbering, a preparer editing a category after posting, the Zakat-allocation note on receipts, and a committed-donation correction flow.

---

### A few quick questions
- **Tabung Fajar thank-you letter** — should it send after a tin is **"counted"**, or after the report is **"checked"** (verified)?
- **Tabung Jumaat** — the combined-total walkthrough timing (a real entry we can verify together).
- **The "who's holding which tins" drill-down** — should it show everyone holding that tin type, or just currently-issued ones?
