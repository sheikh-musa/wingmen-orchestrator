# DocChase validation kit

*Fable 5.1 temporary lane (cc-substrate-4), 2026-10-27, for Musa op#35005. Source idea: `reports/blank-slate-ideas-20261027/blank-slate-ideas.md` Part B idea 1 (live checkout). Scope: a kit for five founder-led interviews this week. No outreach was made, nothing was signed up for, nothing was built. Musa does the outreach himself. Firm entries list public business contact channels only, never personal data about individuals.*

**The bet in one line.** Small UAE accounting and bookkeeping firms (1 to 15 people) lose hours every month chasing their clients over WhatsApp for bank statements, invoices and sign-offs, and the corporate-tax plus e-invoicing timeline is about to make that worse. DocChase is a WhatsApp bot that does the chasing, files what comes back, and warns the firm before deadlines, sold as a managed service at AED 300 per firm per month.

**The rule.** Build only if at least 2 of 5 interviewed firms say yes to AED 300 a month on the terms in section 2. Otherwise record what they said and stop.

---

## 1. The 20-minute interview script

Principles: ask about the past and the present, never about a hypothetical future product until the last three minutes; ask for stories and numbers, not opinions; never name a feature before they have described the pain in their own words; write down their exact phrases. If they start describing a tool they wish existed, let them finish, then return to the script.

**Opening (1 minute).** "Thank you for the time. I run a small software team and I am studying how small accounting practices in the UAE handle the paperwork side of client work. This is research, not a sales call, and there is nothing to buy today. I will ask about how things actually work in your firm. Is it okay if I take notes?"

**Block A. Context (3 minutes).**
1. How many people work in the firm, and roughly how many clients do you look after each month?
2. What mix of work is it: monthly bookkeeping, VAT returns, corporate tax, audit support, payroll?
3. Which software do you run the books in? (Let them name it. Do not list options.)

**Block B. How documents arrive today (6 minutes).**
4. Walk me through the last month-end or quarter-end. From the day the period closes, what happens first?
5. How do your clients usually send you bank statements and invoices? (Wait. If they name a channel, ask: "What share of clients use that channel? What do the others do?")
6. When a client is late, what do you do, and who in the firm does it?
7. Tell me about the most recent client you had to chase more than twice. What did that take?
8. When the documents finally arrive, where do they go and who files them?

**Block C. The cost (4 minutes).**
9. In a typical month, how many hours would you say the firm spends asking for documents and sorting what comes back? (Push gently for a number: "Is it closer to five hours or twenty?")
10. Who does those hours: you, a senior, a junior, an admin?
11. Has late paperwork ever caused a late filing, a penalty or a lost client? What happened?
12. What have you already tried to fix this? (Portals, reminder templates, a WhatsApp group per client, a junior whose job it is.) What happened to each?

**Block D. Deadlines (2 minutes).**
13. How do you keep track of each client's VAT and corporate tax deadlines today?
14. Have the corporate tax registration and filing waves changed how much paperwork you handle per client? In what way?

**Block E. Willingness to pay (3 minutes, only now describe the idea).**
"Here is what I am considering, in one sentence: a WhatsApp assistant on your firm's number that asks each client for the documents you need, reminds them until they send, files what comes back in a folder per client, and warns you ahead of each client's VAT and corporate tax deadlines. You would set it up once per client and it runs every month."
15. What is your first reaction? (Silence. Let them talk.)
16. Which part of that would you actually use, and which part would you ignore?
17. Price test, exact wording in section 2.
18. "If this existed next month, which three clients would you put on it first, and why those?"

**Close (1 minute).** "That was very useful. Two last things: may I come back to you in two weeks with what I have learned from the other firms, and is there another small practice you think I should speak to?" Record the referral as a firm name only.

**What the interviewer must not do.** Do not say "would you use it", "does that sound useful" or "don't you hate chasing clients". Do not mention competitors unless they do. Do not promise a build date. Do not collect any client documents or client names from the firm.

**Scoring sheet (fill in within ten minutes of the call).**

| Field | Record |
|---|---|
| Firm size, clients per month, software | |
| Primary document channel and share of clients on it | |
| Hours per month on chasing and filing (their number) | |
| Who does the hours (owner, senior, junior, admin) | |
| A real penalty or lost client caused by late documents (yes or no, story) | |
| What they already tried and why it failed | |
| First reaction to the one-sentence idea (verbatim) | |
| Price test outcome (section 2 code) | |
| Three clients they would put on first (count only) | |
| Referral given (firm name) | |

---

## 2. Yes or no criteria and the price-test wording

**Price test, read exactly, after question 16:**

"If this ran on your firm's WhatsApp number for all your monthly clients, and it cost AED 300 a month for the firm, not per client, with a one-off AED 1,000 setup where we load your clients and document checklists, would you sign up for a three-month trial starting next month and pay the first month up front?"

Then stop talking. Record the answer with one of these codes:

| Code | Meaning | Counts as a yes? |
|---|---|---|
| Y1 | Yes, and asks a logistics question (when, how to start, which number) | Yes |
| Y2 | Yes, but wants a condition met first (Arabic, a specific software export, a specific document type). Record the condition. | Yes, if the condition is within the described product |
| M | "Maybe", "send me details", "let me think", "if the price were lower" | No |
| N1 | No, the pain is real but they would not pay for software for it | No, record why |
| N2 | No, the pain is not real for them (clients already compliant, portal already works) | No |

**Yes or no criteria for the whole test.**

- **BUILD** if at least 2 of 5 firms score Y1 or Y2, and at least 3 of 5 report ten or more hours a month on chasing and filing, and at least 1 of 5 tells a real penalty or lost-client story.
- **DO NOT BUILD** if fewer than 2 firms score Y1 or Y2. "Maybe" is a no. A yes at a lower price is a "maybe" unless the lower price still clears costs (section 5 gives the WhatsApp API cost; the floor is roughly AED 150 a month).
- **RE-SCOPE** rather than build if 3 or more firms say the chasing is fine but the deadline tracking is the pain, or the reverse. Then re-run the five interviews on the narrower product before building.
- **KILL SIGNAL** regardless of the above: if 3 or more firms already run a tool that chases clients over WhatsApp automatically (section 5 says whether such a tool exists today), stop.

Price anchors for the conversation: AED 300 a month is about the cost of one junior's half day; a single late corporate tax registration penalty is AED 10,000. Do not say these unless asked "why that price".

**Follow-through if BUILD.** The yes firms get a written one-page offer within 48 hours: AED 1,000 setup, AED 300 a month, three-month trial, cancel any time after, their data in a store designated for them before the first client message is processed (the residency rule applies here exactly as it does for every client). Collect the first month before building anything beyond the pilot.

---

## 3. Fifteen to twenty small accounting and bookkeeping firms in Abu Dhabi and Dubai

Compiled 2026-10-27 from public business listings and the firms' own websites only (Yellow Pages UAE, the Wafeq partner directory, Clutch UAE, TechBehemoths, the Xero advisor directory, firm sites). Contact channels are the firm's own publicly listed business line or general address, copied as listed; no individual is named. Ten in Abu Dhabi, ten in Dubai. Headcount is third-party verified only where stated; "unknown" means the site language suggests a small team but nothing confirms it. Two firms (Accurate, Fastlane) may exceed 15 staff by their own client-count claims and are kept as upper-bound candidates. Pick the five interviews from the "strong fit" rows first.

| # | Firm | City | Website | Public business contact as listed | Size signal | Fit services | Chasing or package signal | Tooling seen |
|---|---|---|---|---|---|---|---|---|
| 1 | AH Chartered Accountants | Abu Dhabi, Al Zahiyah | ahtaxaccounting.ae | info@ahtaxaccounting.ae; +971 50 548 0159 | 2 to 9 (TechBehemoths); founded 2024; "50+ clients" | Bookkeeping, VAT, corporate tax, outsourced CFO | Monthly and quarterly retainers; lists "deadline reminders" as a service; no lock-in | Zoho partner badge; Zoho Books, QuickBooks, Wafeq |
| 2 | Soft Power Audit & Tax Agency | Abu Dhabi, Corniche | softpoweraa.com | +971 50 132 1087 (WhatsApp); email via site form | unknown | Monthly bookkeeping, backlog, VAT, corporate tax | "Flexible packages for growing businesses"; no prices | none visible |
| 3 | Arabian Zone | Abu Dhabi, Khalidiya | arabianzone.ae | info@arabianzone.ae; +971 505 285 045 | unknown; since 2015 | Bookkeeping, VAT returns, corporate tax compliance | Process step 1 is the client handing over invoices and statements "physically or digitally" | Works with Zoho Books, QuickBooks, Tally, Xero |
| 4 | Accurate Accounting Solutions | Abu Dhabi, Al Hisn | accurate.ae | info@accurate.ae; +971 50 292 8775 (WhatsApp) | "15+ professionals"; "1000+ clients" (upper bound) | Bookkeeping, VAT, corporate tax, payroll | none visible | Tally and Zoho partner |
| 5 | SGA World Auditing Accounting | Abu Dhabi, Al Bateen (also Dubai) | sgaworld.ae | info@sgaworld.ae; +971 2 675 6726 | unknown | Bookkeeping, VAT, corporate tax, payroll | "Secure online access" to invoices; no packages | Zoho Books, QuickBooks named |
| 6 | NAS Chartered Accountants | Abu Dhabi, Hamdan St | no website; Xero advisor profile | +971 2 658 4848 | 1 partner + 12 staff (network listing) | Accounting, tax, audit | none visible; no website suggests manual client comms | Xero partner since 2016 |
| 7 | SAT Management Consultancy | Abu Dhabi, Mussafah (also Dubai) | zohoabudhabi.com | +971 2 556 5605; WhatsApp +971 55 118 6833 | unknown; founded 2012 | Bookkeeping, VAT filing support, corporate tax readiness; mainly Zoho implementation | e-invoicing deadline banner | Authorised Zoho partner |
| 8 | Inbooks Financial Services | Abu Dhabi, Al Nahyan | inbooks.ae | info@inbooks.ae; +971 55 106 2555 (WhatsApp) | unknown; "14+ years" | Monthly books, VAT, corporate tax, backlog clean-up | Publishes a tax compliance calendar and a cost estimator | QuickBooks ProAdvisor badge |
| 9 | Zaebek Consultancy Services | Abu Dhabi, MBZ City | zaebek.com | enquiries@zaebek.com; +971 52 126 9802 | 1 to 10 (ensun) | Bookkeeping, VAT, corporate tax, excise | none visible | none |
| 10 | IBIS Accounting & Book Keeping | Abu Dhabi | ibisaccounts.com | WhatsApp +971 58 572 3447; contact form | unknown; founded 2024 | Accounting, VAT, corporate tax, payroll | none; site partly placeholder | none |
| 11 | Simplybooks | Dubai, Meydan free zone | simplybooks.ae | hello@simplybooks.ae; +971 58 607 6700 | unknown; "50+ SME clients" | Cloud bookkeeping, VAT, corporate tax | Published monthly tiers AED 699 to 2,999; "structured handover" | Xero, Zoho Books, Wafeq, Odoo |
| 12 | Fastlane | Dubai | fastlanecareer.com | +971 55 127 3479 (WhatsApp) | unknown; claims "5,000+ businesses" (upper bound) | Bookkeeping, VAT, corporate tax | AED 499 and 999 a month plans; "deadline tracking" | Zoho Books |
| 13 | Excellent Accountants | Dubai | excellentaccountants.ae | info@excellentaccountants.ae; +971 4 338 8085 | unknown | Bookkeeping, VAT, corporate tax filing | Plans from AED 999 to 2,999 a month by transaction volume | none |
| 14 | Tax Easy UAE | Dubai, Business Bay | taxeasyuae.com | taxeasyuae@gmail.com; +971 55 688 9789 (WhatsApp) | unknown | Bookkeeping, VAT, corporate tax, payroll | Tiers by transaction volume; advertises "deadline reminders before every filing" and a compliance calendar | Zoho, Xero, QuickBooks |
| 15 | DIAC | Dubai, Oud Metha | diac.ae | info@diac.ae; +971 56 807 2491 | unknown; "14+ years" | Bookkeeping, VAT, corporate tax | Indicative AED 500 to 1,500 a month; manages compliance calendars | QuickBooks, Xero, Zoho |
| 16 | The Accountant LLC | Dubai, Al Wasl | theaccountant.ae | info@theaccountant.ae; +971 4 266 3220 | unknown; "8+ years" | Bookkeeping, VAT, corporate tax, Xero migration | Fixed monthly fee; "proactive deadline monitoring"; a client area | Xero certified advisor |
| 17 | Zyla Accountants | Dubai, IFZA | zylaaccountants.ae | hello@zylaaccountants.ae; +971 4 418 6069 | 3 Dubai staff (trade press) | Bookkeeping, corporate tax, FTA tax agent | WhatsApp and Slack client channels | Xero partner, Dext |
| 18 | Big Bookkeepers LLC | Dubai | bigbookkeepers.com | info@bigbookkeepers.com; +971 4 529 3431 | unknown; "50+ businesses" | Monthly bookkeeping, VAT returns, corporate tax advisory | Monthly support plans; external sign-in app | Zoho authorised partner |
| 19 | Escrow Consulting Group | Dubai, Sports City | escrowconsultinggroup.com | info@escrowconsultinggroup.com; +971 52 573 2844 | 2 to 9 (Clutch) | Monthly and catch-up bookkeeping, VAT, corporate tax | Has a client portal page; free three-month trial | Zoho, Wafeq, Odoo |
| 20 | BookRobo | Dubai | bookrobo.com | +971 4 324 4811; email via site | unknown; since 2003 | Monthly accounting, VAT, corporate tax | none visible | none |

**Why each fits, and where to start.**

Strong fit (interview these first): 1 AH (tiny, SME retainers, already promises deadline reminders by hand), 3 Arabian Zone (process literally starts with the client handing over paper), 9 Zaebek (1 to 10 staff, no tooling at all), 11 Simplybooks (published monthly tiers, the recurring-document model), 14 Tax Easy (WhatsApp-first, already sells deadline reminders, so the pain is proven), 6 NAS (13 people, no website, likely all manual), 19 Escrow (2 to 9 staff, monthly and catch-up work).

Good fit: 2 Soft Power, 8 Inbooks (runs a calendar tool but no client reminders), 12 Fastlane (volume plans, internal deadline tracking only), 13 Excellent, 15 DIAC (manual compliance calendars), 10 IBIS (brand new, early pain), 20 BookRobo.

Partial fit, useful as controls: 16 The Accountant (Xero client area), 17 Zyla (uses Dext for receipts, so partly solved), 18 Big Bookkeepers (external sign-in app), 5 SGA World (mid-size network branch), 7 SAT (Zoho implementer more than bookkeeper), 4 Accurate (may be too large).

Excluded as too large: Asad Abbas, Swift Audit, KGRN, BCL Globiz. Excluded as Dubai firms with only an Abu Dhabi landing page: AccBooks, ProTax, Numerix, Finhub, Velmont Crest. Not reached: the FTA tax-agent register and the ADGM and DMCC member directories.

---

## 4. Outreach message drafts, honest about being a pre-build interview

Musa sends these himself. Each one says plainly that there is no product yet.

**WhatsApp (to the firm's public business number).**

"Hello, this is Musa from Wingmen, a small software team based in Abu Dhabi. I am researching how small accounting practices in the UAE collect documents from clients each month, before deciding whether to build anything. I am asking five firms for a 20-minute call this week. It is not a sales call, there is no product yet, and I will share what I learn from the other firms afterwards. Would someone who handles month-end at your firm have 20 minutes on a call or at your office this week?"

**LinkedIn (to the firm's company page or a partner's public profile, as a connection note or message).**

"Hi, I run a small software team in Abu Dhabi and I am doing pre-build research on one specific problem: how small UAE accounting firms chase clients for bank statements, invoices and sign-offs, and how much time it costs. I have no product yet and nothing to sell. I am interviewing five practices this week for 20 minutes each and will send everyone the summary of what I hear. If this is a real pain at your firm, I would value 20 minutes. If it is not, a one-line reply saying so is just as useful to me."

**Email (to the firm's general info address).**

Subject: 20-minute research call on client paperwork at small UAE practices (no product, nothing to sell)

"Dear team,

I am Musa, founder of Wingmen, a small software team in Abu Dhabi. Before building anything, I am interviewing five small accounting and bookkeeping firms in Abu Dhabi and Dubai about one question: how do you get bank statements, invoices and sign-offs out of clients each month, and what does the chasing cost you in hours?

This is research, not a sales call. There is no product yet. The call takes 20 minutes, by phone, video or at your office at a time that suits you this week. In return I will send you a short summary of what the other firms told me, with no firm named.

If the person who runs month-end at your firm is willing, please reply with a time, or tell me it is not a problem for you, which is also useful.

Thank you,
Musa
Wingmen, Abu Dhabi"

Rules for the outreach: one message per firm per channel, no follow-up more than once, no personal mobile numbers, no mention of other firms by name, and no claims about what the product will do beyond the one sentence in the interview script.

---

## 5. Competitor reality check

Checked by a read-only web pass on 2026-10-27 (vendor pricing and help pages, FTA and Ministry of Finance pages; source numbers refer to the list at the end of this section).

**Does any existing tool already do this?** No tool found does a WhatsApp-native outbound document chase for small UAE firms. Every WhatsApp integration found points the other way (the firm sending invoices and payment reminders to its own debtors) or is inbound only. Every tool that does chase clients for documents is email or SMS based, priced per seat in US dollars, and has no UAE deadline calendar. The idea is not killed by the market. The nearest threats are Zoho Practice (WhatsApp plus client portal, sitting on the Zoho Books install base, but no published UAE price and no deadline engine) and one UAE CRM that claims WhatsApp document-collection reminders but whose site was unreachable three times, so that claim is unverified and should be checked before any build.

| Tool | Chases clients over WhatsApp? | Collects documents by portal or email? | UAE VAT or corporate tax deadline reminders? | UAE price |
|---|---|---|---|---|
| Zoho Books (UAE edition) | Partial and the wrong direction: native WhatsApp sends invoices, statements and payment reminders to the firm's own customers (prepaid credits at Meta rates) [1][2][3] | Customer portal and a Documents module; no document-request workflow [4] | No | AED 60 to 660 per organisation per month on annual billing [4] |
| Zoho Practice | WhatsApp integration listed on Standard and Premium; client portal with document exchange and information requests on Premium; automated reminders and deadline tracking not stated [5] | Yes on Premium | No | UAE page not available; no AED price found [6] |
| Xero plus Hubdoc | No | Hubdoc captures by email, mobile and scan; no client reminders; Xero's own partner kit tells accountants to use recurring calendar invites [7][8] | VAT filing and UAE e-invoicing support; no corporate tax reminders [7] | USD 29, 50, 75 a month; price rise 1 Nov 2026 [7] |
| QuickBooks Online | No | Accountant access only; no document-request feature [9] | No | AED prices not rendered on the UAE page; third-party figures unverified [10] |
| Dext | Inbound only: a client can send receipts to Dext over WhatsApp; no outbound requests or reminders [11] | Mobile, email, bank fetch | No | Not public for the UAE |
| Karbon | No | Client requests on all tiers; automatic client reminders on the Business tier only [12] | No | USD 59 to 89 per user per month, annual [12] |
| TaxDome | No; SMS via Twilio for US and Canada only [13] | Yes, strong: requests, organisers, reminders [14] | No | USD 800 to 1,200 per user per year [14] |
| Canopy | No | Client requests with automated email reminders [15] | No | USD 74 to 149 per user per month [15] |
| Financial Cents | No | Client tasks and requests on all tiers; automated follow-ups on Scale and above [16] | No | USD 19 solo; USD 49 to 69 per user per month [16] |
| Content Snare | No, email and SMS only with monthly SMS quotas [17] | Yes, best in class no-login request link | No | USD 35 to 215 plus per month [17] |
| Wafeq (UAE and Saudi) | Sends invoices over WhatsApp through a partner integration; no reminders or collection [18] | No | No | AED 57 to 200 a month annual, 69 to 249 monthly [19] |
| Qoyod | WhatsApp invoice notifications via third parties; Saudi-centric [20] | No | No | SAR 138 to 379.5 a month; UAE availability unverified [21] |
| TallyPrime | Outbound invoices and bulk payment reminders over WhatsApp; no document collection [22] | No | No | AED 100 to 200 a year add-on [22] |
| UAE-specific tools | One UAE practice tool offers portal uploads and VAT renewal reminders, no WhatsApp, no corporate tax [23]; one UAE CRM claims WhatsApp document-collection reminders, site unreachable, unverified [24]; Indian tools do "practice management inside WhatsApp" for GST, not the UAE [25] | | | |

**What is genuinely missing, and therefore the product:** WhatsApp-first outbound requests with the reply filed automatically; the client never installs or logs into anything; Arabic and English templates; a UAE corporate tax, VAT and e-invoicing calendar driving the chase; flat per-firm pricing (AED 300 a month undercuts a three-seat Karbon or Canopy by two to three times).

**WhatsApp Business API cost, the risk the source idea flagged.** Meta bills per delivered message by category since 1 July 2025; each number gets 1,000 free service messages a month, chargeable beyond that from 1 October 2026 [26]. UAE per-message rates are only on Meta's downloadable rate card; third parties quote about USD 0.016 per utility message and USD 0.058 per marketing message, unverified against the card [27]. A document chase is a utility template, so a 15-person firm's clients cost dirhams, not hundreds of dirhams, per month. Provider fees: 360dialog about EUR 49 per number per month with Meta pass-through [28]; Twilio no platform fee plus USD 0.005 a message [29]; Wati from SGD 119 a month [30]; Interakt USD 55 to 69 a month [31]. Conclusion: at AED 300 a month per firm the message cost is well inside margin; the real friction is Meta business verification per firm number, which takes days, not money. That is why section 2 sets the price floor near AED 150.

**Regulatory dates that drive "why now" (verified where stated).**

| Item | Fact | Verification |
|---|---|---|
| E-invoicing pilot | 1 July 2026, by invitation and voluntary (Ministerial Decision 244 of 2025) [32][33] | Verified via advisory summaries of the Ministry decision; the Ministry page shows the timeline as an image [34] |
| Businesses at or above AED 50M revenue | Service-provider appointment by 31 July 2026, reported extended to 30 October 2026; mandatory issue and receive from 1 January 2027 [32][35][36] | Dates verified; the extension is reported by secondary sources only |
| Businesses under AED 50M revenue | Service provider by 31 March 2027; mandatory from 1 July 2027 [32][33] | Verified |
| Government entities | Service provider by 31 March 2027; mandatory 1 October 2027 [32][33] | Verified |
| E-invoicing penalty | AED 5,000 per month or part for failing to implement or appoint a provider (Cabinet Decision 106 of 2025) [37] | Secondary source only |
| Corporate tax registration | Staggered by licence month through 2024 for existing entities; new entities within three months of incorporation (FTA Decision 3 of 2024) [38] | Verified |
| Late registration penalty | AED 10,000 (Cabinet Decision 75 of 2023 as amended) [39]; waiver if the first return is filed within seven months [40] | Verified, primary |
| Corporate tax return | Due within nine months of the tax-period end; a 31 December 2025 year end files by 30 September 2026 [41]; the first wave was 30 September 2025 [42] | Verified, primary |
| Late filing penalty | AED 500 a month for the first twelve months, AED 1,000 a month from month thirteen [39] | Verified, primary |
| VAT return | Due 28 days after the tax period ends [43]; quarterly by default, monthly where the FTA assigns it [44][45] | 28-day rule verified; the quarterly default and the turnover threshold for monthly were not found on the FTA site |

**Verdict for the kit.** The idea survives the competitor check. The two things that could still kill it are in the interviews, not the market: firms that do not actually lose hours to chasing, and firms whose clients will not reply to a bot. The one external check to run before building is the unreachable UAE CRM's WhatsApp claim.

**Sources.**
1. zoho.com/ae/books/help/integrations/whatsapp/integrate-with-whatsapp.html
2. zstatic.zohostatic.com/ae/books/help/integrations/how-credits-work.html
3. zoho.com/qa/books/kb/integrations/invoice-notif-via-whatsapp.md
4. zoho.com/ae/books/pricing/
5. zoho.com/us/practice/pricing/
6. zoho.com/practice/pricing/
7. xero.com/ae/pricing-plans/
8. xero.com/ae/partner-toolkit/switch-to-xero/hubdoc-email-templates/
9. quickbooks.intuit.com/ae/pricing/
10. osome.com/ae/guides/best-bookkeeping-software-for-small-business/
11. help.dext.com/en/articles/402584-adding-documents-via-whatsapp
12. karbonhq.com/pricing/
13. help.taxdome.com/article/taxdome-integrations
14. softwarefinder.com/accounting-software/taxdome
15. getcanopy.com/pricing
16. financial-cents.com/pricing/
17. contentsnare.com/pricing/
18. wafeq.com/en-ae/press-releases/media-center/interakt-and-wafeq-announce-seamless-integration-to-simplify-invoice-sharing-via-whatsapp
19. wafeq.com/en-ae/pricing
20. qoyod.com/marketplace/notifications/whatsapp-notifications/
21. qoyod.com/en/knowledge-base/qoyod-plans-and-pricing-subscription-tiers-features-add-ons/
22. tallysolutions.com/mena/tallyprime-with-whatsapp-business/
23. rispercrm.com/blog/best-client-portal-software-for-accountants-complete-2026-guide
24. hellogrowthcrm.com/crm-for-accounting-uae (unreachable at time of check)
25. suvit.io/post/top-10-practice-management-software
26. developers.facebook.com/docs/whatsapp/pricing
27. richautomate.in/blog/whatsapp-api-pricing-by-country-2026
28. docs.360dialog.com/docs/360dialog/prices-plans-and-payment-options
29. twilio.com/en-us/messaging/channels/whatsapp/pricing
30. wati.io/pricing/
31. interakt.shop/pricing/
32. kpmg.com/us/en/taxnewsflash/news/2025/10/uae-framework-scope-implementation-e-invoicing-system.html
33. taxnews.ey.com/news/2025-1964-uae-ministry-of-finance-publishes-ministerial-decisions-on-e-invoicing
34. mof.gov.ae/einvoicing/
35. wafeq.com/en-ae/tax-and-reporting/uae-e-invoicing-timeline
36. vatupdate.com/2026/10/20/uae-refines-its-e-invoicing-framework-ahead-of-the-2027-rollout/
37. middleeastbriefing.com/news/uae-penalties-non-compiance-new-e-invoicing-regime/
38. deloitte.com/middle-east/en/services/tax/perspectives/fta-publishes-a-decision-specifying-timelines-for-applying-for-ct-registration.html
39. mof.gov.ae/wp-content/uploads/2024/03/Cabinet-Decision-No.-75-of-2023-and-its-amendments-on-the-Administrative-Penalties-for-Violations-Related-to-the-Application-of-Federal-Decree-Law-No.-47-of-2022.pdf
40. deloitte.com/middle-east/en/services/tax/perspectives/public-clarification-on-penalty-waiver-released.html
41. tax.gov.ae/en/media.centre/news/pr.2809.aspx
42. gulfnews.com/business/corporate-tax/uae-corporate-tax-some-businesses-will-need-to-file-their-first-tax-returns-by-sept-30-1.1727150415239
43. tax.gov.ae/en/taxes/Vat/vat.topics/filing.vat.returns.and.making.payments.aspx
44. zoho.com/ae/books/vat/faq/general/vat-returns-filing.html
45. tallysolutions.com/mena/uae-vat/vat-return-filing-period-uae/

---

## 6. What happens after the five calls

- Score each call on the section 1 sheet within ten minutes; keep the sheets in the operator's own notes, not in any shared project.
- Tally against section 2 on Friday. BUILD, DO NOT BUILD or RE-SCOPE is a one-line decision with the tally attached.
- If BUILD: the pilot reuses the fleet's per-client Telegram channel model on WhatsApp Business API (dedicated number per firm, per-firm data store designated before the first message), the chase logic is a checklist per client per period, and the deadline calendar is a static UAE VAT and corporate tax rules table. Nothing of that is built before two yeses and one paid month.
