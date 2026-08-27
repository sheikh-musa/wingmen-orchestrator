# cc-quality review — console fc-v52 (drain board + /api/assign + grouping)

**Verdict: ✅ PASS** — the new mutation surface (`POST /api/assign` → `console_assign.py`) is auth-gated, charset-validated before the subprocess, injection-safe (list-not-shell + bound-param SQL), fails closed on unknown agent, leaks no secret, and writes a well-formed directive bus row; the drain-board query/shaping is correct + scoped; client grouping/coords-above/bloat-tap are clean; 62 + 6 + 3 tests green; renders confirm. No blockers. My standing irsyad version LOW is resolved. One tiny optional hardening.

- **Reviewer:** cc-quality (Head of Quality) — content-hash GATE-4. **This batch TOUCHES app.py + db.py + a new script** (unlike v50/v51), so it got a full security pass.
- **Request:** bus id 21726 (P2, orch-console).
- **Content hash:** `b52beb180ed061e6` (fc-v52).
- **Commit:** `1ddc452` (worktree agent-a0029b4a…), parent = fc-v51 tip (FF-clean). +585/-74 across app.py, db.py, console_assign.py (new), fleet.html/js, irsyad/lanes/sw version, test_app.py (+9 tests), 4 render PNGs.
- **diff SHA-256:** `59702bf156e299517f79fbe3e3ac01ede30ad09fcdb2c73fc2125ecf328fbfa7`
- **Reviewed (UTC):** 2026-08-14T23:37:20Z
- **Method:** verify-not-assert — read console_assign.py in full + the app.py handler + db.py query, confirmed the auth gate ordering and the no-subprocess-on-bad-agent path, ran the suites, and eyeballed the drain-board render.

---

## Security crux — `POST /api/assign` (the new write path)

Every property the request asked me to check holds:

| Check | Result | Evidence |
|---|---|---|
| Auth-gated | ✅ | `do_POST`: allowlist (404 if not listed, `/api/assign` added) → `if not self._authed(): return 401` → dispatch. Same gate as every mutation. Test `test_api_assign_requires_auth`. |
| Agent charset validated BEFORE subprocess | ✅ | `re.match(r"^[A-Za-z0-9_-]{1,64}$", agent)` → 400 before any spawn; no shell metachar can reach argv. Test `test_api_assign_bad_agent_400_no_subprocess` proves no subprocess runs for a bad agent. |
| No shell / no injection | ✅ | `subprocess.run([interp, script, agent, ask, "--priority", priority], timeout=30)` — a LIST, never `shell=True`. In `console_assign.py` every value (agent/subject/body/priority) is a **bound SQL param**; the ask is argv, never interpolated. |
| Unknown agent → 400 | ✅ | `console_assign.py` does `SELECT 1 FROM agents WHERE id=%s` and `sys.exit(2)` if absent; the handler maps `returncode==2 → 400`, else 500. |
| Empty ask / priority | ✅ | empty ask → 400 (`test_api_assign_empty_ask_400`); priority not in {P0,P1,P2} → coerced to P2. |
| No service-key leak | ✅ | DSN read from env, never printed; success response is only `{ok, agent, id}`. Password/service-key never appears in stdout (only `assigned agent_messages #N`) or stderr (unknown-agent message carries no secret); psycopg omits the password from connection errors. |
| Well-formed bus row | ✅ | `from_agent='orch-console'` (hardcoded — no attribution spoofing), `message_type='directive'`, `requires_response=true`, `is_test=false`. So the target drains it via its normal inbox (`to_agent=<id> AND read_at IS NULL`), closing the loop. |
| Audited | ✅ | `auth.audit(..., "/api/assign:<agent>:<pri>", "run")` before, then `200`/`400`/`500`. |

## drain-board data path
- **db.build_inbox_backlog_query**: `WHERE read_at IS NULL AND is_test IS NOT TRUE AND lower(to_agent) NOT IN ('musa','operator') ORDER BY to_agent, id DESC LIMIT 400` — correct (live pending), scoped (excludes drill traffic + the operator's own pseudo-inbox), capped, static (no param → no injection). Flags `needs_response` (`requires_response AND responded_at IS NULL`) + `assigned` (`from_agent='orch-console'`). Read-only (console session is SELECT-only by construction). Tests: `test_build_inbox_backlog_query_shape`.
- **app._drain_board**: groups by `to_agent`, caps `items` at 5 with a `more` count, sorts needs-response-first then by depth then id (exception-first). Guarded in `_fleet_payload` (a failure → empty board, never blanks the aggregate). Tests: `test_drain_board_groups_sorts_and_caps`, `test_drain_board_empty_is_empty_list`.

## Client + other changes
- **Assign UI** — target chosen from a dropdown of LIVE bodies (lanes→base_agent_id, coords→agent_id), a composed ask, an `assignBusy` double-send guard, single `POST /api/assign {agent, ask, priority}`. One target, one ask — no mass-assign, no accidental one-tap (the operator composes + taps Assign).
- **Lane family grouping** — `l["family"] = _family_of(session/lane/base/agent) or "~"` (the SAME helper the token resolver + GAP-B use); sort `(family, flagged, bucket, agent_id)` groups a family's instances together while keeping working-first/flagged-up within each. Coords-above-lanes + bloat-tap-reveal-top-3 are client-only, sound.
- **Version fc-v52 synced** across sw.js/fleet.js/lanes.html **and irsyad.html/irsyad.js** — the standing irsyad-badge LOW I flagged across fc-v48→v51 is now **resolved**.

## Verification
- `pytest tests/console/test_app.py` → **62 passed** (9 new, incl. the assign/drain security tests above).
- `node fleet_pace.test.js` → 6; `node fleet_topbloat.test.js` → 3.
- Render (drain-board): "10 pending · 4 bodies", per-body groups sorted exception-first, ASSIGNED (console-origin) + REPLY (needs_response) badges, item cap + "+N more pending…", inline assign form — matches the design.

## Finding (LOW · optional hardening, not a blocker)
On a 500 from the script, the handler returns `error: (r.stderr or "")[-160:]` to the client. This is authed-only and psycopg does not echo the password, so it is **not a service-key leak** — but on a DB-connection error the stderr tail could surface host/dbname to the operator. Optional: return a generic `"assign failed (db error)"` instead of the stderr tail. No action required for ship.

## Bottom line
The first console change to add a real write endpoint, and it's built to the fleet's vetted-script standard: auth → charset-validate → list-subprocess → bound-param write, with the agent existence re-checked server-side and each guard locked by a test. The drain board correctly turns "Your asks" into a live, self-shrinking per-body work board, and a console assignment is a real bus row that the target actually drains. **Ships.** Only a tiny optional error-sanitization nit remains — and my long-standing irsyad version LOW is finally closed.

— cc-quality
