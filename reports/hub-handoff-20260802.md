# Hub (cc-orchestrator) Handoff — 2026-08-02 ~07:00Z

Operator asked for a handoff (op#9308) after a marathon session. This is the **operational in-flight snapshot**; DURABLE facts (norms, NETS design, publish mechanism, seed-cred, hosting-commercial) are in the auto-loaded memory files — read `MEMORY.md` first, they carry the "why".

## Posture (binding)
- I am the **hub** (cc-orchestrator) on the VPS (wingmen-core). Can't SSH to the Mini → lane liveness/recycle + share.wingmen.dev publish go through **Nazim (orch-console)**.
- **op#9253: FULL AUTONOMOUS TILT** on the irsyad backlog — drive all items to done; surface to the operator ONLY genuine blockers/decisions-only-he-can-make. **Ping the GAZZABYTE group (`irsyad_support_send.sh`) as each item lands**, not the operator's phone.
- **Money/pricing/hosting = cc-finance's lane** (op#9217) — hub stays OUT; only relay build-side inputs to cc-finance via bus, never the operator. (finance-console mis-routes to my queue — mark-read, don't act.)
- Money-path/security = MY gate: verify-at-source always; grants verified at `execution_status='granted'`; deployed==proven byte-identical. **CAI-684 norm**: reversible security containment of a LIVE exposure = act-now-ratify-after (5 bounds); irreversible/delete/money = gate-first ALWAYS.

## In-flight streams
| Stream | Owner | State | Next |
|---|---|---|---|
| **Tin-edit correction (client=BOTH)** | cc-irsyad | CODE-COMPLETE (weekly feat/tabung-tin-correct-report-detail + jumaat feat/tabung-tin-correct-jumaat, review-clean); PRs NOT open yet (rebasing onto main) | rebase → 2 focused PRs (weekly, then jumaat stacked) → CI self-seeds irsyad-qa + runs BOTH e2e (on the SYNTHTEST gate) → I do independent hub-review (canCorrect=canPrepare&&draft both surfaces + server RPC backstop + frozen-on-signed) + deployed==proven → **ship BOTH together** (no weekly-only) → **ping Gazzabyte + flip board to Done**. NON-cai-gated (mig134 already gated+live). tabung-CORRECTNESS gate red-by-design (needs operator secret E2E_QAC_ADMIN_PASSWORD) = ignore like shop-synthtest. |
| **Platform T2 school-split** | cc-ihsanos | **PR #223 OPEN (not merged)**; cc-ihsanos QUIET since 08-01 22:08Z (~7h — LIVENESS-CHECK via Nazim, may be idle) | merge #223 over the pre-existing shop-synthtest red → Vercel both green (deployed==proven) → live on silo |
| **Fees & student-payment reports** | cc-ihsanos | spec queued (after T2) | verify-at-source what fee/payment DATA actually exists (mostly empty per SMS-quote) before designing; spec → me |
| **Payment layer + eNETS** | hub via cc-irsyad AFTER tin-edit | design **cai-RATIFIED (CAI-682)**; specs sourced (eNETS2 HMAC+KeyID+eventId+OAuth2, Direct-Debit); topology = in-repo `src/shared/payment/`; interface v0 in `scratchpad/payment-provider-interface-v0.ts` | build after tin-edit lands. eNETS creds/terms from IRSYAD-via-Gazzabyte NEXT WEEK → secure handoff (one-time-secret link → server-only org.settings insert, NEVER chat). 2 HARD pre-grant: webhook-provenance adversarial proof (replay/forge REJECTED, secret-absent FAILS-CLOSED) + no-riba on terms. NO fast-path. Nazim builds Storefront-HitPay against v0 in parallel. |
| **Client 10-item backlog** | triaged (cc-irsyad #14338) | order at tin-edit ship | #1 bank-keywords=quick win; #8/#9 school-RBAC→coordinate cc-ihsanos; #2/#3/#4/#10 money/PII→cai-route when scoped; #7 defer(client KIV). Client wants rolling updates in-group + on the board; offered reprioritise. |
| **Incident #14335** | closed+RATIFIED | guard #224 MERGED + rewire #225 MERGED + all goumlyne synthetics DISABLED + reuse-clean(no scrub) | env-inject (cc-storefront, in flight) + **12006-(b) DELETE = operator-gated, NAZIM's thread**; ceayj = env-inject-forward + rotate-at-migration |

## Done this session (deployed==proven / live)
mig134 tin-edit DB relax (goumlyne maxmig **134**) · module-toggle #222 + sales@gazzabyte enrolled on goumlyne (Gazzabyte controls Irsyad modules) · CI-off-client-silo #218 · docs `share.wingmen.dev/r/platform-docs` · status board `/r/gazzabyte-status` (refreshed 2 Aug 13:35, verified) · fund-matching/preparer-signing/bank-upload live.

## Operator todos (non-blocking — `scratchpad/operator_todos.txt`)
1. GH secret **E2E_QAC_ADMIN_PASSWORD** (greens tabung-correctness gate; #218 pattern).
2. Rotate the throwaway **irsyad-qa DB password** (Settings>Database>Reset; low urgency, empty QA project). Creds in `scratchpad/qa_*.txt`.
3. eNETS product/creds/terms — Irsyad returning next week via Gazzabyte.

## Mechanics
- **Each turn**: `operator_log.unprocessed()` → answer inbound → `mark_handled_through(<max_id>)`. Reply on the channel the operator/client used.
- **Comms**: `scripts/tg_send.sh` (operator, hub pen-iv, orch-channel); `scripts/irsyad_support_send.sh` (Gazzabyte group, client-facing — NEVER paste secrets/internal terms); Nazim relays lane nudges (Mini-local).
- **Bus watcher**: `scratchpad/await_gate.sh` (edit THRESH=maxid, run via Bash run_in_background:true). Launch ONE per turn; the `&`-orphan trap bit me repeatedly — always run_in_background:true, never `&`.
- **Publish to share.wingmen.dev**: gh-api PUT the file to branch `share/gazzabyte-platform-docs` → Nazim runs `publish_share.sh <file> <slug> "<title>" "<tag>"` on the Mini → I live-verify (blob + render + client-clean). Slugs: platform-docs, gazzabyte-status.
- **Guarded money-path apply**: direct psycopg to goumlyne, `--expect-ref goumlynecruxrlmzlntp` + blob-verify + forward-only; manual conn control (psycopg3 with-block commits on exit — see memory).

## Lanes
cc-irsyad (irsyad/tabung; on tin-edit → then payment layer) · cc-ihsanos (platform; T2 #223 + fees-reports; QUIET 7h, check) · cc-storefront (CI-off-silo/shop; env-inject + shop-cell cluster deferred) · cc-finance (money/pricing; own channel @wingmen_revenue_bot) · Nazim/orch-console (SRE, incident-track, operator-(b), lane-spin, Shipforge/Storefront/HitPay).

**Immediate next**: cc-irsyad's tin-edit PRs → e2e → my verify → SHIP (the near-term win + first Gazzabyte land-ping); and liveness-check cc-ihsanos (#223 merge stalled 7h).
