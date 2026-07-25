# Spec — MIF bank-statement importer (irsyad, op#6533)

**From:** cc-orchestrator (hub) · **Repo:** ihsanos · client-facing (irsyad), MONEY-domain (bank→donation reconciliation). **Work in an ISOLATED worktree off origin/main.**

## Goal
Let the existing bank-import flow also accept **MIF bank-statement CSVs** (currently OCBC `.xlsx` only). The client's samples are on the hub: `logs/tg_media/MIF Bank Statement _1-31 Jan 2026_.csv` (+ Feb/Mar/Apr/May/June). Copy one into the worktree (or scp from the hub `Musa@100.83.21.34:~/wingmen/orchestrator/logs/tg_media/`) to test against.

## Design (reuse the matcher — one repo, zero forks)
The OCBC path is: `parseOcbcSheet()` (`src/shared/lib/ocbc-parser.ts`) → normalized `ParseResult { rows: ParsedOcbcRow[] }` → `parseBankStatementAction` (`src/actions/bank-import.ts`) maps to `PreviewRow[]` (donor/category suggestions) → admin reviews → `commitBankImportAction` creates donations. **Read those 2 files first for the exact types.**
`ParsedOcbcRow = { post_date: "YYYY-MM-DD", amount: number(>0 credit), reference_raw: string, paynow_tag: string|null, transaction_type: string }`.

**Build a MIF parser that outputs the SAME shape**, so the matcher + commit are UNCHANGED:
1. `src/shared/lib/mif-parser.ts` — `parseMifCsv(csvText: string): ParseResult` (reuse the `ParseResult`/row type — export a shared `ParsedBankRow` from ocbc-parser or a shared module; don't duplicate the type).
   - MIF CSV header (comma-sep, first row): `Account No.,Account Currency,Opening Balance,Closing Book Balance,Closing Available Balance,Total Credit Amount,Total Credit Count,Statement Value Date,Total Debit Count,Total Debit Amount,Hold Amount,Statement Date,Post Date,Debit Amount,Credit Amount,Transaction Type Code,Ref For Account Owner,Statement Details Info,Our Ref,Supplementary Details`
   - Map per row: `post_date` ← `Post Date` (YYYYMMDD → ISO, reuse a date helper); `amount` ← `Credit Amount`; **FILTER to credits only** (Credit Amount > 0; skip debit/zero rows, same as OCBC); `reference_raw` ← `Statement Details Info` (contains "PayNow: <tag>", e.g. "…PayNow: Jumaat", "…PayNow: MIF"); `paynow_tag` ← reuse `extractPaynowTag()` from ocbc-parser (already handles the "PayNow: <tag>" regex); `transaction_type` ← `Transaction Type Code` (NTRF/INT); sender name ← `Ref For Account Owner` (if the PreviewRow/matcher uses a payer-name field, wire it — check what OCBC populates).
   - Parse CSV robustly (a field like Statement Details Info may contain commas — use a real CSV parse, not naive split; check if the repo already has a CSV util, e.g. the people csv-import or a papaparse dep).
   - Header-detect / validate (mirror OCBC's `EXPECTED_HEADER_TOKENS`) so a wrong file errors clearly.
2. Route by format in `parseBankStatementAction`: detect OCBC `.xlsx` vs MIF `.csv` (by file extension and/or header tokens) → call the right parser → same downstream. Keep OCBC working unchanged.
3. UI `src/app/dashboard/admin/bank-import/bank-import-client.tsx`: `accept=".xlsx,.csv"` + copy that says OCBC .xlsx **or** MIF .csv. `org_admin`-only stays.

## Acceptance (real-flow proof — money code)
- Unit test (mirror `ocbc-parser.test.ts`): `parseMifCsv` on a fixture from the real sample → correct count of credit rows, correct amounts/dates/paynow_tags, debits+zero skipped. Reconcile the parsed credit total against the sample's `Total Credit Amount` column (sanity).
- Run the sample through end-to-end (parse → preview) and confirm the credit transactions surface correctly.
- `next build` green (both projects). tsc + eslint clean. Existing OCBC tests still pass.
- Do NOT commit real donor PII from the sample into fixtures — synthesize or redact names in test fixtures.

## Report back
Post `agent_messages` → cc-orchestrator (update): commit sha, parse-test result (rows parsed + total reconciled to the sample), build status. Hub reviews + does the client-facing "it's live". **Do NOT import real donation data** — this is the IMPORTER only; the actual data load is a separate gated migration.
