# cc-quality review — console content `7ee5cf177bdf65a8` (fc-v87 `/builds`, JSON-serialize hotfix)

- **Reviewer:** cc-quality (Head of Quality). Ran on claude-opus-4-8.
- **Target:** content hash `7ee5cf177bdf65a8` = commit `b54c682` on `lane/console-revamp` (= `82d36d2cc43d3f4b` + the serialization hotfix). Hash independently recomputed ✓.
- **Supersedes:** my verdict for `82d36d2cc43d3f4b` (#78575/#78578).
- **Request:** bus 78599 (live `/api/build-status` is 500ing — urgent hotfix).
- **Date:** 2026-10-28.

## Verdict: CODE **PASS** — cleared to deploy. The live 500 is fixed; verified end-to-end through the real code path.

The first ship surfaced a real production bug my stub/SQL-only checks did not catch: `fetch_ask_delivery_times` returned raw `datetime` (`asked_at`/`delivered_at`) and a `Decimal` `total_hours`; `app.py`'s `_json` is plain `json.dumps`, so `/api/build-status` 500s live with `Object of type datetime is not JSON serializable`. **My own prior live confirm ran the SQL query directly but did not exercise the handler's `json.dumps` — that is the gap this hotfix closes, and I've now verified the serialization path end-to-end.**

### The fix (`b54c682`) — verified
Delta vs the PASSed `82d36d2cc43d3f4b` is **exactly** `git diff d1941bc..b54c682 -- nervous_system/console/` = one db.py function (`fetch_ask_delivery_times`), 8+1 lines, nothing else:
- `total_hours` now `::float8` in SQL (Decimal → float).
- A post-query loop converts `asked_at`/`delivered_at` to `.isoformat()` strings.

**Dispositive end-to-end check (read-only, as the real `console_readonly` via `CONSOLE_DB_URL`):** imported `nervous_system.console.db`, called `fetch_build_status()` + `fetch_ask_delivery_times()`, built the exact handler payload `{programmes, delivery_times}`, and `json.dumps`'d it:
- **`json.dumps` OK — 17463 bytes** (no TypeError). The route will serve 200.
- 3 programmes / 12 measured / 50 not-measured.
- **Zero** raw `datetime`/`Decimal` fields remaining across all rows; `asked_at`/`delivered_at` = `str`, `total_hours` = `float`. The `programmes` half is also JSON-safe (int `updated_age_s` + jsonb-derived strings) — no latent second serialization bug.
- Live sample `line` is a curated triage summary, not raw capture — the never-raw fix still holds in practice.

Locked by `tests/console/test_db.py::test_delivery_times_json_serializable` (watched fail with the exact TypeError, then pass) and `test_delivery_times_never_render_raw_captured_ask` — **I ran both: PASS**.

## Carried, still clear
Never-raw fix intact (0 raw `left(ask)` in db.py); migration 125 applied live + 3 programmes visible as `console_readonly` under RLS (confirmed in my `82d36d2cc43d3f4b` addendum); `/api/build-status` bearer-gated; `builds.js` escaping + honesty; Dubai UTC+4; version lockstep (8 carriers = fc-v87). MINORs carried: measured-query no `triage_state`/`closed_at` filter; `asks.html` nav unchanged; app-level owner enforcement.

## Bottom line
`7ee5cf177bdf65a8` is **cleared to deploy** — the live `/api/build-status` 500 is fixed and proven serializable end-to-end against the real store through the actual handler path. Ship the hotfix. Aesthetic eyeball stays orch-console's per 77019.

Ran on claude-opus-4-8.
