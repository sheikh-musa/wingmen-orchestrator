# cc-quality Track-2 FULL audit — tabung-keluarga.ts display_name resolutions (CAI-RESP-1203)

- **Scope:** classify every donor/student `display_name` resolution in `src/actions/tabung-keluarga.ts` (origin/main) as task-scoped-search (fine, CAI-642-precedent) vs unconditional-browse (needs the same org_admin gate as `getKkTopStudentsAction`).
- **Requester:** orch-console (bus #29366), framed non-urgent ("the live leak is closed via PR #388; this just clears the rest").
- **Reviewer:** cc-quality, Sonnet 5 (standard tier, PII-attr class, as specified).

## ⚠ HEADLINE FINDING — the framing is wrong. PR #388 does NOT close the live leak; it closes ONE of at least THREE currently-live, ungated exposures on the same page.

I verified PR #388's actual diff at source rather than trust its title. It adds an `org_admin`-only gate to `getKkTopStudentsAction` (action layer) and wraps the "Top students" panel in `{isAdmin && (...)}` (render layer) — nothing else. That closing `)}` lands **immediately before** the `{/* Unreturned */}` section comment in `keluarga-admin-client.tsx`. Two more data sources on the exact same page, rendering the exact same class of student PII, are untouched:

### 🔴 NEW FINDING 1 (same severity as the confirmed leak, NOT fixed by #388): `listUnreturnedKkTinsAction` — "Tins still out" table
- **Action:** `listUnreturnedKkTinsAction` (line 1175). Its primary path calls `createServiceClient().rpc("tabung_unreturned_kk_tins", ...)` — a **service-role RPC that bypasses RLS entirely**. I pulled the live function definition on goumlyne: it's a plain SQL `JOIN sch_students s ... JOIN persons p ...` with **zero minors/sch_students exclusion of any kind** — `student_name` is a hardcoded output column.
- **Render path:** `src/app/dashboard/tabung/keluarga/keluarga-admin-client.tsx`, the "Tins still out" table (`{unreturned.map((row) => ... <td>{row.student_name}</td> ...)}`). I grepped every `isAdmin` occurrence in this 1228-line file (only 3: the "New batch" button, an empty-state string, and a prop pass-through to `ClassCompletionReport`) — **none gate this table**.
- **Role that reaches it:** the page (`src/app/dashboard/tabung/keluarga/page.tsx`) has **no role check at all** beyond `org_members` membership — `if (membershipError || !membership) redirect("/dashboard")`, nothing else. `listUnreturnedKkTinsAction` is fetched and shipped into the initial page props unconditionally, for **any role** that is a member of the org. This is the default, primary view of the page — worse exposure surface than the "Top students" secondary panel.

### 🔴 NEW FINDING 2 (same page, same mechanism): `listKkTinsByStatusAction` — per-batch status drill-down
- **Action:** `listKkTinsByStatusAction` (line 1398) — status-cell click drill-down. This one does **not** use the service-role bypass (plain RLS-bound client), but its trigger (`handleStatusCellClick`, the status-count cells under each batch) and its render (`filterTins.map(...) → row.student_name`) are **also outside any `isAdmin` gate** — same exhaustive grep confirms this.
- Because this path is NOT service-role-bypassed, it should be structurally backstopped by the persons-RLS carve-out (mig192) for `cashier`/`viewer`/`preparer` — see the "structural backstop" note below. **I have not live-verified this with a simulated non-admin session**; flagging the mechanism, not asserting it as proven.

### The rest of the file, classified

| Function | Classification | Reasoning |
|---|---|---|
| `lookupTinWithStudentAction` | **Task-scoped — fine** | Barcode/serial scan, single result, phone decrypted only for the one match. Counter-scan flow. |
| `lookupTinsByStudentNameAction` | **Task-scoped — fine** | Explicit ≥3-char name search required (blocks roster enumeration), hard-capped at 25 results, no phone/PII in bulk. Doc comment explicitly designed against this exact bug class. Reached only from `counter/counter-scan-client.tsx`. |
| `listUnreturnedKkTinsAction` (+ PostgREST fallback) | **🔴 Unconditional browse — LIVE, not gated by #388** | See Finding 1 above. |
| `listKkTinsByStatusAction` | **🔴 Unconditional browse — LIVE, not gated by #388** | See Finding 2 above. Likely RLS-backstopped for cashier/viewer/preparer (unverified live). |
| `getKkTopStudentsAction` | **Confirmed leak, fixed by PR #388** | Not re-litigated — matches your framing exactly. |
| `getKkStudentsByClassAction` | **Unconditional browse at the code level, but structurally different from #388's fix target** | No search param (class-scoped only). Uses the **plain RLS-bound client**, no service-role bypass — so mig192's `persons!inner` carve-out should drop the whole joined row for `cashier`/`viewer`/`preparer` (not just the name), same mechanism the codebase's own CAI-RESP-1107/1109/1111 fix (mig196) already documented for a sibling function in this file. Render (`class-completion-report.tsx`) shows `s.display_name` with **no `isAdmin` gate** — the only `isAdmin` conditional there gates serial-number *removal buttons*, not name display. Structurally protected for the 3 carved-out roles; **not verified live**; exposed to any OTHER role that can reach this page. |
| `getKkUnreturnedStudentsByClassAction` | **Same as above** | Identical shape/mechanism, the "still out" side of the same drill-down. |
| `listMissingTinReportsAction` | **Unconditional browse at the code level, but better-gated than the others** | Page-gated via `requireRole(["org_admin","cashier","preparer"])` (excludes `viewer`) — a real server-side role restriction, unlike the keluarga page. Its own doc comment claims RLS is "ALL org members, not just staff" — I verified this directly against the live `tabung_tin_missing_reports` policy (`auth_user_org_ids()`, confirmed broad). But the query embeds `persons!inner`, same RLS-backstop mechanism as above (not service-role-bypassed) — so `cashier`/`preparer` should structurally see empty results for student-linked rows, not leaked names. Not verified live. |

## The structural backstop, and why I'm not calling it a clean PASS

Postgres RLS on an `!inner`-joined table causes the whole parent row to disappear when the current role can't see the joined child row — this is standard RLS+INNER JOIN semantics, and I have **direct in-repo precedent** for this exact behavior in this exact codebase: the CAI-RESP-1107/1109/1111 fix (mig196, referenced in `supabase/migrations/_reserved.txt`) exists specifically because a sibling `sch_students!inner(persons!inner(display_name))` embed was silently dropping whole rows for non-admin roles (there, it caused a *money* bug — $0 reports — not a *safety* feature, but it's the same mechanism). So for `getKkStudentsByClassAction`, `getKkUnreturnedStudentsByClassAction`, and `listMissingTinReportsAction`, I believe cashier/viewer/preparer structurally see nothing rather than real names — but "I believe, reasoning from a documented precedent" is not the same as "I watched it happen." I did not stand up a simulated non-admin session to confirm this live (out of scope for a source-classification sweep at standard tier) — flagging it as the one thing that would upgrade these three from "probably fine" to "confirmed fine."

This backstop **does not exist at all** for `listUnreturnedKkTinsAction`'s primary path, because it never touches `persons` through the RLS-bound client — the RPC runs entirely under service-role with the exclusion never even attempted.

## Recommendation

Findings 1 and 2 should be treated with the **same urgency as the leak PR #388 is already fixing** — they're on the same page, expose the same class of data, and are live right now. The fix shape is already proven in this codebase twice over (PR #388's own pattern, and mig203/mig204's structural `NOT EXISTS sch_students` anti-join): either (a) org_admin-gate `listUnreturnedKkTinsAction`/`listKkTinsByStatusAction` the same way PR #388 gated `getKkTopStudentsAction` (fast, matches precedent, but loses the outstanding-tins worklist for cashier/preparer who may have a real operational need to see it), or (b) give `tabung_unreturned_kk_tins` (and the status-filtered query) the mig204-style structural minors-exclusion so it's safe to keep showing to non-admin roles for their legitimate return-tracking task, mirroring the umum_top_donors durable fix rather than the interim gate. (b) is more work but preserves the feature's actual purpose; that's a design call for you/cai, not mine to make.

Route to cai/orch-console as appropriate — this is Satr-class and time-sensitive regardless of how the original request was framed.
