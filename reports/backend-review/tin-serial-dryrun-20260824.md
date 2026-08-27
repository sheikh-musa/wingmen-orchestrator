# Tin (Tabung) Serial Import — DRY-RUN Matching Analysis

**Date:** 2026-08-24  
**Org:** irsyad `73339164-7c1f-40ba-a093-33f1f292dd4c` (goumlyne silo)  
**Source:** `~/.wingmen/quarantine/report_donor_1.xlsx` — tabs `Tabung Fajar` + `Tabung Kedai` only  
**Mode:** READ-ONLY. No DB writes, no file writes except this report. Nothing was attached to any donation.  
**Scope note:** `Tabung Keluarga`, `Masjid`, `2026 Ramadhan` tabs and any student-linked rows EXCLUDED (minors / out of scope).

## Method

- Primary match key: **donation amount == sheet `Total`** AND **collection date == sheet `Collection Date`**.
- Donor **name** is a supporting hint only (fuzzy: case-insensitive substring / token overlap), never the sole basis — there are known duplicate person records.
- `donated_at` is stored as `timestamptz`; the org uses two conventions (16:00 UTC = midnight Singapore, and 00:00 UTC). A donation is treated as a date-match if **either** its Singapore-local date **or** its UTC date equals the sheet date. This is the most permissive honest read and avoids false UNMATCHED from a timezone boundary.
- Serials collected per row = every populated cell among the Returned + New/Issue serial columns (D, E, I, J).
- Classification: exactly-one amount+date match & name consistent -> **CONFIDENT**; exactly-one but name not consistent -> **AMBIGUOUS (name-mismatch)**; more than one -> **AMBIGUOUS (multiple)**; none -> **UNMATCHED**.
- NOTHING is auto-attached. Every CONFIDENT row is still a human-review proposal.

## Tabung Fajar

- **Donor rows (non-empty):** 179  (plus 510 blank/junk rows skipped)
- **CONFIDENT:** 64
- **AMBIGUOUS (name-mismatch):** 5
- **AMBIGUOUS (multiple):** 0
- **UNMATCHED:** 110
- Data-quality within donor rows: 9 row(s) with NO serial numbers, 3 row(s) with amount = 0.

### CONFIDENT — 64 rows (showing up to 10 samples; full set is the remaining CONFIDENT rows, reviewable on request)

| sheet row | donor name | amount | date | serial(s) to attach | matched donation id | matched person |
|---|---|---|---|---|---|---|
| 16 | Hajah Janiah | 432.1 | 2026-01-06 | 25287, 25436 | 1c3b390d-2b38-4234-94a4-672f6b4fd497 | Hajah Janiah Bte Hodari |
| 17 | Hajah Janiah | 186.2 | 2026-01-06 | 25286, 25441 | abb4ba02-f6eb-42a0-b476-40540e0bb957 | Hajah Janiah Bte Hodari |
| 20 | Ms Aishah Shaul Hamid | 301.07 | 2026-01-07 | 2300054 | 625632fb-6ad8-4f17-bc6d-e20f7f9eb10d | Ms Aishah Shaul Hamid |
| 21 | Nor Aishah | 488.65 | 2026-01-15 | 25144, 25359 | e8c7e3b8-f052-4b63-b270-0505b57d4c6d | Nor Aishah Binte Ariffin |
| 22 | Nor Aishah | 578.15 | 2026-01-15 | 25145, 25360 | b5c17409-9a40-4d98-b20a-44a6711147a4 | Nor Aishah Binte Ariffin |
| 26 | Rahimah Jasmin | 825.55 | 2026-01-14 | 25259, 250889 | 55bc14c5-0dcb-4216-a069-f7dd65ae17be | Rahimah Jasmin |
| 27 | Mas Riza Mohd Razali | 243.75 | 2026-01-14 | 2400118, 240937 | dc02810a-81ba-4966-8021-f0025db42a83 | Mas Riza Mohd Razali |
| 28 | Mas Riza Mohd Razali | 229.45 | 2026-01-14 | 2400119, 25437 | 74578214-c5ef-4990-a876-3a0c87ec0c91 | Mas Riza Mohd Razali |
| 29 | Zalinah Jaafar | 314.6 | 2026-01-10 | 25077, 25352 | 5bd195fa-5cab-4a91-a024-a2dfcb247521 | Zalinah Jaafar |
| 30 | Zalinah Jaafar | 195.41 | 2026-01-10 | 25188, 25353 | fb105989-39d1-4502-9037-8f6ab1a551ac | Zalinah Jaafar |

### AMBIGUOUS (name-mismatch) — 5 rows (FULL list)

| sheet row | sheet name | amount | date | serial(s) | single donation matched | its person name |
|---|---|---|---|---|---|---|
| 18 | Hamba Allah | 330.65 | 2026-01-07 | 2400192 | 91228b59-b6e6-44bc-8c90-68673477277e |  |
| 19 | Hamba Allah | 169.45 | 2026-01-07 | 2300470 | eb4760cf-ec9c-4bfe-b6e3-28119e91de13 |  |
| 49 | Cikgu Isham | 213.85 | 2026-02-02 | 25232, 240940 | 8652ea2f-9ca0-4415-af59-1644a093b34c | Cikgu Sham |
| 69 | Tabung Office | 1899.56 | 2026-02-20 | Tabung Office | 9562fbd8-2560-47e3-8c53-84f3835c8247 |  |
| 78 | Hamba Allah | 80.95 | 2026-02-23 | Own Tabung | db8cd21b-10ee-4037-9a9a-ac2dbe476dbf |  |

### AMBIGUOUS (multiple) — 0 rows (FULL list)

_None._

### UNMATCHED — 110 rows (FULL list)

| sheet row | sheet name | amount | date | serial(s) | reason / note |
|---|---|---|---|---|---|
| 2 | Rusle Bin Sarkinan | 202.6 | 2026-01-07 | 25456, 240936 | no amount+date donation |
| 3 | Hamidah Bte Amyadi | 278.6 | 2026-01-07 | 25490, 250896 | no amount+date donation |
| 4 | Mis'anah Bte Samat | 559.9 | 2026-10-01 | 25185, 25466 | no amount+date donation |
| 5 | Nur Jannah Al Firdaus | 387.35 | 2026-01-10 | 25194, 25357 | no amount+date donation |
| 6 | Nur Jannah Al Firdaus | 328.65 | 2026-01-10 | 25499, 25358 | no amount+date donation |
| 7 | En Anuar Bin Hashim | 2129.1 | 2026-01-10 | 25393, 25355 | no amount+date donation |
| 8 | En Anuar Bin Hashim | 350.4 | 2026-01-10 | 25394, 25356 | no amount+date donation |
| 9 | Faridah | 464.05 | 2026-02-01 | 25388, 25439 | no amount+date donation |
| 10 | hakiem Hasbi | 106.0 | 2026-02-01 | 25454, 240931 | no amount+date donation |
| 11 | Siti Suriah Binti Taib | 1734.0 | 2026-06-01 | 25330 | no amount+date donation — SWAP-DATE candidate: 2026-01-06 -> Siti Suriah Taib (ba66b50f-2d8b-4456-bee2-214fbff175fa) |
| 12 | Stall 4 | 117.75 | 2026-07-01 | 25458 | no amount+date donation — SWAP-DATE candidate: 2026-01-07 -> Stall 4 Irsyad Canteen (67a42182-3c8d-4604-9b7d-70386cca8fd4) |
| 13 | Datin Hayati | 297.85 | 2026-07-01 | 25081 | no amount+date donation — SWAP-DATE candidate: 2026-01-07 -> Datin Hayati (3d8bfa9a-9fc0-4803-9ba7-455376479522) |
| 14 | Hamba Allah | 96.3 | 2026-07-01 | 2300468 | no amount+date donation — swap-date 2026-01-07 has 1 amount-match(es) (name check: not-consistent) |
| 15 | Encik Sanusi | 427.1 | 2026-02-01 | 2300016 | no amount+date donation |
| 23 |  | 9965.92 | None | (none) | no/invalid date |
| 25 | Rahimah Jasmin | 619.7 | 2026-01-14 | 25258, 250888 | no amount+date donation |
| 63 | Tuan Borhan/Puan Bechek Md Hasan | 0.0 | None | 25443 | no/invalid date |
| 64 | Zahid Koviyan | 0.0 | 2026-02-02 | 24154/24151, 24150 | no amount+date donation |
| 65 |  | 15539.069999999996 | None | (none) | no/invalid date |
| 74 | Shafiq Bin Mohamed Affandi Bedok | 541.15 | 2026-02-20 | Tabung 1, 26411 | no amount+date donation |
| 75 | Shafiq Bin Mohamed Affandi Bedok | 428.75 | 2026-02-20 | Tabung 2, 26413 | no amount+date donation |
| 76 | Shafiq Bin Mohamed Affandi Bedok | 321.02 | 2026-02-20 | Tabung 3, 26408 | no amount+date donation |
| 84 | Misha Ayesha Mohamed Taufik | 0.0 | 2026-02-20 | 26409 | no amount+date donation |
| 87 |  | 9120.979999999998 | None | (none) | no/invalid date |
| 98 | Puan Saemah Bik | 463.0 | 2026-02-28 | 25433, 26412 | no amount+date donation |
| 99 | Puan Saemah Bik | 282.45 | 2026-02-28 | 25435, 26419 | no amount+date donation |
| 100 | Puan Maina B Mahmod | 342.0 | 2026-02-28 | 25463, 26417 | no amount+date donation |
| 101 | Puan Maina B Mahmod | 261.5 | 2026-02-28 | 25465, 240941 | no amount+date donation |
| 102 | Puan Sapura Nor | 240.3 | 2026-03-16 | 25228, 25449 | no amount+date donation |
| 103 | Puan Sapura Nor | 245.45 | 2026-03-16 | 25226 | no amount+date donation |
| 104 | Puan Nadirah | 221.5 | 2026-03-16 | 25027, 25440 | no amount+date donation |
| 105 | Puan Nadirah | 163.7 | 2026-03-16 | 25496, 25450 | no amount+date donation |
| 106 | Puan Norlin Embong | 48.8 | 2026-03-11 | 25218, 25032 | no amount+date donation |
| 107 | Puan Norlin Embong | 59.1 | 2026-03-11 | 25219, 25161 | no amount+date donation |
| 108 | Puan Rahayu | 281.95 | 2026-03-13 | 25203, 25154 | no amount+date donation |
| 109 | Puan Rahayu | 327.1 | 2026-03-13 | 25204 | no amount+date donation |
| 110 | Ustaz Faiz Fathi | 264.1 | 2026-03-27 | Own Tabung | no amount+date donation |
| 111 | Puan Asmah Kashim | 337.65 | 2026-02-20 | 25406 | no amount+date donation |
| 112 | Cikgu Sakina | 438.2 | 2025-03-05 | 25413 | no amount+date donation |
| 113 | Hambal Allah | 68.85 | 2026-03-27 | 25237 | no amount+date donation |
| 114 | Hakiem Hasbi | None | 2026-03-13 | 25162 | no/invalid amount |
| 115 |  | 8262.099999999999 | None | (none) | no/invalid date |
| 117 | Mdm Rozana | 539.55 | 2026-03-26 | 25328, 26271 | no amount+date donation |
| 118 | Encik Abdul Khalid | 1880.0 | 2026-03-31 | 25270, 25153 | no amount+date donation |
| 119 | En Rahim | 360.45 | 2026-04-06 | 25290, 25155, 25165 | no amount+date donation |
| 120 | Puan Husnah Binte Hussain | 169.6 | 2026-04-08 | 25211, 25448 | no amount+date donation |
| 121 | Siti Aminah Amin | 374.3 | 2026-04-06 | 25061 | no amount+date donation |
| 122 | Siti Aminah Amin | 216.2 | 2026-04-06 | 25062 | no amount+date donation |
| 123 | Puan Hajah Kamariah | 195.8 | 2026-04-06 | 25040, 25157 | no amount+date donation |
| 124 | Puan Hajah Kamariah | 206.4 | 2026-04-06 | 25041, 25447 | no amount+date donation |
| 125 | Cikgu Shuhada | 627.65 | 2026-04-06 | 240938 | no amount+date donation |
| 126 |  | 3402.75 | None | (none) | no/invalid date |
| 129 | Tuan Rusle Bin Sarkinan | 318.3 | 2026-04-08 | 240936, 25159 | no amount+date donation |
| 130 | Noridah Binte Mahmood | 356.15 | 2026-03-18 | 230917, 25156 | no amount+date donation |
| 131 | Puan Saemah Bik | 445.0 | 2026-04-15 | 25422, 25164 | no amount+date donation |
| 132 | Tuan Jamal | 265.15 | 2026-04-15 | 25429, 26284 | no amount+date donation |
| 133 | Tuan Jamal | 219.55 | 2026-04-15 | 25430, 26275 | no amount+date donation |
| 134 | Puan Janiah | 327.1 | 2026-04-14 | 2300416, 25019 | no amount+date donation |
| 135 | Puan Janiah | 361.8 | 2026-04-14 | 25019, 25019 | no amount+date donation |
| 136 | En Amsyar | 174.48000000000002 | 2026-04-24 | 25306, 25163 | no amount+date donation |
| 137 | En Basir Mohaed Yusof | 190.0 | 2026-04-24 | 25079, 25080 | no amount+date donation |
| 138 | En Basir Mohaed Yusof | 189.95 | 2026-04-24 | 25080, 26455 | no amount+date donation |
| 139 | Puan Zainab Abdul Manap | 91.95 | 2026-04-28 | 25484, 26436 | no amount+date donation |
| 140 | Puan Zainab Abdul Manap | 73.5 | 2026-04-28 | 25485, 26442 | no amount+date donation |
| 141 |  | 3012.93 | None | (none) | no/invalid date |
| 143 | Puan Rosidah | 364.7 | 2026-04-29 | 25033, 26441 | no amount+date donation |
| 144 | Puan Rosidah | 304.75 | 2026-04-29 | 25034, 26446 | no amount+date donation |
| 145 | Puan Liza | 439.28 | 2026-04-29 | 25225, 26437 | no amount+date donation |
| 146 | Puan Mariam | 459.7 | 2026-05-04 | 25105, 26447 | no amount+date donation |
| 147 | Puan Shariffah Shariwen | 404.5 | 2026-05-21 | 25255, 26450 | no amount+date donation |
| 148 | Puan Hamidah Amyadi | 615.9 | 2026-05-20 | 250896, 26439 | no amount+date donation |
| 149 | Hamba Allah | 147.05 | 2026-05-26 | 26281 | no amount+date donation |
| 150 | Hamba Allah | 123.6 | 2026-05-26 | 26273 | no amount+date donation |
| 151 |  | 2859.48 | None | (none) | no/invalid date |
| 153 | En Saifullizan Bin Selamat | 1005.2 | 2026-05-26 | 25423, 26443 | no amount+date donation |
| 154 | En Saifullizan Bin Selamat | 938.65 | 2026-05-26 | 26448, 26448 | no amount+date donation |
| 155 | Puan Nur Jannah Al Firdaus | 510.1 | 2026-05-26 | 25357, 26438 | no amount+date donation |
| 156 | Puan Nur Jannah Al Firdaus | 342.3 | 2026-05-26 | 25358, 26445 | no amount+date donation |
| 157 | Puan Nur Faiezah | 462.7 | 2026-05-26 | 25086, 26440 | no amount+date donation |
| 158 |  | 3258.95 | None | 26444 | no/invalid date |
| 160 | Puan Nur Faiezah | 332.95 | 2026-05-26 | 25087, 26440 | no amount+date donation |
| 161 | Puan Hamidah Kurdi | 139.6 | 2026-05-31 | 25300, 26382 | no amount+date donation |
| 162 | Puan Ruhama | 89.4 | 2026-05-29 | 25162, 26376 | no amount+date donation |
| 163 | Siti Suriah Binti Taib | 1464.0 | 2026-06-09 | 250898, 26379 | no amount+date donation |
| 164 | Mdm Sahrina | 359.0 | 2026-06-19 | 25452, 26386 | no amount+date donation |
| 165 | Puan Zalinah Jaafar | 173.45 | 2026-06-18 | 25221, 26501 | no amount+date donation |
| 166 | Puan Zalinah Jaafar | 252.5 | 2026-06-18 | 25352, 26506 | no amount+date donation |
| 167 | Puan Zalinah Jaafar | 360.3 | 2026-06-18 | 25353, 26507 | no amount+date donation |
| 168 | Tabung Office | 4551.1 | 2026-06-24 | Tabung Office | no amount+date donation |
| 169 | Puan Sharifah Shifa | 614.65 | 2026-06-01 | 25213, 26384 | no amount+date donation |
| 170 | Puan Sharifah Shifa | 133.6 | 2026-06-01 | 25214, 26388 | no amount+date donation |
| 171 | Puan Noreen Omar | 256.77 | 2026-06-01 | 25260, nil | no amount+date donation |
| 172 | Puan Aida | 324.05 | 2026-06-08 | 2400273, 26378 | no amount+date donation |
| 173 | Puan Aida | 334.85 | 2026-06-08 | 2400274, 26383 | no amount+date donation |
| 174 | Puan Saripah | 109.1 | 2026-06-09 | 25285, 26387 | no amount+date donation |
| 175 | Puan Saripah | 1407.25 | 2026-06-09 | 25283, 26449 | no amount+date donation |
| 176 | Puan Faridah Sanip | 400.8 | 2026-06-23 | 25439, 26502 | no amount+date donation |
| 177 |  | 11303.369999999999 | None | (none) | no/invalid date |
| 179 | Puan Salmah | 238.4 | 2026-06-25 | 25208, 26503 | no amount+date donation |
| 180 | Puan Salmah | 288.0 | 2026-06-25 | 25209, 26505 | no amount+date donation |
| 181 | Tuan Ahmad AlKastalani | 296.0 | 2026-06-25 | 26462, 26496 | no amount+date donation |
| 182 | Tuan Ahmad AlKastalani | 505.7 | 2026-06-25 | 26463, 26504 | no amount+date donation |
| 183 | Puan Hamidah Bte Yaacon | 387.05 | 2026-06-02 | 172200, nil | no amount+date donation |
| 184 | Canteen Stall 4 | 170.3 | 2026-07-01 | 230938 | no amount+date donation |
| 185 | Puan Norizan Ismail | 218.0 | 2026-06-30 | 2400275, 26390 | no amount+date donation |
| 186 | Puan Norizan Ismail | 132.2 | 2026-06-30 | 2400279, 26508 | no amount+date donation |
| 187 | Puan Norizan Ismail | 231.9 | 2026-06-30 | 2400276, 26498 | no amount+date donation |
| 188 | Puan Norizan Ismail | 297.3 | 2026-06-30 | 2400277, 26497 | no amount+date donation |
| 189 | Puan Norizan Ismail | 203.7 | 2026-06-30 | 2400278, nil | no amount+date donation |
| 190 |  | 2968.55 | None | (none) | no/invalid date |

## Tabung Kedai

- **Donor rows (non-empty):** 6  (plus 989 blank/junk rows skipped)
- **CONFIDENT:** 0
- **AMBIGUOUS (name-mismatch):** 0
- **AMBIGUOUS (multiple):** 0
- **UNMATCHED:** 6
- Data-quality within donor rows: 0 row(s) with NO serial numbers, 0 row(s) with amount = 0.

### CONFIDENT — 0 rows (showing up to 10 samples; full set is the remaining CONFIDENT rows, reviewable on request)

| sheet row | donor name | amount | date | serial(s) to attach | matched donation id | matched person |
|---|---|---|---|---|---|---|

### AMBIGUOUS (name-mismatch) — 0 rows (FULL list)

_None._

### AMBIGUOUS (multiple) — 0 rows (FULL list)

_None._

### UNMATCHED — 6 rows (FULL list)

| sheet row | sheet name | amount | date | serial(s) | reason / note |
|---|---|---|---|---|---|
| 4 | Sariah Store | 3291.5 | 2026-02-01 | 25231, T24260, T24350, 25438 | no amount+date donation |
| 5 | Julaiha Muslim | 793.95 | 2026-02-02 | T24349 | no amount+date donation |
| 6 | Zahid Koviyan | 1104.35 | 2026-02-02 | T20064, T24154, T24151 | no amount+date donation |
| 7 | Zahid Koviyan | 2910.35 | 2026-02-02 | T20063, T24150 | no amount+date donation |
| 8 | Usrah Medical Clinic | 5473.1 | 2026-04-06 | T20054, T24152 | no amount+date donation |
| 9 | Albatross Barber | 1344.05 | 2026-04-09 | T20101, T25158 | no amount+date donation |

## Kedai tab — actual column structure found

The `Tabung Kedai` header is **row 3** (rows 1-2 blank), data from row 4. 12 columns:

| col | letter | header | role |
|---|---|---|---|
| 1 | A | S/N | serial index |
| 2 | B | Name | donor / shop name |
| 3 | C | Collection Date | collection date |
| 4 | D | Return Tabung Serial Number | returned serial (per client) |
| 5 | E | Return Tabung Serial Number | returned serial 2 (per client) |
| 6 | F | Amount Notes | notes subtotal |
| 7 | G | Amount Coins | coins subtotal |
| 8 | H | Total | amount matched on |
| 9 | I | Issue Tabung Serial Number | newly-issued serial |
| 10 | J | Issue Tabung Serial Number | newly-issued serial 2 |
| 11 | K | Irsyad Representative | collector |
| 12 | L | Remarks | free text (often 'Bank in $X - date') |

Serials per row collected from D, E, I, J (client confirmed returned serials are D+E; I+J are the issued replacements). Note: unlike Fajar, Kedai mixes numeric serials (e.g. `25231`) and `T`-prefixed serials (e.g. `T24260`) in the same columns; several rows put a `T`-serial in the Collection-Date-adjacent columns as expected.

## Data-quality observations

- **Every Kedai row is UNMATCHED**: none of the 6 Kedai `Total` amounts (3291.5, 793.95, 1104.35, 2910.35, 5473.10, 1344.05) exist as a donation for this org at any date. These shop/kedai collections appear **not to be recorded as `donations` rows** in the goumlyne silo (or are recorded under a different amount/mechanism). Human decision needed on whether Kedai belongs in this import at all.
- **Fajar UNMATCHED is large (110 of 179).** For the large majority the sheet `Total` does not exist as any donation amount for the org (not a date problem) — these collections look like they are simply **not yet entered as donations**, or the recorded amount differs (e.g. notes/coins split differently).
- **Day/month date confusion (minor):** a handful of UNMATCHED Fajar rows exact-match an amount on the **day/month-swapped** date with a consistent name (e.g. sheet `2026-06-01` Siti Suriah Taib 1734 ↔ donation `2026-01-06`; sheet `2026-07-01` Stall 4 117.75 ↔ `2026-01-07`). These are flagged inline as `SWAP-DATE candidate`. Only ~3-4 rows fit this pattern, so it was NOT used to reclassify — but it signals the sheet's dates were partly entered DD/MM and mis-read as MM/DD. A reviewer should sanity-check the exact collection dates with the client. Several Fajar rows also carry implausible months (e.g. `2026-10-01`) consistent with the same swap.
- **Amount collisions:** some UNMATCHED rows share an amount with a donation on a totally unrelated date/name (2022-2024) — coincidental, correctly left UNMATCHED.
- **Name duplicates confirmed relevant:** matching deliberately did not rely on name; several sheet names differ in spelling/titles from the person record (e.g. 'Hamidah Bte Amyadi' vs 'Hamidah Amyadi'), which the fuzzy check tolerates for CONFIDENT but the 5 AMBIGUOUS(name-mismatch) rows are where a lone amount+date hit lands on a person whose name does not overlap — human must confirm these are the right donor before any serial is attached.
- **Blank rows:** 510 blank/junk trailing rows in Fajar and 989 in Kedai were skipped (they inflate `max_row`).
- Within real donor rows: Fajar had 9 rows with no serial at all (nothing to attach even if matched) and 3 rows with amount 0.

## Bottom line

- **Fajar:** 64 CONFIDENT proposals ready for human review; 5 need name adjudication; 110 have no system donation to attach to (mostly not-yet-entered).
- **Kedai:** 0 matchable — the 6 shop collections are absent from `donations`. Needs a client/product decision before any Kedai import proceeds.
- Nothing was written. This is a proposal set for human review only.
