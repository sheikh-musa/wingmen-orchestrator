# cc-quality review — console content `e94652bb405b5f88` (fc-v89, op#35396)

- **Reviewer:** cc-quality (Head of Quality). Ran on claude-opus-4-8.
- **Target:** console content hash `e94652bb405b5f88` in worktree `orchestrator.wt-console-revamp` (branch `lane/console-revamp`), a two-nit fast-follow on top of content `6f32a31f4a1c0655`.
- **Request:** bus #80509 (initial review) and #80517 (re-glance after folding both nits), from cc-console-revamp-1. Committing to orch-console #80499 (Musa op#35396).
- **Delivered via:** agent_messages bus rows #80511 (initial CODE PASS on `6f32a31f4a1c0655`) and #80524 (re-glance PASS on `e94652bb405b5f88`) — transcribed verbatim below; cc-quality did not write this file into the worktree itself this round, so the lane (me) is recording its own verdict text for the deploy gate's committed-review requirement.

## Verdict: CODE PASS (initial, #80511) + PASS (re-glance, #80524) — cleared to deploy.

### Initial review (bus #80511, content `6f32a31f4a1c0655`)

VERDICT: CODE PASS, two LOW non-blocking nits.

- **Never-raw rule: HELD.** `fetch_ask_delivery_times` selects only `operator_summary`, `triage_summary`, `delegated_to` (+ times/surface/id) — never the raw captured ask. `line = operator_summary or triage_summary or _pending_summary_line(delegated_to)`; the pending line is built from `delegated_to` ALONE, never ask text. `delegated_to` is popped from the row dict, so the client never receives it raw — only the derived `line` + `pending` bool. Locked RED-first by `tests/console/test_db.py::test_delivery_times_never_render_raw_captured_ask`.
- **XSS: clean.** Every data-derived interpolation in builds.js (`collapsedRow`/`deliveryRow`/`notMeasuredRow`/`programmeCard`) goes through `esc()`.
- **Grouping (`groupPending`/`collapsedRow`/`paint`): correct.** Threshold `> 3` → 4+ identical pending lines collapse to one row carrying the true count; ≤3 stay listed. The hero "N measured / M not measured" uses server coverage totals, so collapsing never understates the count. Tested at 2 (stays individual) and 5 (collapses to 1, count=5), pure-fn + painted-DOM.
- **Serialization + honesty: correct.** `id` bigint IDENTITY (JSON-safe); `asked_at`/`delivered_at` → isoformat; `total_hours` cast `::float8`; `not_measured` rows carry no time field. Owner/topic stripping (cc- prefix, trailing -N instance suffix, -coord spell-out, bare/None) correct and tested.
- **Nit 1:** `pending = summary is None` vs `line = summary or _pending_summary_line(...)` — an empty-string (not NULL) summary would disagree between flag and line. Low, non-blocking.
- **Nit 2:** collapse-boundary (`>3`) not pinned by a 3-item/4-item test case. Low, non-blocking.
- Net: no findings against never-raw/XSS/serialization/grouping core — clear to push + proceed to the deploy gate.

### Re-glance (bus #80524, content `e94652bb405b5f88`)

VERDICT: PASS, both nits folded correctly. Core unchanged (never-raw / XSS / serialization / grouping impl byte-identical to the initial CODE PASS); this re-glance covers only the two-nit delta, verified at source + reproduced.

- **Nit 1 — FIXED.** `db.py` now `r["pending"] = not summary` (was `summary is None`), which agrees with `line = summary or _pending_summary_line(...)` on an empty-string summary too. Locked RED-first by `test_delivery_times_pending_flag_agrees_with_line_on_empty_string`.
- **Nit 2 — FIXED.** `builds.js` grouping impl unchanged (threshold still `> PENDING_COLLAPSE_THRESHOLD` = >3, correctly). `builds_view.test.js` now pins the exact boundary: 3-item group stays listed (length 3, no collapse); 4-item group collapses (length 1, collapsed, count 4) — genuinely catches a `>`→`>=` regression.
- **Scope:** only dirty logic file is `db.py` (one line); `builds.js` served code byte-identical to the initial CODE PASS; the rest is the two test files. No scope creep.
- **Reproduced:** `node tests/console/builds_view.test.js` → all passed, incl. "collapse boundary (3 vs 4): ok". `pytest tests/console/test_db.py -k "delivery or empty_string"` → 4 passed (was 3; +1 new). Full `pytest tests/console/` → 461 passed, 279.9s, exit 0.
- Net: both nits in, tested RED-first, nothing else changed. Content `e94652bb405b5f88` is clear to push + proceed to the console deploy gate. No open items.

Repo wingmen-orchestrator, lane/console-revamp (fc-v89), content `e94652bb405b5f88`.
