# Hub (cc-orchestrator) Restore-Point Handoff — 2026-08-03 ~00:2xZ

Written for a fresh clear (op#9512, Nazim clears via reset_orch.sh). READ THIS IN FULL, then CLAUDE.md, then reconcile inboxes. DURABLE facts (norms, NETS, publish mechanism, seed-cred, hosting-commercial, client-scoped-reports, hub-go-to-mini-lane-nudge) are in the auto-loaded `MEMORY.md` — read those first, they carry the "why".

## Identity / posture (confirm from YOUR OWN .env + substrate, not this string)
- **hub** (cc-orchestrator) on the VPS **wingmen-core**, tmux `orch`. Hold `orch_lease` (verify: `python -c "from scripts.lib import orch_lease; print(orch_lease.check())"`).
- `fleet_health_lease` held by **cc-fleet-health@Sheikhs-Mini** → watchdog/fleet-status pens DEFERRED (do NOT run them).
- **op#9253 FULL AUTONOMOUS TILT** on the irsyad backlog; ping the GAZZABYTE group (`irsyad_support_send.sh`) as each client-visible item lands, not the operator.
- **Money/pricing/hosting = cc-finance** (op#9217) — hub stays out; relay build-side inputs only.
- Money-path/security/PII/residency = MY gate: verify-at-source; grants at `execution_status='granted'`; deployed==proven byte-identical. CAI-684 norm. **PII/residency: never put real PII in a non-compliant store without a cai clearance** (held all session — CAI-687 ADCDA console, CAI-525 BAPT).

## SHIPPED this session (all deployed==proven, verified at source)
- **Tin-correction** weekly #226 (3477f94) + jumaat #227 (8e1777a) — both surfaces LIVE; adversarial review 4/4; Gazzabyte pinged.
- **School-page-split T2 #223** (3070a52) — live on silo (op#9029 complete).
- **Hadi RLS fix #228** (e44dade + mig135 on ceayj) — SYSTEMATIC all-merchant soft-delete bug; empirical prod leak-check (anon=0/cross-org=0) + cai CAI-686/verified + hash-chained audit annotation (cc-storefront, audit 1053). CLOSED.
- **Edit Bank Keywords #230** (80149cb) — client backlog #1; authz-reviewed (server gate); Gazzabyte + operator pinged.
- **Dialog dedup #229** (4132ccc).
- **Entity-derived-org tenancy #231** (0501a71) — CAI-688/689; adversarial review 3/3; jumaat staff-only tightening confirmed (affected 2 accts = DISABLED test, zero real impact). Batched #1 getOrgContext ordering.
- **BAPT-4** (cosem-adcda a1da261, /bapt) — group filter + masked PII-reducing display + no-E A/B/C/D/F scale (op#9502, D pass-floor, F only fail). Deployed==proven on hosted CI (ubuntu-latest), operator-confirmed on device. Prod content + functional D-boundary grading verified.
- mig134→irsyad-qa (QA parity) · client status board refreshed (share.wingmen.dev/r/gazzabyte-status) · cc-ihsanos + cc-irsyad recycled.

## IN-FLIGHT / OPEN (with owners)
| Stream | Owner | State / next |
|---|---|---|
| **BAPT-4 /bapt scoping refinement (op#9510)** | cc-cosem-adcda (Nazim last-mile) | scope /bapt to ONLY the BAPT-4 candidates from the 3 namelists; deploy via **ubuntu-latest** (Studio WAN FLAPPING/unreliable — SRE saw 200, Nazim saw dead; stay on hosted). Trial is TOMORROW AM. |
| **cc-irsyad next backlog** | cc-irsyad | I directed it (14582/14585) to scope the next-ready 10-item backlog item (money/PII → spec → I cai-route) + report externally-blocked items. Awaiting its reply. |
| **eNETS/payment build (CAI-682)** | hub via cc-irsyad | PARKED on client payment-type detail (op confirmed A-F rig etc. but eNETS specs = ~next week via Gazzabyte). Relay the instant it lands. |
| **Operator ADCDA console hosting (CAI-687)** | operator DECISION (parked) | operator chose "host interactive"; cai ruled DEDICATED UAE-residency-compliant gated surface + noindex + time-boxed. Blocked: no UAE-compliant host + no cc-infra lane = real provisioning. Interim: fresh console FILE delivered to Ray. Operator deciding file-now vs build-host vs host+live-feed. |
| **cc-ihsanos fees/student-payment reports spec** | cc-ihsanos | in progress (verify-at-source what data exists first). |
| **getOrgContext .limit(1) sweep** | cc-ihsanos (platform hr-*/sch-*) | TRACKED debt (CAI-688), NON-URGENT, entity-derived-org fix, after fees-reports. |
| **Studio WAN death / durable runner fix** | cc-fleet-health (SRE) | SRE owns KeepAlive+offline-signal fix. VPS-runner permanent-backup needs ROOT = operator fork (I said I'd surface as a non-urgent resilience option). cosem workflows on ubuntu-latest TEMP (restore self-hosted after WAN stable). |
| cc-storefront | cc-storefront | Hadi audit annotation DONE. env-inject + shop-cell cluster deferred. |

## Mechanics
- **Each turn**: read `operator_log.unprocessed()` → answer via `scripts/tg_send.sh` → `mark_handled_through(<max_id>)`; AND drain the bus (unread = `read_at IS NULL`, mark read after processing). Reply on the channel the operator/client used.
- **Bus watcher**: `scratchpad/await_gate.sh` sweeps `read_at IS NULL` (gap-proof — do NOT use `id>THRESH`, it misses mid-turn arrivals). Run ONE per turn, `run_in_background:true`, never `&`. (Scratchpad is session-local — recreate it; see the script content pattern.) CI/Vercel pollers: `ci_wait.sh <pr> [owner/repo]` (default ihsanos — pass the repo!), `vercel_wait.sh <sha>`.
- **Comms**: `tg_send.sh` (operator, hub pen-iv); `irsyad_support_send.sh` (Gazzabyte group, client-facing — NEVER secrets/internal); Nazim relays Mini-lane nudges. **Standing arrangement (Nazim): he PROACTIVELY nudges any hub→Mini-lane directive** (see memory) — but a hub bus GO can still stall an idle lane; verify lane liveness before reporting "working" (bit me twice: cc-storefront + cc-irsyad).
- **Prod migration apply**: direct psycopg, `--expect-ref` + `--expect-blob` + forward-only; verify DSN ref + a positive-identity marker before ANY write (I have GOUMLYNE_DATABASE_URL + IHSANOS_PROD_DATABASE_URL=ceayj; irsyad-qa DSN was in the prior scratchpad qa_dsn.txt; mgmt API 403s on irsyad-qa).
- **Independent adversarial review** on security/access-control/money changes (operator standard): spawn a general-purpose agent tasked to FALSIFY the claims; verify its findings + own the conclusion (it can be wrong — it had weekly/jumaat flipped on #231; verify at source).

## My own accuracy notes (verify-not-assert)
Corrected TWICE this session: told operator cc-irsyad was "mid-build" (was idle-stalled on an unread bus GO) + called 2 accts "live" (were disabled test accts). Fully verify a qualifier ("live", "working") before asserting it, not just the adjacent fact. Both caught by looping the context-holder (Nazim, cai) — keep doing that.
